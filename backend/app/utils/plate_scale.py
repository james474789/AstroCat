"""
Plate scale (arcsec per image pixel) from FITS header keywords and solver
results.

Pure functions over a header mapping (an astropy Header or the stored
raw_header dict), so the extractors and the data repair derive the same
answer. Three header traps this guards against, all seen in real libraries:

- RESOLUTN/RESOUNIT: PixInsight writes the print resolution (72 per inch).
  It is not a plate scale and is never read here.
- IMAGEW/IMAGEH: ZWO ASIAIR plate-solves a downsampled copy (e.g. 1164x880
  of a 4656x3520 frame) and writes that copy's WCS into the full-size file,
  so CD/CRPIX refer to the small grid. See wcs_frame().
- astrometry.net solves of a downsampled upload: its pixscale is per
  upload pixel. See solved_pixel_scale().

XPIXSZ/FOCALLEN is only a last resort, never an override: capture software
often leaves FOCALLEN at a default (SGP writes 50 mm for a 678 mm scope)
while its solved SCALE is right. Values outside MIN_SCALE..MAX_SCALE are
rejected outright; a residual mismatch against the rig's measured scale is
handled at read time by the Q1 guard (star_quality.resolve_scale).
"""

import math
from typing import Any, Mapping, Optional, Tuple

# arcsec per radian / 1000 (um pixel over mm focal length)
ARCSEC_UM_PER_MM = 206.265

MIN_SCALE = 0.01    # arcsec/px; below this is not a real imaging system
MAX_SCALE = 3600.0  # 1 deg/px; above this is not a real imaging system

# Header keywords that carry an arcsec/px value directly. RESOLUTN is
# deliberately absent (see module docstring).
SCALE_KEYWORDS = ("PIXSCALE", "SCALE", "SECPIX", "SECPIX1")

# Scale source labels
SOURCE_WCS = "WCS"
SOURCE_KEYWORD = "KEYWORD"
SOURCE_OPTICS = "OPTICS"


def header_num(header: Mapping, key: str) -> Optional[float]:
    """A finite float for `key`, or None (missing, unparsable, or unreadable card)."""
    try:
        value = header.get(key)
    except Exception:
        return None
    if value is None or isinstance(value, (bool, Exception)):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def with_image_size(header: Mapping, width: Optional[int], height: Optional[int]) -> Mapping:
    """
    `header` with NAXIS1/NAXIS2 filled from the image size when absent (XISF
    keywords don't carry them). Returns a copy only when something is added.
    """
    missing = {k: v for k, v in (("NAXIS1", width), ("NAXIS2", height))
               if v and header_num(header, k) is None}
    if not missing:
        return header
    return {**dict(header), **missing}


def plausible_scale(scale: Optional[float]) -> Optional[float]:
    if scale is None:
        return None
    return scale if MIN_SCALE <= scale <= MAX_SCALE else None


def wcs_matrix_scale(header: Mapping) -> Optional[float]:
    """
    arcsec per WCS-grid pixel from the linear transform: the CD matrix when
    any CD card is present (it overrides CDELT per the FITS standard), else
    CDELT combined with PC (identity when absent). Geometric mean of the two
    axis scales, so rotated and slightly non-square solutions both work.
    """
    cd = {k: header_num(header, f"CD{k}") for k in ("1_1", "1_2", "2_1", "2_2")}
    if any(v is not None for v in cd.values()):
        m11, m12, m21, m22 = (cd[k] or 0.0 for k in ("1_1", "1_2", "2_1", "2_2"))
    else:
        cdelt1 = header_num(header, "CDELT1")
        cdelt2 = header_num(header, "CDELT2")
        if not cdelt1 and not cdelt2:
            return None
        cdelt1 = cdelt1 or cdelt2
        cdelt2 = cdelt2 or cdelt1
        pc = {k: header_num(header, f"PC{k}") for k in ("1_1", "1_2", "2_1", "2_2")}
        p11 = pc["1_1"] if pc["1_1"] is not None else 1.0
        p22 = pc["2_2"] if pc["2_2"] is not None else 1.0
        p12, p21 = pc["1_2"] or 0.0, pc["2_1"] or 0.0
        m11, m12, m21, m22 = cdelt1 * p11, cdelt1 * p12, cdelt2 * p21, cdelt2 * p22

    sx = math.hypot(m11, m21)  # degrees per pixel along axis 1
    sy = math.hypot(m12, m22)  # degrees per pixel along axis 2
    if sx > 0 and sy > 0:
        return math.sqrt(sx * sy) * 3600.0
    s = sx or sy
    return s * 3600.0 if s > 0 else None


def wcs_frame(header: Mapping) -> Tuple[Optional[float], Optional[float]]:
    """
    (width, height) of the pixel grid the header WCS refers to.

    Normally NAXIS1 x NAXIS2. ZWO ASIAIR solves a downsampled copy and records
    its size as IMAGEW x IMAGEH; the WCS then belongs to that smaller grid,
    which shows as CRPIX lying inside it. Software that later rewrites the WCS
    for the full frame (e.g. PixInsight registration) leaves IMAGEW behind
    but moves CRPIX out of the small grid, so CRPIX decides.
    """
    n1, n2 = header_num(header, "NAXIS1"), header_num(header, "NAXIS2")
    iw, ih = header_num(header, "IMAGEW"), header_num(header, "IMAGEH")
    cx, cy = header_num(header, "CRPIX1"), header_num(header, "CRPIX2")
    if (n1 and n2 and iw and ih and cx is not None and cy is not None
            and 0 < iw < n1 and 0 < ih < n2
            and abs(iw / n1 - ih / n2) <= 0.02 * (iw / n1)
            and 0 <= cx <= iw + 1 and 0 <= cy <= ih + 1):
        return iw, ih
    return n1, n2


def wcs_pixel_scale(header: Mapping) -> Optional[float]:
    """arcsec per *image* pixel from the header WCS, corrected for a downsampled solve grid."""
    grid_scale = wcs_matrix_scale(header)
    if not grid_scale:
        return None
    n1 = header_num(header, "NAXIS1")
    fw, _ = wcs_frame(header)
    if n1 and fw and fw != n1:
        return grid_scale * fw / n1
    return grid_scale


def optics_scale(header: Mapping) -> Optional[float]:
    """arcsec/px from XPIXSZ (um, as written: already binned) and FOCALLEN (mm)."""
    pix = header_num(header, "XPIXSZ")
    if pix is None:
        pix = header_num(header, "PIXSIZE1")
    focal = header_num(header, "FOCALLEN")
    if not pix or not focal or pix <= 0 or focal <= 0:
        return None
    return plausible_scale(pix / focal * ARCSEC_UM_PER_MM)


def keyword_scale(header: Mapping) -> Optional[float]:
    for key in SCALE_KEYWORDS:
        v = plausible_scale(header_num(header, key))
        if v:
            return v
    return None


def header_pixel_scale(header: Mapping) -> Tuple[Optional[float], Optional[str]]:
    """
    (arcsec/px, source) for an image from its header: the WCS (a measured
    solution), else an explicit scale keyword, else XPIXSZ/FOCALLEN optics.
    Implausible values are skipped. (None, None) when nothing usable is present.
    """
    for scale, source in ((plausible_scale(wcs_pixel_scale(header)), SOURCE_WCS),
                          (keyword_scale(header), SOURCE_KEYWORD),
                          (optics_scale(header), SOURCE_OPTICS)):
        if scale:
            return scale, source
    return None, None


def solved_pixel_scale(calibration: Mapping[str, Any], width_pixels: Optional[int],
                       height_pixels: Optional[int]) -> Optional[float]:
    """
    arcsec per *original* pixel from an astrometry.net calibration.

    The solver is sent a downsampled JPEG, so its `pixscale` is per JPEG
    pixel. The field's angular size does not depend on the resampling:
    use width_arcsec / width, else the field radius (half-diagonal, degrees)
    over the original half-diagonal, else pixscale as-is.
    """
    def num(key):
        try:
            v = float(calibration.get(key))
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) and v > 0 else None

    width_arcsec = num("width_arcsec")
    if width_arcsec and width_pixels:
        return width_arcsec / width_pixels
    radius = num("radius")
    if radius and width_pixels and height_pixels:
        return radius * 2 * 3600.0 / math.hypot(width_pixels, height_pixels)
    return num("pixscale")
