"""
Rig / site assignment and timezone fill (R0, docs/design/P0-R0-equipment-sites.md §4.5).

Pure core:
- `assign_rig(image, rigs)` -> (rig_id | None, reason). Never guesses
  between two rigs.
- `assign_site(lat, lon, sites)` -> nearest site within 10 km, else None.
- `default_site_eligible_rigs(stats, default_site_id)`: rigs whose images
  with coordinates are >= 90% at the default site (coordinate-less images of
  those rigs get the default site).
- `local_to_utc(local, tz)` / `fill_capture_utc(...)`: capture_date_utc for
  FITS_LOCAL / EXIF_LOCAL rows once a site (with timezone) is known.
- `infer_clock_mode(pairs)`: does a camera's EXIF clock run on UTC? Decided
  from frames that carry both GPS time and DateTimeOriginal, on dates when
  the site's zone differs from UTC. Unknown -> the site timezone is used.

`assign_equipment_sync(session, image)` is the indexer hook (cached rigs and
sites, 60 s TTL); it never raises. The batch task is app/tasks/equipment.py.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.utils.capture_time import (
    EXIF_LOCAL, FITS_LOCAL, LOCAL_SOURCES, parse_exif_datetime, parse_fits_datetime, parse_offset, unchar,
)
from app.utils.header_values import valid_site
from app.utils.optics import effective_focal_mm, pixel_scale
from app.utils.rig_optics import binning_factor, dims_match, relative_binning, valid_pixel_scale

logger = logging.getLogger(__name__)

SCALE_TOLERANCE = 0.07
FOCAL_TOLERANCE = 0.05
SITE_MAX_KM = 10.0
DEFAULT_SITE_SHARE = 0.90
CACHE_TTL_SECONDS = 60

CLOCK_UTC = "UTC"
CLOCK_LOCAL = "LOCAL"
_CLOCK_MATCH = timedelta(minutes=10)
_CLOCK_MIN_VOTES = 3
_CLOCK_MAJORITY = 0.8


@dataclass
class RigInfo:
    id: int
    camera_id: int
    patterns: List[str]
    sensor_width_px: Optional[int] = None
    sensor_height_px: Optional[int] = None
    pixel_size_um: Optional[float] = None
    focal_length_mm: Optional[float] = None
    modifier_factor: float = 1.0
    binning: int = 1
    is_active: bool = True
    measured_scale_arcsec: Optional[float] = None

    @property
    def effective_focal(self) -> Optional[float]:
        return effective_focal_mm(self.focal_length_mm, self.modifier_factor)

    def declared_scale(self, binning: Optional[int] = None) -> Optional[float]:
        return pixel_scale(self.pixel_size_um, self.focal_length_mm, binning or self.binning, self.modifier_factor)


@dataclass
class SiteInfo:
    id: int
    latitude: float
    longitude: float
    timezone: str
    is_default: bool = False


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _num(value: Any) -> Optional[float]:
    value = unchar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------

def camera_matches(camera_name: Any, patterns: Sequence[str]) -> bool:
    from app.services.equipment_detection import normalize_camera_name

    if not patterns:
        return False
    raw = unchar(camera_name)
    if raw is None:
        return False
    key = normalize_camera_name(raw) or ""
    low = str(raw).lower()
    return any(p and (p.lower() in key or p.lower() in low) for p in patterns)


def _dims_fit(image: dict, rig: RigInfo) -> Optional[int]:
    """
    The image's binning relative to rig's camera (see relative_binning), or
    None when its known dimensions rule the rig out. A rig with no known
    sensor dims has nothing to check against, so it fits at rig.binning.
    """
    if not (rig.sensor_width_px and rig.sensor_height_px):
        return rig.binning
    w, h = image.get("width_pixels"), image.get("height_pixels")
    if not (w and h):
        return rig.binning
    return relative_binning(w, h, image.get("xpixsz"), image.get("binning"),
                            rig.sensor_width_px, rig.sensor_height_px, rig.pixel_size_um)


def measured_scale_normalized(image: Dict[str, Any], rig: RigInfo) -> Optional[float]:
    """
    A solved light sub's pixel scale, rescaled to rig.binning via its binning
    relative to rig's camera. Used to keep rigs.measured_scale_arcsec on one
    basis even when its assigned frames were captured at different binnings.
    """
    scale = valid_pixel_scale(image.get("pixel_scale_arcsec"))
    if scale is None:
        return None
    rel_bin = _dims_fit(image, rig)
    if not rel_bin:
        return None
    return scale * rig.binning / rel_bin


def assign_rig(image: Dict[str, Any], rigs: Iterable[RigInfo]) -> Tuple[Optional[int], str]:
    """
    image: {camera_name, width_pixels, height_pixels, binning, pixel_scale_arcsec, xpixsz,
            focallen (FOCALLEN header), focal_length (EXIF column)}.
    """
    fits: List[Tuple[RigInfo, int]] = []
    for r in rigs:
        if not camera_matches(image.get("camera_name"), r.patterns):
            continue
        rel_bin = _dims_fit(image, r)
        if rel_bin is not None:
            fits.append((r, rel_bin))
    if not fits:
        return (None, "no_camera_match")

    scale = valid_pixel_scale(image.get("pixel_scale_arcsec"))
    if scale is not None:
        best = None
        for rig, rel_bin in fits:
            ref = rig.declared_scale(rel_bin)
            if not ref and rig.measured_scale_arcsec:
                ref = rig.measured_scale_arcsec * rel_bin / rig.binning
            if not ref:
                continue
            err = abs(ref - scale) / ref
            if err <= SCALE_TOLERANCE and (best is None or err < best[0]):
                best = (err, rig)
        return (best[1].id, "scale_match") if best else (None, "scale_mismatch")

    candidates = [r for r, _ in fits]
    focal = _num(image.get("focallen")) or _num(image.get("focal_length"))
    if focal:
        hits = [r for r in candidates
                if r.effective_focal and abs(r.effective_focal - focal) / r.effective_focal <= FOCAL_TOLERANCE]
        if len(hits) == 1:
            return (hits[0].id, "focallen_match")

    active = [r for r in candidates if r.is_active]
    if len(active) == 1:
        return (active[0].id, "single_rig_for_camera")
    return (None, "ambiguous" if len(candidates) > 1 else "no_active_rig")


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------

def assign_site(lat: Any, lon: Any, sites: Iterable[SiteInfo], max_km: float = SITE_MAX_KM) -> Optional[int]:
    coords = valid_site(lat, lon)
    if coords is None:
        return None
    best = None
    for s in sites:
        d = haversine_km(coords[0], coords[1], s.latitude, s.longitude)
        if d <= max_km and (best is None or d < best[0]):
            best = (d, s.id)
    return best[1] if best else None


def default_site_eligible_rigs(stats: Iterable[Tuple[int, Optional[int], int]], default_site_id: Optional[int],
                               share: float = DEFAULT_SITE_SHARE) -> List[int]:
    """
    stats: (rig_id, site_id, count) for images WITH coordinates. A rig is
    eligible when >= share of those images are at the default site.
    """
    if default_site_id is None:
        return []
    totals: Dict[int, int] = {}
    at_default: Dict[int, int] = {}
    for rig_id, site_id, count in stats:
        totals[rig_id] = totals.get(rig_id, 0) + int(count or 0)
        if site_id == default_site_id:
            at_default[rig_id] = at_default.get(rig_id, 0) + int(count or 0)
    return sorted(r for r, t in totals.items() if t and at_default.get(r, 0) / t >= share)


# ---------------------------------------------------------------------------
# Timezone fill
# ---------------------------------------------------------------------------

def local_to_utc(local: Optional[datetime], tz_name: Optional[str]) -> Optional[datetime]:
    """Naive local wall time in tz_name -> naive UTC (DST-aware; ambiguous times take the first)."""
    if local is None or not tz_name:
        return None
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    aware = local.replace(tzinfo=tz, fold=0)
    return aware.astimezone(dt_timezone.utc).replace(tzinfo=None)


def utc_offset(when_utc: datetime, tz_name: str) -> Optional[timedelta]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return when_utc.replace(tzinfo=dt_timezone.utc).astimezone(tz).utcoffset()


def local_capture_time(source: Optional[str], capture_date: Optional[datetime],
                       date_loc: Any = None, exif_original: Any = None) -> Optional[datetime]:
    """The camera-local wall time of a FITS_LOCAL / EXIF_LOCAL row (header first, then capture_date)."""
    if source == FITS_LOCAL:
        return parse_fits_datetime(date_loc)
    if source == EXIF_LOCAL:
        return parse_exif_datetime(exif_original) or capture_date
    return None


def fill_capture_utc(source: Optional[str], local: Optional[datetime], tz_name: Optional[str],
                     clock_mode: Optional[str] = None, offset_value: Any = None) -> Optional[datetime]:
    """
    capture_date_utc for a local-time row, or None when the row must not be
    touched (source already UTC or offset-bearing) / cannot be derived.
    clock_mode UTC: the camera clock runs on UTC, so the wall time is UTC.
    """
    if source not in LOCAL_SOURCES or local is None:
        return None
    if parse_offset(offset_value) is not None:
        return None  # carries an offset: never reinterpret it with the site zone
    if clock_mode == CLOCK_UTC:
        return local
    return local_to_utc(local, tz_name)


# images.capture_utc_basis (R1 §3.2): how a local-time row's UTC was derived.
BASIS_SITE_TZ = "SITE_TZ"
BASIS_DEFAULT_SITE_TZ = "DEFAULT_SITE_TZ"
BASIS_CAMERA_UTC = "CAMERA_UTC"


def capture_utc_with_basis(source: Optional[str], local: Optional[datetime], site_tz: Optional[str],
                           default_tz: Optional[str] = None, clock_mode: Optional[str] = None,
                           offset_value: Any = None) -> Tuple[Optional[datetime], Optional[str]]:
    """
    (capture_date_utc, capture_utc_basis) for a FITS_LOCAL / EXIF_LOCAL row.

    site_tz is the timezone of the row's own site (None when it has no site);
    default_tz is the default site's timezone, used only for site-less rows.
    Order: a camera whose clock runs on UTC (CAMERA_UTC), else the row's site
    zone (SITE_TZ), else, without a site, the default site's zone
    (DEFAULT_SITE_TZ). (None, None) when nothing applies.
    """
    if source not in LOCAL_SOURCES or local is None:
        return None, None
    if parse_offset(offset_value) is not None:
        return None, None  # carries an offset: never reinterpret it
    if clock_mode == CLOCK_UTC:
        return local, BASIS_CAMERA_UTC
    if site_tz:
        utc = local_to_utc(local, site_tz)
        return utc, (BASIS_SITE_TZ if utc is not None else None)
    if default_tz:
        utc = local_to_utc(local, default_tz)
        return utc, (BASIS_DEFAULT_SITE_TZ if utc is not None else None)
    return None, None


def infer_clock_mode(pairs: Iterable[Tuple[datetime, datetime, str]]) -> Optional[str]:
    """
    pairs: (exif_wall_time, gps_utc, site_tz). Votes only count when the
    zone's offset at that moment is non-zero (winter London can't tell).
    Returns CLOCK_UTC / CLOCK_LOCAL with >= 3 votes and an 80% majority, else None.
    """
    utc_votes = local_votes = 0
    for wall, gps, tz_name in pairs:
        if wall is None or gps is None or not tz_name:
            continue
        offset = utc_offset(gps, tz_name)
        if not offset:
            continue
        diff = wall - gps
        if abs(diff) <= _CLOCK_MATCH:
            utc_votes += 1
        elif abs(diff - offset) <= _CLOCK_MATCH:
            local_votes += 1
    total = utc_votes + local_votes
    if total < _CLOCK_MIN_VOTES:
        return None
    if utc_votes / total >= _CLOCK_MAJORITY:
        return CLOCK_UTC
    if local_votes / total >= _CLOCK_MAJORITY:
        return CLOCK_LOCAL
    return None


# ---------------------------------------------------------------------------
# Loading (sync) + indexer hook
# ---------------------------------------------------------------------------

def load_rig_infos_sync(session) -> List[RigInfo]:
    from sqlalchemy import select
    from app.models.equipment import Camera, Optic, Rig

    rows = session.execute(
        select(Rig.id, Rig.camera_id, Rig.modifier_factor, Rig.binning, Rig.is_active, Rig.measured_scale_arcsec,
               Camera.match_patterns, Camera.name, Camera.sensor_width_px, Camera.sensor_height_px,
               Camera.pixel_size_um, Optic.focal_length_mm)
        .join(Camera, Camera.id == Rig.camera_id)
        .join(Optic, Optic.id == Rig.optic_id)
    ).all()
    return [rig_info_from_row(r) for r in rows]


def rig_info_from_row(r) -> RigInfo:
    from app.services.equipment_detection import normalize_camera_name

    patterns = [str(p).lower() for p in (r.match_patterns or []) if p]
    if not patterns:
        key = normalize_camera_name(r.name)
        patterns = [key] if key else []
    return RigInfo(
        id=r.id, camera_id=r.camera_id, patterns=patterns,
        sensor_width_px=r.sensor_width_px, sensor_height_px=r.sensor_height_px,
        pixel_size_um=r.pixel_size_um, focal_length_mm=r.focal_length_mm,
        modifier_factor=r.modifier_factor or 1.0, binning=r.binning or 1,
        is_active=bool(r.is_active), measured_scale_arcsec=r.measured_scale_arcsec,
    )


def load_site_infos_sync(session) -> List[SiteInfo]:
    from sqlalchemy import select
    from app.models.equipment import Site

    rows = session.execute(select(Site.id, Site.latitude, Site.longitude, Site.timezone, Site.is_default)).all()
    return [SiteInfo(id=r.id, latitude=r.latitude, longitude=r.longitude, timezone=r.timezone,
                     is_default=bool(r.is_default)) for r in rows]


_cache: Dict[str, Any] = {"loaded_at": 0.0, "rigs": [], "sites": [], "clock_modes": {}}

# Written by the batch task (camera key -> CLOCK_UTC/CLOCK_LOCAL) so the
# indexer hook applies the same clock interpretation to new files.
CLOCK_MODES_KEY = "equipment:clock_modes"


def load_clock_modes() -> Dict[str, str]:
    try:
        import json
        from app.services.data_migrations import redis_client
        raw = redis_client().get(CLOCK_MODES_KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def store_clock_modes(modes: Dict[str, str]) -> None:
    try:
        import json
        from app.services.data_migrations import redis_client
        redis_client().set(CLOCK_MODES_KEY, json.dumps(modes))
    except Exception as e:
        logger.warning(f"Could not store camera clock modes: {e}")


def invalidate_cache() -> None:
    _cache["loaded_at"] = 0.0


def _cached_equipment(session) -> Tuple[List[RigInfo], List[SiteInfo]]:
    if time.time() - _cache["loaded_at"] > CACHE_TTL_SECONDS:
        with session.begin_nested():
            _cache["rigs"] = load_rig_infos_sync(session)
            _cache["sites"] = load_site_infos_sync(session)
        _cache["clock_modes"] = load_clock_modes()
        _cache["loaded_at"] = time.time()
    return _cache["rigs"], _cache["sites"]


def image_to_dict(image) -> Dict[str, Any]:
    raw = image.raw_header if isinstance(getattr(image, "raw_header", None), dict) else {}
    return {
        "camera_name": image.camera_name,
        "width_pixels": image.width_pixels,
        "height_pixels": image.height_pixels,
        "binning": image.binning,
        "pixel_scale_arcsec": image.pixel_scale_arcsec,
        "xpixsz": raw.get("XPIXSZ"),
        "focallen": raw.get("FOCALLEN"),
        "focal_length": getattr(image, "focal_length", None),
    }


def apply_equipment(image, rigs: List[RigInfo], sites: List[SiteInfo],
                    clock_modes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Set rig_id/rig_source, site_id and (for local-time rows) capture_date_utc
    on an ORM image in place. MANUAL rigs are untouched. A coordinate-less
    image keeps its current site. Returns {"rig_reason": ...}.
    """
    reason = "manual"
    if image.rig_source != "MANUAL":
        rig_id, reason = assign_rig(image_to_dict(image), rigs)
        image.rig_id = rig_id
        image.rig_source = "AUTO" if rig_id else None

    site_id = assign_site(image.site_latitude, image.site_longitude, sites)
    if site_id is not None or valid_site(image.site_latitude, image.site_longitude) is not None:
        image.site_id = site_id

    if image.capture_time_source in LOCAL_SOURCES:
        site = next((s for s in sites if s.id == image.site_id), None)
        default = next((s for s in sites if s.is_default), None)
        raw = image.raw_header if isinstance(image.raw_header, dict) else {}
        local = local_capture_time(image.capture_time_source, image.capture_date, raw.get("DATE-LOC"),
                                   raw.get("EXIF:EXIF DateTimeOriginal") or raw.get("PIL:DateTimeOriginal"))
        from app.services.equipment_detection import normalize_camera_name
        clock = (clock_modes or {}).get(normalize_camera_name(image.camera_name) or "")
        utc, basis = capture_utc_with_basis(
            image.capture_time_source, local, site.timezone if site else None,
            default_tz=default.timezone if (default is not None and image.site_id is None) else None,
            clock_mode=clock,
            offset_value=raw.get("EXIF:EXIF OffsetTimeOriginal") or raw.get("PIL:OffsetTimeOriginal"))
        image.capture_date_utc = utc
        image.capture_utc_basis = basis
    return {"rig_reason": reason}


def assign_equipment_sync(session, image) -> bool:
    """
    Indexer hook (after assign_target_sync). Uses cached rigs/sites. Never
    raises: equipment assignment must not break indexing. Returns True on success.
    Camera clock modes (UTC-clock DSLRs) come from the last batch run.
    """
    try:
        rigs, sites = _cached_equipment(session)
        if not rigs and not sites:
            return True
        apply_equipment(image, rigs, sites, _cache.get("clock_modes"))
        return True
    except Exception as e:
        logger.warning(f"Equipment assignment skipped for image {getattr(image, 'id', None)}: {e}")
        return False
