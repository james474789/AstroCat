"""
Targets API (F2)
Aggregated per-target integration dashboard: list, unassigned summary, detail, goals.

See docs/design/F2-target-integration.md §3.5/3.7.
"""

import hashlib
import json
import math
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text, delete, Numeric
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.database import get_db
from app.models.image import Image, ImageSubtype, FrameType
from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog, NamedStarCatalog, Sh2Catalog
from app.models.target import TargetGoal
from app.schemas.target import TargetGoalInput
from app.services.targets import normalize_designation
from app.utils.filter_names import normalize_filter, filter_sort_key
from app.utils.path_security import validate_path_safety
from app.utils.rig_optics import (
    build_filter_rig_rows, valid_pixel_scale, parse_pixel_size, known_pixel_size, binning_factor,
)
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY_LIST = "cache:targets:list"
CACHE_KEY_LIST_PREFIX = "cache:targets:list:"
CACHE_TTL_LIST = 120


def _cache_key_for_path(path: Optional[str]) -> str:
    """Redis key for the targets list, scoped to `path` (root list when path is falsy)."""
    if not path:
        return CACHE_KEY_LIST
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_LIST_PREFIX}{digest}"


def _normalize_path_prefix(path: str) -> str:
    """
    Turn a folder path into a prefix that only matches paths *under* it, e.g.
    "/data/2025" -> "/data/2025/", so it can't also match "/data/2025-backup".
    Handles both '/' and '\\' separators since file_path may use either
    depending on the platform that indexed it (see filesystem.py list_directory).
    """
    if path.endswith("/") or path.endswith("\\"):
        return path
    return path + ("\\" if "\\" in path and "/" not in path else "/")


def _path_clause(path: Optional[str]):
    """SQLAlchemy predicate restricting Image.file_path to under `path` (both separators)."""
    if not path:
        return None
    prefix = _normalize_path_prefix(path)
    alt_prefix = prefix.replace("/", "\\") if "/" in prefix else prefix.replace("\\", "/")
    if alt_prefix == prefix:
        return Image.file_path.startswith(prefix)
    return Image.file_path.startswith(prefix) | Image.file_path.startswith(alt_prefix)


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a folder name containing % or _ is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _path_like_sql(path: Optional[str]) -> Optional[str]:
    """
    A `file_path LIKE :path_prefix ESCAPE '\\' OR file_path LIKE :path_alt_prefix ESCAPE '\\'`
    fragment for the raw-SQL queries below, or None when unscoped. Bind params
    are returned separately since text() params can't be built from a plain string.
    """
    if not path:
        return None
    return "(file_path LIKE :path_prefix ESCAPE '\\' OR file_path LIKE :path_alt_prefix ESCAPE '\\')"


def _path_like_params(path: Optional[str]) -> dict:
    if not path:
        return {}
    prefix = _normalize_path_prefix(path)
    alt_prefix = prefix.replace("/", "\\") if "/" in prefix else prefix.replace("\\", "/")
    return {
        "path_prefix": _escape_like(prefix) + "%",
        "path_alt_prefix": _escape_like(alt_prefix) + "%",
    }


def _validate_folder_path(path: str) -> None:
    """Reject any path outside the configured image roots (mirrors filesystem.py)."""
    allowed_roots = [Path(p).resolve() for p in settings.image_paths_list]
    if not validate_path_safety(path, allowed_roots):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Path not within allowed directories or contains unsafe elements",
        )


def _light_subs_clause():
    """
    Canonical "counts toward integration" predicate (README §2.1): a LIGHT
    sub-frame (not a master, not a calibration frame, not planetary).
    TODO(F1): replace with light_subs_clause() from app.utils.frame_filters
    once F1b (the full frame-type classifier) merges - it doesn't exist yet.
    """
    return (Image.frame_type == FrameType.LIGHT) & (Image.subtype == ImageSubtype.SUB_FRAME)


async def _get_redis():
    try:
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning(f"Targets cache: failed to connect to redis: {e}")
        return None


async def _invalidate_targets_cache():
    """Drop the root list plus every per-folder cache entry (each keyed by path hash)."""
    r = await _get_redis()
    if not r:
        return
    try:
        keys = [CACHE_KEY_LIST]
        async for key in r.scan_iter(match=f"{CACHE_KEY_LIST_PREFIX}*"):
            keys.append(key)
        if keys:
            await r.delete(*keys)
        await r.close()
    except Exception as e:
        logger.warning(f"Targets cache: failed to invalidate: {e}")


# ---------------------------------------------------------------------------
# Catalog display metadata (bulk lookup, keyed by normalized target_key)
# ---------------------------------------------------------------------------

async def _build_catalog_metadata_map(db: AsyncSession) -> Dict[str, dict]:
    """
    Build a {target_key -> catalog metadata} map for every catalog object, so
    the targets list can show common_name/object_type/constellation without a
    per-target query. Only runs once per cache refresh (every CACHE_TTL_LIST).
    """
    meta: Dict[str, dict] = {}

    for row in (await db.execute(select(MessierCatalog))).scalars().all():
        key = f"M{row.messier_number}"
        meta[key] = {
            "catalog_type": "MESSIER",
            "designation": row.designation,
            "common_name": row.common_name,
            "object_type": row.object_type,
            "constellation": row.constellation,
            "apparent_magnitude": row.apparent_magnitude,
            "ra_degrees": row.ra_degrees,
            "dec_degrees": row.dec_degrees,
        }

    for row in (await db.execute(select(NGCCatalog))).scalars().all():
        if row.messier_designation:
            key = normalize_designation(row.messier_designation)
        else:
            key = normalize_designation(row.designation)
        if key in meta:
            continue  # Messier entry already covers this object with richer data
        meta[key] = {
            "catalog_type": "IC" if (row.designation or "").upper().startswith("IC") else "NGC",
            "designation": row.designation,
            "common_name": row.common_name,
            "object_type": row.object_type,
            "constellation": row.constellation,
            "apparent_magnitude": row.apparent_magnitude,
            "ra_degrees": row.ra_degrees,
            "dec_degrees": row.dec_degrees,
        }

    for row in (await db.execute(select(CaldwellCatalog))).scalars().all():
        src_key = normalize_designation(row.source_designation) if row.source_designation else None
        key = src_key if (src_key and src_key in meta) else normalize_designation(row.designation)
        if key not in meta:
            meta[key] = {
                "catalog_type": "CALDWELL",
                "designation": row.designation,
                "common_name": row.common_name,
                "object_type": row.object_type,
                "constellation": row.constellation,
                "apparent_magnitude": row.apparent_magnitude,
                "ra_degrees": row.ra_degrees,
                "dec_degrees": row.dec_degrees,
            }
        elif not meta[key].get("common_name") and row.common_name:
            meta[key]["common_name"] = row.common_name

    for row in (await db.execute(select(Sh2Catalog))).scalars().all():
        key = normalize_designation(row.designation)
        if key not in meta:
            meta[key] = {
                "catalog_type": "SH2",
                "designation": row.designation,
                "common_name": row.common_name,
                "object_type": row.object_type,
                "constellation": row.constellation,
                "apparent_magnitude": row.apparent_magnitude,
                "ra_degrees": row.ra_degrees,
                "dec_degrees": row.dec_degrees,
            }

    for row in (await db.execute(select(NamedStarCatalog))).scalars().all():
        key = normalize_designation(row.designation)
        if key not in meta:
            meta[key] = {
                "catalog_type": "NAMED_STAR",
                "designation": row.designation,
                "common_name": row.common_name,
                "object_type": "Star",
                "constellation": None,
                "apparent_magnitude": row.magnitude,
                "ra_degrees": row.ra_degrees,
                "dec_degrees": row.dec_degrees,
            }

    return meta


async def _fetch_obj_display_names(db: AsyncSession, obj_keys: List[str]) -> Dict[str, str]:
    """For OBJ:<x> keys, the display name is the most frequent original object_name spelling."""
    if not obj_keys:
        return {}
    stmt = (
        select(Image.target_key, Image.object_name, func.count(Image.id).label("cnt"))
        .where(Image.target_key.in_(obj_keys))
        .group_by(Image.target_key, Image.object_name)
        .order_by(Image.target_key, func.count(Image.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    result: Dict[str, str] = {}
    for r in rows:
        if r.target_key not in result and r.object_name:
            result[r.target_key] = r.object_name
    return result


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------

async def _compute_targets_list(db: AsyncSession, path: Optional[str] = None) -> List[dict]:
    """
    Build the full folded targets list (§3.5): one row per (target_key, raw
    filter_name), folded into per-target summaries with a per-filter
    breakdown. This is the function cached under cache:targets:list (or a
    per-folder variant when `path` is given - see _cache_key_for_path).

    When `path` is set, every stat (seconds, subs, nights, rigs, masters,
    cover image, ...) is scoped to images whose file_path is under that
    folder - i.e. folder-scoped totals, not just folder-scoped membership.
    """
    light_clause = _light_subs_clause()
    path_clause = _path_clause(path)

    stmt = (
        select(
            Image.target_key,
            Image.filter_name,
            func.count(Image.id).label("subs"),
            func.sum(Image.exposure_time_seconds).label("secs"),
            func.min(Image.capture_date).label("first"),
            func.max(Image.capture_date).label("last"),
        )
        .where(light_clause, Image.target_key.isnot(None))
        .group_by(Image.target_key, Image.filter_name)
    )
    if path_clause is not None:
        stmt = stmt.where(path_clause)
    rows = (await db.execute(stmt)).all()

    if not rows:
        return []

    targets: Dict[str, dict] = {}
    for row in rows:
        key = row.target_key
        t = targets.setdefault(key, {
            "total_seconds": 0.0,
            "total_subs": 0,
            "first_capture": None,
            "last_capture": None,
            "_filter_raw": {},
        })
        norm = normalize_filter(row.filter_name)
        bucket = t["_filter_raw"].setdefault(norm, {"raw_names": set(), "subs": 0, "seconds": 0.0})
        bucket["raw_names"].add(row.filter_name or "")
        bucket["subs"] += row.subs or 0
        bucket["seconds"] += float(row.secs or 0)

        t["total_seconds"] += float(row.secs or 0)
        t["total_subs"] += row.subs or 0
        if row.first and (t["first_capture"] is None or row.first < t["first_capture"]):
            t["first_capture"] = row.first
        if row.last and (t["last_capture"] is None or row.last > t["last_capture"]):
            t["last_capture"] = row.last

    keys = list(targets.keys())

    # Nights (README §2.2: simple date-boundary count, no dependency on F7 sessions)
    path_like_sql = _path_like_sql(path)
    nights_stmt = text(f"""
        SELECT target_key, count(distinct date(capture_date - interval '12 hours')) as nights
        FROM images
        WHERE target_key = ANY(:keys) AND frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
        {f"AND {path_like_sql}" if path_like_sql else ""}
        GROUP BY target_key
    """)
    for r in (await db.execute(nights_stmt, {"keys": keys, **_path_like_params(path)})).all():
        targets[r.target_key]["nights"] = r.nights

    # Rigs
    rigs_stmt = (
        select(
            Image.target_key,
            func.array_agg(func.distinct(Image.camera_name)).label("cameras"),
            func.array_agg(func.distinct(Image.telescope_name)).label("telescopes"),
        )
        .where(light_clause, Image.target_key.in_(keys))
        .group_by(Image.target_key)
    )
    if path_clause is not None:
        rigs_stmt = rigs_stmt.where(path_clause)
    for r in (await db.execute(rigs_stmt)).all():
        targets[r.target_key]["cameras"] = sorted([c for c in (r.cameras or []) if c])
        targets[r.target_key]["telescopes"] = sorted([tt for tt in (r.telescopes or []) if tt])

    # Masters (subtype = INTEGRATION_MASTER, frame_type = LIGHT)
    masters_stmt = (
        select(Image.target_key, func.count(Image.id).label("cnt"))
        .where(
            Image.target_key.in_(keys),
            Image.frame_type == FrameType.LIGHT,
            Image.subtype == ImageSubtype.INTEGRATION_MASTER,
        )
        .group_by(Image.target_key)
    )
    if path_clause is not None:
        masters_stmt = masters_stmt.where(path_clause)
    for r in (await db.execute(masters_stmt)).all():
        targets[r.target_key]["master_count"] = r.cnt

    # Planetary count (excluded from integration sums, shown separately per §7 decisions)
    planetary_stmt = (
        select(Image.target_key, func.count(Image.id).label("cnt"))
        .where(
            Image.target_key.in_(keys),
            Image.frame_type == FrameType.LIGHT,
            Image.subtype == ImageSubtype.PLANETARY,
        )
        .group_by(Image.target_key)
    )
    if path_clause is not None:
        planetary_stmt = planetary_stmt.where(path_clause)
    for r in (await db.execute(planetary_stmt)).all():
        targets[r.target_key]["planetary_count"] = r.cnt

    # Cover image: highest-rated master, else most recent master, else most recent light w/ thumbnail
    cover_stmt = text(f"""
        SELECT DISTINCT ON (target_key) target_key, id
        FROM images
        WHERE target_key = ANY(:keys)
          AND frame_type = 'LIGHT'
          AND thumbnail_path IS NOT NULL
          {f"AND {path_like_sql}" if path_like_sql else ""}
        ORDER BY target_key,
          (subtype = 'INTEGRATION_MASTER') DESC,
          CASE WHEN subtype = 'INTEGRATION_MASTER' THEN COALESCE(rating, -1) ELSE -1 END DESC,
          capture_date DESC NULLS LAST
    """)
    for r in (await db.execute(cover_stmt, {"keys": keys, **_path_like_params(path)})).all():
        targets[r.target_key]["cover_image_id"] = r.id

    # Display metadata
    catalog_meta = await _build_catalog_metadata_map(db)
    obj_keys = [k for k in keys if k.startswith("OBJ:")]
    obj_names = await _fetch_obj_display_names(db, obj_keys)

    # Goals
    goals_rows = (await db.execute(select(TargetGoal).where(TargetGoal.target_key.in_(keys)))).scalars().all()
    goals_map: Dict[str, Dict[str, float]] = {}
    for g in goals_rows:
        goals_map.setdefault(g.target_key, {})[g.filter_group] = g.goal_seconds

    results = []
    for key, t in targets.items():
        target_goals = goals_map.get(key, {})
        filters = []
        for norm, bucket in t["_filter_raw"].items():
            filters.append({
                "filter": norm,
                "raw_names": sorted(n for n in bucket["raw_names"] if n),
                "subs": bucket["subs"],
                "seconds": bucket["seconds"],
                "goal_seconds": target_goals.get(norm),
            })
        filters.sort(key=lambda f: filter_sort_key(f["filter"]))

        meta = catalog_meta.get(key)
        if meta:
            base_name = meta.get("designation") or key
            display_name = f"{base_name} — {meta['common_name']}" if meta.get("common_name") else base_name
            catalog_type = meta.get("catalog_type")
            object_type = meta.get("object_type")
            constellation = meta.get("constellation")
        else:
            display_name = obj_names.get(key, key[4:] if key.startswith("OBJ:") else key)
            catalog_type = None
            object_type = None
            constellation = None

        results.append({
            "target_key": key,
            "display_name": display_name,
            "catalog_type": catalog_type,
            "object_type": object_type,
            "constellation": constellation,
            "total_seconds": t["total_seconds"],
            "total_subs": t["total_subs"],
            "planetary_count": t.get("planetary_count", 0),
            "nights": t.get("nights", 0),
            "first_capture": t["first_capture"].isoformat() if t["first_capture"] else None,
            "last_capture": t["last_capture"].isoformat() if t["last_capture"] else None,
            "filters": filters,
            "cameras": t.get("cameras", []),
            "telescopes": t.get("telescopes", []),
            "master_count": t.get("master_count", 0),
            "cover_image_id": t.get("cover_image_id"),
        })

    return results


async def get_cached_targets_list(db: AsyncSession, path: Optional[str] = None) -> List[dict]:
    """
    Read-through Redis cache around _compute_targets_list (§3.5, TTL 120s).
    Each distinct `path` gets its own cache entry (see _cache_key_for_path);
    _invalidate_targets_cache clears all of them together.
    """
    cache_key = _cache_key_for_path(path)
    r = await _get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                await r.close()
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Targets cache read failed: {e}")

    result = await _compute_targets_list(db, path=path)

    if r:
        try:
            await r.setex(cache_key, CACHE_TTL_LIST, json.dumps(result))
            await r.close()
        except Exception as e:
            logger.warning(f"Targets cache write failed: {e}")

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def list_targets(
    search: Optional[str] = Query(None),
    sort: str = Query("integration", description="integration|name|last|subs"),
    order: str = Query("desc"),
    min_hours: Optional[float] = Query(None),
    filter: Optional[str] = Query(None, description="Normalized filter bucket, e.g. Ha"),
    has_master: Optional[bool] = Query(None),
    catalog: Optional[str] = Query(None, description="MESSIER|NGC|IC|CALDWELL|SH2|OTHER"),
    path: Optional[str] = Query(None, description="Scope to images whose file_path is under this folder"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=10000),
    keys_only: bool = Query(False, description="Return only target_key values (lightweight)"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, filterable/sortable list of targets (§3.7), optionally scoped to a folder."""
    if path:
        _validate_folder_path(path)
    items = await get_cached_targets_list(db, path=path)

    if keys_only:
        return {"keys": [t["target_key"] for t in items]}

    if search:
        q = search.strip().lower()
        items = [
            t for t in items
            if q in t["target_key"].lower() or q in t["display_name"].lower()
        ]

    if min_hours is not None:
        threshold = min_hours * 3600
        items = [t for t in items if t["total_seconds"] >= threshold]

    if filter:
        items = [t for t in items if any(f["filter"] == filter for f in t["filters"])]

    if has_master is not None:
        items = [t for t in items if (t["master_count"] > 0) == has_master]

    if catalog:
        catalog = catalog.upper()
        if catalog == "OTHER":
            items = [t for t in items if not t["catalog_type"]]
        else:
            items = [t for t in items if t["catalog_type"] == catalog]

    sort_keys = {
        "integration": lambda t: t["total_seconds"],
        "name": lambda t: t["display_name"].lower(),
        "last": lambda t: t["last_capture"] or "",
        "subs": lambda t: t["total_subs"],
    }
    key_fn = sort_keys.get(sort, sort_keys["integration"])
    items = sorted(items, key=key_fn, reverse=(order != "asc"))

    total = len(items)
    total_pages = math.ceil(total / page_size) if page_size > 0 else 1
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/unassigned/summary")
async def unassigned_summary(
    path: Optional[str] = Query(None, description="Scope to images whose file_path is under this folder"),
    db: AsyncSession = Depends(get_db),
):
    """Count and total exposure of LIGHT subs with no target assigned."""
    if path:
        _validate_folder_path(path)
    light_clause = _light_subs_clause()
    stmt = select(
        func.count(Image.id),
        func.coalesce(func.sum(Image.exposure_time_seconds), 0),
    ).where(light_clause, Image.target_key.is_(None))
    path_clause = _path_clause(path)
    if path_clause is not None:
        stmt = stmt.where(path_clause)
    count, total_seconds = (await db.execute(stmt)).one()
    return {"count": count or 0, "total_seconds": float(total_seconds or 0)}


@router.get("/{target_key}")
async def get_target_detail(target_key: str, db: AsyncSession = Depends(get_db)):
    """Full detail for one target: filter x rig matrix, nightly timeline, masters, goals, catalog info."""
    items = await get_cached_targets_list(db)
    summary = next((t for t in items if t["target_key"] == target_key), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="Target not found or has no integration yet")

    light_clause = _light_subs_clause()

    # by_filter_rig - rig identified by camera + measured pixel scale (telescope
    # names are usually the mount driver); focal length derived from pixel size.
    header_pix = func.coalesce(
        Image.raw_header["XPIXSZ"].astext, Image.raw_header["PIXSIZE1"].astext
    )
    header_bin = Image.raw_header["XBINNING"].astext
    scale_bucket = func.round(func.cast(Image.pixel_scale_arcsec, Numeric), 2)
    rig_stmt = (
        select(
            Image.filter_name,
            Image.camera_name,
            scale_bucket.label("scale"),
            header_pix.label("pix"),
            func.coalesce(Image.binning, header_bin).label("bin"),
            func.count(Image.id).label("subs"),
            func.sum(Image.exposure_time_seconds).label("secs"),
        )
        .where(light_clause, Image.target_key == target_key)
        .group_by(Image.filter_name, Image.camera_name, scale_bucket, header_pix,
                  func.coalesce(Image.binning, header_bin))
    )
    rig_rows = (await db.execute(rig_stmt)).all()
    rig_buckets = []
    for r in rig_rows:
        # Header XPIXSZ is already the binned size; table sizes are unbinned.
        pix = parse_pixel_size(r.pix)
        if pix is None:
            known = known_pixel_size(r.camera_name)
            pix = known * binning_factor(r.bin) if known else None
        rig_buckets.append({
            "filter": normalize_filter(r.filter_name),
            "camera": r.camera_name,
            "pixel_scale": valid_pixel_scale(r.scale),
            "pixel_size_um": pix,
            "subs": r.subs or 0,
            "seconds": float(r.secs or 0),
        })
    by_filter_rig = sorted(
        build_filter_rig_rows(rig_buckets),
        key=lambda e: (filter_sort_key(e["filter"]), -e["seconds"]),
    )

    # nights_detail
    nights_stmt = text("""
        SELECT date(capture_date - interval '12 hours') as night, filter_name,
               sum(exposure_time_seconds) as secs
        FROM images
        WHERE target_key = :key AND frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
          AND capture_date IS NOT NULL
        GROUP BY night, filter_name
        ORDER BY night
    """)
    night_rows = (await db.execute(nights_stmt, {"key": target_key})).all()
    nights_map: Dict[Any, Dict[str, float]] = {}
    for r in night_rows:
        norm = normalize_filter(r.filter_name)
        bucket = nights_map.setdefault(r.night, {})
        bucket[norm] = bucket.get(norm, 0.0) + float(r.secs or 0)
    nights_detail = [
        {"night": night.isoformat() if hasattr(night, "isoformat") else str(night), "filters": filters}
        for night, filters in sorted(nights_map.items(), key=lambda kv: str(kv[0]))
    ]

    # masters
    masters_stmt = (
        select(Image)
        .where(
            Image.target_key == target_key,
            Image.frame_type == FrameType.LIGHT,
            Image.subtype == ImageSubtype.INTEGRATION_MASTER,
        )
        .order_by(Image.capture_date.desc())
    )
    master_rows = (await db.execute(masters_stmt)).scalars().all()
    masters = [
        {
            "id": m.id,
            "file_name": m.file_name,
            "thumbnail_path": m.thumbnail_path,
            "rating": m.rating,
            "capture_date": m.capture_date.isoformat() if m.capture_date else None,
            "filter_name": m.filter_name,
        }
        for m in master_rows
    ]

    # goals (with have_seconds progress)
    goal_rows = (await db.execute(select(TargetGoal).where(TargetGoal.target_key == target_key))).scalars().all()
    filter_seconds = {f["filter"]: f["seconds"] for f in summary["filters"]}
    goals = []
    for g in goal_rows:
        if g.filter_group == "ANY":
            have = summary["total_seconds"]
        else:
            have = filter_seconds.get(g.filter_group, 0.0)
        goals.append({"filter_group": g.filter_group, "goal_seconds": g.goal_seconds, "have_seconds": have})

    # catalog info
    catalog_meta = await _build_catalog_metadata_map(db)
    meta = catalog_meta.get(target_key)
    catalog_info = None
    if meta:
        catalog_info = {
            "catalog_type": meta["catalog_type"],
            "designation": meta["designation"],
            "common_name": meta.get("common_name"),
            "object_type": meta.get("object_type"),
            "constellation": meta.get("constellation"),
            "apparent_magnitude": meta.get("apparent_magnitude"),
            "ra_degrees": meta.get("ra_degrees"),
            "dec_degrees": meta.get("dec_degrees"),
        }

    return {
        **summary,
        "by_filter_rig": by_filter_rig,
        "nights_detail": nights_detail,
        "masters": masters,
        "goals": goals,
        "catalog": catalog_info,
    }


@router.put("/{target_key}/goals")
async def update_target_goals(
    target_key: str,
    goals: List[TargetGoalInput],
    db: AsyncSession = Depends(get_db),
):
    """Replace the goal set for a target. A goal_seconds <= 0 deletes that filter's goal."""
    await db.execute(delete(TargetGoal).where(TargetGoal.target_key == target_key))

    saved = []
    for g in goals:
        if g.goal_seconds and g.goal_seconds > 0:
            db.add(TargetGoal(target_key=target_key, filter_group=g.filter_group, goal_seconds=g.goal_seconds))
            saved.append({"filter_group": g.filter_group, "goal_seconds": g.goal_seconds})

    await db.commit()
    await _invalidate_targets_cache()

    return {"target_key": target_key, "goals": saved}
