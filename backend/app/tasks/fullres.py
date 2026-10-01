"""
Full-resolution viewer tasks (V1, docs/design/20261001-V1-full-resolution-viewer.md §4.4).

These run on their own "fullres" queue and worker so a 2.8 GB master can never stack up on the
main pool or delay indexing and thumbnails.
"""

import logging
import time

from celery.exceptions import SoftTimeLimitExceeded

from app.worker import celery_app

logger = logging.getLogger(__name__)


def build_image(image, preset, r=None):
    """Render `image` and write its pyramid to the cache. Returns the manifest.

    Idempotent: if the pyramid for this file/preset already exists, nothing is rebuilt.
    """
    from app.services.fullres import cache, pyramid, render

    r = r or cache.redis_client()
    key = cache.key_for_file(image.file_path, preset)
    existing = cache.read_manifest(key)
    if existing:
        cache.set_status(key, "ready", 100, r=r)
        return existing

    last = {"state": None, "pct": -1}

    def progress(state, pct=None):
        pct = None if pct is None else int(pct)
        if state != last["state"] or (pct is not None and pct != last["pct"]):
            last["state"], last["pct"] = state, pct if pct is not None else -1
            cache.set_status(key, state, pct, r=r)

    started = time.monotonic()
    progress("loading")
    result = render.render_full(image, preset, progress=progress)
    render_ms = int((time.monotonic() - started) * 1000)
    progress("tiling", 0)
    manifest = pyramid.build(
        result.array, key,
        {
            "image_id": image.id,
            "preset": preset,
            "scale": result.scale,
            "native_width": result.native_width,
            "native_height": result.native_height,
            "channels": result.channels,
            "render_ms": render_ms,
            "notes": result.notes,
        },
        progress=lambda pct: progress("tiling", pct),
    )
    cache.record_built(key, manifest["bytes"], r=r)
    cache.set_status(key, "ready", 100, r=r)
    return manifest


@celery_app.task(bind=True, name="app.tasks.fullres.build_pyramid",
                 soft_time_limit=14 * 60, time_limit=15 * 60)
def build_pyramid(self, image_id: int, preset: str):
    """Build the deep-zoom pyramid for one image and preset."""
    import os
    from app.database import SessionLocal
    from app.models.image import Image
    from app.services.fullres import cache

    r = cache.redis_client()
    key = None
    try:
        with SessionLocal() as session:
            image = session.query(Image).filter(Image.id == image_id).first()
            if not image:
                return {"status": "error", "message": "Image not found"}
            if not os.path.exists(image.file_path):
                return {"status": "error", "message": "Source file not found"}
            key = cache.key_for_file(image.file_path, preset)
            manifest = build_image(image, preset, r=r)
        try:
            cache.evict(r=r)
        except Exception:
            logger.warning("fullres eviction after build failed", exc_info=True)
        return {"status": "completed", "image_id": image_id, "key": key, "bytes": manifest["bytes"]}
    except SoftTimeLimitExceeded:
        logger.error("Full-res build timed out for image %s", image_id)
        error = "Timed out building the full-resolution view"
    except Exception as e:
        logger.exception("Full-res build failed for image %s", image_id)
        error = str(e) or e.__class__.__name__
    finally:
        if key:
            cache.release_build_lock(key, r=r)
    if key:
        cache.set_status(key, "error", error=error, r=r)
    return {"status": "error", "message": error}


@celery_app.task(name="app.tasks.fullres.evict")
def evict():
    """Periodic LRU eviction down to FULLRES_CACHE_MAX_GB."""
    from app.services.fullres import cache
    return cache.evict()
