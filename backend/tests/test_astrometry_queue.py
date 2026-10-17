"""
Plate-solve queue: a bulk rescan must not park one Celery task per image, a
permanent upload error must not retry forever, and polling must not hold a
worker slot.
"""

import asyncio
import errno
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.tasks import astrometry


class FakeLock:
    def acquire(self, **kw):
        return True

    def release(self):
        pass


class FakeRedis:
    def __init__(self):
        self.zsets = {}

    def lock(self, *a, **kw):
        return FakeLock()

    def get(self, key):
        return None

    def zadd(self, key, mapping, nx=False):
        z = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if nx and member in z:
                continue
            added += member not in z
            z[member] = score
        return added

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zpopmin(self, key, count=1):
        z = self.zsets.get(key, {})
        items = sorted(z.items(), key=lambda kv: kv[1])[:count]
        for member, _ in items:
            del z[member]
        return items

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def zremrangebyscore(self, key, lo, hi):
        z = self.zsets.get(key, {})
        for member in [m for m, s in z.items() if s <= hi]:
            del z[member]

    def pending(self):
        z = self.zsets.get(astrometry.PENDING_KEY, {})
        return [int(m) for m, _ in sorted(z.items(), key=lambda kv: kv[1])]


@pytest.fixture
def fake_redis():
    fake = FakeRedis()
    with patch.object(astrometry, "_redis", return_value=fake):
        yield fake


def _http_error(code):
    request = httpx.Request("POST", "http://nova.astrometry.net/api/upload")
    return httpx.HTTPStatusError("err", request=request, response=httpx.Response(code, request=request))


# --- error classification ---------------------------------------------------

@pytest.mark.parametrize("exc", [
    httpx.ConnectTimeout("t"),
    httpx.ReadError("r"),
    _http_error(502),
    _http_error(429),
    BlockingIOError(errno.EAGAIN, "busy"),
])
def test_transient_errors_are_retryable(exc):
    assert astrometry.is_retryable_error(exc)


@pytest.mark.parametrize("exc", [
    FileNotFoundError(errno.ENOENT, "gone"),
    ValueError("Failed to load image for conversion"),
    ValueError("Astrometry login failed: bad apikey"),
    _http_error(400),
])
def test_permanent_errors_are_not_retryable(exc):
    assert not astrometry.is_retryable_error(exc)


# --- hints --------------------------------------------------------------------

def _image(**kw):
    base = dict(id=1, ra_center_degrees=None, dec_center_degrees=None, field_radius_degrees=None,
                pixel_scale_arcsec=None, width_pixels=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_scale_hint_is_field_width_in_degrees():
    hints = astrometry.build_solve_hints(_image(pixel_scale_arcsec=1.8, width_pixels=6000), "NONE")
    assert hints["scale_units"] == "degwidth"
    assert hints["scale_lower"] == pytest.approx(3.0 * 0.9)
    assert hints["scale_upper"] == pytest.approx(3.0 * 1.1)


def test_failed_image_gets_a_blind_solve():
    image = _image(ra_center_degrees=10.0, dec_center_degrees=41.0, pixel_scale_arcsec=1.8, width_pixels=6000)
    assert astrometry.build_solve_hints(image, "FAILED") == {}


# --- pending set and feeder ---------------------------------------------------

def test_enqueue_dedupes_and_puts_priority_first(fake_redis):
    assert astrometry.enqueue_astrometry([5, 6, 7]) == 3
    assert astrometry.enqueue_astrometry([1, 2], priority=True) == 2
    assert astrometry.enqueue_astrometry([6, 7, 8]) == 1  # re-running a bulk rescan
    assert fake_redis.pending() == [1, 2, 5, 6, 7, 8]
    astrometry.enqueue_astrometry([9], front=True)
    assert fake_redis.pending()[0] == 9


def _sync_session(active):
    session = MagicMock()
    session.query.return_value.filter.return_value.scalar.return_value = active
    cm = MagicMock()
    cm.__enter__.return_value = session
    return cm


def test_feeder_only_fills_free_slots(fake_redis):
    astrometry.enqueue_astrometry(range(100, 120))
    with patch.object(astrometry, "SessionLocal", return_value=_sync_session(active=5)), \
            patch.object(astrometry, "_max_submissions", return_value=8), \
            patch.object(astrometry.astrometry_task, "delay") as delay:
        assert astrometry.feed_astrometry_queue.run() == 3
        assert [c.args[0] for c in delay.call_args_list] == [100, 101, 102]
        # The three just dispatched still hold their slots until they claim one.
        assert astrometry.feed_astrometry_queue.run() == 0
    assert fake_redis.zcard(astrometry.PENDING_KEY) == 17


# --- upload -------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


def _async_session(active, image):
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[FakeResult(active), FakeResult(image)])
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _submit(task, image, upload, active=0):
    with patch.object(astrometry, "_solver_config", return_value=("nova", "key", astrometry.NOVA_API_URL)), \
            patch.object(astrometry, "_max_submissions", return_value=8), \
            patch.object(astrometry, "AsyncSessionLocal", return_value=_async_session(active, image)), \
            patch.object(astrometry.AstrometryService, "upload_file", upload), \
            patch.object(astrometry, "_set_status", AsyncMock()) as set_status, \
            patch.object(astrometry.monitor_astrometry_task, "apply_async") as monitor:
        try:
            result = asyncio.run(astrometry._submit(task, image.id if image else 1))
        except RuntimeError as e:
            result = e
    return result, set_status, monitor


def _task(retries=0):
    task = MagicMock()
    task.request.retries = retries
    task.max_retries = astrometry.MAX_UPLOAD_RETRIES
    task.retry.return_value = RuntimeError("retry")
    return task


def test_permanent_upload_error_fails_without_retry(fake_redis):
    task = _task()
    image = SimpleNamespace(**vars(_image()), file_path="/x.cr2", astrometry_status="NONE", plate_solve_provider=None)
    upload = AsyncMock(side_effect=ValueError("Failed to load image for conversion"))
    result, set_status, monitor = _submit(task, image, upload)
    assert result["status"] == "failed"
    task.retry.assert_not_called()
    set_status.assert_awaited_with(image.id, "FAILED")
    monitor.assert_not_called()


def test_transient_upload_error_retries_with_status_restored(fake_redis):
    task = _task()
    image = SimpleNamespace(**vars(_image()), file_path="/x.cr2", astrometry_status="NONE", plate_solve_provider=None)
    upload = AsyncMock(side_effect=httpx.ConnectTimeout("t"))
    result, set_status, _ = _submit(task, image, upload)
    assert isinstance(result, RuntimeError)
    task.retry.assert_called_once()
    set_status.assert_awaited_with(image.id, "NONE")


def test_transient_upload_error_gives_up_after_max_retries(fake_redis):
    task = _task(retries=astrometry.MAX_UPLOAD_RETRIES)
    image = SimpleNamespace(**vars(_image()), file_path="/x.cr2", astrometry_status="NONE", plate_solve_provider=None)
    upload = AsyncMock(side_effect=httpx.ConnectTimeout("t"))
    result, set_status, _ = _submit(task, image, upload)
    assert result["status"] == "failed"
    task.retry.assert_not_called()


def test_successful_upload_hands_off_to_monitor(fake_redis):
    task = _task()
    image = SimpleNamespace(**vars(_image()), file_path="/x.cr2", astrometry_status="NONE", plate_solve_provider=None)
    upload = AsyncMock(return_value={"subid": 42})
    result, set_status, monitor = _submit(task, image, upload)
    assert result == {"status": "submitted", "submission_id": 42}
    set_status.assert_awaited_with(image.id, "PROCESSING", astrometry_submission_id="42")
    args = monitor.call_args.args[0]
    assert args[:2] == (42, image.id)


def test_throttled_image_goes_back_to_front_of_pending(fake_redis):
    astrometry.enqueue_astrometry([50, 51])
    upload = AsyncMock()
    result, _, _ = _submit(_task(), None, upload, active=8)
    assert result["status"] == "throttled"
    assert fake_redis.pending()[0] == 1
    upload.assert_not_called()


# --- monitor ------------------------------------------------------------------

def _monitor(check, started_at):
    # Tasks use the thread's loop like the worker does; asyncio.run in the tests above clears it.
    asyncio.set_event_loop(asyncio.new_event_loop())
    with patch.object(astrometry, "_check_submission", check), \
            patch.object(astrometry, "_set_status", AsyncMock()) as set_status, \
            patch.object(astrometry.monitor_astrometry_task, "apply_async") as reschedule, \
            patch.object(astrometry.feed_astrometry_queue, "delay") as feed:
        result = astrometry.monitor_astrometry_task.run(42, 7, started_at)
    return result, set_status, reschedule, feed


def test_monitor_reschedules_while_solving():
    import time
    result, set_status, reschedule, feed = _monitor(AsyncMock(return_value=None), time.time())
    assert result == {"status": "pending"}
    reschedule.assert_called_once()
    set_status.assert_not_awaited()
    feed.assert_not_called()


def test_monitor_times_out_and_frees_the_slot():
    import time
    started = time.time() - astrometry.MONITOR_TIMEOUT - 1
    result, set_status, reschedule, feed = _monitor(AsyncMock(return_value=None), started)
    assert result == {"status": "timeout"}
    set_status.assert_awaited_with(7, "FAILED")
    reschedule.assert_not_called()
    feed.assert_called_once()


def test_monitor_finish_tops_up_the_queue():
    import time
    done = AsyncMock(return_value={"status": "solved", "job_id": 1})
    result, _, reschedule, feed = _monitor(done, time.time())
    assert result["status"] == "solved"
    reschedule.assert_not_called()
    feed.assert_called_once()


def test_cleanup_cutoff_outlasts_monitor():
    assert astrometry.STUCK_AFTER.total_seconds() > astrometry.MONITOR_TIMEOUT
