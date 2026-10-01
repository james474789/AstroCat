"""
Full-resolution deep-zoom viewer API (V1, docs/design/20261001-V1-full-resolution-viewer.md §4.5).

Mounted under /api/images (cookie auth like the rest of the image routes).
"""

import logging
import mimetypes
import os
import re
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.config import settings
from app.database import get_db
from app.models.image import Image
from app.services.fullres import cache, render
from app.utils.path_security import validate_path_safety

logger = logging.getLogger(__name__)
router = APIRouter()

PRESET_PATTERN = "^(" + "|".join(render.PRESETS) + ")$"
TILE_RE = re.compile(r"^(\d+)_(\d+)\.jpg$")
IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}

# key -> image_id, for keys whose manifest has already been checked in this process
_OWNERS: dict = {}
_OWNERS_MAX = 10_000


def _base_url(image_id):
    return f"{settings.api_prefix}/images/{image_id}/fullres"


async def _load_image(image_id: int, db: AsyncSession) -> Image:
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    if not os.path.exists(image.file_path):
        raise HTTPException(status_code=404, detail="Source file missing")
    if not validate_path_safety(image.file_path, settings.image_paths_list):
        raise HTTPException(status_code=403, detail="Access denied: Invalid file path")
    return image


def _prepare(image: Image, preset: Optional[str]):
    """Resolve the preset and cache key for an image: (preset, presets, probe, key)."""
    st = os.stat(image.file_path)
    try:
        probe = render.probe_source(image, st)
    except Exception as e:
        logger.warning("Full-res probe failed for image %s: %s", image.id, e)
        raise HTTPException(status_code=422, detail="This image cannot be opened at full resolution")
    presets = render.valid_presets(probe.colour)
    preset = preset or render.default_preset(image)
    if preset not in presets:
        raise HTTPException(status_code=409, detail=f"Preset '{preset}' is not available for this image")
    return preset, presets, probe, cache.key_for_file(image.file_path, preset, st)


def _envelope(image, preset, presets, **extra):
    return {"image_id": image.id, "preset": preset, "presets": presets,
            "default_preset": render.default_preset(image), **extra}


def _enqueue(image_id, preset, key, retry=False, force=False):
    """Status for `key`, enqueueing a build if none is running. Runs in a worker thread."""
    r = cache.redis_client()
    if force:
        cache.delete_key(key, r)
        cache.release_build_lock(key, r)
    status = cache.get_status(key, r)
    if status and status["state"] == "error" and retry:
        cache.clear_status(key, r)
        status = None
    if status and status["state"] in cache.STATES_ACTIVE + ("error",):
        return status
    if not cache.acquire_build_lock(key, r):
        return {"state": "queued", "pct": None, "error": None}
    cache.set_status(key, "queued", None, r=r)
    try:
        from app.tasks.fullres import build_pyramid
        build_pyramid.delay(image_id, preset)
    except Exception as e:
        logger.error("Could not enqueue full-res build for image %s: %s", image_id, e)
        cache.release_build_lock(key, r)
        cache.set_status(key, "error", error="Could not queue the build", r=r)
        return {"state": "error", "pct": None, "error": "Could not queue the build"}
    return {"state": "queued", "pct": None, "error": None}


@router.get("/{image_id}/fullres")
async def get_fullres(
    image_id: int,
    preset: Optional[str] = Query(None, pattern=PRESET_PATTERN, description="Stretch preset; defaults per image"),
    retry: bool = Query(False, description="Re-queue a build that previously failed"),
    db: AsyncSession = Depends(get_db),
):
    """Status of the full-resolution view: 200 when ready, 202 while it is being built."""
    image = await _load_image(image_id, db)
    preset, presets, probe, key = await run_in_threadpool(_prepare, image, preset)

    if render.is_native_candidate(image, probe, preset):
        manifest = {"image_id": image.id, "preset": preset, "type": "image", "width": probe.width,
                    "height": probe.height, "scale": 1, "native_width": probe.width,
                    "native_height": probe.height, "channels": 3 if probe.colour else 1, "notes": []}
        return _envelope(image, preset, presets, state="ready", manifest=manifest, dzi_url=None,
                         source_url=f"{_base_url(image.id)}/source")

    manifest = await run_in_threadpool(cache.read_manifest, key)
    if manifest:
        await run_in_threadpool(cache.touch, key)
        return _envelope(image, preset, presets, state="ready", manifest=manifest, key=key,
                         dzi_url=f"{_base_url(image.id)}/{key}/image.dzi")

    status = await run_in_threadpool(_enqueue, image.id, preset, key, retry)
    return JSONResponse(status_code=202, content=_envelope(image, preset, presets, key=key, **status))


@router.post("/{image_id}/fullres/rebuild", status_code=202, dependencies=[Depends(require_admin)])
async def rebuild_fullres(
    image_id: int,
    preset: Optional[str] = Query(None, pattern=PRESET_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    """Drop the cached pyramid for this image/preset and build it again (admin only)."""
    image = await _load_image(image_id, db)
    preset, presets, _probe, key = await run_in_threadpool(_prepare, image, preset)
    status = await run_in_threadpool(lambda: _enqueue(image.id, preset, key, force=True))
    return _envelope(image, preset, presets, key=key, **status)


@router.get("/{image_id}/fullres/source")
async def get_fullres_source(image_id: int, db: AsyncSession = Depends(get_db)):
    """The original JPEG/PNG/WebP, inline, for images small enough to skip the pyramid."""
    image = await _load_image(image_id, db)
    ext = os.path.splitext(image.file_path)[1].lower()
    if ext not in render.NATIVE_EXTS:
        raise HTTPException(status_code=404, detail="No native full-resolution source for this format")
    media_type = mimetypes.guess_type(image.file_path)[0] or "application/octet-stream"
    return FileResponse(image.file_path, media_type=media_type)


def _check_owner(image_id: int, key: str):
    """The key must be well-formed and its manifest must belong to this image."""
    if not cache.KEY_RE.match(key):
        raise HTTPException(status_code=400, detail="Invalid key")
    owner = _OWNERS.get(key)
    if owner is None:
        manifest = cache.read_manifest(key)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Not found")
        owner = manifest.get("image_id")
        if len(_OWNERS) >= _OWNERS_MAX:
            _OWNERS.clear()
        _OWNERS[key] = owner
    if owner != image_id:
        raise HTTPException(status_code=404, detail="Not found")


def _serve(path, media_type):
    if not validate_path_safety(path, [cache.cache_root()]):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type=media_type, headers=IMMUTABLE)


@router.get("/{image_id}/fullres/{key}/image.dzi")
async def get_dzi(image_id: int, key: str):
    _check_owner(image_id, key)
    return await run_in_threadpool(_serve, cache.key_dir(key) / "image.dzi", "application/xml")


@router.get("/{image_id}/fullres/{key}/image_files/{level}/{tile}")
async def get_tile(image_id: int, key: str, level: Annotated[int, Path(ge=0)], tile: str):
    _check_owner(image_id, key)
    match = TILE_RE.match(tile)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid tile")
    path = cache.key_dir(key) / "image_files" / str(level) / f"{int(match.group(1))}_{int(match.group(2))}.jpg"
    return await run_in_threadpool(_serve, path, "image/jpeg")
