"""
Equipment assignment task (R0, docs/design/P0-R0-equipment-sites.md §4.5).

`assign_equipment(scope)` assigns images.rig_id / site_id in batches of
1000, fills capture_date_utc for local-time rows at sites with a timezone,
refreshes rigs.measured_scale_arcsec / measured_count and invalidates the
targets caches. MANUAL rigs are never touched. Idempotent.

`queue_assign_equipment(scope)` is the debounced entry point used by the
Equipment API: at most one pending task at a time (Redis key), and a run
lock so two runs never overlap.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.worker import celery_app

logger = logging.getLogger(__name__)

TASK_NAME = "app.tasks.equipment.assign_equipment"
PENDING_KEY = "equipment:assign:pending"
RUN_LOCK_KEY = "equipment:assign:lock"
PENDING_TTL_SECONDS = 15 * 60
RUN_LOCK_TTL_SECONDS = 2 * 3600
DEBOUNCE_SECONDS = 5
BATCH_SIZE = 1000
SCOPES = ("unassigned", "all")


def _redis():
    from app.services.data_migrations import redis_client
    return redis_client()


def queue_assign_equipment(scope: str = "all", countdown: int = DEBOUNCE_SECONDS) -> Optional[str]:
    """
    Queue assign_equipment unless one is already pending (then only widen a
    pending "unassigned" run to "all"). Returns the new task id, or None.
    """
    scope = scope if scope in SCOPES else "all"
    try:
        r = _redis()
        if not r.set(PENDING_KEY, scope, nx=True, ex=PENDING_TTL_SECONDS):
            if scope == "all":
                r.set(PENDING_KEY, "all", xx=True, keepttl=True)
            return None
    except Exception as e:
        logger.warning(f"Equipment assign: debounce unavailable ({e}); queueing anyway")
    result = celery_app.send_task(TASK_NAME, kwargs={"scope": scope}, countdown=countdown)
    return result.id


def _pending_scope(r, scope: str) -> str:
    """Consume the pending marker; a widened marker ("all") wins."""
    try:
        pipe = r.pipeline()
        pipe.get(PENDING_KEY)
        pipe.delete(PENDING_KEY)
        pending = pipe.execute()[0]
    except Exception:
        pending = None
    if isinstance(pending, bytes):
        pending = pending.decode()
    return "all" if "all" in (scope, pending) else "unassigned"


@celery_app.task(bind=True, name=TASK_NAME, soft_time_limit=3 * 3600, time_limit=4 * 3600)
def assign_equipment(self, scope: str = "unassigned"):
    try:
        r = _redis()
        if not r.set(RUN_LOCK_KEY, "1", nx=True, ex=RUN_LOCK_TTL_SECONDS):
            # Another run is in progress: try again shortly (the pending marker stays).
            celery_app.send_task(TASK_NAME, kwargs={"scope": scope}, countdown=30)
            return {"status": "deferred"}
    except Exception as e:
        logger.warning(f"Equipment assign: lock unavailable ({e}); running unlocked")
        r = None

    try:
        scope = _pending_scope(r, scope) if r is not None else scope
        return run_assignment(scope)
    except Exception as e:
        logger.error(f"Equipment assignment failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        if r is not None:
            try:
                r.delete(RUN_LOCK_KEY)
            except Exception:
                pass


def run_assignment(scope: str = "unassigned") -> Dict[str, Any]:
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.services import equipment_assignment as ea

    summary: Dict[str, Any] = {"scope": scope, "rig_changed": 0, "site_changed": 0,
                               "utc_filled": 0, "reasons": defaultdict(int)}
    with SessionLocal() as session:
        rigs = ea.load_rig_infos_sync(session)
        sites = ea.load_site_infos_sync(session)
        summary["rigs"], summary["sites"] = len(rigs), len(sites)

        _assign_rigs_and_sites(session, rigs, sites, scope, summary)
        summary["default_site_filled"] = _fill_default_site(session, sites)
        clock_modes = _infer_clock_modes(session)
        summary["clock_modes"] = clock_modes
        ea.store_clock_modes(clock_modes)
        _fill_capture_utc(session, sites, clock_modes, scope, summary)
        summary["measured"] = _refresh_measured(session)
        session.commit()

    ea.invalidate_cache()
    _invalidate_targets_cache()
    summary["reasons"] = dict(summary["reasons"])
    summary["status"] = "completed"
    logger.info(f"Equipment assignment: {summary}")
    return summary


_ROWS_SQL = """
    SELECT id, camera_name, width_pixels, height_pixels, binning, pixel_scale_arcsec,
           raw_header->'XPIXSZ' AS xpixsz, raw_header->'FOCALLEN' AS focallen, focal_length,
           site_latitude, site_longitude, rig_id, rig_source, site_id
    FROM images
    WHERE id > :after {where}
    ORDER BY id
    LIMIT :limit
"""


def _assign_rigs_and_sites(session, rigs, sites, scope: str, summary: Dict[str, Any]) -> None:
    from sqlalchemy import text
    from app.services.equipment_assignment import assign_rig, assign_site
    from app.utils.header_values import valid_site

    where = ""
    if scope != "all":
        where = ("AND ((rig_id IS NULL AND COALESCE(rig_source, '') <> 'MANUAL') "
                 "OR (site_id IS NULL AND site_latitude IS NOT NULL))")
    stmt = text(_ROWS_SQL.format(where=where))
    after = 0
    while True:
        rows = session.execute(stmt, {"after": after, "limit": BATCH_SIZE}).mappings().all()
        if not rows:
            break
        after = rows[-1]["id"]
        grouped = defaultdict(list)
        for row in rows:
            updates = {}
            if row["rig_source"] != "MANUAL":
                rig_id, reason = assign_rig(row, rigs)
                summary["reasons"][reason] += 1
                source = "AUTO" if rig_id else None
                if rig_id != row["rig_id"] or source != row["rig_source"]:
                    updates["rig_id"], updates["rig_source"] = rig_id, source
                    summary["rig_changed"] += 1
            if valid_site(row["site_latitude"], row["site_longitude"]) is not None:
                site_id = assign_site(row["site_latitude"], row["site_longitude"], sites)
                if site_id != row["site_id"]:
                    updates["site_id"] = site_id
                    summary["site_changed"] += 1
            if updates:
                grouped[tuple(sorted(updates))].append({**updates, "id": row["id"]})
        for keys, params in grouped.items():
            sets = ", ".join(f"{k} = :{k}" for k in keys)
            session.execute(text(f"UPDATE images SET {sets} WHERE id = :id"), params)
        session.commit()


def _fill_default_site(session, sites) -> int:
    """
    Coordinate-less images get the default site only when their rig's images
    with coordinates are >= 90% at that site; otherwise their site is cleared.
    """
    from sqlalchemy import text
    from app.services.equipment_assignment import default_site_eligible_rigs

    no_coords = "(site_latitude IS NULL OR site_longitude IS NULL OR (site_latitude = 0 AND site_longitude = 0))"
    default = next((s for s in sites if s.is_default), None)
    eligible: List[int] = []
    if default is not None:
        stats = session.execute(text(f"""
            SELECT rig_id, site_id, count(*) FROM images
            WHERE rig_id IS NOT NULL AND NOT {no_coords}
            GROUP BY rig_id, site_id
        """)).all()
        eligible = default_site_eligible_rigs([tuple(r) for r in stats], default.id)

    if eligible:
        filled = session.execute(text(f"""
            UPDATE images SET site_id = :sid
            WHERE {no_coords} AND rig_id = ANY(:rigs) AND site_id IS DISTINCT FROM :sid
        """), {"sid": default.id, "rigs": eligible}).rowcount
        session.execute(text(f"""
            UPDATE images SET site_id = NULL
            WHERE {no_coords} AND site_id IS NOT NULL AND (rig_id IS NULL OR NOT (rig_id = ANY(:rigs)))
        """), {"rigs": eligible})
    else:
        filled = 0
        session.execute(text(f"UPDATE images SET site_id = NULL WHERE {no_coords} AND site_id IS NOT NULL"))
    session.commit()
    return filled or 0


def _infer_clock_modes(session) -> Dict[str, str]:
    """Per camera key: does its EXIF clock run on UTC? From GPS_UTC frames at a site."""
    from sqlalchemy import text
    from app.services.equipment_assignment import infer_clock_mode
    from app.services.equipment_detection import normalize_camera_name
    from app.utils.capture_time import parse_exif_datetime

    rows = session.execute(text("""
        SELECT i.camera_name,
               COALESCE(i.raw_header->'EXIF:EXIF DateTimeOriginal', i.raw_header->'PIL:DateTimeOriginal') AS wall,
               i.capture_date_utc AS gps, s.timezone AS tz
        FROM images i JOIN sites s ON s.id = i.site_id
        WHERE i.capture_time_source = 'GPS_UTC' AND i.capture_date_utc IS NOT NULL
    """)).all()
    pairs = defaultdict(list)
    for camera, wall, gps, tz in rows:
        key = normalize_camera_name(camera)
        if key:
            pairs[key].append((parse_exif_datetime(wall), gps, tz))
    modes = {}
    for key, items in pairs.items():
        mode = infer_clock_mode(items)
        if mode:
            modes[key] = mode
    return modes


def _fill_capture_utc(session, sites, clock_modes: Dict[str, str], scope: str, summary: Dict[str, Any]) -> None:
    """
    FITS_LOCAL / EXIF_LOCAL rows: capture_date_utc + capture_utc_basis.
    A UTC-clock camera -> CAMERA_UTC; else the row's site timezone (SITE_TZ);
    else, for a row with no site, the default site's timezone (DEFAULT_SITE_TZ,
    R1 §3.2). NULL when none applies.
    """
    from sqlalchemy import text
    from app.services.equipment_assignment import capture_utc_with_basis, local_capture_time
    from app.services.equipment_detection import normalize_camera_name

    tz_by_site = {s.id: s.timezone for s in sites}
    default = next((s for s in sites if s.is_default), None)
    default_tz = default.timezone if default is not None else None
    by_basis: Dict[str, int] = defaultdict(int)
    stmt = text("""
        SELECT id, camera_name, capture_date, capture_date_utc, capture_utc_basis, capture_time_source, site_id,
               raw_header->'DATE-LOC' AS date_loc,
               COALESCE(raw_header->'EXIF:EXIF DateTimeOriginal', raw_header->'PIL:DateTimeOriginal') AS exif_original,
               COALESCE(raw_header->'EXIF:EXIF OffsetTimeOriginal', raw_header->'PIL:OffsetTimeOriginal') AS offset_value
        FROM images
        WHERE id > :after AND capture_time_source IN ('FITS_LOCAL', 'EXIF_LOCAL')
        ORDER BY id
        LIMIT :limit
    """)
    after = 0
    while True:
        rows = session.execute(stmt, {"after": after, "limit": BATCH_SIZE}).mappings().all()
        if not rows:
            break
        after = rows[-1]["id"]
        params = []
        for row in rows:
            local = local_capture_time(row["capture_time_source"], row["capture_date"],
                                       row["date_loc"], row["exif_original"])
            clock = clock_modes.get(normalize_camera_name(row["camera_name"]) or "")
            site_id = row["site_id"]
            utc, basis = capture_utc_with_basis(
                row["capture_time_source"], local, tz_by_site.get(site_id) if site_id is not None else None,
                default_tz=default_tz if site_id is None else None,
                clock_mode=clock, offset_value=row["offset_value"])
            if basis:
                by_basis[basis] += 1
            if utc != row["capture_date_utc"] or basis != row.get("capture_utc_basis"):
                params.append({"utc": utc, "basis": basis, "id": row["id"]})
                if utc is not None and utc != row["capture_date_utc"]:
                    summary["utc_filled"] += 1
        if params:
            session.execute(text("UPDATE images SET capture_date_utc = :utc, capture_utc_basis = :basis "
                                 "WHERE id = :id"), params)
        session.commit()
    summary["utc_by_basis"] = dict(by_basis)


def _refresh_measured(session) -> int:
    """
    rigs.measured_scale_arcsec / measured_count = median solved scale of
    assigned light subs, each rescaled to rig.binning via its binning
    relative to rig's camera (Python pass: a plain SQL median would mix bin 1
    and bin 2 frames of the same rig into one meaningless number).
    """
    import statistics
    from sqlalchemy import text
    from app.services.equipment_assignment import load_rig_infos_sync, measured_scale_normalized

    rigs = {r.id: r for r in load_rig_infos_sync(session)}
    rows = session.execute(text("""
        SELECT rig_id, width_pixels, height_pixels, binning, pixel_scale_arcsec,
               raw_header->'XPIXSZ' AS xpixsz
        FROM images
        WHERE rig_id IS NOT NULL AND frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
          AND pixel_scale_arcsec BETWEEN 0.05 AND 300 AND abs(pixel_scale_arcsec - 72.0) >= 0.001
    """)).mappings().all()

    by_rig: Dict[int, List[float]] = defaultdict(list)
    for row in rows:
        rig = rigs.get(row["rig_id"])
        if rig is None:
            continue
        norm = measured_scale_normalized(dict(row), rig)
        if norm is not None:
            by_rig[rig.id].append(norm)

    for rig_id in rigs:
        scales = by_rig.get(rig_id)
        if scales:
            session.execute(text("UPDATE rigs SET measured_scale_arcsec = :m, measured_count = :c WHERE id = :id"),
                            {"m": statistics.median(scales), "c": len(scales), "id": rig_id})
        else:
            session.execute(text("UPDATE rigs SET measured_scale_arcsec = NULL, measured_count = 0 WHERE id = :id"),
                            {"id": rig_id})
    session.commit()
    return sum(1 for scales in by_rig.values() if scales)


def _invalidate_targets_cache() -> None:
    """Drop every cache:targets:* key (the targets list and its per-folder variants) and recs:* (R1)."""
    try:
        r = _redis()
        for pattern in ("cache:targets:*", "recs:*"):
            keys = list(r.scan_iter(match=pattern))
            if keys:
                r.delete(*keys)
    except Exception as e:
        logger.warning(f"Equipment assign: targets cache invalidation failed: {e}")
