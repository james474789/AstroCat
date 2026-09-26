"""
Targets API (F2)
Aggregated per-target integration dashboard: list, unassigned summary, detail, goals.

See docs/design/F2-target-integration.md §3.5/3.7.
"""

import json
import math
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text, delete
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.database import get_db
from app.models.image import Image, ImageSubtype, FrameType
from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog, NamedStarCatalog, Sh2Catalog
from app.models.target import TargetGoal
from app.schemas.target import TargetGoalInput
from app.services.targets import normalize_designation
from app.utils.filter_names import normalize_filter, filter_sort_key
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY_LIST = "cache:targets:list"
CACHE_TTL_LIST = 120


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
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(CACHE_KEY_LIST)
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

async def _compute_targets_list(db: AsyncSession) -> List[dict]:
    """
    Build the full folded targets list (§3.5): one row per (target_key, raw
    filter_name), folded into per-target summaries with a per-filter
    breakdown. This is the function cached under cache:targets:list.
    """
    light_clause = _light_subs_clause()

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
    nights_stmt = text("""
        SELECT target_key, count(distinct date(capture_date - interval '12 hours')) as nights
        FROM images
        WHERE target_key = ANY(:keys) AND frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
        GROUP BY target_key
    """)
    for r in (await db.execute(nights_stmt, {"keys": keys})).all():
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
    for r in (await db.execute(planetary_stmt)).all():
        targets[r.target_key]["planetary_count"] = r.cnt

    # Cover image: highest-rated master, else most recent master, else most recent light w/ thumbnail
    cover_stmt = text("""
        SELECT DISTINCT ON (target_key) target_key, id
        FROM images
        WHERE target_key = ANY(:keys)
          AND frame_type = 'LIGHT'
          AND thumbnail_path IS NOT NULL
        ORDER BY target_key,
          (subtype = 'INTEGRATION_MASTER') DESC,
          CASE WHEN subtype = 'INTEGRATION_MASTER' THEN COALESCE(rating, -1) ELSE -1 END DESC,
          capture_date DESC NULLS LAST
    """)
    for r in (await db.execute(cover_stmt, {"keys": keys})).all():
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
            display_name = f"{key} — {meta['common_name']}" if meta.get("common_name") else key
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


async def get_cached_targets_list(db: AsyncSession) -> List[dict]:
    """Read-through Redis cache around _compute_targets_list (§3.5, TTL 120s)."""
    r = await _get_redis()
    if r:
        try:
            cached = await r.get(CACHE_KEY_LIST)
            if cached:
                await r.close()
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Targets cache read failed: {e}")

    result = await _compute_targets_list(db)

    if r:
        try:
            await r.setex(CACHE_KEY_LIST, CACHE_TTL_LIST, json.dumps(result))
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
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=10000),
    keys_only: bool = Query(False, description="Return only target_key values (lightweight)"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, filterable/sortable list of targets (§3.7)."""
    items = await get_cached_targets_list(db)

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
async def unassigned_summary(db: AsyncSession = Depends(get_db)):
    """Count and total exposure of LIGHT subs with no target assigned."""
    light_clause = _light_subs_clause()
    stmt = select(
        func.count(Image.id),
        func.coalesce(func.sum(Image.exposure_time_seconds), 0),
    ).where(light_clause, Image.target_key.is_(None))
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

    # by_filter_rig
    rig_stmt = (
        select(
            Image.filter_name,
            Image.camera_name,
            Image.telescope_name,
            func.count(Image.id).label("subs"),
            func.sum(Image.exposure_time_seconds).label("secs"),
        )
        .where(light_clause, Image.target_key == target_key)
        .group_by(Image.filter_name, Image.camera_name, Image.telescope_name)
    )
    rig_rows = (await db.execute(rig_stmt)).all()
    by_filter_rig_map: Dict[tuple, dict] = {}
    for r in rig_rows:
        norm = normalize_filter(r.filter_name)
        k = (norm, r.camera_name, r.telescope_name)
        entry = by_filter_rig_map.setdefault(k, {
            "filter": norm, "camera": r.camera_name, "telescope": r.telescope_name,
            "subs": 0, "seconds": 0.0,
        })
        entry["subs"] += r.subs or 0
        entry["seconds"] += float(r.secs or 0)
    by_filter_rig = sorted(by_filter_rig_map.values(), key=lambda e: filter_sort_key(e["filter"]))

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
