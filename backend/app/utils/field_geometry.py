"""
Field-of-view geometry helpers.
"""

import math
from typing import Optional


def field_radius_from_scale(
    width_pixels: Optional[int],
    height_pixels: Optional[int],
    pixel_scale_arcsec: Optional[float],
) -> Optional[float]:
    """Half-diagonal field radius in degrees, or None if any input is missing/non-positive."""
    if not width_pixels or not height_pixels or not pixel_scale_arcsec:
        return None
    if width_pixels <= 0 or height_pixels <= 0 or pixel_scale_arcsec <= 0:
        return None
    diagonal = math.sqrt(width_pixels ** 2 + height_pixels ** 2)
    return (diagonal / 2.0) * pixel_scale_arcsec / 3600.0


def effective_field_radius(
    radius_degrees: Optional[float],
    width_pixels: Optional[int],
    height_pixels: Optional[int],
    pixel_scale_arcsec: Optional[float],
) -> Optional[float]:
    """
    Return `radius_degrees` if it's a usable positive value, otherwise derive
    it from pixel scale and image dimensions. Sidecar .ini solves often omit
    the radius (parsed as 0), which would otherwise disable target MATCH
    resolution for the image.
    """
    if radius_degrees and radius_degrees > 0:
        return radius_degrees
    return field_radius_from_scale(width_pixels, height_pixels, pixel_scale_arcsec)
