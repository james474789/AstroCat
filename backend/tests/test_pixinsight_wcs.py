import math

import numpy as np
import pytest

from app.extractors.fits_extractor import FITSExtractor
from app.utils.pixinsight_wcs import MARKER_KEY, pixinsight_wcs_cards, with_pixinsight_wcs

# Values from a real ImageSolver 6.4.2 solution (16368 x 4753 mosaic).
HEIGHT = 4753
PROPS = {
    "PCL:AstrometricSolution:ProjectionSystem": {"value": "Gnomonic"},
    "PCL:AstrometricSolution:ReferenceCelestialCoordinates": {"value": np.array([308.5419068, 42.98591909])},
    "PCL:AstrometricSolution:ReferenceImageCoordinates": {"value": np.array([8150.73365101, 2359.1252103])},
    "PCL:AstrometricSolution:LinearTransformationMatrix": {
        "value": np.array([[-0.00274, 0.00419103], [-0.00428891, -0.00271187]])},
}


def test_cards_flip_to_fits_axes():
    cards = pixinsight_wcs_cards(PROPS, HEIGHT)
    assert cards["CRVAL1"] == pytest.approx(308.5419068)
    assert cards["CRPIX1"] == pytest.approx(8151.23365101)
    assert cards["CRPIX2"] == pytest.approx(HEIGHT - 2359.1252103 + 0.5)
    # Top-down -> bottom-up flips the second column only.
    assert (cards["CD1_1"], cards["CD1_2"]) == pytest.approx((-0.00274, -0.00419103))
    assert (cards["CD2_1"], cards["CD2_2"]) == pytest.approx((-0.00428891, 0.00271187))
    # A normal (non-mirrored) sky image has a negative CD determinant.
    assert cards["CD1_1"] * cards["CD2_2"] - cards["CD1_2"] * cards["CD2_1"] < 0


def test_extractor_reads_solution_over_pointing_keywords():
    header = with_pixinsight_wcs({"OBJCTRA": "20 45 03.379", "OBJCTDEC": "+50 38 05.38", "CDELT1": 1.0},
                                 pixinsight_wcs_cards(PROPS, HEIGHT))
    header["NAXIS1"], header["NAXIS2"] = 16368, HEIGHT
    assert "CDELT1" not in header and header[MARKER_KEY] is True

    ext = FITSExtractor.__new__(FITSExtractor)
    ext.file_path = "x.xisf"
    wcs = ext._extract_wcs(header)

    # Centre is near the reference point, not the 311.26/50.63 mount pointing.
    assert wcs["ra_center"] == pytest.approx(308.5, abs=2.0)
    assert wcs["dec_center"] == pytest.approx(43.0, abs=2.0)
    assert wcs["pixel_scale"] == pytest.approx(math.sqrt(abs(-0.00274 * 0.00271187 - 0.00419103 * 0.00428891)) * 3600, rel=1e-3)
    assert wcs["rotation"] == pytest.approx(math.degrees(math.atan2(0.00419103, 0.00271187)), abs=1e-6)


@pytest.mark.parametrize("props, height", [
    (None, HEIGHT),
    ({}, HEIGHT),
    (PROPS, None),
    ({**PROPS, "PCL:AstrometricSolution:ProjectionSystem": {"value": "Stereographic"}}, HEIGHT),
    ({**PROPS, "PCL:AstrometricSolution:LinearTransformationMatrix": {"value": np.zeros((2, 2))}}, HEIGHT),
])
def test_no_usable_solution(props, height):
    assert pixinsight_wcs_cards(props, height) is None
