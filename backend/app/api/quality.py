"""
Star quality over time (Q1c, docs/design/Q1-star-quality.md §7.4).

GET /api/quality/nights    observing nights with Light subs (for the picker)
GET /api/quality/timeline  one night's subs with FWHM/HFR, altitude, events, flags
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.equipment import Rig, Site
from app.services.session_quality import build_timeline
from app.utils.filter_names import normalize_filter
from app.utils.observing_night import NIGHT_JOIN_SQL, NIGHT_SQL
from app.utils.star_metric_hints import extract_hints
from app.utils.star_quality import SCALE_SQL

router = APIRouter()

LIGHT_SUBS_SQL = "images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'"
# Header keywords the timeline needs (focuser, pier side, guiding); only these
# are pulled out of raw_header so a busy night stays a small query.
_HEADER_KEYS = ["FOCPOS", "FOCUSPOS", "FOCPOSN", "FOC-POS", "FOCTEMP", "FOCUSTEM", "FOCUSTMP", "FOC-TEMP",
                "PIERSIDE", "GUIDERMS", "GUIDE_RMS", "RMSTOTAL", "AIRMASS"]


def _filters_sql(target_key: Optional[str], rig_id: Optional[int], site_id: Optional[int]) -> str:
    parts = []
    if target_key:
        parts.append("images.target_key = :target_key")
    if rig_id is not None:
        parts.append("images.rig_id = :rig_id")
    if site_id is not None:
        parts.append("images.site_id = :site_id")
    return "".join(f" AND {p}" for p in parts)


async def _rig_names(db: AsyncSession, ids) -> Dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict((await db.execute(select(Rig.id, Rig.name).where(Rig.id.in_(ids)))).all())


@router.get("/nights")
async def list_nights(
    target_key: Optional[str] = Query(None),
    rig_id: Optional[int] = Query(None),
    site_id: Optional[int] = Query(None),
    limit: int = Query(400, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Observing nights with Light subs, newest first; one entry per night with a per-rig breakdown."""
    params = {"target_key": target_key, "rig_id": rig_id, "site_id": site_id, "limit": limit}
    rows = (await db.execute(text(f"""
        WITH n AS (
            SELECT {NIGHT_SQL} AS night, images.rig_id, images.target_key,
                   images.star_metrics_status AS status, images.fwhm_px, images.hfr_px,
                   {SCALE_SQL} AS scale
            FROM images {NIGHT_JOIN_SQL} LEFT JOIN rigs r ON r.id = images.rig_id
            WHERE {LIGHT_SUBS_SQL} AND images.capture_date IS NOT NULL
            {_filters_sql(target_key, rig_id, site_id)}
        ), recent AS (
            SELECT night FROM n GROUP BY night ORDER BY night DESC LIMIT :limit
        )
        SELECT n.night, n.rig_id, count(*) AS subs,
               count(*) FILTER (WHERE status = 'OK') AS measured,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fwhm_px) FILTER (WHERE status = 'OK') AS fwhm_px,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fwhm_px * scale) FILTER (WHERE status = 'OK') AS fwhm_arcsec,
               array_agg(DISTINCT target_key) FILTER (WHERE target_key IS NOT NULL) AS targets
        FROM n JOIN recent USING (night)
        GROUP BY n.night, n.rig_id
        ORDER BY n.night DESC, subs DESC
    """), params)).all()

    names = await _rig_names(db, [r.rig_id for r in rows])
    nights: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.night.isoformat() if hasattr(r.night, "isoformat") else str(r.night)
        entry = nights.setdefault(key, {"night": key, "subs": 0, "measured": 0, "targets": set(), "rigs": []})
        entry["subs"] += r.subs
        entry["measured"] += r.measured
        entry["targets"].update(r.targets or [])
        entry["rigs"].append({
            "rig_id": r.rig_id, "rig_name": names.get(r.rig_id), "subs": r.subs, "measured": r.measured,
            "median_fwhm_px": round(r.fwhm_px, 3) if r.fwhm_px is not None else None,
            "median_fwhm_arcsec": round(r.fwhm_arcsec, 3) if r.fwhm_arcsec is not None else None,
        })
    out = []
    for entry in nights.values():
        entry["targets"] = sorted(entry["targets"])
        out.append(entry)
    return {"nights": out}


@router.get("/timeline")
async def night_timeline(
    night: date = Query(..., description="Observing night (local date the night began), YYYY-MM-DD"),
    target_key: Optional[str] = Query(None),
    rig_id: Optional[int] = Query(None),
    site_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """One night's Light subs in time order with star quality, altitude, events, flags and summaries."""
    params = {
        "night": night, "target_key": target_key, "rig_id": rig_id, "site_id": site_id,
        "lo": datetime.combine(night, datetime.min.time()) - timedelta(days=1),
        "hi": datetime.combine(night, datetime.min.time()) + timedelta(days=3),
        "hint_keys": _HEADER_KEYS,
    }
    rows = (await db.execute(text(f"""
        SELECT images.id, images.capture_date_utc, images.capture_date, images.exposure_time_seconds,
               images.filter_name, images.target_key, images.rig_id, images.site_id,
               images.ra_center_degrees, images.dec_center_degrees, images.rotation_degrees,
               images.site_latitude, images.site_longitude, images.file_name,
               images.star_metrics_status, images.fwhm_px, images.hfr_px, images.eccentricity,
               images.star_count, (images.star_metrics->>'bkg_adu')::float AS bkg,
               {SCALE_SQL} AS scale,
               s.latitude AS s_lat, s.longitude AS s_lon, s.timezone AS s_tz, s.name AS s_name,
               (SELECT jsonb_object_agg(key, value) FROM jsonb_each(images.raw_header)
                 WHERE key = ANY(:hint_keys)) AS hdr
        FROM images {NIGHT_JOIN_SQL} LEFT JOIN rigs r ON r.id = images.rig_id
        WHERE {LIGHT_SUBS_SQL} AND images.capture_date BETWEEN :lo AND :hi
          AND {NIGHT_SQL} = :night
          {_filters_sql(target_key, rig_id, site_id)}
        ORDER BY coalesce(images.capture_date_utc, images.capture_date), images.id
    """), params)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No Light subs on that night")

    default_site = (await db.execute(select(Site).where(Site.is_default.is_(True)))).scalars().first()
    points: List[Dict[str, Any]] = []
    for r in rows:
        lat = r.s_lat if r.s_lat is not None else r.site_latitude
        lon = r.s_lon if r.s_lon is not None else r.site_longitude
        if (lat is None or lon is None) and default_site is not None:
            lat, lon = default_site.latitude, default_site.longitude
        points.append({
            "id": r.id,
            # Without a UTC time the camera-local time stands in (flagged for the UI).
            "t": r.capture_date_utc or r.capture_date,
            "time_is_utc": r.capture_date_utc is not None,
            "exposure_s": r.exposure_time_seconds,
            "filter": normalize_filter(r.filter_name),
            "target_key": r.target_key,
            "rig_id": r.rig_id,
            "ra": r.ra_center_degrees, "dec": r.dec_center_degrees,
            "rotation": r.rotation_degrees,
            "lat": lat, "lon": lon,
            "status": r.star_metrics_status,
            "fwhm_px": r.fwhm_px, "hfr_px": r.hfr_px, "ecc": r.eccentricity, "stars": r.star_count,
            "bkg": r.bkg, "scale": r.scale,
            "hints": extract_hints(r.hdr or {}, r.file_name),
        })

    timeline = build_timeline(points, night.isoformat())

    # Display timezone: the (most used) assigned site's, else the default site's, else UTC.
    tz_counts: Dict[str, int] = {}
    site_names: Dict[str, str] = {}
    for r in rows:
        if r.s_tz:
            tz_counts[r.s_tz] = tz_counts.get(r.s_tz, 0) + 1
            site_names[r.s_tz] = r.s_name
    tz = max(tz_counts, key=tz_counts.get) if tz_counts else (default_site.timezone if default_site else "UTC")
    timeline["site"] = {"timezone": tz, "name": site_names.get(tz) or (default_site.name if default_site else None)}
    names = await _rig_names(db, [p["rig_id"] for p in timeline["points"]])
    timeline["rigs"] = [{"rig_id": i, "rig_name": names.get(i)} for i in sorted({p["rig_id"] for p in timeline["points"]}, key=lambda v: (v is None, v))]
    timeline["targets"] = sorted({p["target_key"] for p in timeline["points"] if p["target_key"]})
    return timeline
