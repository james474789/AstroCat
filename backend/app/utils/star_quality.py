"""
Star quality presentation helpers (Q1, docs/design/Q1-star-quality.md §5.1, §7.1).

Sizes are stored in native pixels; arcsec is derived here from the image's
plate scale, falling back to its rig's measured scale. Pure functions: the
API gathers the inputs.
"""

from statistics import median
from typing import Any, Dict, Iterable, Optional

from app.utils.optics import SAMPLING_MAX_RATIO, SAMPLING_MIN_RATIO

MEASURED = "OK"   # the only status whose values are AstroCat-measured


def resolve_scale(image_scale: Optional[float], rig_scale: Optional[float]):
    """(arcsec/px, source) with source IMAGE | RIG | None."""
    for value, source in ((image_scale, "IMAGE"), (rig_scale, "RIG")):
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < v < 3600:
            return v, source
    return None, None


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
