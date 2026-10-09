"""
One online overlay layer for an image: settings gate, cone around the plate solution,
Redis cache, upstream fetch, de-duplication against the local catalogs, projection.

Cached rows are the normalised catalog rows of a cone (not pixels), keyed by the query
itself, so they stay valid for any image of the same field. Catalog data and minor-body
positions at a fixed epoch don't change, hence the long TTL. A failed query is
remembered briefly so a flaky service isn't hammered by every viewer.
"""

import hashlib
import json
import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from app.config import settings
from app.services import sky_overlay
from app.services.online_catalogs import skybot, vizier
from app.services.online_catalogs.registry import (BY_KEY, KIND_SKYBOT, LIMIT_MAG, LIMIT_SIZE, REGISTRY,
                                                   OnlineCatalogSpec)
from app.services.online_catalogs.vizier import OnlineCatalogError

logger = logging.getLogger(__name__)

CACHE_PREFIX = "skyonline:v1"
CACHE_TTL_S = 30 * 24 * 3600
FAIL_TTL_S = 5 * 60
QUERY_MARGIN_DEG = 0.25            # objects centred just outside the frame can reach into it
NEBULA_MARGIN_DEG = 1.0            # dark/bright nebulae can span degrees
AUTO_SIZE_PX = 8.0                 # PGC default: galaxies at least this many pixels across
DEDUP_SEP_ARCMIN = 0.5

REASON_DISABLED = "disabled"
REASON_NO_WCS = "no_wcs"
REASON_NO_CAPTURE_TIME = "no_capture_time"
REASON_FIELD_TOO_WIDE = "field_too_wide"

NOTICE_STACK = "Stacked image: positions are for the recorded capture time; moving bodies may be offset or missing from the stack"
NOTICE_FILE_TIME = "Capture time comes from the file date, so positions may be wrong"


class ThrottledError(OnlineCatalogError):
    """The same query failed moments ago; not retried yet."""


# ---- settings ------------------------------------------------------------------------------

def catalog_settings() -> Dict[str, Dict[str, Any]]:
    """{key: {enabled, limit}} for every registered catalog; missing entries are off."""
    from app.services.quality_settings import runtime_settings
    stored = runtime_settings().get("online_catalogs") or {}
    out = {}
    for spec in REGISTRY:
        entry = stored.get(spec.key) if isinstance(stored, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        limit = entry.get("limit")
        out[spec.key] = {
            "enabled": entry.get("enabled") is True,
            "limit": float(limit) if isinstance(limit, (int, float)) and math.isfinite(limit) else None,
        }
    return out


def enabled_specs() -> List[OnlineCatalogSpec]:
    cfg = catalog_settings()
    return [s for s in REGISTRY if cfg[s.key]["enabled"]]


# ---- query planning ------------------------------------------------------------------------

def _plate_scale_arcsec(frame: sky_overlay.SkyFrame, radius_deg: float) -> float:
    diag = math.hypot(frame.width, frame.height)
    return radius_deg * 2 * 3600.0 / diag if diag else 1.0


def plan_query(spec: OnlineCatalogSpec, frame: sky_overlay.SkyFrame, limit: Optional[float],
               image: Any = None) -> Dict[str, Any]:
    """
    What to ask upstream for this frame: {ra, dec, radius, constraints, limit, jd}, or
    {reason} when the layer can't be shown for this image.
    """
    ra, dec, radius = frame.field_circle()
    if not all(math.isfinite(v) for v in (ra, dec, radius)):
        return {"reason": REASON_NO_WCS}
    effective = limit if limit is not None else spec.default_limit
    plan: Dict[str, Any] = {"ra": round(ra, 5), "dec": round(dec, 5), "constraints": {}, "jd": None}

    if spec.kind == KIND_SKYBOT:
        if radius > skybot.MAX_RADIUS_DEG:
            return {"reason": REASON_FIELD_TOO_WIDE}
        epoch = skybot.epoch_for(getattr(image, "capture_date_utc", None), getattr(image, "exposure_time_seconds", None))
        if epoch is None:
            return {"reason": REASON_NO_CAPTURE_TIME}
        plan.update(radius=round(radius, 4), jd=round(skybot.julian_date(epoch), 6), limit=effective)
        return plan

    margin = NEBULA_MARGIN_DEG if spec.key in ("LDN", "LBN", "BARNARD") else QUERY_MARGIN_DEG
    plan["radius"] = round(radius + margin, 4)
    if spec.limit_kind == LIMIT_SIZE:
        if effective is None:   # auto: about AUTO_SIZE_PX across at this plate scale
            effective = max(0.1, AUTO_SIZE_PX * _plate_scale_arcsec(frame, radius) / 60.0)
        effective = round(effective, 2)
        plan["constraints"]["logD25"] = f">={math.log10(effective * 10.0):.3f}"   # D25 is log(0.1 arcmin)
    elif spec.limit_kind == LIMIT_MAG and effective is not None and spec.key == "ABELL":
        plan["constraints"]["m10"] = f"<={effective:g}"
    plan["limit"] = effective
    return plan


def cache_key(spec: OnlineCatalogSpec, plan: Dict[str, Any]) -> str:
    body = json.dumps({k: plan.get(k) for k in ("ra", "dec", "radius", "constraints", "limit", "jd")}, sort_keys=True)
    return f"{CACHE_PREFIX}:{spec.key}:{hashlib.sha1(body.encode()).hexdigest()[:20]}"


# ---- cache ---------------------------------------------------------------------------------

async def _redis():
    try:
        import redis.asyncio as redis_async
        return redis_async.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


async def _cache_get(key: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """(cached rows, recent failure message); both None when nothing is cached or Redis is down."""
    r = await _redis()
    if not r:
        return None, None
    try:
        raw, failed = await r.mget(key, f"{key}:fail")
        await r.close()
        return (json.loads(raw) if raw else None), failed
    except Exception as e:
        logger.debug(f"Online catalog cache read failed: {e}")
        return None, None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    r = await _redis()
    if not r:
        return
    try:
        await r.setex(key, ttl, value)
        await r.close()
    except Exception as e:
        logger.debug(f"Online catalog cache write failed: {e}")


async def fetch_rows(spec: OnlineCatalogSpec, plan: Dict[str, Any], use_cache: bool = True) -> List[Dict[str, Any]]:
    key = cache_key(spec, plan)
    if use_cache:
        rows, failed = await _cache_get(key)
        if rows is not None:
            return rows
        if failed:
            raise ThrottledError(failed)
    try:
        if spec.kind == KIND_SKYBOT:
            rows = await skybot.cone(plan["jd"], plan["ra"], plan["dec"], plan["radius"], plan.get("limit"))
        else:
            rows = await vizier.cone(spec, plan["ra"], plan["dec"], plan["radius"], plan["constraints"])
    except OnlineCatalogError as e:
        await _cache_set(f"{key}:fail", str(e)[:300], FAIL_TTL_S)
        raise
    await _cache_set(key, json.dumps(rows), CACHE_TTL_S)
    return rows


# ---- de-duplication and objects ------------------------------------------------------------

def _keys(row: Dict[str, Any]) -> Iterable[str]:
    for v in [row.get("designation"), *(row.get("aliases") or [])]:
        k = sky_overlay.norm_designation(v)
        if k:
            yield k


def dedup_against_local(rows: List[Dict[str, Any]], local_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Drop online rows the local catalogs already show: a shared designation or alias
    ('NGC224' in PGC's alternate names), or a non-star local object within DEDUP_SEP_ARCMIN.
    """
    local_keys = {k for r in local_rows for k in _keys(r)}
    pts = [(r["ra"], r["dec"]) for r in local_rows
           if r.get("catalog") != "NAMED_STAR" and r.get("ra") is not None and r.get("dec") is not None]
    lra = np.radians([p[0] for p in pts]) if pts else None
    ldec = np.radians([p[1] for p in pts]) if pts else None
    limit = math.cos(math.radians(DEDUP_SEP_ARCMIN / 60.0))
    out = []
    for row in rows:
        if any(k in local_keys for k in _keys(row)):
            continue
        if lra is not None:
            ra, dec = math.radians(row["ra"]), math.radians(row["dec"])
            cos_sep = np.sin(dec) * np.sin(ldec) + np.cos(dec) * np.cos(ldec) * np.cos(lra - ra)
            if np.any(cos_sep >= limit):
                continue
        out.append(row)
    return out


def build_layer_objects(frame: sky_overlay.SkyFrame, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """build_objects() for one online layer, carrying each row's link and motion through."""
    extra = {}
    for r in rows:
        k = f"{r['catalog']}:{sky_overlay.norm_designation(r['designation'])}"
        extra.setdefault(k, {"url": r.get("url"), "motion_arcsec_h": r.get("motion_arcsec_h"),
                             "is_comet": r.get("is_comet")})
    objects = sky_overlay.build_objects(frame, rows)
    for o in objects:
        o.update(extra.get(o["key"], {}))
    return objects


def notice_for(spec: OnlineCatalogSpec, image: Any) -> Optional[str]:
    if spec.kind != KIND_SKYBOT:
        return None
    notes = []
    subtype = getattr(getattr(image, "subtype", None), "value", getattr(image, "subtype", None))
    if subtype == "INTEGRATION_MASTER":
        notes.append(NOTICE_STACK)
    if getattr(image, "capture_time_source", None) == "FILE_MTIME":
        notes.append(NOTICE_FILE_TIME)
    return "; ".join(notes) or None


def get_spec(key: str) -> Optional[OnlineCatalogSpec]:
    return BY_KEY.get((key or "").upper())
