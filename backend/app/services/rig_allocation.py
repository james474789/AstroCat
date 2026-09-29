"""
Find & allocate images with no rig (R0b, docs/design/R0b-rig-allocation.md).

Pure core (no session, no I/O), same style as equipment_assignment:
- `suggest_rig(sample, rigs)` -> (rig_id | None, basis). Unlike assign_rig it
  ignores frame dimensions (crops, drizzle) and accepts a looser scale, so it
  is only ever a pre-selection the user confirms.
- `group_unassigned(rows, rigs)`: groups similar rig-less light subs / masters
  for the Equipment page.
- `member_ids(rows, rigs, key)`: the ids of one group, re-derived from its key
  so the assign POST never trusts client-sent ids.
"""

import statistics
from collections import Counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from app.services.equipment_assignment import _num, assign_rig, camera_matches, RigInfo
from app.utils.filter_names import filter_sort_key, normalize_filter
from app.utils.rig_optics import CLUSTER_TOLERANCE, cluster_by_scale, valid_pixel_scale, weighted_median

SUGGEST_SCALE_TOLERANCE = 0.25
SUGGEST_FOCAL_TOLERANCE = 0.10


def allocatable_clause():
    from app.models.image import FrameType, Image, ImageSubtype

    return (Image.frame_type == FrameType.LIGHT) & Image.subtype.in_(
        [ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER])


# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------

def suggest_rig(sample: Dict[str, Any], rigs: List[RigInfo]) -> Tuple[Optional[int], Optional[str]]:
    """
    sample: the same keys as assign_rig's image dict. Returns (rig_id, basis),
    basis in "exact" | "scale" | "focal" | "camera", or (None, None).
    """
    rig_id, _ = assign_rig(sample, rigs)
    if rig_id is not None:
        return rig_id, "exact"

    cands = [r for r in rigs if camera_matches(sample.get("camera_name"), r.patterns)]
    if not cands:
        return None, None

    s = valid_pixel_scale(sample.get("pixel_scale_arcsec"))
    if s is not None:
        best = None
        for r in cands:
            ref = r.declared_scale() or r.measured_scale_arcsec
            if not ref:
                continue
            err = abs(ref - s) / ref
            if best is None or err < best[0]:
                best = (err, r)
        if best is not None and best[0] <= SUGGEST_SCALE_TOLERANCE:
            return best[1].id, "scale"

    focal = _num(sample.get("focallen")) or _num(sample.get("focal_length"))
    if focal:
        hits = [r for r in cands
                if r.effective_focal and abs(r.effective_focal - focal) / r.effective_focal <= SUGGEST_FOCAL_TOLERANCE]
        if len(hits) == 1:
            return hits[0].id, "focal"

    active = [r for r in cands if r.is_active]
    if len(active) == 1:
        return active[0].id, "camera"
    if len(cands) == 1:
        return cands[0].id, "camera"
    return None, None


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _part(value: Any) -> str:
    return "none" if value is None or value == "" else str(value)


def _partition_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    """(subtype, cam_key, w, h, binning) as rendered key parts, so the key alone identifies the partition."""
    from app.services.equipment_detection import normalize_camera_name

    return (_part(row.get("subtype")), _part(normalize_camera_name(row.get("camera_name")) or ""),
            _part(row.get("width_pixels")), _part(row.get("height_pixels")), _part(row.get("binning")))


def _groups(rows: Iterable[Dict[str, Any]]) -> Iterator[Tuple[str, List[Dict[str, Any]]]]:
    """
    Yield (key, members) for every group. Members are copies of the rows with
    "_scale" set (valid pixel scale or None), sorted by (_scale, id). Shared
    by group_unassigned and member_ids so the two can't drift apart.
    """
    partitions: Dict[Tuple[str, ...], Dict[str, list]] = {}
    for row in rows:
        item = dict(row)
        item["_scale"] = valid_pixel_scale(row.get("pixel_scale_arcsec"))
        p = partitions.setdefault(_partition_key(item), {"scaled": [], "unscaled": []})
        (p["scaled"] if item["_scale"] is not None else p["unscaled"]).append(item)

    for (subtype, cam, w, h, binning), p in partitions.items():
        prefix = f"{subtype}|{cam}|{w}x{h}|{binning}"
        # cluster_by_scale is order-sensitive only through its input order, so sort first.
        scaled = sorted(p["scaled"], key=lambda m: (m["_scale"], m.get("id") or 0))
        for members in cluster_by_scale(scaled, "_scale", CLUSTER_TOLERANCE):
            yield f"{prefix}|{members[0]['_scale']:.4f}", members
        if p["unscaled"]:
            yield f"{prefix}|-", sorted(p["unscaled"], key=lambda m: m.get("id") or 0)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _representative(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The member with the median scale (the first member for an unscaled group)."""
    median = weighted_median([(m["_scale"], 1) for m in members if m["_scale"] is not None])
    if median is None:
        return members[0]
    return next(m for m in members if m["_scale"] == median)


def _describe(key: str, members: List[Dict[str, Any]], rigs: List[RigInfo]) -> Dict[str, Any]:
    first = members[0]
    scales = [m["_scale"] for m in members if m["_scale"] is not None]
    names = Counter(m.get("camera_name") for m in members if m.get("camera_name"))
    camera_name = max(names.items(), key=lambda kv: (kv[1], kv[0]))[0] if names else None
    focals = [f for f in ((_num(m.get("focallen")) or _num(m.get("focal_length"))) for m in members) if f]
    filters = {normalize_filter(m.get("filter_name")) for m in members} - {"None"}
    captures = [m["capture_date"] for m in members if m.get("capture_date") is not None]

    rep = _representative(members)
    _, reason = assign_rig(rep, rigs)
    suggested, basis = suggest_rig(rep, rigs)
    median = weighted_median([(s, 1) for s in scales])
    return {
        "key": key,
        "subtype": first.get("subtype"),
        "camera_name": camera_name,
        "width_pixels": first.get("width_pixels"),
        "height_pixels": first.get("height_pixels"),
        "binning": first.get("binning"),
        "scale_min": round(min(scales), 4) if scales else None,
        "scale_max": round(max(scales), 4) if scales else None,
        "scale_median": round(median, 4) if median is not None else None,
        "focal_mm": round(statistics.median(focals), 1) if focals else None,
        "filters": sorted(filters, key=filter_sort_key),
        "count": len(members),
        "total_exposure_s": float(sum(float(m.get("exposure_time_seconds") or 0) for m in members)),
        "first_capture": _iso(min(captures)) if captures else None,
        "last_capture": _iso(max(captures)) if captures else None,
        "reason": reason,
        "suggested_rig_id": suggested,
        "suggestion_basis": basis,
        "sample_image_id": rep.get("id"),
    }


def group_unassigned(rows: Iterable[Dict[str, Any]], rigs: List[RigInfo]) -> List[Dict[str, Any]]:
    """
    rows: one dict per unassigned allocatable image (id, camera_name, subtype,
    width_pixels, height_pixels, binning, pixel_scale_arcsec, xpixsz, focallen,
    focal_length, filter_name, exposure_time_seconds, capture_date).
    Returns one summary per group, largest first.
    """
    groups = [_describe(key, members, rigs) for key, members in _groups(rows)]
    groups.sort(key=lambda g: (-g["count"], g["key"]))
    return groups


def member_ids(rows: Iterable[Dict[str, Any]], rigs: List[RigInfo], key: str) -> Optional[List[int]]:
    """The ids of the group whose key equals `key`, or None when no group has it."""
    for k, members in _groups(rows):
        if k == key:
            return [m["id"] for m in members]
    return None
