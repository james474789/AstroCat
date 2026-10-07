import pytest

from app.extractors.ini_parser import SidecarParser

ASTAP_SOLVED = """PLTSOLVD=T
CRPIX1= 2.7365000000000000E+003
CRPIX2= 1.8245000000000000E+003
CRVAL1= 3.1541876883120699E+002
CRVAL2= 4.4030690739232476E+001
CDELT1=-1.9277874135544123E-003
CDELT2= 1.9294315232716434E-003
CROTA1= 8.2497810225578519E+001
CROTA2= 8.2472152697455670E+001
CD1_1=-2.5169979766509774E-004
CD1_2=-1.9112853067279516E-003
CD2_1=-1.9128023414620393E-003
CD2_2= 2.5277105350825587E-004
CMDLINE="C:\\Program Files\\astap\\astap.exe" -f "x.jpg" -fov 0 -r 180 -z 0
DIMENSIONS=5472 x 3648
WARNING=Warning inexact scale! Set FOV=7.04d or scale=6.9"/pix
"""

ASTAP_FAILED = """PLTSOLVD=F
CMDLINE="C:\\Program Files\\astap\\astap.exe" -f "x.tif" -fov 0 -r 180 -z 0
DIMENSIONS=568 x 456
ERROR=Not enough stars.
"""

ASTROMETRY_NET = """[Astrometry]
job_id = 434
ra = 205.96387616298142
dec = 47.5419749791008
radius = 6.337493265886427
pixscale = 6.938306759532768
orientation = 357.3665416173576
parity = 1.0

[WCS]
cd1_1 = -0.00192757845412
"""


def _parse(tmp_path, text):
    (tmp_path / "img.ini").write_text(text)
    return SidecarParser.parse(tmp_path / "img.jpg")


def test_astap_solved_ini_yields_position_scale_and_rotation(tmp_path):
    wcs = _parse(tmp_path, ASTAP_SOLVED)
    assert wcs["ra_center"] == pytest.approx(315.41876883)
    assert wcs["dec_center"] == pytest.approx(44.03069074)
    assert wcs["pixel_scale"] == pytest.approx(6.946, abs=0.01)
    # CD-matrix rotation agrees with ASTAP's own CROTA2.
    assert wcs["rotation"] == pytest.approx(82.472, abs=0.01)
    assert wcs["wcs_type"] == "SIDECAR_INI"


def test_astap_rotation_falls_back_to_crota2_without_cd_matrix(tmp_path):
    text = "\n".join(l for l in ASTAP_SOLVED.splitlines() if not l.startswith("CD"))
    assert _parse(tmp_path, text)["rotation"] == pytest.approx(82.472, abs=0.001)


def test_astap_failed_solve_is_not_a_solution(tmp_path):
    assert _parse(tmp_path, ASTAP_FAILED) is None


def test_astrometry_net_ini_keeps_orientation_as_rotation(tmp_path):
    wcs = _parse(tmp_path, ASTROMETRY_NET)
    assert wcs["rotation"] == pytest.approx(357.3665, abs=0.001)
    assert wcs["pixel_scale"] == pytest.approx(6.9383, abs=0.001)
    assert wcs["radius_degrees"] == pytest.approx(6.3375, abs=0.001)


def test_astrometry_net_ini_without_orientation_has_no_rotation(tmp_path):
    text = "\n".join(l for l in ASTROMETRY_NET.splitlines() if not l.startswith("orientation"))
    assert "rotation" not in _parse(tmp_path, text)


def _extract(header_dict):
    from pathlib import Path
    from astropy.io import fits
    from app.extractors.fits_extractor import FITSExtractor

    ext = FITSExtractor.__new__(FITSExtractor)
    ext.file_path = Path("x.fits")
    return ext._extract_wcs(fits.Header(header_dict))


def test_pointing_only_header_has_unknown_rotation():
    wcs = _extract({"OBJCTRA": "20 50 14.386", "OBJCTDEC": "+45 09 28.89"})
    assert wcs["ra_center"] is not None
    assert wcs["rotation"] is None


def test_rotation_keyword_still_used_without_wcs():
    wcs = _extract({"OBJCTRA": "20 50 14.386", "OBJCTDEC": "+45 09 28.89", "POSANGLE": 33.5})
    assert wcs["rotation"] == pytest.approx(33.5)


def test_cd_matrix_rotation_survives_unbuildable_wcs():
    # TAN-SIP without SIP coefficients: astropy WCS can't be built, matrix is still readable.
    wcs = _extract({"CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP", "CRVAL1": 313.2, "CRVAL2": 44.67,
                    "CRPIX1": 3715.0, "CRPIX2": 2577.0, "CD1_1": 2.2172e-05, "CD1_2": 0.0018047,
                    "CD2_1": -0.0018063, "CD2_2": 2.2749e-05})
    assert wcs["rotation"] == pytest.approx(-89.3, abs=0.1)


def test_explicit_zero_rotator_keyword_is_kept():
    wcs = _extract({"OBJCTRA": "20 50 14.386", "OBJCTDEC": "+45 09 28.89", "ROTATANG": 0.0})
    assert wcs["rotation"] == 0.0
