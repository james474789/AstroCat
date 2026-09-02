"""
Tests for the scanner resilience changes:

1. _is_transient_error classifies retryable storage hiccups (NAS EAGAIN etc.)
   vs permanent extraction failures (malformed headers).
2. _process_image_impl re-raises transient errors instead of writing a minimal
   record, so exhausted retries leave the file unindexed and the next scan
   naturally re-queues it (self-healing).
3. _clear_stale_indexer_running_flag clears a stuck "scanning" flag for tasks
   killed before their finally block ran, while keeping it alive for scans
   with fresh heartbeats and active bulk operations.
"""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from astropy.io.fits.verify import VerifyError

from app.tasks.indexer import _is_transient_error, _process_image_impl
from app.tasks.astrometry import _clear_stale_indexer_running_flag

from app.worker import celery_app  # noqa: F401  (import order ensures app/worker loads)


class TestIsTransientError:
    def test_blocking_io_is_transient(self):
        assert _is_transient_error(BlockingIOError(11, "Resource temporarily unavailable")) is True

    def test_timeout_and_connection_errors_are_transient(self):
        assert _is_transient_error(TimeoutError("timed out")) is True
        assert _is_transient_error(ConnectionResetError("reset")) is True

    def test_oserror_with_transient_errno_is_transient(self):
        import errno
        assert _is_transient_error(OSError(errno.ETIMEDOUT, "timed out")) is True
        assert _is_transient_error(OSError(errno.ECONNRESET, "reset")) is True

    def test_verify_error_is_permanent(self):
        assert _is_transient_error(VerifyError("Unparsable card (CCD-TEMP)")) is False

    def test_opaque_io_error_is_not_transient(self):
        import errno
        # ENOENT / EACCES are permanent: the file is gone or unreadable
        assert _is_transient_error(OSError(errno.ENOENT, "No such file")) is False
        assert _is_transient_error(OSError(errno.EACCES, "Permission denied")) is False


class TestProcessImageImplTransient:
    def test_transient_extraction_error_reraises(self, tmp_path):
        target = tmp_path / "ok.fits"
        target.write_bytes(b"SIMULATED")

        with patch(
            "app.tasks.indexer.get_extractor",
            side_effect=BlockingIOError(11, "Resource temporarily unavailable"),
        ), patch("app.tasks.indexer._record_process_failure") as mock_record:
            with pytest.raises(BlockingIOError):
                _process_image_impl(str(target))
            # No minimal-record path taken for transient errors
            mock_record.assert_not_called()

    def test_permanent_extraction_error_records_minimal(self, tmp_path):
        target = tmp_path / "bad.fits"
        target.write_bytes(b"SIMULATED")

        with patch(
            "app.tasks.indexer.get_extractor",
            side_effect=VerifyError("Unparsable card (CCD-TEMP)"),
        ), patch("app.tasks.indexer._record_process_failure") as mock_record, \
             patch("app.tasks.indexer.SessionLocal") as mock_sl, \
             patch("app.tasks.indexer.SyncCatalogMatcher") as mock_matcher:

            mock_sl.return_value.__enter__.return_value = MagicMock()

            result = _process_image_impl(str(target))
            assert result["status"] == "completed"
            mock_record.assert_called_once()


class _FakeRedis:
    """Minimal redis client stub for the watchdog tests."""

    def __init__(self, data):
        self.d = dict(data)

    def get(self, key):
        return self.d.get(key)

    def set(self, key, value):
        self.d[key] = value

    def hget(self, key, field):
        h = self.d.get(key)
        return h.get(field) if isinstance(h, dict) else None

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        for key in list(self.d.keys()):
            if key.startswith(prefix):
                yield key


class TestStaleIndexerFlagWatchdog:
    def _run(self, data):
        fake = _FakeRedis(data)
        with patch("app.tasks.astrometry.redis.from_url", return_value=fake):
            _clear_stale_indexer_running_flag()
        return fake

    def test_flag_not_set_is_noop(self):
        fake = self._run({})
        assert fake.d.get("indexer:is_running") is None

    def test_fresh_heartbeat_keeps_flag(self):
        fresh = datetime.utcnow().isoformat()
        fake = self._run({"indexer:is_running": "1", "indexer:last_heartbeat": fresh})
        assert fake.d["indexer:is_running"] == "1"

    def test_stale_heartbeat_clears_flag(self):
        stale = (datetime.utcnow() - timedelta(minutes=45)).isoformat()
        fake = self._run({"indexer:is_running": "1", "indexer:last_heartbeat": stale})
        assert fake.d["indexer:is_running"] == "0"

    def test_no_heartbeat_but_recent_bulk_keeps_flag(self):
        recent = int(time.time())
        fake = self._run({
            "indexer:is_running": "1",
            "bulk:match:abc123": {"status": "running", "updated_at": str(recent)},
        })
        assert fake.d["indexer:is_running"] == "1"

    def test_no_heartbeat_and_stale_bulk_clears_flag(self):
        stale = int(time.time()) - 3600
        fake = self._run({
            "indexer:is_running": "1",
            "bulk:match:abc123": {"status": "running", "updated_at": str(stale)},
        })
        assert fake.d["indexer:is_running"] == "0"

    def test_missing_heartbeat_no_bulk_clears_flag(self):
        fake = self._run({"indexer:is_running": "1"})
        assert fake.d["indexer:is_running"] == "0"