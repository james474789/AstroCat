"""
Field overlaps: which other images' footprints fall inside an image's field.

Footprints are derived from the WCS columns (centre, pixel scale, rotation,
dimensions, parity) using the same TAN model as CatalogMatcher._construct_wcs
and the frontend pixelToSky: top-left pixel origin, reference pixel at the
image centre. Pure functions only; the API layer does the DB work.

Candidates without a rotation get a circular footprint (half-diagonal radius),
which covers every possible orientation. A current image without a rotation
can't be used at all: its sky->pixel mapping depends on the angle.

That rebuilt TAN is only a fallback for drawing. When the current image has a
stored plate solution (sky_overlay.SkyFrame: SIP, solve-grid rescale, ASTAP row
flip), sky positions are projected through it; on very wide fields the rebuilt
TAN is turned by the meridian convergence between the solver's reference pixel
and the image centre (3.5 deg on a 40 deg field). refine_corners then redraws
each displayed outline from the candidate's own solution where it has one.
"""

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.utils.field_geometry import effective_field_radius

# Candidates must be meaningfully smaller than the current field
SIZE_RATIO_MAX = 0.9
# Grouping thresholds for "the same framing"
GROUP_CENTER_FRACTION = 0.1   # centre separation < this * candidate radius
GROUP_RADIUS_TOLERANCE = 0.05  # radius within +/-5%
GROUP_ROTATION_DEG = 3.0       # rotation within 3 deg, modulo 180 (meridian flips)
MAX_MEMBERS = 50


def tan_params(img: Any) -> Optional[Dict[str, Any]]:
    """TAN parameters for an image, or None if its WCS columns are incomplete (rotation required)."""
    ra = getattr(img, "ra_center_degrees", None)
    dec = getattr(img, "dec_center_degrees", None)
    scale = getattr(img, "pixel_scale_arcsec", None)
    rot = getattr(img, "rotation_degrees", None)
    w = getattr(img, "width_pixels", None)
    h = getattr(img, "height_pixels", None)
    if None in (ra, dec, scale, rot, w, h) or scale <= 0 or w <= 0 or h <= 0:
        return None
    parity = _parity(img)
    s = scale / 3600.0
    s_x = -s * parity
    s_y = -s
    rad = math.radians(rot)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    cd = np.array([[s_x * cos_a, -s_y * sin_a], [s_x * sin_a, s_y * cos_a]])
    return {"ra0": float(ra), "dec0": float(dec), "cd": cd, "cd_inv": np.linalg.inv(cd), "w": w, "h": h}


def _parity(img: Any) -> float:
    parity = getattr(img, "parity", None)
    if parity is None:
        header = getattr(img, "raw_header", None)
        if isinstance(header, dict):
            parity = header.get("astrometry_parity")
    try:
        return -1.0 if float(parity) < 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def pixel_to_sky(params: Dict[str, Any], xy: np.ndarray) -> np.ndarray:
    """(N,2) pixel coords (top-left origin) -> (N,2) [ra, dec] degrees, true gnomonic deprojection."""
    xy = np.asarray(xy, dtype=float)
    d = xy - np.array([params["w"] / 2.0, params["h"] / 2.0])
    xi_eta = np.radians(d @ params["cd"].T)
    xi, eta = xi_eta[:, 0], xi_eta[:, 1]
    ra0, dec0 = math.radians(params["ra0"]), math.radians(params["dec0"])
    denom = math.cos(dec0) - eta * math.sin(dec0)
    dra = np.arctan2(xi, denom)
    dec = np.arcsin((math.sin(dec0) + eta * math.cos(dec0)) / np.sqrt(1.0 + xi * xi + eta * eta))
    ra = np.degrees(ra0 + dra) % 360.0
    return np.column_stack([ra, np.degrees(dec)])


def sky_to_pixel(params: Dict[str, Any], radec: np.ndarray) -> np.ndarray:
    """(N,2) [ra, dec] degrees -> (N,2) pixel coords; NaN for points behind the tangent plane."""
    radec = np.radians(np.asarray(radec, dtype=float))
    ra, dec = radec[:, 0], radec[:, 1]
    ra0, dec0 = math.radians(params["ra0"]), math.radians(params["dec0"])
    dra = ra - ra0
    cos_c = math.sin(dec0) * np.sin(dec) + math.cos(dec0) * np.cos(dec) * np.cos(dra)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = np.cos(dec) * np.sin(dra) / cos_c
        eta = (math.cos(dec0) * np.sin(dec) - math.sin(dec0) * np.cos(dec) * np.cos(dra)) / cos_c
    xi_eta = np.degrees(np.column_stack([xi, eta]))
    d = xi_eta @ params["cd_inv"].T
    out = d + np.array([params["w"] / 2.0, params["h"] / 2.0])
    out[cos_c <= 1e-6] = np.nan
    return out


def angular_separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees (haversine)."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dp = p2 - p1
    dl = math.radians(ra2 - ra1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def clip_to_rect(poly: Sequence[Tuple[float, float]], w: float, h: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman clip of a polygon to [0,w]x[0,h]."""
    edges = [
        (lambda p: p[0] >= 0, lambda a, b: _cross_x(a, b, 0)),
        (lambda p: p[0] <= w, lambda a, b: _cross_x(a, b, w)),
        (lambda p: p[1] >= 0, lambda a, b: _cross_y(a, b, 0)),
        (lambda p: p[1] <= h, lambda a, b: _cross_y(a, b, h)),
    ]
    out = list(poly)
    for inside, cross in edges:
        if not out:
            break
        src, out = out, []
        prev = src[-1]
        for cur in src:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
    return out


def _cross_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def _cross_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def polygon_area(poly: Sequence[Tuple[float, float]]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def overlap_area(poly: Sequence[Tuple[float, float]], w: float, h: float) -> float:
    return polygon_area(clip_to_rect(poly, w, h))


def circle_overlaps_rect(cx: float, cy: float, r: float, w: float, h: float) -> bool:
    nx = min(max(cx, 0.0), w)
    ny = min(max(cy, 0.0), h)
    return (cx - nx) ** 2 + (cy - ny) ** 2 < r * r


def _radius(img: Any) -> Optional[float]:
    return effective_field_radius(
        getattr(img, "field_radius_degrees", None), img.width_pixels, img.height_pixels, img.pixel_scale_arcsec,
    )


def _projector(cur: Dict[str, Any], frame: Any = None) -> Callable[[np.ndarray], np.ndarray]:
    """(N,2) [ra, dec] -> (N,2) current-image pixels: the exact frame when there is one, else the rebuilt TAN."""
    if frame is None:
        return lambda radec: sky_to_pixel(cur, radec)

    def project(radec):
        radec = np.asarray(radec, dtype=float)
        x, y = frame.project(radec[:, 0], radec[:, 1])
        return np.column_stack([x, y])
    return project


def _footprint_sky(cand: Any) -> Optional[np.ndarray]:
    """Sky points to project for a candidate: its centre (rotation unknown) or its 4 corners (rebuilt TAN)."""
    if cand.rotation_degrees is None:
        return np.array([[cand.ra_center_degrees, cand.dec_center_degrees]], dtype=float)
    params = tan_params(cand)
    if params is None:
        return None
    cw, ch = params["w"], params["h"]
    return pixel_to_sky(params, [[0, 0], [cw, 0], [cw, ch], [0, ch]])


def _footprint_from_pixels(cur: Dict[str, Any], cand: Any, cand_radius: float,
                           pix: np.ndarray) -> Optional[Dict[str, Any]]:
    """Footprint from the candidate's projected points (see _footprint_sky), or None if it doesn't overlap."""
    if np.isnan(pix).any():
        return None
    w, h = cur["w"], cur["h"]
    if len(pix) == 1:
        center = pix[0]
        r_px = cand_radius * 3600.0 / cur["scale"]
        if not circle_overlaps_rect(center[0], center[1], r_px, w, h):
            return None
        return {"shape": "circle", "center": [float(center[0]), float(center[1])], "radius_px": float(r_px),
                "corners": None, "area": math.pi * r_px * r_px}
    poly = [(float(x), float(y)) for x, y in pix]
    if overlap_area(poly, w, h) <= 0:
        return None
    return {"shape": "polygon", "center": None, "radius_px": None,
            "corners": [[x, y] for x, y in poly], "area": polygon_area(poly)}


def _footprint(cur: Dict[str, Any], cand: Any, cand_radius: float,
               to_cur: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> Optional[Dict[str, Any]]:
    """Candidate footprint in the current image's pixels, or None if it doesn't overlap."""
    sky = _footprint_sky(cand)
    if sky is None:
        return None
    return _footprint_from_pixels(cur, cand, cand_radius, (to_cur or _projector(cur))(sky))


def _rot_close(a: float, b: float) -> bool:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d) < GROUP_ROTATION_DEG


def _same_framing(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if a["shape"] != b["shape"]:
        return False
    if abs(a["radius"] - b["radius"]) > GROUP_RADIUS_TOLERANCE * a["radius"]:
        return False
    if angular_separation(a["ra"], a["dec"], b["ra"], b["dec"]) >= GROUP_CENTER_FRACTION * a["radius"]:
        return False
    if a["shape"] == "polygon" and not _rot_close(a["rot"], b["rot"]):
        return False
    return True


def _subtype_value(img: Any) -> Optional[str]:
    st = getattr(img, "subtype", None)
    return getattr(st, "value", st)


def _rep_key(item: Dict[str, Any]):
    """Sort key: masters first, then most recent capture."""
    img = item["img"]
    is_master = _subtype_value(img) == "INTEGRATION_MASTER"
    ts = img.capture_date.timestamp() if getattr(img, "capture_date", None) else float("-inf")
    return (0 if is_master else 1, -ts)


def _member(img: Any) -> Dict[str, Any]:
    return {
        "id": img.id,
        "file_name": img.file_name,
        "subtype": _subtype_value(img),
        "capture_date": getattr(img, "capture_date", None),
        "exposure_time_seconds": getattr(img, "exposure_time_seconds", None),
        "filter_name": getattr(img, "filter_name", None),
    }


def compute_field_overlaps(current: Any, candidates: Sequence[Any], current_frame: Any = None) -> Dict[str, Any]:
    """
    Group the candidates whose footprints overlap the current image's field and are smaller than it.
    current_frame (a sky_overlay.SkyFrame) projects into the current image exactly; without it the
    rebuilt TAN is used, which needs the current image's rotation.
    Returns {"groups": [...], "reason": None | "not_solved" | "no_rotation"}; groups sorted largest first.
    """
    if current_frame is None and getattr(current, "rotation_degrees", "missing") is None:
        return {"groups": [], "reason": "no_rotation"}
    cur = tan_params(current) if current_frame is None else _frame_params(current)
    cur_radius = _radius(current) if cur else None
    if cur is None or not cur_radius:
        return {"groups": [], "reason": "not_solved"}
    cur["scale"] = current.pixel_scale_arcsec
    to_cur = _projector(cur, current_frame)

    # Every candidate's sky points go through one projection call: per-call overhead dominates otherwise
    pending = []
    for cand in candidates:
        if cand.id == current.id or not cand.pixel_scale_arcsec or not cand.width_pixels or not cand.height_pixels:
            continue
        if cand.ra_center_degrees is None or cand.dec_center_degrees is None:
            continue
        radius = _radius(cand)
        if not radius or radius >= SIZE_RATIO_MAX * cur_radius:
            continue
        sky = _footprint_sky(cand)
        if sky is not None:
            pending.append((cand, radius, sky))
    pix_all = to_cur(np.concatenate([p[2] for p in pending])) if pending else np.empty((0, 2))

    items = []
    offset = 0
    for cand, radius, sky in pending:
        pix = pix_all[offset:offset + len(sky)]
        offset += len(sky)
        fp = _footprint_from_pixels(cur, cand, radius, pix)
        if fp is None:
            continue
        items.append({**fp, "img": cand, "radius": radius, "rot": cand.rotation_degrees,
                      "ra": cand.ra_center_degrees, "dec": cand.dec_center_degrees})

    # Greedy clustering; seeds are visited representative-first so each group is anchored on its best member
    items.sort(key=_rep_key)
    clusters: List[List[Dict[str, Any]]] = []
    for item in items:
        for cluster in clusters:
            if _same_framing(cluster[0], item):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    groups = []
    for cluster in clusters:
        rep = cluster[0]
        img = rep["img"]
        groups.append({
            "id": img.id,
            "file_name": img.file_name,
            "object_name": getattr(img, "object_name", None),
            "subtype": _subtype_value(img),
            "capture_date": getattr(img, "capture_date", None),
            "count": len(cluster),
            "master_count": sum(1 for m in cluster if _subtype_value(m["img"]) == "INTEGRATION_MASTER"),
            "shape": rep["shape"],
            "corners": rep["corners"],
            "center": rep["center"],
            "radius_px": rep["radius_px"],
            "area": rep["area"],
            "members": [_member(m["img"]) for m in cluster[:MAX_MEMBERS]],
            # Uncapped, for server-side bulk actions (not part of the API response schema)
            "member_ids": [m["img"].id for m in cluster],
        })
    groups.sort(key=lambda g: -g["area"])
    return {"groups": groups, "reason": None}


def _frame_params(img: Any) -> Optional[Dict[str, Any]]:
    """Frame size for a current image projected through its SkyFrame (no rotation needed)."""
    w = getattr(img, "width_pixels", None)
    h = getattr(img, "height_pixels", None)
    scale = getattr(img, "pixel_scale_arcsec", None)
    if not w or not h or not scale or w <= 0 or h <= 0 or scale <= 0:
        return None
    return {"w": w, "h": h}


def refine_corners(groups: List[Dict[str, Any]], current: Any, current_frame: Any,
                   cand_frames: Dict[int, Any]) -> List[Dict[str, Any]]:
    """
    Redraw each group's outline from its representative's own plate solution (cand_frames: image id ->
    SkyFrame), projected into the current image (current_frame, else its rebuilt TAN). Groups whose
    representative has no solution keep their rebuilt outline; a circle (rotation unknown) becomes
    the exact polygon when a solution exists. Groups whose exact outline misses the field are dropped.
    """
    cur = tan_params(current) if current_frame is None else _frame_params(current)
    if cur is None:
        return groups
    to_cur = _projector(cur, current_frame)
    w, h = cur["w"], cur["h"]
    out = []
    for g in groups:
        frame = cand_frames.get(g["id"])
        if frame is None:
            out.append(g)
            continue
        cw, ch = frame.width, frame.height
        ra, dec = frame.unproject([0, cw, cw, 0], [0, 0, ch, ch])
        corners = to_cur(np.column_stack([ra, dec]))
        if np.isnan(corners).any():
            out.append(g)
            continue
        poly = [(float(x), float(y)) for x, y in corners]
        if overlap_area(poly, w, h) <= 0:
            continue
        out.append({**g, "shape": "polygon", "corners": [[x, y] for x, y in poly], "center": None,
                    "radius_px": None, "area": polygon_area(poly)})
    out.sort(key=lambda g: -g["area"])
    return out
