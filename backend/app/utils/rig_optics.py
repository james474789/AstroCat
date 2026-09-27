"""
Rig optics helpers for the Target detail "By Filter & Rig" table.

Telescope names in headers are unreliable (often the mount driver), so a rig
is identified by camera + measured pixel scale instead, and focal length is
derived from the camera's pixel size:

    focal_length_mm = 206.265 * pixel_size_um / pixel_scale_arcsec
"""

import re
import statistics
from typing import Any, Callable, Dict, Iterable, List, Optional

ARCSEC_PER_RADIAN_OVER_1000 = 206.265

# Seed sensor table (R0): cameras whose files don't carry XPIXSZ (DSLR raws,
# some capture software). `pattern` is matched as a lowercase substring of the
# camera name, first match wins - so list more specific names first. Entries
# with a "mode" describe an alternative sensor mode of the same camera (e.g.
# the ASI294MM "unlocked" 8288x5644 mode at 2.315 um) and only apply when an
# image's dimensions match them.
SEED_SENSORS: List[Dict[str, Any]] = [
    {"pattern": "asi1600mm", "name": "ZWO ASI1600MM Pro", "maker": "ZWO", "pixel_um": 3.8,
     "width": 4656, "height": 3520, "is_color": False, "is_cooled": True},
    {"pattern": "asi1600mc", "name": "ZWO ASI1600MC Pro", "maker": "ZWO", "pixel_um": 3.8,
     "width": 4656, "height": 3520, "is_color": True, "is_cooled": True},
    {"pattern": "asi1600", "name": "ZWO ASI1600", "maker": "ZWO", "pixel_um": 3.8,
     "width": 4656, "height": 3520, "is_color": None, "is_cooled": None},
    {"pattern": "asi294mm", "name": "ZWO ASI294MM Pro", "maker": "ZWO", "pixel_um": 2.315,
     "width": 8288, "height": 5644, "is_color": False, "is_cooled": True, "mode": "unlocked"},
    {"pattern": "asi294mm", "name": "ZWO ASI294MM Pro", "maker": "ZWO", "pixel_um": 4.63,
     "width": 4144, "height": 2822, "is_color": False, "is_cooled": True},
    {"pattern": "asi294mc", "name": "ZWO ASI294MC Pro", "maker": "ZWO", "pixel_um": 4.63,
     "width": 4144, "height": 2822, "is_color": True, "is_cooled": True},
    {"pattern": "asi294", "name": "ZWO ASI294", "maker": "ZWO", "pixel_um": 4.63,
     "width": 4144, "height": 2822, "is_color": None, "is_cooled": None},
    {"pattern": "asi2600mm", "name": "ZWO ASI2600MM Pro", "maker": "ZWO", "pixel_um": 3.76,
     "width": 6248, "height": 4176, "is_color": False, "is_cooled": True},
    {"pattern": "asi2600mc", "name": "ZWO ASI2600MC Pro", "maker": "ZWO", "pixel_um": 3.76,
     "width": 6248, "height": 4176, "is_color": True, "is_cooled": True},
    {"pattern": "asi533mc", "name": "ZWO ASI533MC Pro", "maker": "ZWO", "pixel_um": 3.76,
     "width": 3008, "height": 3008, "is_color": True, "is_cooled": True},
    {"pattern": "asi183mm", "name": "ZWO ASI183MM Pro", "maker": "ZWO", "pixel_um": 2.4,
     "width": 5496, "height": 3672, "is_color": False, "is_cooled": True},
    {"pattern": "asi120", "name": "ZWO ASI120", "maker": "ZWO", "pixel_um": 3.75,
     "width": 1280, "height": 960, "is_color": None, "is_cooled": False},
    {"pattern": "asi224", "name": "ZWO ASI224MC", "maker": "ZWO", "pixel_um": 3.75,
     "width": 1304, "height": 976, "is_color": True, "is_cooled": False},
    {"pattern": "asi462", "name": "ZWO ASI462MC", "maker": "ZWO", "pixel_um": 2.9,
     "width": 1936, "height": 1096, "is_color": True, "is_cooled": False},
    {"pattern": "qhy5lii", "name": "QHY5L-II", "maker": "QHY", "pixel_um": 3.75,
     "width": 1280, "height": 960, "is_color": None, "is_cooled": False},
    {"pattern": "qhy268m", "name": "QHY268M", "maker": "QHY", "pixel_um": 3.76,
     "width": 6280, "height": 4210, "is_color": False, "is_cooled": True},
    {"pattern": "pl16803", "name": "FLI PL16803", "maker": "FLI", "pixel_um": 9.0,
     "width": 4096, "height": 4096, "is_color": False, "is_cooled": True},
    {"pattern": "pl9000", "name": "FLI PL9000", "maker": "FLI", "pixel_um": 12.0,
     "width": 3056, "height": 3056, "is_color": False, "is_cooled": True},
    {"pattern": "stl-11000", "name": "SBIG STL-11000", "maker": "SBIG", "pixel_um": 9.0,
     "width": 4008, "height": 2672, "is_color": False, "is_cooled": True},
    {"pattern": "h694", "name": "Starlight Xpress H694", "maker": "Starlight Xpress", "pixel_um": 4.54,
     "width": 2750, "height": 2200, "is_color": False, "is_cooled": True},
    {"pattern": "eos 6d", "name": "Canon EOS 6D", "maker": "Canon", "pixel_um": 6.54,
     "width": 5472, "height": 3648, "is_color": True, "is_cooled": False},
    {"pattern": "eos 80d", "name": "Canon EOS 80D", "maker": "Canon", "pixel_um": 3.72,
     "width": 6000, "height": 4000, "is_color": True, "is_cooled": False},
    {"pattern": "eos 100d", "name": "Canon EOS 100D", "maker": "Canon", "pixel_um": 4.3,
     "width": 5184, "height": 3456, "is_color": True, "is_cooled": False},
    {"pattern": "eos 500d", "name": "Canon EOS 500D", "maker": "Canon", "pixel_um": 4.69,
     "width": 4752, "height": 3168, "is_color": True, "is_cooled": False},
    {"pattern": "eos r7", "name": "Canon EOS R7", "maker": "Canon", "pixel_um": 3.2,
     "width": 6960, "height": 4640, "is_color": True, "is_cooled": False},
    {"pattern": "eos r8", "name": "Canon EOS R8", "maker": "Canon", "pixel_um": 5.93,
     "width": 6000, "height": 4000, "is_color": True, "is_cooled": False},
    {"pattern": "eos ra", "name": "Canon EOS Ra", "maker": "Canon", "pixel_um": 5.36,
     "width": 6720, "height": 4480, "is_color": True, "is_cooled": False},
]

# Backwards-compatible (pattern, unbinned pixel size) view of the seed table:
# the default-mode entry per pattern, in table order.
KNOWN_PIXEL_SIZES_UM = []
for _entry in SEED_SENSORS:
    if not _entry.get("mode") and all(p != _entry["pattern"] for p, _ in KNOWN_PIXEL_SIZES_UM):
        KNOWN_PIXEL_SIZES_UM.append((_entry["pattern"], _entry["pixel_um"]))


def dims_match(width, height, w2, h2, tol: float = 0.01) -> bool:
    """True when (width, height) equals (w2, h2) in either orientation, within tol (min 2 px)."""
    if not (width and height and w2 and h2):
        return False
    a = sorted((int(width), int(height)))
    b = sorted((int(w2), int(h2)))
    return all(abs(x - y) <= max(2, y * tol) for x, y in zip(a, b))


def seed_sensor(camera: Optional[str], width: Optional[int] = None,
                height: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    The SEED_SENSORS entry for a camera name, preferring the entry whose
    dimensions match (width, height), so the ASI294MM unlocked mode gets
    2.315 um. Mode entries are only returned on a dimensions match.
    """
    if not camera:
        return None
    name = str(camera).lower()
    fallback = None
    for entry in SEED_SENSORS:
        if entry["pattern"] not in name:
            continue
        if width and height and dims_match(width, height, entry.get("width"), entry.get("height")):
            return entry
        if fallback is None and not entry.get("mode"):
            fallback = entry
    return fallback


# Scales outside this range, or the 72"/px placeholder some solvers write on
# failure, are treated as "no plate solve".
_MIN_VALID_SCALE = 0.05
_MAX_VALID_SCALE = 300.0
_SENTINEL_SCALES = (72.0,)

# Buckets whose median scales are within this ratio are merged into one rig.
CLUSTER_TOLERANCE = 0.05


def valid_pixel_scale(scale: Optional[float]) -> Optional[float]:
    if scale is None:
        return None
    try:
        s = float(scale)
    except (TypeError, ValueError):
        return None
    if not (_MIN_VALID_SCALE <= s <= _MAX_VALID_SCALE):
        return None
    if any(abs(s - sentinel) < 1e-3 for sentinel in _SENTINEL_SCALES):
        return None
    return s


def parse_pixel_size(raw: Any) -> Optional[float]:
    """Parse an XPIXSZ-style header value; returns um or None."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(raw))
        if not m:
            return None
        v = float(m.group(0))
    return v if 0.5 <= v <= 50 else None


def binning_factor(binning: Optional[str]) -> int:
    if not binning:
        return 1
    m = re.match(r"\s*(\d+)", str(binning))
    return max(1, int(m.group(1))) if m else 1


def known_pixel_size(camera: Optional[str],
                     camera_lookup: Optional[Callable[[str], Optional[float]]] = None) -> Optional[float]:
    """
    Unbinned pixel size (um) for a camera name. `camera_lookup` (R0: built
    from the cameras table) is consulted first; the seed table is the fallback.
    """
    if not camera:
        return None
    if camera_lookup is not None:
        try:
            size = camera_lookup(camera)
        except Exception:
            size = None
        if size:
            return size
    name = camera.lower()
    for key, size in KNOWN_PIXEL_SIZES_UM:
        if key in name:
            return size
    return None


def focal_length_mm(pixel_size_um: Optional[float], scale: Optional[float]) -> Optional[float]:
    if not pixel_size_um or not scale:
        return None
    return ARCSEC_PER_RADIAN_OVER_1000 * pixel_size_um / scale


def cluster_by_scale(items: Iterable[Dict[str, Any]], key: str = "pixel_scale",
                     tolerance: float = CLUSTER_TOLERANCE) -> List[List[Dict[str, Any]]]:
    """
    Group items (each with a positive item[key]) into clusters whose members
    are within `tolerance` of the cluster's smallest value. Sorted ascending.
    """
    clusters: List[List[Dict[str, Any]]] = []
    for item in sorted(items, key=lambda x: x[key]):
        if clusters:
            anchor = clusters[-1][0][key]
            if item[key] <= anchor * (1 + tolerance):
                clusters[-1].append(item)
                continue
        clusters.append([item])
    return clusters


def weighted_median(pairs: List[tuple]) -> Optional[float]:
    """pairs: [(value, weight)]. Returns the weighted median value (None if empty)."""
    return _weighted_median(pairs)


def _weighted_median(pairs: List[tuple]) -> Optional[float]:
    """pairs: [(value, weight)]. Returns the weighted median value."""
    pairs = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


# Q1b: buckets may carry per-sub star quality samples under this key. Medians
# can't be merged like sums, so both builders concatenate the samples of the
# buckets they fold into a row; the caller summarises (and removes) them.
QUALITY_SAMPLES_KEY = "quality_samples"


def _with_samples(row: Dict[str, Any], members: List[Dict[str, Any]]) -> Dict[str, Any]:
    if any(QUALITY_SAMPLES_KEY in m for m in members):
        row[QUALITY_SAMPLES_KEY] = [s for m in members for s in (m.get(QUALITY_SAMPLES_KEY) or [])]
    return row


def build_filter_rig_rows(buckets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse fine-grained buckets into one row per (filter, camera, rig).

    Each bucket: {"filter", "camera", "pixel_scale", "pixel_size_um", "subs", "seconds"}
    where pixel_scale is already validated (or None) and pixel_size_um is the
    effective (binned) pixel size or None.

    Buckets with a pixel scale are clustered by scale (within
    CLUSTER_TOLERANCE); buckets without one form a single "unknown" row.
    """
    groups: Dict[tuple, Dict[str, list]] = {}
    for b in buckets:
        g = groups.setdefault((b["filter"], b["camera"]), {"scaled": [], "unscaled": []})
        (g["scaled"] if b.get("pixel_scale") else g["unscaled"]).append(b)

    rows: List[Dict[str, Any]] = []
    for (filt, camera), g in groups.items():
        clusters = cluster_by_scale(g["scaled"], "pixel_scale")

        for members in clusters + ([g["unscaled"]] if g["unscaled"] else []):
            subs = sum(m["subs"] for m in members)
            seconds = sum(m["seconds"] for m in members)
            scale = _weighted_median([(m.get("pixel_scale"), m["subs"]) for m in members])
            pix = _weighted_median([(m.get("pixel_size_um"), m["subs"]) for m in members])
            fl = focal_length_mm(pix, scale)
            rows.append(_with_samples({
                "filter": filt,
                "camera": camera,
                "pixel_scale": round(scale, 2) if scale else None,
                "focal_length": round(fl) if fl else None,
                "subs": subs,
                "seconds": seconds,
            }, members))
    return rows


def build_filter_rig_rows_with_rigs(buckets: Iterable[Dict[str, Any]],
                                    rigs: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    R0 (spec §4.8): like build_filter_rig_rows, but buckets carrying a known
    "rig_id" are grouped per (filter, rig) and labelled with the declared rig
    (rigs = {id: {"name", "camera", "focal_length"}}). Other buckets fall back
    to camera + scale clustering. Every row gains "rig_id"/"rig_name" (None
    for fallback rows); the other keys are unchanged.
    """
    by_rig: Dict[tuple, List[Dict[str, Any]]] = {}
    rest: List[Dict[str, Any]] = []
    for b in buckets:
        rig_id = b.get("rig_id")
        if rig_id is not None and rig_id in rigs:
            by_rig.setdefault((b["filter"], rig_id), []).append(b)
        else:
            rest.append(b)

    rows: List[Dict[str, Any]] = []
    for (filt, rig_id), members in by_rig.items():
        rig = rigs[rig_id]
        scale = _weighted_median([(m.get("pixel_scale"), m["subs"]) for m in members])
        fl = rig.get("focal_length")
        if not fl:
            pix = _weighted_median([(m.get("pixel_size_um"), m["subs"]) for m in members])
            fl = focal_length_mm(pix, scale)
        rows.append(_with_samples({
            "filter": filt,
            "camera": rig.get("camera") or members[0].get("camera"),
            "pixel_scale": round(scale, 2) if scale else None,
            "focal_length": round(fl) if fl else None,
            "subs": sum(m["subs"] for m in members),
            "seconds": sum(m["seconds"] for m in members),
            "rig_id": rig_id,
            "rig_name": rig.get("name"),
        }, members))
    for row in build_filter_rig_rows(rest):
        row["rig_id"] = None
        row["rig_name"] = None
        rows.append(row)
    return rows
