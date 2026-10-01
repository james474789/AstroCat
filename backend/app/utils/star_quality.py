"""
Star quality presentation helpers (Q1, docs/design/20260927-Q1-star-quality.md §5.1, §7.1).

Sizes are stored in native pixels; arcsec is derived here from the image's
plate scale, falling back to its rig's measured scale. Pure functions: the
API gathers the inputs.
"""

from statistics import median
from typing import Any, Dict, Iterable, Optional

from app.utils.optics import SAMPLING_MAX_RATIO, SAMPLING_MIN_RATIO

MEASURED = "OK"   # the only status whose values are AstroCat-measured


# An image scale further than this factor from its rig's measured scale is
# treated as bad data (some header solves store e.g. 72"/px for a 2.3"/px
# rig). Loose enough to allow 2x2 binning.
SCALE_MISMATCH_FACTOR = 2.5


def _valid_scale(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 3600 else None


def resolve_scale(image_scale: Optional[float], rig_scale: Optional[float]):
    """
    (arcsec/px, source): the image's plate scale (IMAGE), else its rig's
    measured scale (RIG), or the rig's when the image's disagrees with it by
    more than SCALE_MISMATCH_FACTOR (RIG_OVERRIDE). (None, None) when unknown.
    """
    img, rig = _valid_scale(image_scale), _valid_scale(rig_scale)
    if img and rig and not (1 / SCALE_MISMATCH_FACTOR <= img / rig <= SCALE_MISMATCH_FACTOR):
        return rig, "RIG_OVERRIDE"
    if img:
        return img, "IMAGE"
    if rig:
        return rig, "RIG"
    return None, None


# SQL mirror of resolve_scale; needs `LEFT JOIN rigs r ON r.id = images.rig_id`.
SCALE_SQL = (
    "(CASE WHEN r.measured_scale_arcsec > 0 AND (images.pixel_scale_arcsec IS NULL "
    f"OR images.pixel_scale_arcsec NOT BETWEEN r.measured_scale_arcsec / {SCALE_MISMATCH_FACTOR} "
    f"AND r.measured_scale_arcsec * {SCALE_MISMATCH_FACTOR}) "
    "THEN r.measured_scale_arcsec "
    "WHEN images.pixel_scale_arcsec > 0 AND images.pixel_scale_arcsec < 3600 THEN images.pixel_scale_arcsec END)"
)


def to_arcsec(px: Optional[float], scale: Optional[float]) -> Optional[float]:
    if px is None or scale is None:
        return None
    return round(float(px) * float(scale), 3)


def sampling(fwhm_px: Optional[float]) -> Optional[str]:
    """UNDER | OK | OVER from pixels per FWHM (the rig sampling band in optics.py)."""
    if fwhm_px is None or fwhm_px <= 0:
        return None
    if fwhm_px < SAMPLING_MIN_RATIO:
        return "UNDER"
    if fwhm_px > SAMPLING_MAX_RATIO:
        return "OVER"
    return "OK"


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return round(median(vals), 3) if vals else None


def night_context(own_fwhm_px: Optional[float], own_hfr_px: Optional[float],
                  peers: Iterable[Dict[str, Any]], scale: Optional[float],
                  night: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    How this sub compares with the other measured subs of its night on the
    same rig and filter (peers includes the sub itself). None with < 3 peers.
    """
    peers = list(peers)
    if len(peers) < 3:
        return None
    med_fwhm = _median(p.get("fwhm_px") for p in peers)
    med_hfr = _median(p.get("hfr_px") for p in peers)
    delta = None
    if own_fwhm_px is not None and med_fwhm:
        delta = round((own_fwhm_px - med_fwhm) / med_fwhm * 100.0, 1)
    return {
        "night": night,
        "subs": len(peers),
        "median_fwhm_px": med_fwhm,
        "median_hfr_px": med_hfr,
        "median_fwhm_arcsec": to_arcsec(med_fwhm, scale),
        "median_hfr_arcsec": to_arcsec(med_hfr, scale),
        "fwhm_delta_pct": delta,
    }


def quality_summary(image, rig_scale: Optional[float] = None,
                    context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The `quality` block of ImageDetail: both units, so the UI toggle never refetches."""
    scale, scale_source = resolve_scale(getattr(image, "pixel_scale_arcsec", None), rig_scale)
    details = getattr(image, "star_metrics", None) or {}
    grid = details.get("grid_hfr")
    return {
        "status": image.star_metrics_status,
        "source": details.get("source"),
        "measured": image.star_metrics_status == MEASURED,
        "scale_arcsec": round(scale, 4) if scale else None,
        "scale_source": scale_source,
        "hfr_px": image.hfr_px,
        "fwhm_px": image.fwhm_px,
        "hfr_arcsec": to_arcsec(image.hfr_px, scale),
        "fwhm_arcsec": to_arcsec(image.fwhm_px, scale),
        "fwhm_major_px": details.get("fwhm_major_px"),
        "fwhm_minor_px": details.get("fwhm_minor_px"),
        "eccentricity": image.eccentricity,
        "theta_deg": details.get("theta_deg"),
        "star_count": image.star_count,
        "bkg_adu": details.get("bkg_adu"),
        "noise_adu": details.get("noise_adu"),
        "fwhm_method": details.get("fwhm_method"),
        "sampling": sampling(image.fwhm_px) if image.star_metrics_status == MEASURED else None,
        "grid_hfr_px": grid,
        "grid_hfr_arcsec": [[to_arcsec(v, scale) for v in row] for row in grid] if grid and scale else None,
        "hints": details.get("hints") or {},
        "error": details.get("error"),
        "reason": details.get("reason"),
        "measured_at": image.star_metrics_at.isoformat() if getattr(image, "star_metrics_at", None) else None,
        "algo_version": getattr(image, "star_metrics_version", None),
        "night": context,
    }


# --------------------------------------------------------------------------
# Aggregates over many subs (Q1b: Targets "By Filter & Rig")
# --------------------------------------------------------------------------

# One sample per measured sub, as built by SAMPLE_SQL-style json arrays:
# [fwhm_px, hfr_px, eccentricity, star_count, scale_arcsec_or_null]
SAMPLE_FIELDS = ("fwhm_px", "hfr_px", "eccentricity", "star_count", "scale")


def _percentile(values, pct: float) -> Optional[float]:
    """Linear-interpolated percentile (same definition as Postgres percentile_cont)."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 3)
    pos = (len(vals) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo), 3)


def summarize_samples(samples) -> Optional[Dict[str, Any]]:
    """
    Median / best (p10) / worst (p90) star quality over a set of measured
    subs, in px and, for subs with a known scale, arcsec. None when empty.
    Arcsec statistics come from each sub's own scale, so mixed scales are fine.
    """
    rows = [s for s in (samples or []) if s and s[0] is not None]
    if not rows:
        return None
    fwhm_px = [r[0] for r in rows]
    hfr_px = [r[1] for r in rows]
    fwhm_as = [r[0] * r[4] for r in rows if r[4]]
    hfr_as = [r[1] * r[4] for r in rows if r[4] and r[1] is not None]
    return {
        "measured": len(rows),
        "median_fwhm_px": _percentile(fwhm_px, 0.5),
        "best_fwhm_px": _percentile(fwhm_px, 0.1),
        "p90_fwhm_px": _percentile(fwhm_px, 0.9),
        "median_hfr_px": _percentile(hfr_px, 0.5),
        "median_fwhm_arcsec": _percentile(fwhm_as, 0.5),
        "best_fwhm_arcsec": _percentile(fwhm_as, 0.1),
        "p90_fwhm_arcsec": _percentile(fwhm_as, 0.9),
        "median_hfr_arcsec": _percentile(hfr_as, 0.5),
        "with_scale": len(fwhm_as),
        "median_ecc": _percentile([r[2] for r in rows], 0.5),
        "median_stars": _percentile([r[3] for r in rows], 0.5),
    }
