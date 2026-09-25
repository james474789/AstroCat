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

from app.models.image import FrameType, Image, ImageSubtype
from app.tasks.indexer import _process_image_impl
from app.tasks.bulk import bulk_match_task, bulk_astrometry_task

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
