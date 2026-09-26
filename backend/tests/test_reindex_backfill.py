"""
Tests for the self-healing catch-up wired into reindex_all (F1/F2 follow-up):

A full rescan now also runs the incremental frame-type/target backfills, so
rows that predate those columns (e.g. after upgrading to a build that added
them, with no one having run the one-off backfill scripts) get filled in
automatically instead of silently staying unclassified/unassigned forever.

Both calls must stay in incremental mode (reclassify_all/process_all=False)
so a routine rescan never clobbers a user override or forces an expensive
full re-resolve -- that remains the job of the explicit Admin "re-resolve
all" button / Celery task.
"""

from unittest.mock import MagicMock, patch

from app.config import settings
from app.tasks.indexer import reindex_all


def test_reindex_all_runs_incremental_backfills_after_scan(monkeypatch):
    monkeypatch.setattr(settings, "image_paths", "/data/mount1")

    fake_scan_result = {"files_found": 0, "files_queued": 0, "files_removed": 0}

    with patch("app.tasks.indexer.redis") as mock_redis, \
         patch("app.tasks.indexer.os.path.exists", return_value=True), \
         patch("app.tasks.indexer._scan_directory", return_value=fake_scan_result), \
         patch("app.tasks.indexer.update_mount_stats"), \
         patch("app.scripts.backfill_frame_types.backfill_frame_types") as mock_ft_backfill, \
         patch("app.scripts.backfill_targets.backfill_targets") as mock_target_backfill:
        mock_redis.from_url.return_value = MagicMock()
        mock_ft_backfill.return_value = {"total": 0, "updated": 0}

        reindex_all.run()

    mock_ft_backfill.assert_called_once_with(dry_run=False, reclassify_all=False)
    mock_target_backfill.assert_called_once_with(process_all=False)


def test_reindex_all_skips_backfill_when_scan_times_out(monkeypatch):
    """
    A soft-timeout during the directory walk re-raises immediately -- the
    backfill step must not run that cycle (it'll be picked up by the next
    successful scan) so it doesn't add more work on top of an already
    over-budget task.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    monkeypatch.setattr(settings, "image_paths", "/data/mount1")

    with patch("app.tasks.indexer.redis") as mock_redis, \
         patch("app.tasks.indexer.os.path.exists", return_value=True), \
         patch("app.tasks.indexer._scan_directory", side_effect=SoftTimeLimitExceeded()), \
         patch("app.tasks.indexer.update_mount_stats"), \
         patch("app.scripts.backfill_frame_types.backfill_frame_types") as mock_ft_backfill, \
         patch("app.scripts.backfill_targets.backfill_targets") as mock_target_backfill:
        mock_redis.from_url.return_value = MagicMock()

        try:
            reindex_all.run()
        except SoftTimeLimitExceeded:
            pass

    mock_ft_backfill.assert_not_called()
    mock_target_backfill.assert_not_called()
