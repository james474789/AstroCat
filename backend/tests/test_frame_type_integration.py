"""
Integration-style tests (mocked session) for the F1 frame-type wiring,
in the style of test_indexer_resilience.py:

1. A MANUAL row is not overwritten on re-index.
2. Catalog matches are removed when a solved image becomes DARK.
3. bulk.py's mount-point selection queries exclude non-LIGHT frames.
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.sql.dml import Delete

from app.config import settings
from app.models.image import FrameType, Image, ImageSubtype
from app.tasks.indexer import _process_image_impl
from app.tasks.bulk import bulk_match_task, bulk_astrometry_task
from app.scripts.backfill_frame_types import backfill_frame_types

from app.worker import celery_app  # noqa: F401  (import order ensures app/worker loads)


def _fake_extractor(raw_header, extra_metadata=None):
    """Build a fake extractor whose .extract() returns a minimal metadata dict."""
    metadata = {
        "raw_header": raw_header,
        "width_pixels": 100,
        "height_pixels": 100,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    extractor = MagicMock()
    extractor.extract.return_value = metadata
    extractor.get_file_stats.return_value = {
        "file_size_bytes": 123,
        "created_at": None,
        "modified_at": None,
    }
    return extractor


class TestManualFrameTypeSurvivesReindex:
    def test_manual_row_not_overwritten(self, tmp_path):
        target = tmp_path / "M31_DARK_0001.fits"
        target.write_bytes(b"SIMULATED")

        existing = Image(
            id=1,
            file_path=str(target),
            file_name=target.name,
            frame_type=FrameType.LIGHT,
            frame_type_source="MANUAL",
            subtype=ImageSubtype.SUB_FRAME,
            astrometry_status="NONE",
            is_plate_solved=False,
        )

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar_one_or_none.return_value = existing

        # Header clearly says DARK; if the classifier's result were applied
        # this would flip frame_type. It must not, because the row is MANUAL.
        extractor = _fake_extractor(raw_header={"IMAGETYP": "DARK"})

        with patch("app.tasks.indexer.get_extractor", return_value=extractor), \
             patch("app.tasks.indexer.SessionLocal") as mock_sl:
            mock_sl.return_value.__enter__.return_value = fake_session

            result = _process_image_impl(str(target), generate_thumbnail=False)

        assert result["status"] == "completed"
        assert existing.frame_type == FrameType.LIGHT
        assert existing.frame_type_source == "MANUAL"


class TestCatalogMatchCleanupOnFrameTypeChange:
    def test_matches_removed_when_solved_image_becomes_dark(self, tmp_path):
        target = tmp_path / "M31_0002.fits"
        target.write_bytes(b"SIMULATED")

        existing = Image(
            id=42,
            file_path=str(target),
            file_name=target.name,
            frame_type=FrameType.LIGHT,
            frame_type_source="DEFAULT",
            subtype=ImageSubtype.SUB_FRAME,
            astrometry_status="SOLVED",
            is_plate_solved=True,
            ra_center_degrees=10.0,
            dec_center_degrees=20.0,
        )

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar_one_or_none.return_value = existing

        # Header now says DARK, so the row transitions LIGHT -> DARK.
        extractor = _fake_extractor(raw_header={"IMAGETYP": "DARK"})

        with patch("app.tasks.indexer.get_extractor", return_value=extractor), \
             patch("app.tasks.indexer.SessionLocal") as mock_sl, \
             patch("app.tasks.indexer.SyncCatalogMatcher") as mock_matcher:
            mock_sl.return_value.__enter__.return_value = fake_session

            result = _process_image_impl(str(target), generate_thumbnail=False)

        assert result["status"] == "completed"
        assert existing.frame_type == FrameType.DARK
        assert existing.frame_type_source == "HEADER"

        # The matcher must never run for a non-LIGHT frame.
        mock_matcher.assert_not_called()

        # A DELETE against ImageCatalogMatch (excluding MANUAL) must have
        # been issued to clean up any stale matches.
        delete_calls = [
            call.args[0] for call in fake_session.execute.call_args_list
            if call.args and isinstance(call.args[0], Delete)
        ]
        assert len(delete_calls) == 1
        compiled = str(delete_calls[0])
        assert "image_catalog_matches" in compiled.lower()


class _FakeRedis:
    def hset(self, *a, **k): pass
    def expire(self, *a, **k): pass
    def set(self, *a, **k): pass


class TestBulkSelectionExcludesNonLight:
    def test_bulk_match_task_filters_light_only(self):
        fake_session = MagicMock()
        fake_query = MagicMock()
        fake_session.query.return_value = fake_query
        fake_query.filter.return_value = fake_query
        fake_query.all.return_value = []

        with patch("app.tasks.bulk.SessionLocal") as mock_sl, \
             patch("app.tasks.bulk.redis") as mock_redis:
            mock_sl.return_value.__enter__.return_value = fake_session
            mock_redis.from_url.return_value = _FakeRedis()

            bulk_match_task.run("/data/mount1")

        args, _ = fake_query.filter.call_args
        clause_strs = [str(a) for a in args]
        assert any("frame_type" in s for s in clause_strs), clause_strs

    def test_bulk_astrometry_task_filters_light_only(self):
        fake_session = MagicMock()
        fake_query = MagicMock()
        fake_session.query.return_value = fake_query
        fake_query.filter.return_value = fake_query
        fake_query.all.return_value = []

        with patch("app.tasks.bulk.SessionLocal") as mock_sl, \
             patch("app.tasks.bulk.redis") as mock_redis:
            mock_sl.return_value.__enter__.return_value = fake_session
            mock_redis.from_url.return_value = _FakeRedis()

            bulk_astrometry_task.run("/data/mount1", False)

        args, _ = fake_query.filter.call_args
        clause_strs = [str(a) for a in args]
        assert any("frame_type" in s for s in clause_strs), clause_strs


class TestBackfillFrameTypes:
    def test_backfill_updates_and_cleans_up_matches(self, monkeypatch):
        monkeypatch.setattr(settings, "image_paths", "/data/mount1")

        # Row 1: never classified, lives in a Darks/ directory -> becomes DARK.
        row1 = (1, "/data/mount1/Darks/img1.fits", None, FrameType.LIGHT, None)
        # Row 2: never classified, header says LIGHT -> stays LIGHT but gains a source.
        row2 = (2, "/data/mount1/img2.fits", {"IMAGETYP": "LIGHT"}, FrameType.LIGHT, None)

        select_result_1 = MagicMock()
        select_result_1.all.return_value = [row1, row2]
        select_result_2 = MagicMock()
        select_result_2.all.return_value = []

        fake_session = MagicMock()
        fake_session.execute.side_effect = [select_result_1, MagicMock(), MagicMock(), select_result_2]

        with patch("app.scripts.backfill_frame_types.SessionLocal") as mock_sl, \
             patch("app.scripts.backfill_frame_types._clear_stats_cache") as mock_clear_cache:
            mock_sl.return_value.__enter__.return_value = fake_session

            summary = backfill_frame_types(dry_run=False, reclassify_all=False, batch_size=2)

        assert summary["total"] == 2
        assert summary["updated"] == 2
        mock_clear_cache.assert_called_once()

        # Calls: select, bulk update, cleanup delete, commit, select (empty) -> stop.
        from sqlalchemy.sql.dml import Update, Delete
        update_calls = [c for c in fake_session.execute.call_args_list if c.args and isinstance(c.args[0], Update)]
        delete_calls = [c for c in fake_session.execute.call_args_list if c.args and isinstance(c.args[0], Delete)]
        assert len(update_calls) == 1
        assert len(delete_calls) == 1

        # The bulk update payload marks row 1 DARK and row 2 LIGHT/HEADER.
        payload = update_calls[0].args[1]
        by_id = {row["id"]: row for row in payload}
        assert by_id[1]["frame_type"] == FrameType.DARK
        assert by_id[2]["frame_type"] == FrameType.LIGHT
        assert by_id[2]["frame_type_source"] == "HEADER"

        fake_session.commit.assert_called()

    def test_dry_run_makes_no_writes(self, monkeypatch):
        monkeypatch.setattr(settings, "image_paths", "/data/mount1")

        row1 = (1, "/data/mount1/Darks/img1.fits", None, FrameType.LIGHT, None)
        select_result_1 = MagicMock()
        select_result_1.all.return_value = [row1]

        fake_session = MagicMock()
        fake_session.execute.side_effect = [select_result_1]

        with patch("app.scripts.backfill_frame_types.SessionLocal") as mock_sl, \
             patch("app.scripts.backfill_frame_types._clear_stats_cache") as mock_clear_cache:
            mock_sl.return_value.__enter__.return_value = fake_session

            summary = backfill_frame_types(dry_run=True, reclassify_all=False, batch_size=2)

        assert summary["total"] == 1
        assert summary["updated"] == 1
        # Dry run: a single SELECT (the batch is smaller than batch_size, so
        # the loop stops without a second page), no UPDATE/DELETE/commit, no
        # cache clear.
        assert fake_session.execute.call_count == 1
        fake_session.commit.assert_not_called()
        mock_clear_cache.assert_not_called()
