"""
Equipment & Sites API (R0, docs/design/P0-R0-equipment-sites.md §4.6).

Two routers:
- `router` at /api/equipment: cameras, optics, filters, rigs (+ mount, up to
  MAX_MOUNTED_RIGS at once),
  detection proposals, Telescopius import, assignment.
- `sites_router` at /api/sites: sites and their horizon profiles.

Every endpoint needs a logged-in user (router-level dependency in main.py);
every write also needs an admin (`require_admin`). Response shapes follow
the R0 API contract: nullable fields are null, never omitted.

Any create/update/delete of a camera, optic, rig or site queues a debounced
assignment run (app/tasks/equipment.py).
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.config import settings
from app.database import get_db
from app.models.equipment import (
    MAX_MOUNTED_RIGS, Camera, Filter, Optic, Rig, Site, SOURCE_DETECTED, SOURCE_MANUAL, SOURCE_TELESCOPIUS,
    rig_filters,
)
from app.models.image import FrameType, Image, ImageSubtype
from app.schemas.equipment import (
    CameraCreate, CameraUpdate, DetectApply, FilterCreate, FilterUpdate, HorizonUpdate, OpticCreate,
    OpticUpdate, RigCreate, RigUpdate, SiteCreate, SiteUpdate,
)
from app.services.site_horizon import HORIZON_CACHE_KEY, HORIZON_CACHE_TTL  # shared with the recommender
from app.utils.filter_names import normalize_filter
from app.utils.optics import DEFAULT_SEEING_ARCSEC, pixel_scale, rig_optics_summary
from app.utils.star_quality import SCALE_SQL

logger = logging.getLogger(__name__)

router = APIRouter()
sites_router = APIRouter()

DETECT_CACHE_KEY = "cache:equipment:detect:{flag}"
DETECT_CACHE_TTL = 600
RECS_CACHE_PATTERN = "recs:*"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _valid_timezone(tz: Optional[str]) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not tz:
        raise HTTPException(status_code=400, detail="timezone is required")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400, detail=f"Unknown timezone '{tz}'")
    return tz


def _band_for(name: str) -> str:
    bucket = normalize_filter(name)
    return "Other" if bucket.startswith("Other:") else bucket


def _camera_patterns(name: str, patterns: Optional[List[str]]) -> List[str]:
    from app.services.equipment_detection import normalize_camera_name

    if patterns:
        return patterns
    key = normalize_camera_name(name)
    return [key] if key else []


async def _redis():
    try:
        import redis.asyncio as redis_async
        return redis_async.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


async def _cache_get(key: str):
    r = await _redis()
    if not r:
        return None
    try:
        raw = await r.get(key)
        await r.close()
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"Equipment cache read failed: {e}")
        return None


async def _cache_set(key: str, value, ttl: int) -> None:
    r = await _redis()
    if not r:
        return
    try:
        await r.setex(key, ttl, json.dumps(value, default=str))
        await r.close()
    except Exception as e:
        logger.warning(f"Equipment cache write failed: {e}")


async def _cache_delete(*keys: str) -> None:
    r = await _redis()
    if not r:
        return
    try:
        await r.delete(*keys)
        await r.close()
    except Exception as e:
        logger.warning(f"Equipment cache delete failed: {e}")


async def _invalidate_recommendations() -> None:
    """Drop cached recommendations (R1): rigs, sites, mounts and horizons change them."""
    r = await _redis()
    if not r:
        return
    try:
        keys = [k async for k in r.scan_iter(match=RECS_CACHE_PATTERN)]
        if keys:
            await r.delete(*keys)
        await r.close()
    except Exception as e:
        logger.warning(f"Recommendations cache invalidation failed: {e}")


async def _equipment_changed(queue: bool = True, scope: str = "all") -> Optional[str]:
    """Invalidate detection proposals and recommendations, and queue a (debounced) assignment run."""
    await _cache_delete(DETECT_CACHE_KEY.format(flag=0), DETECT_CACHE_KEY.format(flag=1))
    await _invalidate_recommendations()
    if not queue:
        return None
    try:
        from app.tasks.equipment import queue_assign_equipment
        return await asyncio.to_thread(queue_assign_equipment, scope)
    except Exception as e:
        logger.warning(f"Could not queue equipment assignment: {e}")
        return None


async def _unique_name(db: AsyncSession, model, name: str, exclude_id: Optional[int] = None) -> str:
    """name, or "name (2)", "name (3)"... if already taken (case-insensitive)."""
    limit = model.__table__.c.name.type.length or 100
    rows = (await db.execute(select(model.id, model.name))).all()
    taken = {n.lower() for i, n in rows if i != exclude_id}
    candidate, n = name[:limit], 2
    while candidate.lower() in taken:
        suffix = f" ({n})"
        candidate = name[:limit - len(suffix)] + suffix
        n += 1
    return candidate


async def _ensure_name_free(db: AsyncSession, model, name: str, exclude_id: Optional[int] = None) -> None:
    stmt = select(model.id).where(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    if (await db.execute(stmt)).first():
        raise HTTPException(status_code=409, detail=f"'{name}' already exists")


async def _get_or_404(db: AsyncSession, model, obj_id: int):
    obj = await db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return obj


def _light_subs():
    return (Image.frame_type == FrameType.LIGHT) & (Image.subtype == ImageSubtype.SUB_FRAME)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def camera_dict(c: Camera, rig_count: int = 0) -> Dict[str, Any]:
    return {
        "id": c.id, "name": c.name, "maker": c.maker,
        "sensor_width_px": c.sensor_width_px, "sensor_height_px": c.sensor_height_px,
        "pixel_size_um": c.pixel_size_um, "is_color": c.is_color, "is_cooled": c.is_cooled,
        "match_patterns": list(c.match_patterns or []), "source": c.source,
        "external_ref": c.external_ref, "notes": c.notes, "rig_count": rig_count,
        "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
    }


def optic_dict(o: Optic, rig_count: int = 0) -> Dict[str, Any]:
    return {
        "id": o.id, "name": o.name, "kind": o.kind, "aperture_mm": o.aperture_mm,
        "focal_length_mm": o.focal_length_mm, "source": o.source, "external_ref": o.external_ref,
        "notes": o.notes, "rig_count": rig_count,
        "created_at": _iso(o.created_at), "updated_at": _iso(o.updated_at),
    }


def filter_dict(f: Filter) -> Dict[str, Any]:
    return {
        "id": f.id, "name": f.name, "band": f.band, "bandwidth_nm": f.bandwidth_nm,
        "match_patterns": list(f.match_patterns or []), "source": f.source,
        "external_ref": f.external_ref,
        "created_at": _iso(f.created_at), "updated_at": _iso(f.updated_at),
    }


# Q1d: a rig's delivered FWHM replaces the site's typical seeing in the
# sampling check once it has this many measured subs in its window.
DELIVERED_MIN_SUBS = 50
DELIVERED_WINDOW_DAYS = 90


def rig_dict(rig: Rig, usage: Optional[tuple] = None, seeing: float = DEFAULT_SEEING_ARCSEC,
             delivered: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cam, opt = rig.camera, rig.optic
    seeing_source = "SITE"
    if delivered and delivered.get("n", 0) >= DELIVERED_MIN_SUBS and delivered.get("median_arcsec"):
        seeing, seeing_source = delivered["median_arcsec"], "MEASURED"
    computed = rig_optics_summary(
        pixel_um=cam.pixel_size_um if cam else None,
        width_px=cam.sensor_width_px if cam else None,
        height_px=cam.sensor_height_px if cam else None,
        focal_mm=opt.focal_length_mm if opt else None,
        aperture_mm=opt.aperture_mm if opt else None,
        modifier_factor=rig.modifier_factor, binning=rig.binning,
        measured_scale=rig.measured_scale_arcsec, seeing_arcsec=seeing,
    )
    count, last = usage or (0, None)
    return {
        "id": rig.id, "name": rig.name, "camera_id": rig.camera_id, "optic_id": rig.optic_id,
        "camera_name": cam.name if cam else None, "optic_name": opt.name if opt else None,
        "modifier_name": rig.modifier_name, "modifier_factor": rig.modifier_factor or 1.0,
        "binning": rig.binning or 1, "is_active": bool(rig.is_active), "is_mounted": bool(rig.is_mounted),
        "mount_name": rig.mount_name,
        "filter_ids": [f.id for f in rig.filters],
        "filters": [{"id": f.id, "name": f.name, "band": f.band} for f in rig.filters],
        "measured_scale_arcsec": rig.measured_scale_arcsec, "measured_count": rig.measured_count or 0,
        **computed,
        "sampling_seeing_source": seeing_source,
        "delivered_fwhm": delivered,
        "image_count": int(count or 0), "last_used": _iso(last),
        "created_at": _iso(rig.created_at), "updated_at": _iso(rig.updated_at),
    }


def site_dict(s: Site, image_count: int = 0, measured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "measured_seeing": measured,
        "id": s.id, "name": s.name, "latitude": s.latitude, "longitude": s.longitude,
        "elevation_m": s.elevation_m, "timezone": s.timezone, "bortle": s.bortle, "sqm": s.sqm,
        "typical_seeing_arcsec": s.typical_seeing_arcsec if s.typical_seeing_arcsec else DEFAULT_SEEING_ARCSEC,
        "is_default": bool(s.is_default), "horizon": s.horizon, "horizon_source": s.horizon_source,
        "image_count": int(image_count or 0),
        "created_at": _iso(s.created_at), "updated_at": _iso(s.updated_at),
    }


async def _rig_counts(db: AsyncSession, column) -> Dict[int, int]:
    rows = (await db.execute(select(column, func.count(Rig.id)).group_by(column))).all()
    return {k: v for k, v in rows}


async def _rig_usage(db: AsyncSession, rig_ids: Optional[Iterable[int]] = None) -> Dict[int, tuple]:
    stmt = (select(Image.rig_id, func.count(Image.id), func.max(Image.capture_date))
            .where(_light_subs(), Image.rig_id.isnot(None)).group_by(Image.rig_id))
    if rig_ids is not None:
        stmt = stmt.where(Image.rig_id.in_(list(rig_ids)))
    return {r: (c, last) for r, c, last in (await db.execute(stmt)).all()}


async def _site_counts(db: AsyncSession) -> Dict[int, int]:
    stmt = (select(Image.site_id, func.count(Image.id))
            .where(_light_subs(), Image.site_id.isnot(None)).group_by(Image.site_id))
    return {s: c for s, c in (await db.execute(stmt)).all()}


async def _rig_delivered(db: AsyncSession, rig_ids: Optional[Iterable[int]] = None) -> Dict[int, Dict[str, Any]]:
    """
    Q1d: each rig's delivered FWHM over its last DELIVERED_WINDOW_DAYS of
    measured Light subs (relative to its own latest sub, so a rig that has
    been idle for months still reports its recent form).
    """
    rows = (await db.execute(text(f"""
        WITH m AS (
            SELECT images.rig_id, images.fwhm_px, images.fwhm_px * {SCALE_SQL} AS arc, images.capture_date,
                   max(images.capture_date) OVER (PARTITION BY images.rig_id) AS last
            FROM images LEFT JOIN rigs r ON r.id = images.rig_id
            WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
              AND images.star_metrics_status = 'OK' AND images.rig_id IS NOT NULL
              AND images.capture_date IS NOT NULL
              {"AND images.rig_id = ANY(:ids)" if rig_ids is not None else ""}
        )
        SELECT rig_id, count(*) AS n, min(capture_date) AS since, max(capture_date) AS until,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY arc) AS median_arcsec,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY arc) AS best_arcsec,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fwhm_px) AS median_px,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY fwhm_px) AS best_px
        FROM m WHERE capture_date >= last - make_interval(days => :days)
        GROUP BY rig_id
    """), {"days": DELIVERED_WINDOW_DAYS, **({"ids": list(rig_ids)} if rig_ids is not None else {})})).all()
    r3 = lambda v: round(float(v), 3) if v is not None else None  # noqa: E731
    return {r.rig_id: {
        "n": int(r.n), "window_days": DELIVERED_WINDOW_DAYS,
        "since": _iso(r.since), "until": _iso(r.until),
        "median_arcsec": r3(r.median_arcsec), "best_arcsec": r3(r.best_arcsec),
        "median_px": r3(r.median_px), "best_px": r3(r.best_px),
    } for r in rows}


SEEING_MAX_SCALE = 3.0      # arcsec/px: coarser rigs can't resolve the seeing
SEEING_SITE_WINDOW_DAYS = 365


async def _site_measured(db: AsyncSession) -> Dict[int, Dict[str, Any]]:
    """
    Q1d: a site's measured seeing = the sharpest delivered FWHM (median over
    each rig's own last DELIVERED_WINDOW_DAYS at that site) among rigs that
    can resolve seeing (median scale <= SEEING_MAX_SCALE), used there within
    the last SEEING_SITE_WINDOW_DAYS of the site's activity, >= 20 subs.
    Delivered FWHM includes optics and guiding, so it is an upper bound on
    the seeing; the sharpest capable rig is the closest to it. None when the
    site has no such rig (e.g. camera lenses only).
    """
    rows = (await db.execute(text(f"""
        WITH m AS (
            SELECT images.site_id, images.rig_id, images.fwhm_px * {SCALE_SQL} AS arc, {SCALE_SQL} AS scale,
                   images.capture_date,
                   max(images.capture_date) OVER (PARTITION BY images.site_id, images.rig_id) AS rig_last,
                   max(images.capture_date) OVER (PARTITION BY images.site_id) AS site_last
            FROM images LEFT JOIN rigs r ON r.id = images.rig_id
            WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
              AND images.star_metrics_status = 'OK' AND images.site_id IS NOT NULL
              AND images.rig_id IS NOT NULL AND images.capture_date IS NOT NULL
        ), per_rig AS (
            SELECT site_id, rig_id, count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY arc) AS median_arcsec,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY scale) AS median_scale
            FROM m
            WHERE capture_date >= rig_last - make_interval(days => :days)
              AND rig_last >= site_last - make_interval(days => :site_days)
              AND arc IS NOT NULL
            GROUP BY site_id, rig_id HAVING count(*) >= 20
        )
        SELECT DISTINCT ON (site_id) site_id, rig_id, n, median_arcsec
        FROM per_rig WHERE median_scale <= :max_scale
        ORDER BY site_id, median_arcsec
    """), {"days": DELIVERED_WINDOW_DAYS, "site_days": SEEING_SITE_WINDOW_DAYS,
           "max_scale": SEEING_MAX_SCALE})).all()
    names = dict((await db.execute(select(Rig.id, Rig.name).where(Rig.id.in_([r.rig_id for r in rows])))).all()) if rows else {}
    return {r.site_id: {
        "fwhm_arcsec": round(float(r.median_arcsec), 2), "rig_id": r.rig_id, "rig_name": names.get(r.rig_id),
        "n": int(r.n), "window_days": DELIVERED_WINDOW_DAYS,
    } for r in rows}


async def _default_seeing(db: AsyncSession) -> float:
    value = (await db.execute(select(Site.typical_seeing_arcsec).where(Site.is_default.is_(True)))).scalar()
    return value or DEFAULT_SEEING_ARCSEC


async def _load_rig(db: AsyncSession, rig_id: int) -> Rig:
    rig = (await db.execute(
        select(Rig).where(Rig.id == rig_id).execution_options(populate_existing=True)
    )).unique().scalar_one_or_none()
    if rig is None:
        raise HTTPException(status_code=404, detail="Rig not found")
    return rig


async def _rig_response(db: AsyncSession, rig_id: int) -> Dict[str, Any]:
    rig = await _load_rig(db, rig_id)
    usage = await _rig_usage(db, [rig_id])
    delivered = await _rig_delivered(db, [rig_id])
    return rig_dict(rig, usage.get(rig_id), await _default_seeing(db), delivered.get(rig_id))


async def _set_rig_filters(db: AsyncSession, rig_id: int, filter_ids: List[int]) -> None:
    ids = sorted(set(int(i) for i in filter_ids))
    if ids:
        found = set((await db.execute(select(Filter.id).where(Filter.id.in_(ids)))).scalars().all())
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"Unknown filter ids: {missing}")
    await db.execute(delete(rig_filters).where(rig_filters.c.rig_id == rig_id))
    for fid in ids:
        await db.execute(rig_filters.insert().values(rig_id=rig_id, filter_id=fid))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("")
@router.get("/", include_in_schema=False)
async def get_equipment(db: AsyncSession = Depends(get_db)):
    cameras = (await db.execute(select(Camera).order_by(Camera.name))).scalars().all()
    optics = (await db.execute(select(Optic).order_by(Optic.focal_length_mm))).scalars().all()
    filters = (await db.execute(select(Filter).order_by(Filter.name))).scalars().all()
    rigs = (await db.execute(select(Rig).order_by(Rig.is_mounted.desc(), Rig.is_active.desc(), Rig.name)))\
        .unique().scalars().all()
    sites = (await db.execute(select(Site).order_by(Site.is_default.desc(), Site.name))).scalars().all()

    cam_counts = await _rig_counts(db, Rig.camera_id)
    opt_counts = await _rig_counts(db, Rig.optic_id)
    usage = await _rig_usage(db)
    delivered = await _rig_delivered(db)
    site_counts = await _site_counts(db)
    site_measured = await _site_measured(db)
    seeing = next((s.typical_seeing_arcsec for s in sites if s.is_default and s.typical_seeing_arcsec),
                  DEFAULT_SEEING_ARCSEC)
    return {
        "cameras": [camera_dict(c, cam_counts.get(c.id, 0)) for c in cameras],
        "optics": [optic_dict(o, opt_counts.get(o.id, 0)) for o in optics],
        "filters": [filter_dict(f) for f in filters],
        "rigs": [rig_dict(r, usage.get(r.id), seeing, delivered.get(r.id)) for r in rigs],
        "sites": [site_dict(s, site_counts.get(s.id, 0), site_measured.get(s.id)) for s in sites],
        "telescopius_available": bool(settings.telescopius_api_key),
        "max_mounted_rigs": MAX_MOUNTED_RIGS,
    }


# ---------------------------------------------------------------------------
# Cameras / optics / filters
# ---------------------------------------------------------------------------

@router.post("/cameras", status_code=201, dependencies=[Depends(require_admin)])
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_name_free(db, Camera, body.name)
    data = body.model_dump()
    data["match_patterns"] = _camera_patterns(body.name, body.match_patterns)
    cam = Camera(**data, source=SOURCE_MANUAL)
    db.add(cam)
    await db.commit()
    await _equipment_changed()
    return camera_dict(cam, 0)


@router.put("/cameras/{camera_id}", dependencies=[Depends(require_admin)])
async def update_camera(camera_id: int, body: CameraUpdate, db: AsyncSession = Depends(get_db)):
    cam = await _get_or_404(db, Camera, camera_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name") is None:
        data.pop("name", None)
    else:
        await _ensure_name_free(db, Camera, data["name"], exclude_id=camera_id)
    if "match_patterns" in data and data["match_patterns"] is None:
        data["match_patterns"] = []
    for k, v in data.items():
        setattr(cam, k, v)
    cam.updated_at = datetime.utcnow()
    await db.commit()
    await _equipment_changed()
    counts = await _rig_counts(db, Rig.camera_id)
    return camera_dict(cam, counts.get(cam.id, 0))


@router.delete("/cameras/{camera_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_camera(camera_id: int, db: AsyncSession = Depends(get_db)):
    cam = await _get_or_404(db, Camera, camera_id)
    used = (await db.execute(select(func.count(Rig.id)).where(Rig.camera_id == camera_id))).scalar()
    if used:
        raise HTTPException(status_code=409, detail=f"Camera is used by {used} rig(s); delete or edit them first")
    await db.delete(cam)
    await db.commit()
    await _equipment_changed(queue=False)
    return Response(status_code=204)


@router.post("/optics", status_code=201, dependencies=[Depends(require_admin)])
async def create_optic(body: OpticCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_name_free(db, Optic, body.name)
    opt = Optic(**body.model_dump(), source=SOURCE_MANUAL)
    db.add(opt)
    await db.commit()
    await _equipment_changed()
    return optic_dict(opt, 0)


@router.put("/optics/{optic_id}", dependencies=[Depends(require_admin)])
async def update_optic(optic_id: int, body: OpticUpdate, db: AsyncSession = Depends(get_db)):
    opt = await _get_or_404(db, Optic, optic_id)
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items()
            if not (k in ("name", "kind", "focal_length_mm") and v is None)}
    if "name" in data:
        await _ensure_name_free(db, Optic, data["name"], exclude_id=optic_id)
    for k, v in data.items():
        setattr(opt, k, v)
    opt.updated_at = datetime.utcnow()
    await db.commit()
    await _equipment_changed()
    counts = await _rig_counts(db, Rig.optic_id)
    return optic_dict(opt, counts.get(opt.id, 0))


@router.delete("/optics/{optic_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_optic(optic_id: int, db: AsyncSession = Depends(get_db)):
    opt = await _get_or_404(db, Optic, optic_id)
    used = (await db.execute(select(func.count(Rig.id)).where(Rig.optic_id == optic_id))).scalar()
    if used:
        raise HTTPException(status_code=409, detail=f"Optic is used by {used} rig(s); delete or edit them first")
    await db.delete(opt)
    await db.commit()
    await _equipment_changed(queue=False)
    return Response(status_code=204)


@router.post("/filters", status_code=201, dependencies=[Depends(require_admin)])
async def create_filter(body: FilterCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_name_free(db, Filter, body.name)
    data = body.model_dump()
    data["band"] = data.get("band") or _band_for(body.name)
    data["match_patterns"] = body.match_patterns or [body.name.lower()]
    flt = Filter(**data, source=SOURCE_MANUAL)
    db.add(flt)
    await db.commit()
    await _equipment_changed(queue=False)
    return filter_dict(flt)


@router.put("/filters/{filter_id}", dependencies=[Depends(require_admin)])
async def update_filter(filter_id: int, body: FilterUpdate, db: AsyncSession = Depends(get_db)):
    flt = await _get_or_404(db, Filter, filter_id)
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items()
            if not (k in ("name", "band") and v is None)}
    if "name" in data:
        await _ensure_name_free(db, Filter, data["name"], exclude_id=filter_id)
    if "match_patterns" in data and data["match_patterns"] is None:
        data["match_patterns"] = []
    for k, v in data.items():
        setattr(flt, k, v)
    flt.updated_at = datetime.utcnow()
    await db.commit()
    await _equipment_changed(queue=False)
    return filter_dict(flt)


@router.delete("/filters/{filter_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_filter(filter_id: int, db: AsyncSession = Depends(get_db)):
    flt = await _get_or_404(db, Filter, filter_id)
    await db.execute(delete(rig_filters).where(rig_filters.c.filter_id == filter_id))
    await db.delete(flt)
    await db.commit()
    await _equipment_changed(queue=False)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Rigs
# ---------------------------------------------------------------------------

async def _check_rig_refs(db: AsyncSession, camera_id: Optional[int], optic_id: Optional[int]) -> None:
    if camera_id is not None and await db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=400, detail="Unknown camera_id")
    if optic_id is not None and await db.get(Optic, optic_id) is None:
        raise HTTPException(status_code=400, detail="Unknown optic_id")


@router.post("/rigs", status_code=201, dependencies=[Depends(require_admin)])
async def create_rig(body: RigCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_name_free(db, Rig, body.name)
    await _check_rig_refs(db, body.camera_id, body.optic_id)
    data = body.model_dump(exclude={"filter_ids"})
    rig = Rig(**data)
    db.add(rig)
    await db.flush()
    await _set_rig_filters(db, rig.id, body.filter_ids)
    await db.commit()
    await _equipment_changed()
    return await _rig_response(db, rig.id)


@router.put("/rigs/{rig_id}", dependencies=[Depends(require_admin)])
async def update_rig(rig_id: int, body: RigUpdate, db: AsyncSession = Depends(get_db)):
    rig = await _get_or_404(db, Rig, rig_id)
    data = body.model_dump(exclude_unset=True)
    filter_ids = data.pop("filter_ids", None)
    for k in ("name", "camera_id", "optic_id", "modifier_factor", "binning", "is_active"):
        if k in data and data[k] is None:
            data.pop(k)
    if "name" in data:
        await _ensure_name_free(db, Rig, data["name"], exclude_id=rig_id)
    await _check_rig_refs(db, data.get("camera_id"), data.get("optic_id"))
    for k, v in data.items():
        setattr(rig, k, v)
    rig.updated_at = datetime.utcnow()
    if filter_ids is not None:
        await _set_rig_filters(db, rig_id, filter_ids)
    await db.commit()
    await _equipment_changed()
    return await _rig_response(db, rig_id)


@router.delete("/rigs/{rig_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_rig(rig_id: int, db: AsyncSession = Depends(get_db)):
    rig = await _get_or_404(db, Rig, rig_id)
    # Clear rig_source too (the FK only nulls rig_id), so manual overrides don't linger.
    await db.execute(update(Image).where(Image.rig_id == rig_id).values(rig_id=None, rig_source=None))
    await db.delete(rig)  # rig_filters rows go with it (secondary + ON DELETE CASCADE)
    await db.commit()
    await _equipment_changed()
    return Response(status_code=204)


@router.post("/rigs/{rig_id}/mount", dependencies=[Depends(require_admin)])
async def mount_rig(rig_id: int, db: AsyncSession = Depends(get_db)):
    rig = await _get_or_404(db, Rig, rig_id)
    if not rig.is_mounted:
        mounted = (await db.execute(select(func.count()).select_from(Rig).where(Rig.is_mounted.is_(True)))).scalar()
        if mounted >= MAX_MOUNTED_RIGS:
            raise HTTPException(status_code=409,
                                detail=f"At most {MAX_MOUNTED_RIGS} rigs can be mounted at once; unmount one first")
    await db.execute(update(Rig).where(Rig.id == rig_id).values(is_mounted=True, updated_at=datetime.utcnow()))
    await db.commit()
    await _invalidate_recommendations()
    return await _rig_response(db, rig_id)


@router.delete("/rigs/{rig_id}/mount", dependencies=[Depends(require_admin)])
async def unmount_rig(rig_id: int, db: AsyncSession = Depends(get_db)):
    await _get_or_404(db, Rig, rig_id)
    await db.execute(update(Rig).where(Rig.id == rig_id).values(is_mounted=False, updated_at=datetime.utcnow()))
    await db.commit()
    await _invalidate_recommendations()
    return await _rig_response(db, rig_id)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

async def _existing_for_detection(db: AsyncSession) -> Dict[str, List[dict]]:
    cameras = (await db.execute(select(Camera))).scalars().all()
    optics = (await db.execute(select(Optic))).scalars().all()
    filters = (await db.execute(select(Filter))).scalars().all()
    rigs = (await db.execute(select(Rig))).unique().scalars().all()
    sites = (await db.execute(select(Site))).scalars().all()
    return {
        "cameras": [{"id": c.id, "name": c.name, "match_patterns": c.match_patterns or [],
                     "sensor_width_px": c.sensor_width_px, "sensor_height_px": c.sensor_height_px,
                     "pixel_size_um": c.pixel_size_um} for c in cameras],
        "optics": [{"id": o.id, "name": o.name, "focal_length_mm": o.focal_length_mm} for o in optics],
        "filters": [{"id": f.id, "name": f.name, "band": f.band, "match_patterns": f.match_patterns or []}
                    for f in filters],
        "rigs": [{"id": r.id, "camera_id": r.camera_id, "binning": r.binning,
                  "measured_scale_arcsec": r.measured_scale_arcsec,
                  "declared_scale": pixel_scale(r.camera.pixel_size_um if r.camera else None,
                                                r.optic.focal_length_mm if r.optic else None,
                                                r.binning, r.modifier_factor)} for r in rigs],
        "sites": [{"id": s.id, "latitude": s.latitude, "longitude": s.longitude} for s in sites],
    }


async def _proposals(db: AsyncSession, include_older: bool, use_cache: bool = True) -> Dict[str, Any]:
    from app.services.equipment_detection import LOOKBACK_MONTHS, fetch_detection_inputs, propose

    key = DETECT_CACHE_KEY.format(flag=int(include_older))
    if use_cache:
        cached = await _cache_get(key)
        if cached:
            return cached
    buckets, image_sites = await db.run_sync(lambda s: fetch_detection_inputs(s, include_older))
    existing = await _existing_for_detection(db)
    result = await asyncio.to_thread(
        propose, buckets, image_sites, existing, lookback_months=None if include_older else LOOKBACK_MONTHS)
    await _cache_set(key, result, DETECT_CACHE_TTL)
    return result


@router.get("/detect")
async def detect_equipment(include_older: bool = Query(False), db: AsyncSession = Depends(get_db)):
    return await _proposals(db, include_older)


@router.post("/detect/apply", dependencies=[Depends(require_admin)])
async def apply_detection(body: DetectApply, db: AsyncSession = Depends(get_db)):
    from app.services.equipment_detection import plan_apply

    default_tz = _valid_timezone(body.timezone)
    accept = [a.model_dump() for a in body.accept]
    for a in accept:
        if a.get("timezone"):
            _valid_timezone(a["timezone"])

    proposals = await _proposals(db, body.include_older, use_cache=False)
    plan = plan_apply(proposals, accept, default_tz)
    if plan["unknown"] and not body.include_older:
        # Accepted from an "include older" listing: look there too.
        proposals = await _proposals(db, True, use_cache=False)
        plan = plan_apply(proposals, accept, default_tz)
    if plan["unknown"]:
        raise HTTPException(status_code=400, detail=f"Unknown or stale proposal ids: {plan['unknown']}")

    refs: Dict[str, int] = {}
    created = {"cameras": 0, "optics": 0, "filters": 0, "rigs": 0, "sites": 0}

    def ref_id(ref: str) -> int:
        kind, _, value = ref.partition(":")
        return int(value) if kind == "existing" else refs[value]

    for spec in plan["cameras"]:
        cam = Camera(
            name=await _unique_name(db, Camera, spec["name"]), maker=spec.get("maker"),
            sensor_width_px=spec.get("sensor_width_px"), sensor_height_px=spec.get("sensor_height_px"),
            pixel_size_um=spec.get("pixel_size_um"), is_color=spec.get("is_color"),
            match_patterns=spec.get("match_patterns") or [], source=SOURCE_DETECTED,
        )
        db.add(cam)
        await db.flush()
        refs[spec["proposal_id"]] = cam.id
        created["cameras"] += 1
    for spec in plan["optics"]:
        opt = Optic(name=await _unique_name(db, Optic, spec["name"]), kind=spec.get("kind") or "TELESCOPE",
                    aperture_mm=spec.get("aperture_mm"), focal_length_mm=spec["focal_length_mm"],
                    source=SOURCE_DETECTED)
        db.add(opt)
        await db.flush()
        refs[spec["proposal_id"]] = opt.id
        created["optics"] += 1
    for spec in plan["filters"]:
        flt = Filter(name=await _unique_name(db, Filter, spec["name"]), band=spec.get("band") or "Other",
                     match_patterns=spec.get("match_patterns") or [], source=SOURCE_DETECTED)
        db.add(flt)
        await db.flush()
        refs[spec["proposal_id"]] = flt.id
        created["filters"] += 1
    for spec in plan["rigs"]:
        rig = Rig(name=await _unique_name(db, Rig, spec["name"]), camera_id=ref_id(spec["camera_ref"]),
                  optic_id=ref_id(spec["optic_ref"]), modifier_factor=spec["modifier_factor"],
                  modifier_name=spec.get("modifier_name"), binning=spec["binning"],
                  measured_scale_arcsec=spec.get("measured_scale_arcsec"))
        db.add(rig)
        await db.flush()
        await _set_rig_filters(db, rig.id, [ref_id(r) for r in spec["filter_refs"]])
        created["rigs"] += 1
    has_default = (await db.execute(select(Site.id).where(Site.is_default.is_(True)))).first() is not None
    for spec in sorted(plan["sites"], key=lambda s: -(s.get("image_count") or 0)):
        site = Site(name=await _unique_name(db, Site, spec["name"]), latitude=spec["latitude"],
                    longitude=spec["longitude"], timezone=spec.get("timezone") or default_tz,
                    is_default=not has_default)
        has_default = True
        db.add(site)
        await db.flush()
        created["sites"] += 1

    await db.commit()
    task_id = await _equipment_changed() if any(created.values()) else None
    return {"created": created, "assign_task_id": task_id}


# ---------------------------------------------------------------------------
# Telescopius import
# ---------------------------------------------------------------------------

@router.post("/import/telescopius", dependencies=[Depends(require_admin)])
async def import_telescopius(db: AsyncSession = Depends(get_db)):
    from app.services.telescopius import TelescopiusError, fetch_equipment, map_telescopius, merge_plan

    api_key = settings.telescopius_api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Telescopius API key not configured")
    try:
        payload = await asyncio.to_thread(fetch_equipment, api_key)
    except TelescopiusError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.warning(f"Telescopius import failed: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Telescopius request failed")

    plan = map_telescopius(payload)
    models = {"cameras": Camera, "optics": Optic, "filters": Filter}
    existing = {}
    for kind, model in models.items():
        rows = (await db.execute(select(model))).unique().scalars().all()
        existing[kind] = [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in rows]
    merged = merge_plan(plan, existing)

    summary = {}
    for kind, model in models.items():
        m = merged[kind]
        for spec in m["create"]:
            db.add(model(**{**spec, "name": await _unique_name(db, model, spec["name"])}, source=SOURCE_TELESCOPIUS))
            await db.flush()
        for row_id, changes in m["update"]:
            obj = await db.get(model, row_id)
            for k, v in changes.items():
                setattr(obj, k, v)
            obj.updated_at = datetime.utcnow()
        summary[kind] = {"created": len(m["create"]), "updated": len(m["update"]), "skipped": m["skipped"]}
    await db.commit()
    changed = any(summary[k]["created"] or summary[k]["updated"] for k in summary)
    await _equipment_changed(queue=changed and bool(merged["cameras"]["update"]))
    return {**summary, "mount_suggestions": merged["mount_suggestions"]}


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

@router.post("/assign", dependencies=[Depends(require_admin)])
async def queue_assignment(scope: str = Query("unassigned", pattern="^(unassigned|all)$")):
    from app.tasks.equipment import queue_assign_equipment

    try:
        task_id = await asyncio.to_thread(queue_assign_equipment, scope)
    except Exception as e:
        logger.warning(f"Could not queue equipment assignment: {e}")
        raise HTTPException(status_code=503, detail="Could not queue the assignment task")
    return {"task_id": task_id, "queued": task_id is not None}


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

async def _site_response(db: AsyncSession, site: Site) -> Dict[str, Any]:
    count = (await db.execute(
        select(func.count(Image.id)).where(_light_subs(), Image.site_id == site.id)
    )).scalar()
    return site_dict(site, count or 0, (await _site_measured(db)).get(site.id))


@sites_router.get("")
@sites_router.get("/", include_in_schema=False)
async def list_sites(db: AsyncSession = Depends(get_db)):
    sites = (await db.execute(select(Site).order_by(Site.is_default.desc(), Site.name))).scalars().all()
    counts = await _site_counts(db)
    measured = await _site_measured(db)
    return [site_dict(s, counts.get(s.id, 0), measured.get(s.id)) for s in sites]


@sites_router.post("", status_code=201, dependencies=[Depends(require_admin)])
@sites_router.post("/", status_code=201, dependencies=[Depends(require_admin)], include_in_schema=False)
async def create_site(body: SiteCreate, db: AsyncSession = Depends(get_db)):
    _valid_timezone(body.timezone)
    await _ensure_name_free(db, Site, body.name)
    if body.is_default:
        await db.execute(update(Site).where(Site.is_default.is_(True)).values(is_default=False))
        await db.flush()
    site = Site(**body.model_dump())
    db.add(site)
    await db.commit()
    await _equipment_changed()
    return await _site_response(db, site)


@sites_router.put("/{site_id}", dependencies=[Depends(require_admin)])
async def update_site(site_id: int, body: SiteUpdate, db: AsyncSession = Depends(get_db)):
    site = await _get_or_404(db, Site, site_id)
    data = body.model_dump(exclude_unset=True)
    for k in ("name", "latitude", "longitude", "timezone", "typical_seeing_arcsec", "is_default"):
        if k in data and data[k] is None:
            data.pop(k)
    if "timezone" in data:
        _valid_timezone(data["timezone"])
    if "name" in data:
        await _ensure_name_free(db, Site, data["name"], exclude_id=site_id)
    if data.get("is_default"):
        await db.execute(update(Site).where(Site.id != site_id, Site.is_default.is_(True)).values(is_default=False))
        await db.flush()
    for k, v in data.items():
        setattr(site, k, v)
    site.updated_at = datetime.utcnow()
    await db.commit()
    await _cache_delete(HORIZON_CACHE_KEY.format(site_id=site_id))
    affects_assignment = bool({"latitude", "longitude", "timezone", "is_default"} & set(data))
    await _equipment_changed(queue=affects_assignment)
    return await _site_response(db, site)


@sites_router.delete("/{site_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_site(site_id: int, db: AsyncSession = Depends(get_db)):
    site = await _get_or_404(db, Site, site_id)
    await db.execute(update(Image).where(Image.site_id == site_id).values(site_id=None))
    await db.delete(site)
    await db.commit()
    await _cache_delete(HORIZON_CACHE_KEY.format(site_id=site_id))
    await _equipment_changed()
    return Response(status_code=204)


@sites_router.get("/{site_id}/horizon/learned")
async def learned_horizon(site_id: int, db: AsyncSession = Depends(get_db)):
    # Same SQL, computation and cache key as the recommendation engine (services/site_horizon.py).
    from app.services.site_horizon import HORIZON_SAMPLES_SQL, profile_from_rows

    site = await _get_or_404(db, Site, site_id)
    key = HORIZON_CACHE_KEY.format(site_id=site_id)
    cached = await _cache_get(key)
    if cached:
        return cached
    rows = [dict(r) for r in (await db.execute(HORIZON_SAMPLES_SQL, {"site_id": site_id})).mappings().all()]
    result = await asyncio.to_thread(profile_from_rows, rows, site.latitude, site.longitude)
    await _cache_set(key, result, HORIZON_CACHE_TTL)
    return result


@sites_router.put("/{site_id}/horizon", dependencies=[Depends(require_admin)])
async def update_horizon(site_id: int, body: HorizonUpdate, db: AsyncSession = Depends(get_db)):
    from app.utils.horizon import normalize_points

    site = await _get_or_404(db, Site, site_id)
    try:
        points = normalize_points(body.points)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    site.horizon = points or None
    site.horizon_source = body.source if points else None
    site.updated_at = datetime.utcnow()
    await db.commit()
    await _invalidate_recommendations()
    return await _site_response(db, site)


@sites_router.post("/{site_id}/horizon/import", dependencies=[Depends(require_admin)])
async def import_horizon(site_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    from app.utils.horizon import parse_hrz

    site = await _get_or_404(db, Site, site_id)
    raw = await file.read(1_000_001)
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=413, detail="Horizon file too large")
    try:
        points = parse_hrz(raw.decode("utf-8-sig", errors="replace"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid .hrz file: {e}")
    if not points:
        raise HTTPException(status_code=400, detail="The .hrz file contains no points")
    site.horizon = points
    site.horizon_source = "IMPORTED"
    site.updated_at = datetime.utcnow()
    await db.commit()
    await _invalidate_recommendations()
    return await _site_response(db, site)


@sites_router.get("/{site_id}/horizon/export")
async def export_horizon(site_id: int, db: AsyncSession = Depends(get_db)):
    from app.utils.horizon import format_hrz

    site = await _get_or_404(db, Site, site_id)
    if not site.horizon:
        raise HTTPException(status_code=404, detail="This site has no saved horizon")
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", site.name).strip("_") or f"site_{site.id}"
    return PlainTextResponse(
        format_hrz(site.horizon, title=site.name),
        headers={"Content-Disposition": f'attachment; filename="{filename}.hrz"'},
    )
