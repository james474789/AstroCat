"""
Find & allocate images with no rig (R0b, docs/design/R0b-rig-allocation.md).

Pure core (no session, no I/O), same style as equipment_assignment. Rig-less
light subs and masters are bucketed by what physically identifies a rig:

    camera  x  derived binning  x  calculated focal length (5% clusters)

- Derived binning is relative to the camera's native sensor (dims, then
  XPIXSZ, see relative_binning); header binning is only a fallback, since
  drivers disagree on it for the same sensor mode.
- Focal length is 206.265 * effective pixel size / solved scale; unsolved
  frames fall back to FOCALLEN / the EXIF focal length. Frames with neither
  form one "unknown focal" bucket per camera and binning.

- `group_unassigned(rows, rigs)`: bucket summaries for the Equipment page.
- `members_by_key(rows, rigs, keys)`: the ids of those buckets, re-derived
  from their keys so the assign POST never trusts client-sent ids.
- `suggest_rig(bucket, rigs)`: an exact assign_rig match, else the rig on the
  same camera and binning whose effective focal length is within 5%.
"""

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from app.services.equipment_assignment import _num, assign_rig, camera_matches, RigInfo
from app.utils.capture_time import unchar
from app.utils.filter_names import filter_sort_key, normalize_filter
from app.utils.rig_optics import (
    ARCSEC_PER_RADIAN_OVER_1000, SEED_SENSORS, binning_factor, cluster_by_scale, parse_pixel_size,
    relative_binning, valid_pixel_scale, weighted_median,
)

FOCAL_TOLERANCE = 0.05   # bucket clustering and rig suggestion
PIXEL_TOLERANCE = 0.05   # effective (binned) pixel size: rig vs bucket


def allocatable_clause():
    from app.models.image import FrameType, Image, ImageSubtype

    return (Image.frame_type == FrameType.LIGHT) & Image.subtype.in_(
        [ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER])


# ---------------------------------------------------------------------------
# Per-frame optics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sensor:
    patterns: Tuple[str, ...]
    width: int
    height: int
    pixel_um: float


def known_sensors(rigs: Iterable[RigInfo]) -> List[Sensor]:
    """Sensors of the configured cameras (via their rigs), then the seed table."""
    out = [Sensor(tuple(r.patterns), r.sensor_width_px, r.sensor_height_px, r.pixel_size_um)
           for r in rigs if r.patterns and r.sensor_width_px and r.sensor_height_px and r.pixel_size_um]
    out += [Sensor((e["pattern"],), e["width"], e["height"], e["pixel_um"])
            for e in SEED_SENSORS if e.get("width") and e.get("height") and e.get("pixel_um")]
    return out


def reference_sensor(camera_name: Any, sensors: Sequence[Sensor]) -> Optional[Sensor]:
    """The native (largest) sensor matching a camera name, so every mode of one camera shares a basis."""
    hits = [s for s in sensors if camera_matches(camera_name, s.patterns)]
    return max(hits, key=lambda s: (s.width * s.height, -s.pixel_um)) if hits else None


def frame_optics(row: Dict[str, Any], sensor: Optional[Sensor]) -> Dict[str, Any]:
    """
    {"bin", "pixel_um" (effective, binned), "scale", "focal", "focal_basis"} for one frame.
    focal_basis: "scale" (calculated from the solve) | "header" (FOCALLEN / EXIF) | None.
    """
    w, h = row.get("width_pixels"), row.get("height_pixels")
    xpixsz = unchar(row.get("xpixsz"))
    b = None
    if sensor is not None:
        b = relative_binning(w, h, xpixsz, row.get("binning"), sensor.width, sensor.height, sensor.pixel_um)
    if not b:
        b = binning_factor(row.get("binning"))

    pixel = parse_pixel_size(xpixsz)
    if pixel is None and sensor is not None:
        pixel = sensor.pixel_um * b

    scale = valid_pixel_scale(row.get("pixel_scale_arcsec"))
    focal, basis = None, None
    if scale and pixel:
        focal, basis = ARCSEC_PER_RADIAN_OVER_1000 * pixel / scale, "scale"
    else:
        header = _num(row.get("focallen")) or _num(row.get("focal_length"))
        if header:
            focal, basis = header, "header"
    return {"bin": b, "pixel_um": pixel, "scale": scale, "focal": focal, "focal_basis": basis}


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------

def _groups(rows: Iterable[Dict[str, Any]], rigs: List[RigInfo]) -> Iterator[Tuple[str, List[Dict[str, Any]]]]:
    """
    Yield (key, members) per bucket. Members are row copies carrying "_opt"
    (frame_optics) and "_focal", sorted by (_focal, id). Shared by
    group_unassigned and members_by_key so the two can't drift apart.
    """
    from app.services.equipment_detection import normalize_camera_name

    sensors = known_sensors(rigs)
    ref_cache: Dict[Any, Optional[Sensor]] = {}
    cam_cache: Dict[Any, str] = {}
    partitions: Dict[Tuple[str, int], Dict[str, list]] = {}
    for row in rows:
        raw = unchar(row.get("camera_name"))
        name = raw if isinstance(raw, str) or raw is None else str(raw)
        if name not in ref_cache:
            ref_cache[name] = reference_sensor(name, sensors)
            cam_cache[name] = normalize_camera_name(name) or "none"
        item = dict(row)
        item["_opt"] = frame_optics(row, ref_cache[name])
        item["_focal"] = item["_opt"]["focal"]
        p = partitions.setdefault((cam_cache[name], item["_opt"]["bin"]), {"known": [], "unknown": []})
        (p["known"] if item["_focal"] else p["unknown"]).append(item)

    for (cam, b), p in partitions.items():
        prefix = f"{cam}|b{b}"
        known = sorted(p["known"], key=lambda m: (m["_focal"], m.get("id") or 0))
        for members in cluster_by_scale(known, "_focal", FOCAL_TOLERANCE):
            yield f"{prefix}|{members[0]['_focal']:.1f}", members
        if p["unknown"]:
            yield f"{prefix}|-", sorted(p["unknown"], key=lambda m: m.get("id") or 0)


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _representative(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The member with the median focal length (the first member for an unknown-focal bucket)."""
    median = weighted_median([(m["_focal"], 1) for m in members if m["_focal"]])
    if median is None:
        return members[0]
    return next(m for m in members if m["_focal"] == median)


def suggest_rig(bucket: Dict[str, Any], rep: Dict[str, Any], rigs: List[RigInfo]) -> Tuple[Optional[int], Optional[str]]:
    """
    bucket: {"camera_name", "binning", "pixel_um", "focal_mm"}; rep: a member
    row (assign_rig's image dict). Returns (rig_id, "exact" | "focal") or (None, None).
    """
    rig_id, _ = assign_rig(rep, rigs)
    if rig_id is not None:
        return rig_id, "exact"

    focal = bucket.get("focal_mm")
    if not focal:
        return None, None
    best = None
    for r in rigs:
        if not camera_matches(bucket.get("camera_name"), r.patterns) or not r.effective_focal:
            continue
        rig_pixel = r.pixel_size_um * r.binning if r.pixel_size_um else None
        if rig_pixel and bucket.get("pixel_um"):
            if abs(rig_pixel - bucket["pixel_um"]) / rig_pixel > PIXEL_TOLERANCE:
                continue
        elif r.binning != bucket.get("binning"):
            continue
        err = abs(r.effective_focal - focal) / r.effective_focal
        if err <= FOCAL_TOLERANCE:
            rank = (err, not r.is_active, r.id)
            if best is None or rank < best[0]:
                best = (rank, r.id)
    return (best[1], "focal") if best else (None, None)


def _describe(key: str, members: List[Dict[str, Any]], rigs: List[RigInfo]) -> Dict[str, Any]:
    names = Counter(m.get("camera_name") for m in members if m.get("camera_name"))
    camera_name = max(names.items(), key=lambda kv: (kv[1], str(kv[0])))[0] if names else None
    focals = [m["_focal"] for m in members if m["_focal"]]
    scales = [m["_opt"]["scale"] for m in members if m["_opt"]["scale"]]
    pixels = [m["_opt"]["pixel_um"] for m in members if m["_opt"]["pixel_um"]]
    subtypes = Counter(m.get("subtype") for m in members)
    dims = Counter((m.get("width_pixels"), m.get("height_pixels")) for m in members
                   if m.get("width_pixels") and m.get("height_pixels"))
    filters = {normalize_filter(m.get("filter_name")) for m in members} - {"None"}
    captures = [m["capture_date"] for m in members if m.get("capture_date") is not None]
    bases = Counter(m["_opt"]["focal_basis"] for m in members if m["_opt"]["focal_basis"])

    rep = _representative(members)
    _, reason = assign_rig(rep, rigs)
    pixel = _median(pixels)
    focal = _median(focals)
    bucket = {
        "key": key,
        "camera_name": camera_name,
        "binning": members[0]["_opt"]["bin"],
        "pixel_um": round(pixel, 3) if pixel else None,
        "focal_mm": round(focal, 1) if focal else None,
        "focal_min": round(min(focals), 1) if focals else None,
        "focal_max": round(max(focals), 1) if focals else None,
        "focal_basis": bases.most_common(1)[0][0] if bases else None,
        "scale_min": round(min(scales), 4) if scales else None,
        "scale_max": round(max(scales), 4) if scales else None,
        "count": len(members),
        "sub_count": subtypes.get("SUB_FRAME", 0),
        "master_count": subtypes.get("INTEGRATION_MASTER", 0),
        "frame_sizes": [f"{w}x{h}" for (w, h), _ in dims.most_common(3)],
        "frame_size_count": len(dims),
        "filters": sorted(filters, key=filter_sort_key),
        "total_exposure_s": float(sum(float(m.get("exposure_time_seconds") or 0) for m in members)),
        "first_capture": _iso(min(captures)) if captures else None,
        "last_capture": _iso(max(captures)) if captures else None,
        "reason": reason,
        "sample_image_id": rep.get("id"),
    }
    bucket["suggested_rig_id"], bucket["suggestion_basis"] = suggest_rig(bucket, rep, rigs)
    return bucket


def group_unassigned(rows: Iterable[Dict[str, Any]], rigs: List[RigInfo]) -> List[Dict[str, Any]]:
    """
    rows: one dict per unassigned allocatable image (id, camera_name, subtype,
    width_pixels, height_pixels, binning, pixel_scale_arcsec, xpixsz, focallen,
    focal_length, filter_name, exposure_time_seconds, capture_date).
    Returns one summary per bucket, largest first.
    """
    groups = [_describe(key, members, rigs) for key, members in _groups(rows, rigs)]
    groups.sort(key=lambda g: (-g["count"], g["key"]))
    return groups


def members_by_key(rows: Iterable[Dict[str, Any]], rigs: List[RigInfo],
                   keys: Iterable[str]) -> Dict[str, List[int]]:
    """{key: ids} for the requested keys that still exist (missing keys are left out)."""
    wanted = set(keys)
    return {k: [m["id"] for m in members] for k, members in _groups(rows, rigs) if k in wanted}
