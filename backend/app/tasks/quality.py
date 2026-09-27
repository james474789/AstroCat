"""
Star quality tasks (Q1, docs/design/Q1-star-quality.md §5).

- measure_star_metrics(image_id): measure one image (queue "quality"). Queued
  by the indexer right after an eligible image is saved, and by the sweeper.
- sweep(): beat task, every 10 minutes. Backfills the library newest-first in
  batches while the quality queue is short, retries FAILED rows, re-queues
  rows measured by an older ALGO_VERSION and rescues PENDING rows whose task
  was lost. Self-healing, so no data migration is needed.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.database import SessionLocal
from app.models.image import FrameType, Image, ImageFormat, ImageSubtype
from app.services.quality_settings import backfill_enabled, measuring_enabled
from app.services.star_metrics import ALGO_VERSION, StarMetrics, measure
from app.utils.star_metric_hints import extract_hints
from app.worker import celery_app

logger = logging.getLogger(__name__)

QUEUE = "quality"
MAX_ATTEMPTS = 3
PENDING_STALE_AFTER = timedelta(hours=2)
SWEEP_LOCK_KEY = "star_metrics:sweep_lock"

ELIGIBLE_SUBTYPES = (ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER)


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------

def is_eligible(image) -> bool:
    """Light subs and masters. PLANETARY and calibration frames are never measured."""
    return image.frame_type == FrameType.LIGHT and image.subtype in ELIGIBLE_SUBTYPES


def _mtime_key(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def needs_measurement(image) -> bool:
    """Should this image be (re-)measured now? Pure: reads only the row."""
    if not measuring_enabled() or not is_eligible(image):
        return False
    status = image.star_metrics_status
    if status is None:
        return True
    if status == "PENDING":
        return False  # queued already; the sweeper rescues lost tasks
    if (image.star_metrics_version or 0) < ALGO_VERSION:
        return True
    details = image.star_metrics or {}
    if status == "FAILED" and int(details.get("attempts") or 0) < MAX_ATTEMPTS:
        return True
    # The file was re-saved (e.g. re-calibrated in place) since it was measured.
    return details.get("file_mtime") != _mtime_key(image.file_last_modified)


def sweep_clause(now: datetime):
    """SQL mirror of needs_measurement for the sweeper (minus the mtime check,
    which the indexer hook covers when it re-processes a changed file)."""
    return and_(
        Image.frame_type == FrameType.LIGHT,
        Image.subtype.in_(ELIGIBLE_SUBTYPES),
        or_(
            Image.star_metrics_status.is_(None),
            and_(Image.star_metrics_status != "PENDING",
                 or_(Image.star_metrics_version.is_(None), Image.star_metrics_version < ALGO_VERSION)),
            and_(Image.star_metrics_status == "FAILED",
                 Image.star_metrics["attempts"].as_integer() < MAX_ATTEMPTS),
            and_(Image.star_metrics_status == "PENDING",
                 or_(Image.star_metrics_at.is_(None), Image.star_metrics_at < now - PENDING_STALE_AFTER)),
        ),
    )


def queue_if_needed(session, image) -> bool:
    """Indexer hook: mark PENDING and queue a measurement. Never raises."""
    try:
        if not needs_measurement(image):
            return False
        image.star_metrics_status = "PENDING"
        image.star_metrics_at = datetime.utcnow()
        session.commit()
        measure_star_metrics.delay(image.id)
        return True
    except Exception as e:
        logger.warning(f"Could not queue star metrics for image {getattr(image, 'id', '?')}: {e}")
        try:
            session.rollback()
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------
# Applying a result
# --------------------------------------------------------------------------

def apply_result(image, result: StarMetrics, hints: Dict[str, Any], now: datetime) -> None:
    """Write a measurement (and any capture-software hints) onto the row."""
    previous = image.star_metrics or {}
    details = dict(result.details)
    details["source"] = "MEASURED"
    if hints:
        details["hints"] = hints
    details["file_mtime"] = _mtime_key(image.file_last_modified)

    status = result.status
    hfr, fwhm, ecc, stars = result.hfr_px, result.fwhm_px, result.eccentricity, result.star_count
    if status == "FAILED":
        details["attempts"] = int(previous.get("attempts") or 0) + 1
    elif status == "SKIPPED" and hints.get("HFR") is not None:
        # Can't measure this file (e.g. JPG) but the capture software did: use its value.
        status, details["source"] = "HINT", "HINT"
        hfr, fwhm, ecc, stars = hints["HFR"], None, None, hints.get("STARS")

    image.star_metrics_status = status
    image.hfr_px, image.fwhm_px, image.eccentricity, image.star_count = hfr, fwhm, ecc, stars
    image.star_metrics_version = ALGO_VERSION
    image.star_metrics_at = now
    image.star_metrics = details


def _failed(message: str) -> StarMetrics:
    return StarMetrics(status="FAILED", details={"error": message[:500], "algo_version": ALGO_VERSION})


def _measure_impl(image_id: int, force: bool = False) -> Dict[str, Any]:
    import os
    from app.tasks.indexer import _is_transient_error

    with SessionLocal() as session:
        image = session.get(Image, image_id)
        if image is None:
            return {"status": "missing", "image_id": image_id}
        if not is_eligible(image) or (not force and image.star_metrics_status not in (None, "PENDING")
                                      and not needs_measurement(image)):
            if image.star_metrics_status == "PENDING":
                image.star_metrics_status = None   # no longer eligible; let the row settle
                session.commit()
            return {"status": "not_needed", "image_id": image_id}
        path, file_format = image.file_path, image.file_format
        raw_header, file_name = image.raw_header, image.file_name

    # Measure outside any DB session: this is the slow part (file read + fit).
    hints = extract_hints(raw_header, file_name)
    fmt = file_format.value if isinstance(file_format, ImageFormat) else str(file_format)
    if not os.path.exists(path):
        result = _failed("File not found")
    else:
        try:
            result = measure(path, fmt, raw_header)
        except Exception as e:
            if _is_transient_error(e):
                raise  # Celery retries with backoff
            logger.warning(f"Star metrics failed for image {image_id} ({path}): {type(e).__name__}: {e}")
            result = _failed(f"{type(e).__name__}: {e}")

    with SessionLocal() as session:
        image = session.get(Image, image_id)
        if image is None:
            return {"status": "missing", "image_id": image_id}
        apply_result(image, result, hints, datetime.utcnow())
        session.commit()
        return {"status": image.star_metrics_status, "image_id": image_id,
                "hfr_px": image.hfr_px, "fwhm_px": image.fwhm_px}


@celery_app.task(bind=True, name="app.tasks.quality.measure_star_metrics",
                 autoretry_for=(OperationalError, BlockingIOError, TimeoutError),
                 retry_backoff=True, retry_backoff_max=60, max_retries=3,
                 soft_time_limit=600, time_limit=900)
def measure_star_metrics(self, image_id: int, force: bool = False):
    return _measure_impl(image_id, force)


# --------------------------------------------------------------------------
# Sweeper
# --------------------------------------------------------------------------

def _redis():
    import redis
    return redis.from_url(settings.redis_url)


def _sweep_impl(r=None) -> Dict[str, Any]:
    if not backfill_enabled():
        return {"status": "disabled"}
    r = r or _redis()
    depth = int(r.llen(QUEUE) or 0)
    if depth > settings.star_metrics_queue_max:
        return {"status": "queue_busy", "queue_depth": depth}
    if not r.set(SWEEP_LOCK_KEY, "1", nx=True, ex=900):
        return {"status": "locked"}
    try:
        now = datetime.utcnow()
        with SessionLocal() as session:
            ids = session.execute(
                select(Image.id)
                .where(sweep_clause(now))
                .order_by(Image.capture_date_utc.desc().nulls_last(), Image.id.desc())
                .limit(settings.star_metrics_sweep_batch)
            ).scalars().all()
            if not ids:
                return {"status": "idle", "queue_depth": depth}
            session.execute(
                update(Image).where(Image.id.in_(ids))
                .values(star_metrics_status="PENDING", star_metrics_at=now)
                .execution_options(synchronize_session=False)
            )
            session.commit()
        for image_id in ids:
            measure_star_metrics.delay(image_id)
        logger.info(f"Star metrics sweep queued {len(ids)} images (queue depth was {depth})")
        return {"status": "queued", "queued": len(ids), "queue_depth": depth}
    finally:
        r.delete(SWEEP_LOCK_KEY)


@celery_app.task(name="app.tasks.quality.sweep", soft_time_limit=600, time_limit=900)
def sweep():
    return _sweep_impl()
