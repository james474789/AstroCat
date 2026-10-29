"""
reindex_all removes Image rows that sit under none of the configured library folders
(ISSUE-001): _scan_directory only examines rows inside the root it walks, so rows left over
from a removed folder used to stay forever. The cleanup must never run on a partial view of the
configured roots (unmounted drive, failed scan, no roots), or an unplugged disk would wipe the index.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.orm import Session, declarative_base

from app.config import settings
from app.tasks import indexer
from app.tasks.indexer import _remove_orphaned_images, reindex_all

Base = declarative_base()


class FakeImage(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True)
    file_path = Column(String, unique=True, nullable=False)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(indexer, "Image", FakeImage)
    monkeypatch.setattr(indexer, "SessionLocal", lambda: Session(engine))

    def seed(paths):
        with Session(engine) as s:
            s.add_all([FakeImage(file_path=p) for p in paths])
            s.commit()

    def remaining():
        with Session(engine) as s:
            return sorted(r[0] for r in s.execute(select(FakeImage.file_path)))

    return seed, remaining


def test_rows_outside_configured_roots_are_removed(db):
    seed, remaining = db
    seed(["/lib/A/1.fits", "/lib/A/sub/2.fits", "/lib/B/3.fits"])
    assert _remove_orphaned_images(["/lib/B"]) == 2
    assert remaining() == ["/lib/B/3.fits"]


def test_sibling_prefix_is_an_orphan(db):
    seed, remaining = db
    seed(["/lib/B2/x.fits", "/lib/B/x.fits"])
    assert _remove_orphaned_images(["/lib/B/"]) == 1
    assert remaining() == ["/lib/B/x.fits"]


def test_no_roots_removes_nothing(db):
    seed, remaining = db
    seed(["/lib/A/1.fits"])
    assert _remove_orphaned_images([]) == 0
    assert remaining() == ["/lib/A/1.fits"]


def test_batches_over_500(db):
    seed, remaining = db
    seed([f"/lib/A/{i}.fits" for i in range(1200)] + ["/lib/B/keep.fits"])
    assert _remove_orphaned_images(["/lib/B"]) == 1200
    assert remaining() == ["/lib/B/keep.fits"]


def _run_reindex(roots, exists, scan_side_effect=None):
    scan_result = {"files_found": 5, "files_queued": 0, "files_removed": 1}
    monkey_roots = ",".join(roots)
    with patch.object(settings, "image_paths", monkey_roots), \
         patch("app.tasks.indexer.redis") as mock_redis, \
         patch("app.tasks.indexer.os.path.exists", side_effect=exists), \
         patch("app.tasks.indexer._scan_directory", return_value=scan_result,
               side_effect=scan_side_effect) as scan, \
         patch("app.tasks.indexer._remove_orphaned_images", return_value=7) as cleanup, \
         patch("app.tasks.indexer.update_mount_stats") as stats, \
         patch("app.scripts.backfill_frame_types.backfill_frame_types", return_value={"total": 0, "updated": 0}), \
         patch("app.scripts.backfill_field_radius.backfill_field_radius", return_value={"fixed": 0, "targets_changed": 0}), \
         patch("app.scripts.backfill_targets.backfill_targets"):
        redis_client = MagicMock()
        mock_redis.from_url.return_value = redis_client
        reindex_all.run()
    return cleanup, stats, redis_client


def test_reindex_runs_cleanup_and_counts_removals():
    cleanup, stats, r = _run_reindex(["/lib/B"], exists=lambda p: True)
    cleanup.assert_called_once_with(["/lib/B"])
    r.set.assert_any_call("indexer:files_removed", "8")  # 1 missing-file + 7 orphans
    stats.assert_called_once()


def test_reindex_skips_cleanup_when_a_root_is_missing():
    cleanup, _, _ = _run_reindex(["/lib/B", "/lib/missing"], exists=lambda p: p == "/lib/B")
    cleanup.assert_not_called()


def test_reindex_skips_cleanup_when_a_scan_fails():
    cleanup, _, _ = _run_reindex(["/lib/B"], exists=lambda p: True, scan_side_effect=RuntimeError("boom"))
    cleanup.assert_not_called()


def test_reindex_skips_cleanup_with_no_roots():
    cleanup, _, _ = _run_reindex([], exists=lambda p: True)
    cleanup.assert_not_called()
