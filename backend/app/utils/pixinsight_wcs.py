"""
PixInsight astrometric solutions as FITS WCS cards.

ImageSolver writes its solution into the XISF as `PCL:AstrometricSolution:*`
properties and does not add WCS keywords to the FITS header, so an XISF
solved in PixInsight looks unsolved to a header-only reader. This turns those
properties into the equivalent linear TAN cards so the rest of the pipeline
(centre, scale, rotation, radius, sky overlay) can treat it like any header WCS.

PixInsight's image axes differ from FITS in two ways, both handled here:
- continuous coordinates with the origin at the top-left corner of the image,
  y increasing downward (FITS: first pixel centre is 1.0, y increasing upward);
- the 2x2 linear matrix maps image offsets (pixels) to tangent-plane offsets
  (degrees) in that top-down frame, so its second column changes sign.

Only the linear part is used; the spline distortion model is ignored.
"""

import re
from typing import Any, Dict, Mapping, Optional

from app.utils.sky_wcs import WCS_KEY

_PREFIX = "PCL:AstrometricSolution:"

# Marks a raw_header whose WCS cards were synthesised from the solution.
MARKER_KEY = "PIWCS"


def _prop(properties: Mapping, name: str):
    entry = properties.get(_PREFIX + name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def pixinsight_wcs_cards(properties: Optional[Mapping], height_pixels: Optional[int]) -> Optional[Dict[str, Any]]:
    """FITS WCS cards for the image's PixInsight solution, or None when it has none we can use."""
    if not properties or not height_pixels:
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
        (a, b), (c, d) = ((float(v) for v in row) for row in matrix)
    except (TypeError, ValueError, IndexError):
        return None

    if not (a * d - b * c):
        return None
    return {
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": ra,
        "CRVAL2": dec,
        "CRPIX1": x0 + 0.5,
        "CRPIX2": height_pixels - y0 + 0.5,
        "CD1_1": a,
        "CD1_2": -b,
        "CD2_1": c,
        "CD2_2": -d,
        "RADESYS": "ICRS",
    }


def with_pixinsight_wcs(header: Mapping, cards: Mapping[str, Any]) -> Dict[str, Any]:
    """`header` with its own WCS cards replaced by `cards` (stale CDELT/PC/SIP cards would corrupt the solution)."""
    kept = {k: v for k, v in header.items()
            if not (isinstance(k, str) and WCS_KEY.match(k) and not re.match(r"^NAXIS[12]?$", k))}
    kept.update(cards)
    kept[MARKER_KEY] = True
    return kept
