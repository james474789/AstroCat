import math

import numpy as np
import pytest
from astropy.wcs import WCS

from app.utils.pixinsight_wcs import (
    MARKER_KEY, MARKER_VERSION, pixinsight_field, pixinsight_scale_rotation, pixinsight_wcs_cards,
    with_pixinsight_wcs,
)
from app.utils.sky_wcs import wcs_cards

P = "PCL:AstrometricSolution:"
G = P + "SplineWorldTransformation:PointGridInterpolation:ImageToNative:"

# Linear part of a real ImageSolver 6.4.2 solution (16368 x 4753 mosaic).
WIDTH, HEIGHT = 16368, 4753
MATRIX = np.array([[-0.00274, 0.00419103], [-0.00428891, -0.00271187]])
REF_IMG = np.array([8150.73365101, 2359.1252103])
REF_SKY = np.array([308.5419068, 42.98591909])
PROPS = {
    P + "ProjectionSystem": {"value": "Gnomonic"},
    P + "ReferenceCelestialCoordinates": {"value": REF_SKY},
    P + "ReferenceImageCoordinates": {"value": REF_IMG},
    P + "LinearTransformationMatrix": {"value": MATRIX},
}


def test_linear_cards_keep_top_down_rows():
    cards = pixinsight_wcs_cards(PROPS, WIDTH, HEIGHT)
    assert cards["CTYPE1"] == "RA---TAN"
    assert cards["CRVAL1"] == pytest.approx(REF_SKY[0])
    # PixInsight pixel (x, y) is FITS pixel (x + 0.5, y + 0.5): no row flip.
    assert (cards["CRPIX1"], cards["CRPIX2"]) == pytest.approx(tuple(REF_IMG + 0.5))
    assert (cards["CD1_1"], cards["CD1_2"], cards["CD2_1"], cards["CD2_2"]) == pytest.approx(tuple(MATRIX.ravel()))


def test_scale_and_rotation_as_pixinsight_reports():
    scale, rotation = pixinsight_scale_rotation(PROPS)
    assert scale == pytest.approx(18.147, abs=0.005)  # PixInsight's "Resolution"
    # PixInsight reports -56.7 for this solution; AstroCat's convention has the opposite sign.
    assert rotation == pytest.approx(math.degrees(math.atan2(0.00419103, 0.00271187)))


def test_header_merge_drops_stale_wcs_and_marks_version():
    header = with_pixinsight_wcs({"OBJCTRA": "20 45 03", "CDELT1": 1.0, "PC1_1": 1.0, "NAXIS1": 10},
                                 pixinsight_wcs_cards(PROPS, WIDTH, HEIGHT))
    assert "CDELT1" not in header and "PC1_1" not in header
    assert header["NAXIS1"] == 10 and header["OBJCTRA"] == "20 45 03"
    assert header[MARKER_KEY] == MARKER_VERSION


def _distorted_props(width=4000, height=3000, delta=8.0):
    """A single-frame-like solution whose grid holds a smooth (cubic) distortion."""
    scale = 2.0 / 3600.0
    m = np.array([[-scale, 0.0], [0.0, -scale]])
    x0, y0 = width / 2, height / 2
    gx_n, gy_n = int(width / delta) + 1, int(height / delta) + 1
    xs = np.arange(gx_n) * delta
    ys = np.arange(gy_n) * delta
    X, Y = np.meshgrid(xs, ys)
    u, v = X - x0, Y - y0
    r2 = (u * u + v * v) / (width / 2) ** 2
    ud, vd = u * (1 + 0.002 * r2), v * (1 + 0.002 * r2)  # barrel distortion, ~4 px at the corners
    l = m[0, 0] * ud + m[0, 1] * vd
    b = m[1, 0] * ud + m[1, 1] * vd
    props = {
        P + "ProjectionSystem": {"value": "Gnomonic"},
        P + "ReferenceCelestialCoordinates": {"value": np.array([83.8, -5.4])},
        P + "ReferenceImageCoordinates": {"value": np.array([x0, y0])},
        P + "LinearTransformationMatrix": {"value": m},
        G + "GridX": {"value": l},
        G + "GridY": {"value": b},
        G + "Delta": {"value": delta},
        G + "Rect": {"value": np.array([0.0, 0.0, width, height])},
    }
    return props, (width, height), (X, Y, l, b)


def test_distortion_grid_fitted_as_sip():
    props, (w, h), (X, Y, l, b) = _distorted_props()
    cards = pixinsight_wcs_cards(props, w, h)
    assert cards["CTYPE1"] == "RA---TAN-SIP" and cards["A_ORDER"] >= 3

    wcs = WCS(wcs_cards(cards), naxis=2)
    # Sky from the grid's native coordinates through a plain TAN at the reference...
    tan = WCS(naxis=2)
    tan.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    tan.wcs.crval = [83.8, -5.4]
    tan.wcs.crpix = [1, 1]
    tan.wcs.cd = np.eye(2)
    sel = (slice(None, None, 25), slice(None, None, 25))
    ra, dec = tan.all_pix2world(l[sel].ravel(), b[sel].ravel(), 0)  # 0-based: offset from CRPIX is (l, b)
    # ...must land back on the grid pixel through the fitted cards (astropy 0-based = continuous - 0.5).
    px, py = wcs.all_world2pix(ra, dec, 0)
    err = np.hypot(px + 0.5 - X[sel].ravel(), py + 0.5 - Y[sel].ravel())
    assert np.median(err) < 0.5 and err.max() < 1.5


def test_field_centre_from_grid():
    props, (w, h), _ = _distorted_props()
    ra, dec, radius = pixinsight_field(props, pixinsight_wcs_cards(props, w, h), w, h)
    assert (ra, dec) == pytest.approx((83.8, -5.4), abs=1e-6)  # grid centre is the tangent point
    half_diag_deg = math.hypot(w / 2, h / 2) * 2.0 / 3600.0
    assert radius == pytest.approx(half_diag_deg * 1.002, rel=0.01)


@pytest.mark.parametrize("props, size", [
    (None, (WIDTH, HEIGHT)),
    ({}, (WIDTH, HEIGHT)),
    (PROPS, (WIDTH, None)),
    ({**PROPS, P + "ProjectionSystem": {"value": "Stereographic"}}, (WIDTH, HEIGHT)),
    ({**PROPS, P + "LinearTransformationMatrix": {"value": np.zeros((2, 2))}}, (WIDTH, HEIGHT)),
])
def test_no_usable_solution(props, size):
    assert pixinsight_wcs_cards(props, *size) is None
