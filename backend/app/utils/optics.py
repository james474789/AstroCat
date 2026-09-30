"""
Optics maths for rigs (R0, docs/design/P0-R0-equipment-sites.md §4.2).

Pure functions; the Equipment API exposes them as computed rig fields
(nothing here is stored except the measured scale cached on the rig).

A modifier_factor is the focal-length multiplier of a reducer/Barlow:
0.8 for a 0.8x reducer (shorter focal length, larger arcsec/px), 2.0 for
a 2x Barlow.
"""

from typing import Any, Dict, Optional, Tuple

ARCSEC_PER_RADIAN_OVER_1000 = 206.265

# "ok" band for seeing / scale (pixels per seeing FWHM): 1-3 px is well
# sampled (Nyquist-ish, 2-3 px per FWHM), below 1 undersampled, above 3 over.
SAMPLING_MIN_RATIO = 1.0
SAMPLING_MAX_RATIO = 3.0
DEFAULT_SEEING_ARCSEC = 2.5
SCALE_CHECK_TOLERANCE = 0.05


def _positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 and v == v else None


def effective_focal_mm(focal_mm: float, modifier_factor: Optional[float] = 1.0) -> Optional[float]:
    focal = _positive(focal_mm)
    if focal is None:
        return None
    return focal * (_positive(modifier_factor) or 1.0)


def pixel_scale(pixel_um: Optional[float], focal_mm: Optional[float], binning: Optional[int] = 1,
                modifier_factor: Optional[float] = 1.0) -> Optional[float]:
    """arcsec/px = 206.265 * pixel_um * binning / (focal_mm * modifier_factor)."""
    px = _positive(pixel_um)
    fl = effective_focal_mm(focal_mm, modifier_factor)
    if px is None or fl is None:
        return None
    return ARCSEC_PER_RADIAN_OVER_1000 * px * (int(binning or 1)) / fl


def fov_deg(width_px: Optional[int], height_px: Optional[int],
            scale_arcsec: Optional[float]) -> Optional[Tuple[float, float]]:
    """(width_deg, height_deg) for a sensor of width x height pixels at scale arcsec/px."""
    w, h, s = _positive(width_px), _positive(height_px), _positive(scale_arcsec)
    if w is None or h is None or s is None:
        return None
    return (w * s / 3600.0, h * s / 3600.0)


WINDOW_MIN_SHORT_FRACTION = 0.22    # default smallest target: 22% of the FOV short side
WINDOW_MAX_LONG_FRACTION = 0.80     # default largest target: 80% of the FOV long side


def target_size_window(fov: Optional[Tuple[float, float]], min_arcmin: Optional[float] = None,
                       max_arcmin: Optional[float] = None) -> Optional[Tuple[float, float]]:
    """
    (min, max) target size in arcmin a rig frames well. A declared value wins;
    otherwise 22% of the FOV short side up to 80% of the long side. None when
    a bound is neither declared nor derivable (no FOV).
    """
    lo, hi = _positive(min_arcmin), _positive(max_arcmin)
    if fov is not None:
        short, long_ = sorted(fov)
        if lo is None:
            lo = WINDOW_MIN_SHORT_FRACTION * short * 60.0
        if hi is None:
            hi = WINDOW_MAX_LONG_FRACTION * long_ * 60.0
    if lo is None or hi is None:
        return None
    return lo, hi


def focal_ratio(focal_mm: Optional[float], aperture_mm: Optional[float],
                modifier_factor: Optional[float] = 1.0) -> Optional[float]:
    fl = effective_focal_mm(focal_mm, modifier_factor)
    ap = _positive(aperture_mm)
    if fl is None or ap is None:
        return None
    return fl / ap


def sampling(scale_arcsec: Optional[float], seeing_arcsec: Optional[float]) -> Optional[Tuple[str, float]]:
    """("under"|"ok"|"over", seeing/scale). ok when 1.0 <= seeing/scale <= 3.0."""
    scale, seeing = _positive(scale_arcsec), _positive(seeing_arcsec)
    if scale is None or seeing is None:
        return None
    ratio = seeing / scale
    if ratio < SAMPLING_MIN_RATIO:
        return ("under", ratio)
    if ratio > SAMPLING_MAX_RATIO:
        return ("over", ratio)
    return ("ok", ratio)


def scale_check(declared: Optional[float], measured: Optional[float],
                tol: float = SCALE_CHECK_TOLERANCE) -> Dict[str, Any]:
    """
    Declared (from the rig's optics) vs measured (median solved) scale.
    delta_pct is relative to the measured scale: 2.46 vs 2.27 -> +8.4%.
    """
    d, m = _positive(declared), _positive(measured)
    if d is None or m is None:
        return {"declared": d, "measured": m, "delta_pct": None, "verdict": "unknown"}
    delta = (d - m) / m
    return {
        "declared": d,
        "measured": m,
        "delta_pct": round(delta * 100.0, 1),
        "verdict": "ok" if abs(delta) <= tol else "mismatch",
    }


def rig_optics_summary(*, pixel_um: Optional[float], width_px: Optional[int], height_px: Optional[int],
                       focal_mm: Optional[float], aperture_mm: Optional[float],
                       modifier_factor: Optional[float] = 1.0, binning: Optional[int] = 1,
                       measured_scale: Optional[float] = None,
                       seeing_arcsec: Optional[float] = DEFAULT_SEEING_ARCSEC) -> Dict[str, Any]:
    """
    The computed fields of a rig in the API contract: scale, fov_deg,
    focal_ratio, effective_focal_mm, sampling, scale_check. Sensor dims are
    unbinned; the FOV is the same binned or not.
    """
    binning = int(binning or 1)
    scale = pixel_scale(pixel_um, focal_mm, binning, modifier_factor)
    unbinned = pixel_scale(pixel_um, focal_mm, 1, modifier_factor)
    fov = fov_deg(width_px, height_px, unbinned)
    seeing = _positive(seeing_arcsec) or DEFAULT_SEEING_ARCSEC
    samp = sampling(scale, seeing)
    eff = effective_focal_mm(focal_mm, modifier_factor)
    ratio = focal_ratio(focal_mm, aperture_mm, modifier_factor)
    return {
        "scale": round(scale, 3) if scale else None,
        "fov_deg": [round(fov[0], 3), round(fov[1], 3)] if fov else None,
        "focal_ratio": round(ratio, 2) if ratio else None,
        "effective_focal_mm": round(eff, 1) if eff else 0.0,
        "sampling": (
            {"verdict": samp[0], "ratio": round(samp[1], 2), "seeing_arcsec": seeing} if samp else None
        ),
        "scale_check": scale_check(scale, measured_scale),
    }
