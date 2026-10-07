"""
PixInsight astrometric solutions as FITS WCS cards.

ImageSolver writes its solution into the XISF as `PCL:AstrometricSolution:*`
properties and does not add WCS keywords to the FITS header, so an XISF
solved in PixInsight looks unsolved to a header-only reader. This turns those
properties into equivalent TAN(-SIP) cards so the rest of the pipeline
(centre, scale, radius, sky overlay) can treat it like any header WCS.

Frames: PixInsight image coordinates are continuous with the origin at the
top-left corner and y increasing downward. The cards keep that row order
(FITS row 1 = top row, as the sky overlay and all image loaders assume), so a
PixInsight pixel (x, y) is FITS pixel (x + 0.5, y + 0.5). "Native" (l, b) are
gnomonic plane coordinates in degrees around ReferenceCelestialCoordinates,
i.e. FITS intermediate world coordinates.

Accuracy: when the solution carries its distortion model (the spline's
ImageToNative point grid, sampled every few pixels), the linear part and a SIP
polynomial are fitted to that grid, so the cards reproduce PixInsight's
pixel -> sky mapping to a fraction of a pixel. Without a grid only the linear
matrix is used.
"""

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from app.utils.sky_wcs import WCS_KEY

_PREFIX = "PCL:AstrometricSolution:"
_GRID = "SplineWorldTransformation:PointGridInterpolation:ImageToNative:"

# Marks a raw_header whose WCS cards were synthesised from the solution; the
# value is the conversion version (bump it to have the backfill redo old rows).
MARKER_KEY = "PIWCS"
MARKER_VERSION = 3

# Single-frame solves reach the target by order 3-5; stitched mosaics can't be
# followed by any polynomial (panel seams), so they get the highest order as a
# best effort. Higher orders lose precision in SIP's raw-pixel monomials.
SIP_ORDERS = (3, 5, 7, 9)
SIP_TARGET_PX = 0.5  # stop at the first order whose median grid residual is below this
GRID_SAMPLES = 40000


def _prop(properties: Mapping, name: str):
    entry = properties.get(_PREFIX + name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def _grid_samples(properties: Mapping) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """(px, py, l, b) at the distortion grid's nodes (a subsample of them), or None without a grid."""
    gx, gy = _prop(properties, _GRID + "GridX"), _prop(properties, _GRID + "GridY")
    delta, rect = _prop(properties, _GRID + "Delta"), _prop(properties, _GRID + "Rect")
    if gx is None or gy is None or delta is None or rect is None:
        return None
    gx, gy = np.asarray(gx, dtype=float), np.asarray(gy, dtype=float)
    if gx.ndim != 2 or gx.shape != gy.shape or min(gx.shape) < 4:
        return None
    rows, cols = gx.shape
    step = max(1, int(math.sqrt(rows * cols / GRID_SAMPLES)))
    j, i = np.mgrid[0:rows:step, 0:cols:step]
    px = float(rect[0]) + i.ravel() * float(delta)
    py = float(rect[1]) + j.ravel() * float(delta)
    l, b = gx[j, i].ravel(), gy[j, i].ravel()
    ok = np.isfinite(l) & np.isfinite(b)
    return px[ok], py[ok], l[ok], b[ok]


def _terms(order: int):
    return [(p, q) for n in range(2, order + 1) for p in range(n + 1) for q in [n - p]]


def _monomials(u: np.ndarray, v: np.ndarray, order: int, scale: float) -> np.ndarray:
    us, vs = u / scale, v / scale
    return np.column_stack([us ** p * vs ** q for p, q in _terms(order)])


def _raw(coeffs: np.ndarray, order: int, scale: float) -> Dict[str, float]:
    """Scaled-unit coefficients -> SIP cards' raw-pixel coefficients."""
    return {f"{p}_{q}": float(c / scale ** (p + q)) for (p, q), c in zip(_terms(order), coeffs)}


def _fit_sip(px, py, l, b, crpix, width, height) -> Optional[Dict[str, Any]]:
    """
    CD and forward/inverse SIP fitted to the grid around `crpix` (the pixel at the
    tangent point), or None when it doesn't beat the plain linear fit. The linear
    and nonlinear terms are fitted together: fitting the linear part first lets it
    absorb some of the distortion, leaving a linear residue SIP can't express.
    """
    x0, y0 = crpix
    u, v = px - x0, py - y0
    scale = max(width, height) / 2.0
    native = np.column_stack([l, b])

    def fit(order):
        design = np.column_stack([u, v, _monomials(u, v, order, scale)]) if order else np.column_stack([u, v])
        coeffs, *_ = np.linalg.lstsq(design, native, rcond=None)  # (2 + nterms, 2)
        cd = coeffs[:2].T
        if not np.linalg.det(cd):
            return None
        cd_inv = np.linalg.inv(cd)
        # Pixel residual: where the cards would put each grid node vs where it is.
        resid = (design @ coeffs - native) @ cd_inv.T
        return cd, cd_inv, coeffs[2:], float(np.median(np.hypot(resid[:, 0], resid[:, 1])))

    linear = fit(0)
    if linear is None:
        return None
    for order in SIP_ORDERS:
        result = fit(order)
        if result is None:
            return None
        if result[3] <= SIP_TARGET_PX or order == SIP_ORDERS[-1]:
            break
    cd, cd_inv, poly, err = result
    if not math.isfinite(err) or err >= linear[3]:
        return None
    sip = poly @ cd_inv.T  # native-plane coefficients -> pixel-offset coefficients (A, B)
    # Inverse (AP, BP): pixel offset as linear-TAN offset plus a polynomial in it.
    lin_u, lin_v = (native @ cd_inv.T).T
    inv, *_ = np.linalg.lstsq(_monomials(lin_u, lin_v, order, scale),
                              np.column_stack([u - lin_u, v - lin_v]), rcond=None)
    return {"cd": cd, "crpix": (x0, y0), "order": order,
            "a": _raw(sip[:, 0], order, scale), "b": _raw(sip[:, 1], order, scale),
            "ap": _raw(inv[:, 0], order, scale), "bp": _raw(inv[:, 1], order, scale),
            "median_resid_px": err}


def _tangent_pixel(properties: Mapping, start: Tuple[float, float], cd: np.ndarray) -> Tuple[float, float]:
    """The pixel PixInsight's distortion grid maps to native (0, 0), by Newton steps from `start`."""
    cd_inv = np.linalg.inv(cd)
    p = np.array(start, dtype=float)
    for _ in range(20):
        native = _grid_native(properties, np.array([p[0]]), np.array([p[1]]))
        step = cd_inv @ np.array([native[0][0], native[1][0]])
        p -= step
        if np.hypot(*step) < 1e-3:
            break
    return float(p[0]), float(p[1])


def pixinsight_wcs_cards(properties: Optional[Mapping], width_pixels: Optional[int],
                         height_pixels: Optional[int]) -> Optional[Dict[str, Any]]:
    """FITS WCS cards (top-down rows) for the image's PixInsight solution, or None when it has none we can use."""
    if not properties or not width_pixels or not height_pixels:
        return None
    try:
        projection = str(_prop(properties, "ProjectionSystem") or "")
        if projection and projection.lower() != "gnomonic":
            return None
        ref_sky = _prop(properties, "ReferenceCelestialCoordinates")
        ref_img = _prop(properties, "ReferenceImageCoordinates")
        matrix = _prop(properties, "LinearTransformationMatrix")
        if ref_sky is None or ref_img is None or matrix is None:
            return None
        ra, dec = float(ref_sky[0]), float(ref_sky[1])
        x0, y0 = float(ref_img[0]), float(ref_img[1])
        cd = np.array([[float(v) for v in row][:2] for row in matrix][:2])
        if cd.shape != (2, 2) or not np.linalg.det(cd):
            return None
    except (TypeError, ValueError, IndexError):
        return None

    sip = None
    try:
        samples = _grid_samples(properties)
        if samples is not None:
            crpix = _tangent_pixel(properties, (x0, y0), cd)
            sip = _fit_sip(*samples, crpix, width_pixels, height_pixels)
    except (TypeError, ValueError, IndexError, np.linalg.LinAlgError):
        sip = None
    if sip:
        cd, (x0, y0) = sip["cd"], sip["crpix"]

    cards = {
        "CTYPE1": "RA---TAN-SIP" if sip else "RA---TAN",
        "CTYPE2": "DEC--TAN-SIP" if sip else "DEC--TAN",
        "CRVAL1": ra,
        "CRVAL2": dec,
        "CRPIX1": float(x0) + 0.5,
        "CRPIX2": float(y0) + 0.5,
        "CD1_1": float(cd[0, 0]),
        "CD1_2": float(cd[0, 1]),
        "CD2_1": float(cd[1, 0]),
        "CD2_2": float(cd[1, 1]),
        "RADESYS": "ICRS",
    }
    if sip:
        order = sip["order"]
        cards.update({"A_ORDER": order, "B_ORDER": order, "AP_ORDER": order, "BP_ORDER": order})
        for prefix, coeffs in (("A", sip["a"]), ("B", sip["b"]), ("AP", sip["ap"]), ("BP", sip["bp"])):
            cards.update({f"{prefix}_{k}": v for k, v in coeffs.items()})
    return cards


def pixinsight_scale_rotation(properties: Optional[Mapping]) -> Optional[Tuple[float, float]]:
    """
    (arcsec/px, rotation degrees) from the solution's linear matrix, as PixInsight
    reports them (its "Resolution"; a SIP fit's CD is only the tangent-point term).
    Rotation is in AstroCat's convention (field_overlaps.tan_params: top-left
    origin, same as astrometry.net orientation); PixInsight shows the opposite sign.
    """
    if not properties:
        return None
    try:
        m = np.array([[float(v) for v in row][:2] for row in _prop(properties, "LinearTransformationMatrix")][:2])
        det = abs(float(np.linalg.det(m)))
    except (TypeError, ValueError, IndexError):
        return None
    if not det:
        return None
    return math.sqrt(det) * 3600.0, math.degrees(math.atan2(m[0, 1], -m[1, 1]))


def _grid_native(properties: Mapping, xs: np.ndarray, ys: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """PixInsight's own pixel -> native mapping (bilinear on its distortion grid), or None without a grid."""
    gx, gy = _prop(properties, _GRID + "GridX"), _prop(properties, _GRID + "GridY")
    delta, rect = _prop(properties, _GRID + "Delta"), _prop(properties, _GRID + "Rect")
    if gx is None or gy is None or delta is None or rect is None:
        return None
    gx, gy = np.asarray(gx, dtype=float), np.asarray(gy, dtype=float)
    rows, cols = gx.shape
    fx = np.clip((xs - float(rect[0])) / float(delta), 0, cols - 1)
    fy = np.clip((ys - float(rect[1])) / float(delta), 0, rows - 1)
    i = np.minimum(fx.astype(int), cols - 2)
    j = np.minimum(fy.astype(int), rows - 2)
    tx, ty = fx - i, fy - j

    def interp(g):
        return (g[j, i] * (1 - tx) * (1 - ty) + g[j, i + 1] * tx * (1 - ty)
                + g[j + 1, i] * (1 - tx) * ty + g[j + 1, i + 1] * tx * ty)
    return interp(gx), interp(gy)


def _native_to_sky(l, b, ra0: float, dec0: float) -> Tuple[np.ndarray, np.ndarray]:
    """Gnomonic deprojection of plane coordinates (degrees) around (ra0, dec0); radians out."""
    xi, eta = np.radians(l), np.radians(b)
    r0, d0 = math.radians(ra0), math.radians(dec0)
    ra = r0 + np.arctan2(xi, math.cos(d0) - eta * math.sin(d0))
    dec = np.arcsin((math.sin(d0) + eta * math.cos(d0)) / np.sqrt(1 + xi * xi + eta * eta))
    return ra, dec


def pixinsight_field(properties: Optional[Mapping], cards: Mapping[str, Any], width: Optional[int],
                     height: Optional[int]) -> Optional[Tuple[float, float, float]]:
    """
    (ra, dec, radius) in degrees: the image centre and the distance to its farthest
    corner. Uses PixInsight's distortion grid when present (matches its own readout
    exactly, where a polynomial can't follow stitched mosaics), else the cards.
    """
    if not width or not height:
        return None
    # Continuous pixels, top-left origin: centre, then the four corners.
    xs = np.array([width / 2, 0.0, width, 0.0, width])
    ys = np.array([height / 2, 0.0, 0.0, height, height])
    try:
        native = _grid_native(properties, xs, ys) if properties else None
        if native is not None:
            ra0, dec0 = (float(v) for v in _prop(properties, "ReferenceCelestialCoordinates"))
            ra, dec = _native_to_sky(native[0], native[1], ra0, dec0)
        else:
            from astropy.wcs import WCS
            from app.utils.sky_wcs import wcs_cards

            w = WCS(wcs_cards(cards), naxis=2)
            ra, dec = np.radians(w.all_pix2world(xs - 0.5, ys - 0.5, 0))
    except Exception:
        return None
    sep = np.arccos(np.clip(np.sin(dec[0]) * np.sin(dec[1:])
                            + np.cos(dec[0]) * np.cos(dec[1:]) * np.cos(ra[1:] - ra[0]), -1.0, 1.0))
    out = (float(np.degrees(ra[0])) % 360.0, float(np.degrees(dec[0])), float(np.degrees(sep.max())))
    return out if all(math.isfinite(v) for v in out) else None


def with_pixinsight_wcs(header: Mapping, cards: Mapping[str, Any]) -> Dict[str, Any]:
    """`header` with its own WCS cards replaced by `cards` (stale CDELT/PC/SIP cards would corrupt the solution)."""
    kept = {k: v for k, v in header.items()
            if not (isinstance(k, str) and WCS_KEY.match(k) and not k.startswith("NAXIS"))}
    kept.update(cards)
    kept[MARKER_KEY] = MARKER_VERSION
    return kept
