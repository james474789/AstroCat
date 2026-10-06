"""A file that keeps killing its worker (OOM) must stop being re-queued forever."""

from unittest.mock import MagicMock, patch

from app.tasks import indexer


class FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, *a):
        pass

    def delete(self, key):
        self.store.pop(key, None)

    def lpush(self, *a):
        pass

    def ltrim(self, *a):
        pass


def _run(fake, impl):
    with patch.object(indexer.redis, "from_url", return_value=fake), \
            patch.object(indexer, "_process_image_impl", impl):
        return indexer.process_image.run("/data/x/big.dng")


def test_parks_file_after_repeated_unfinished_starts():
    fake = FakeRedis()
    # Simulate SIGKILL: the counter is bumped on each start but never cleared.
    with patch.object(indexer.redis, "from_url", return_value=fake):
        for _ in range(indexer.MAX_CRASH_STARTS):
            assert indexer._crash_loop_exceeded("/data/x/big.dng") is False
    impl = MagicMock()
    result = _run(fake, impl)
    assert result["status"] == "skipped"
    assert result["reason"] == "repeated_worker_crash"
    impl.assert_not_called()


def test_clean_runs_never_trip_the_guard():
    fake = FakeRedis()
    impl = MagicMock(return_value={"status": "completed"})
    for _ in range(indexer.MAX_CRASH_STARTS * 3):
        assert _run(fake, impl) == {"status": "completed"}
    assert fake.store == {}


def test_counter_cleared_when_impl_raises():
    fake = FakeRedis()
    impl = MagicMock(side_effect=ValueError("bad header"))
    for _ in range(indexer.MAX_CRASH_STARTS * 2):
        try:
            _run(fake, impl)
        except ValueError:
            pass
    assert not [k for k in fake.store if k.startswith("indexer:starts:")]
