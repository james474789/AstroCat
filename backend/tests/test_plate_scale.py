"""
Plate scale derivation (app/utils/plate_scale.py), the FITS extractor's WCS
logic that uses it, and the 0009 pixel scale repair. Headers are trimmed
copies of real library rows that were stored with a wrong scale.
"""

import math
from types import SimpleNamespace

import pytest
from astropy.io import fits

from app.extractors.fits_extractor import FITSExtractor
from app.scripts.repair_pixel_scale import rederive_header_wcs
from app.services.data_migrations import REGISTRY
from app.utils.plate_scale import (
    SOURCE_KEYWORD, SOURCE_OPTICS, SOURCE_WCS, header_pixel_scale, optics_scale,
    solved_pixel_scale, wcs_frame, wcs_matrix_scale, wcs_pixel_scale, with_image_size,
)

ASI1600_346MM = 3.8 / 346.0 * 206.265  # 2.265"/px


# PixInsight-registered sub (image 59420): no WCS, print resolution 72/inch.
PIXINSIGHT_REGISTERED = {
    "NAXIS": 2, "NAXIS1": 4616, "NAXIS2": 3480, "RA": 312.101505, "DEC": 30.980482,
    "OBJCTRA": "20 48 24.361", "OBJCTDEC": "+30 58 49.74",
    "XPIXSZ": 3.8, "YPIXSZ": 3.8, "FOCALLEN": 345.0, "XBINNING": 1.0,
    "RESOLUTN": 72.0, "RESOUNIT": "inch", "PROGRAM": "PixInsight 1.8.8-7",
    "COMMENT": ["Registration with PixInsight 1.8.8-7"], "HISTORY": ["StarAlignment.inliers: 0.992"],
}

# ZWO ASIAIR light (image 42542): WCS solved on a 1164x880 copy of a 4656x3520 frame.
ASIAIR = {
    "NAXIS": 2, "NAXIS1": 4656, "NAXIS2": 3520, "IMAGEW": 1164.0, "IMAGEH": 880.0,
    "RA": 80.0373, "DEC": 33.9918, "CREATOR": "ZWO ASIAIR",
    "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
    "CRPIX1": 585.187911987, "CRPIX2": 342.168346405,
    "CRVAL1": 79.7185511821, "CRVAL2": 33.7122025229,
    "CD1_1": 0.00251317243902, "CD1_2": -0.0000173710413883,
    "CD2_1": 0.0000168429679611, "CD2_2": 0.00251581348134,
    "XPIXSZ": 3.79999995231628, "FOCALLEN": 346, "XBINNING": 1,
}

# ASIAIR frame re-registered by PixInsight (image 77836): the WCS was rewritten
# for the full frame (CRPIX outside the stale IMAGEW grid), RESOLUTN added.
ASIAIR_REREGISTERED = {
    **ASIAIR,
    "CRPIX1": 2328.5, "CRPIX2": 1760.5, "CRVAL1": 83.6341715518, "CRVAL2": 22.0,
    "CD1_1": 0.000631096166365, "CD1_2": -0.0000134317790101,
    "CD2_1": 0.0000134317790101, "CD2_2": 0.000631096166365,
    "CDELT1": 0.000628665264960219, "CDELT2": 0.000628665264960219,
    "RESOLUTN": 72.0, "RESOUNIT": "inch", "FOCALLEN": 346.17135,
}


def _extract(header):
    ext = FITSExtractor.__new__(FITSExtractor)
    ext.file_path = "test.fits"
    return ext._extract_wcs(header)


# --- matrix scale -----------------------------------------------------------

def test_cd_matrix_takes_precedence_over_cdelt():
    h = {"CD1_1": -0.0005, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.0005, "CDELT1": 0.02, "CDELT2": 0.02}
    assert wcs_matrix_scale(h) == pytest.approx(1.8)


def test_rotated_cd_matrix_uses_both_columns():
    # 90 deg rotation: CD1_1 and CD2_2 ~ 0, scale lives in the off-diagonal.
    h = {"CD1_1": -4.7e-7, "CD1_2": 0.0005, "CD2_1": 0.0005, "CD2_2": 4.7e-7}
    assert wcs_matrix_scale(h) == pytest.approx(1.8, rel=1e-3)


def test_cdelt_with_pc_matrix():
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    h = {"CDELT1": -0.0005, "CDELT2": 0.0005, "PC1_1": c, "PC1_2": -s, "PC2_1": s, "PC2_2": c}
    assert wcs_matrix_scale(h) == pytest.approx(1.8)


def test_no_matrix_is_none():
    assert wcs_matrix_scale({"CRVAL1": 10.0, "CRVAL2": 20.0}) is None


def test_string_values_from_xisf_keywords():
    assert wcs_matrix_scale({"CDELT1": "-0.0005", "CDELT2": "0.0005"}) == pytest.approx(1.8)
    assert optics_scale({"XPIXSZ": "6.54", "FOCALLEN": "101.30127"}) == pytest.approx(13.316, rel=1e-3)


# --- solve grid (ASIAIR IMAGEW/IMAGEH) ---------------------------------------

def test_asiair_wcs_refers_to_downsampled_grid():
    assert wcs_frame(ASIAIR) == (1164.0, 880.0)
    assert wcs_matrix_scale(ASIAIR) == pytest.approx(9.05, rel=1e-3)
    assert wcs_pixel_scale(ASIAIR) == pytest.approx(9.05 / 4, rel=1e-3)


def test_stale_imagew_ignored_when_crpix_outside_small_grid():
    assert wcs_frame(ASIAIR_REREGISTERED) == (4656, 3520)
    assert wcs_pixel_scale(ASIAIR_REREGISTERED) == pytest.approx(2.27, rel=1e-2)


def test_imagew_equal_to_naxis_is_plain_frame():
    h = {**ASIAIR, "IMAGEW": 4656, "IMAGEH": 3520}
    assert wcs_frame(h) == (4656, 3520)


# --- header_pixel_scale -----------------------------------------------------

def test_resolutn_print_dpi_is_never_a_scale():
    assert header_pixel_scale({"RESOLUTN": 72.0, "RESOUNIT": "inch"}) == (None, None)
    scale, source = header_pixel_scale(PIXINSIGHT_REGISTERED)
    assert source == SOURCE_OPTICS
    assert scale == pytest.approx(3.8 / 345.0 * 206.265)


def test_keyword_scale_beats_default_focallen():
    # SGP (image 55517): FOCALLEN left at its 50 mm default, SCALE is the real 678 mm scope.
    h = {"NAXIS1": 4656, "NAXIS2": 3520, "SCALE": 1.15514, "PIXSCALE": 1.15514, "XPIXSZ": 3.8, "FOCALLEN": 50}
    assert header_pixel_scale(h) == (1.15514, SOURCE_KEYWORD)


def test_wcs_preferred_over_optics():
    # 2x2 binned frame whose XPIXSZ was written unbinned: the solved WCS is right.
    h = {"NAXIS1": 2328, "NAXIS2": 1760, "CDELT1": 2 * ASI1600_346MM / 3600, "XPIXSZ": 3.8, "FOCALLEN": 346}
    scale, source = header_pixel_scale(h)
    assert source == SOURCE_WCS
    assert scale == pytest.approx(2 * ASI1600_346MM)


def test_cd_matrix_beats_stale_cdelt():
    # Astrometrica + PixInsight (image 79398): CDELT is stale, CD matches the optics.
    h = {"NAXIS1": 4144, "NAXIS2": 2822, "CDELT1": 0.00049013154794493, "CDELT2": 0.00049013154794493,
         "CD1_1": 0.000293804407801, "CD1_2": -0.0000012202103485,
         "CD2_1": 0.00000125831501996, "CD2_2": 0.000293872932069, "XPIXSZ": 4.63, "FOCALLEN": 900.0}
    scale, source = header_pixel_scale(h)
    assert source == SOURCE_WCS
    assert scale == pytest.approx(4.63 / 900 * 206.265, rel=0.01)


def test_keyword_scale_used_without_optics():
    assert header_pixel_scale({"PIXSCALE": 1.23}) == (1.23, SOURCE_KEYWORD)


def test_implausible_values_rejected():
    assert header_pixel_scale({"CDELT1": 5.0, "NAXIS1": 100, "NAXIS2": 100}) == (None, None)  # 18000"/px
    assert header_pixel_scale({"PIXSCALE": 0.0}) == (None, None)


# --- extractor --------------------------------------------------------------

def test_extractor_pixinsight_registered_sub_gets_optics_scale():
    wcs = _extract(PIXINSIGHT_REGISTERED)
    assert wcs["pixel_scale"] == pytest.approx(2.272, rel=1e-3)
    assert wcs["wcs_type"] == "HEADER_FALLBACK"
    assert wcs["radius_degrees"] == pytest.approx(math.hypot(4616, 3480) / 2 * 2.272 / 3600, rel=1e-3)


def test_extractor_asiair_scale_center_and_radius():
    wcs = _extract(ASIAIR)
    assert wcs["wcs_type"] == "HEADER_WCS"
    assert wcs["pixel_scale"] == pytest.approx(2.263, rel=1e-3)
    # Center of the solve grid is near CRPIX, so near CRVAL (not ~6 deg away).
    assert wcs["ra_center"] == pytest.approx(79.72, abs=0.05)
    assert wcs["dec_center"] == pytest.approx(33.71, abs=0.3)
    expected_radius = math.hypot(4656, 3520) / 2 * 2.263 / 3600
    assert wcs["radius_degrees"] == pytest.approx(expected_radius, rel=0.02)


def test_extractor_reregistered_asiair_uses_full_frame_wcs():
    wcs = _extract(ASIAIR_REREGISTERED)
    assert wcs["wcs_type"] == "HEADER_WCS"
    assert wcs["pixel_scale"] == pytest.approx(2.27, rel=1e-2)
    assert wcs["ra_center"] == pytest.approx(83.634, abs=0.01)


def test_extractor_accepts_astropy_header():
    header = fits.Header()
    for k, v in ASIAIR.items():
        header[k] = v
    wcs = _extract(header)
    assert wcs["pixel_scale"] == pytest.approx(2.263, rel=1e-3)


def test_extractor_unknown_scale_is_none_not_zero():
    wcs = _extract({"NAXIS1": 100, "NAXIS2": 100, "RA": 10.0, "DEC": 20.0})
    assert wcs["pixel_scale"] is None


def test_xisf_header_without_naxis_uses_image_size():
    # XISF mosaic (image 505): string keywords, no NAXIS cards.
    h = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRPIX1": "4474.003644", "CRPIX2": "5109.840475",
         "CRVAL1": "298.334232494", "CRVAL2": "27.9409963229",
         "CD1_1": "-0.00465543649836", "CD1_2": "0.00120281232176",
         "CD2_1": "0.00116819848098", "CD2_2": "0.00466844777544", "XPIXSZ": "4.400", "FOCALLEN": "52.41"}
    assert _extract(h)["wcs_type"] == "HEADER_CRVAL"
    wcs = _extract(with_image_size(h, 8947, 10221))
    assert wcs["wcs_type"] == "HEADER_WCS"
    assert wcs["pixel_scale"] == pytest.approx(17.317, rel=1e-3)
    assert wcs["radius_degrees"] == pytest.approx(math.hypot(8947, 10221) / 2 * 17.317 / 3600, rel=0.01)
    assert "NAXIS1" not in h


def test_with_image_size_keeps_existing_naxis():
    h = {"NAXIS1": 10, "NAXIS2": 20}
    assert with_image_size(h, 99, 99) is h


# --- astrometry.net calibration ---------------------------------------------

def test_solved_scale_prefers_width_arcsec():
    cal = {"pixscale": 79.74, "width_arcsec": 68000.0, "radius": 11.26}
    assert solved_pixel_scale(cal, 5130, 3295) == pytest.approx(68000.0 / 5130)


def test_solved_scale_from_radius_for_downsampled_upload():
    # Image 93827: Canon 6D at 101 mm (~13.3"/px), solved from a small JPEG.
    cal = {"pixscale": 79.7399289409468, "radius": 11.25910426695667}
    assert solved_pixel_scale(cal, 5130, 3295) == pytest.approx(13.30, abs=0.02)


def test_solved_scale_falls_back_to_pixscale():
    assert solved_pixel_scale({"pixscale": 1.5}, None, None) == 1.5
    assert solved_pixel_scale({}, 100, 100) is None


# --- repair -----------------------------------------------------------------

def _row(raw_header, **cols):
    base = dict(id=1, file_path="/data/x.fit", raw_header=raw_header, width_pixels=raw_header.get("NAXIS1"),
                height_pixels=raw_header.get("NAXIS2"), pixel_scale_arcsec=None, ra_center_degrees=None,
                dec_center_degrees=None, field_radius_degrees=None, rotation_degrees=None)
    base.update(cols)
    return SimpleNamespace(**base)


def test_repair_fixes_resolutn_scale_and_is_idempotent():
    wcs = _extract(PIXINSIGHT_REGISTERED)
    row = _row(PIXINSIGHT_REGISTERED, pixel_scale_arcsec=72.0, ra_center_degrees=wcs["ra_center"],
               dec_center_degrees=wcs["dec_center"], field_radius_degrees=wcs["radius_degrees"])
    changes = rederive_header_wcs(row)
    assert changes == {"pixel_scale_arcsec": pytest.approx(2.272, rel=1e-3)}
    row.pixel_scale_arcsec = changes["pixel_scale_arcsec"]
    assert rederive_header_wcs(row) == {}


def test_repair_moves_asiair_center_and_radius():
    row = _row(ASIAIR, pixel_scale_arcsec=9.05, ra_center_degrees=85.9, dec_center_degrees=38.0,
               field_radius_degrees=7.32)
    changes = rederive_header_wcs(row)
    assert changes["pixel_scale_arcsec"] == pytest.approx(2.263, rel=1e-3)
    assert changes["ra_center_degrees"] == pytest.approx(79.72, abs=0.05)
    assert changes["field_radius_degrees"] < 2.0
    assert "rotation_degrees" in changes


def test_repair_clears_zero_scale():
    header = {"NAXIS1": 100, "NAXIS2": 100, "RA": 10.0, "DEC": 20.0}
    wcs = _extract(header)
    row = _row(header, pixel_scale_arcsec=0.0, ra_center_degrees=10.0, dec_center_degrees=20.0,
               field_radius_degrees=wcs["radius_degrees"])
    assert rederive_header_wcs(row) == {"pixel_scale_arcsec": None}


def test_repair_registered_after_existing_migrations():
    ids = [spec.id for spec in REGISTRY]
    assert ids.index("0008_fill_utc_default_site") < ids.index("0009_repair_pixel_scale")
