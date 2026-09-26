"""
Rig optics helpers for the Target detail "By Filter & Rig" table.

Telescope names in headers are unreliable (often the mount driver), so a rig
is identified by camera + measured pixel scale instead, and focal length is
derived from the camera's pixel size:

    focal_length_mm = 206.265 * pixel_size_um / pixel_scale_arcsec
"""

import re
import statistics
from typing import Any, Dict, Iterable, List, Optional

ARCSEC_PER_RADIAN_OVER_1000 = 206.265

# Pixel sizes (um, unbinned) for cameras whose files don't carry XPIXSZ
# (DSLR raws, some capture software). Matched as a lowercase substring of the
# camera name, first match wins - so list more specific names first.
KNOWN_PIXEL_SIZES_UM = [
    ("asi1600", 3.8),
    ("asi294", 4.63),
    ("asi120", 3.75),
    ("qhy5lii", 3.75),
    ("pl16803", 9.0),
    ("pl9000", 12.0),
    ("stl-11000", 9.0),
    ("h694", 4.54),
    ("eos 6d", 6.54),
    ("eos 80d", 3.72),
    ("eos 100d", 4.3),
    ("eos 500d", 4.69),
    ("eos r7", 3.2),
    ("eos r8", 5.93),
]

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


def known_pixel_size(camera: Optional[str]) -> Optional[float]:
    if not camera:
        return None
    name = camera.lower()
    for key, size in KNOWN_PIXEL_SIZES_UM:
        if key in name:
            return size
    return None


def focal_length_mm(pixel_size_um: Optional[float], scale: Optional[float]) -> Optional[float]:
    if not pixel_size_um or not scale:
        return None
    return ARCSEC_PER_RADIAN_OVER_1000 * pixel_size_um / scale


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
        clusters: List[List[Dict[str, Any]]] = []
        for b in sorted(g["scaled"], key=lambda x: x["pixel_scale"]):
            if clusters:
                anchor = clusters[-1][0]["pixel_scale"]
                if b["pixel_scale"] <= anchor * (1 + CLUSTER_TOLERANCE):
                    clusters[-1].append(b)
                    continue
            clusters.append([b])

        for members in clusters + ([g["unscaled"]] if g["unscaled"] else []):
            subs = sum(m["subs"] for m in members)
            seconds = sum(m["seconds"] for m in members)
            scale = _weighted_median([(m.get("pixel_scale"), m["subs"]) for m in members])
            pix = _weighted_median([(m.get("pixel_size_um"), m["subs"]) for m in members])
            fl = focal_length_mm(pix, scale)
            rows.append({
                "filter": filt,
                "camera": camera,
                "pixel_scale": round(scale, 2) if scale else None,
                "focal_length": round(fl) if fl else None,
                "subs": subs,
                "seconds": seconds,
            })
    return rows
