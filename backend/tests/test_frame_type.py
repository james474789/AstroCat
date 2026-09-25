"""
Tests for the F1 frame-type classifier (backend/app/services/frame_type.py).

Pure function, no DB. Covers:
- Every real-world header value listed in docs/design/F1-frame-types.md §3.3.
- Precedence: header beats path, file name beats directory, nearest directory
  beats farther directory.
- The false-positive directory guard cases.
- Case-insensitive header keys, non-string header values, raw_header=None.
- The master hint.
"""

import pytest

from app.config import settings
from app.models.image import FrameType
from app.services.frame_type import FrameTypeResult, classify_frame_type


@pytest.fixture(autouse=True)
def _mount_roots(monkeypatch):
    """Configure a predictable mount root for path-based classification tests."""
    monkeypatch.setattr(settings, "image_paths", "/data/mount1,/data/mount2")
    yield


# ---------------------------------------------------------------------------
# Step 1: header values
# ---------------------------------------------------------------------------

NINA_VALUES = [
    ("LIGHT", FrameType.LIGHT),
    ("DARK", FrameType.DARK),
    ("FLAT", FrameType.FLAT),
    ("BIAS", FrameType.BIAS),
    ("DARKFLAT", FrameType.DARK_FLAT),
]

SGP_MAXIM_VALUES = [
    ("Light Frame", FrameType.LIGHT),
    ("Dark Frame", FrameType.DARK),
    ("Flat Field", FrameType.FLAT),
    ("Flat Frame", FrameType.FLAT),
    ("Bias Frame", FrameType.BIAS),
]

INDI_EKOS_KSTARS_VALUES = [
    ("Light Frame", FrameType.LIGHT),
    ("Dark Frame", FrameType.DARK),
    ("Flat Frame", FrameType.FLAT),
    ("Bias Frame", FrameType.BIAS),
]

ASIAIR_VALUES = [
    ("Light", FrameType.LIGHT),
    ("Dark", FrameType.DARK),
    ("Flat", FrameType.FLAT),
    ("Bias", FrameType.BIAS),
]

PIXINSIGHT_WBPP_VALUES = [
    ("Master Dark", FrameType.DARK),
    ("Master Flat", FrameType.FLAT),
    ("Master Bias", FrameType.BIAS),
    ("Master Light", FrameType.LIGHT),
]

APT_VALUES = [
    ("Light", FrameType.LIGHT),
    ("Dark", FrameType.DARK),
    ("Flat", FrameType.FLAT),
    ("Bias", FrameType.BIAS),
]

SIRIL_VALUES = [
    ("Offset", FrameType.BIAS),
]

ALL_HEADER_VALUES = (
    [("N.I.N.A.", v, ft) for v, ft in NINA_VALUES]
    + [("SGP/MaxIm", v, ft) for v, ft in SGP_MAXIM_VALUES]
    + [("INDI/Ekos/KStars", v, ft) for v, ft in INDI_EKOS_KSTARS_VALUES]
    + [("ASIAIR/ASIStudio", v, ft) for v, ft in ASIAIR_VALUES]
    + [("PixInsight WBPP", v, ft) for v, ft in PIXINSIGHT_WBPP_VALUES]
    + [("APT", v, ft) for v, ft in APT_VALUES]
    + [("Siril", v, ft) for v, ft in SIRIL_VALUES]
)


@pytest.mark.parametrize("program,value,expected", ALL_HEADER_VALUES,
                         ids=[f"{p}:{v}" for p, v, _ in ALL_HEADER_VALUES])
def test_header_values(program, value, expected):
    result = classify_frame_type({"IMAGETYP": value}, "/data/mount1/M31/img_0001.fits")
    assert result.frame_type == expected
    assert result.source == "HEADER"


def test_master_hint_set_when_master_present():
    result = classify_frame_type({"IMAGETYP": "Master Flat"}, "/data/mount1/img.fits")
    assert result.frame_type == FrameType.FLAT
    assert result.is_master_hint is True


def test_master_hint_not_set_when_absent():
    result = classify_frame_type({"IMAGETYP": "Dark Frame"}, "/data/mount1/img.fits")
    assert result.is_master_hint is False


@pytest.mark.parametrize("key", ["FRAMETYP", "FRAME", "IMAGETYPE"])
def test_alternate_header_keys(key):
    result = classify_frame_type({key: "DARK"}, "/data/mount1/img.fits")
    assert result.frame_type == FrameType.DARK
    assert result.source == "HEADER"


def test_header_key_priority_imagetyp_wins():
    # IMAGETYP is checked before FRAMETYP.
    result = classify_frame_type(
        {"IMAGETYP": "LIGHT", "FRAMETYP": "DARK"}, "/data/mount1/img.fits"
    )
    assert result.frame_type == FrameType.LIGHT


def test_case_insensitive_header_keys():
    result = classify_frame_type({"imagetyp": "DARK"}, "/data/mount1/img.fits")
    assert result.frame_type == FrameType.DARK
    assert result.source == "HEADER"


def test_non_string_header_value_int_falls_through():
    # An int value like a stray keyword can't match; should fall through
    # to filename/path/default without raising.
    result = classify_frame_type({"IMAGETYP": 42}, "/data/mount1/Darks/img.fits")
    assert result.frame_type == FrameType.DARK
    assert result.source == "PATH"


def test_none_header_value_is_skipped_for_next_key():
    result = classify_frame_type(
        {"IMAGETYP": None, "FRAMETYP": "FLAT"}, "/data/mount1/img.fits"
    )
    assert result.frame_type == FrameType.FLAT
    assert result.source == "HEADER"


def test_raw_header_none():
    result = classify_frame_type(None, "/data/mount1/M31_LIGHT_001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "FILENAME"


def test_unrecognized_header_value_falls_through():
    result = classify_frame_type({"IMAGETYP": "Tricolor"}, "/data/mount1/Darks/img.fits")
    assert result.frame_type == FrameType.DARK
    assert result.source == "PATH"


def test_unrecognized_header_value_focus_falls_through_to_default():
    result = classify_frame_type({"IMAGETYP": "Focus"}, "/data/mount1/M31/img_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "DEFAULT"


# ---------------------------------------------------------------------------
# Step 2: file name
# ---------------------------------------------------------------------------

def test_filename_flat_example():
    result = classify_frame_type(None, "/data/mount1/2026-03-14_M31_FLAT_Ha_1.2s_0001.fits")
    assert result.frame_type == FrameType.FLAT
    assert result.source == "FILENAME"


def test_filename_dark_example():
    result = classify_frame_type(None, "/data/mount1/DARK_300.00s_-10C_G100_0003.fits")
    assert result.frame_type == FrameType.DARK
    assert result.source == "FILENAME"


@pytest.mark.parametrize("stem,expected", [
    ("darkflat_001", FrameType.DARK_FLAT),
    ("darkflats_001", FrameType.DARK_FLAT),
    ("flatdark_001", FrameType.DARK_FLAT),
    ("flatdarks_001", FrameType.DARK_FLAT),
    ("df_001", FrameType.DARK_FLAT),
    ("dark_flat_001", FrameType.DARK_FLAT),
    ("flat_dark_001", FrameType.DARK_FLAT),
    ("bias_001", FrameType.BIAS),
    ("biases_001", FrameType.BIAS),
    ("offset_001", FrameType.BIAS),
    ("offsets_001", FrameType.BIAS),
    ("dark_001", FrameType.DARK),
    ("darks_001", FrameType.DARK),
    ("flat_001", FrameType.FLAT),
    ("flats_001", FrameType.FLAT),
    ("light_001", FrameType.LIGHT),
    ("lights_001", FrameType.LIGHT),
])
def test_filename_keywords(stem, expected):
    result = classify_frame_type(None, f"/data/mount1/{stem}.fits")
    assert result.frame_type == expected
    assert result.source == "FILENAME"


def test_filename_first_token_wins_when_multiple_types_present():
    # "flat" appears before "dark" in the stem, and neither forms a
    # dark-flat pair, so the first token (flat) wins.
    result = classify_frame_type(None, "/data/mount1/flat_then_dark_001.fits")
    assert result.frame_type == FrameType.FLAT
    assert result.source == "FILENAME"


def test_filename_darkflat_wins_over_dark_alone():
    result = classify_frame_type(None, "/data/mount1/dark_flat_calibration_001.fits")
    assert result.frame_type == FrameType.DARK_FLAT


def test_filename_beats_directory():
    result = classify_frame_type(None, "/data/mount1/Darks/M31_LIGHT_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "FILENAME"


# ---------------------------------------------------------------------------
# Step 3: directory names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dirname,expected", [
    ("Darks", FrameType.DARK),
    ("DARK", FrameType.DARK),
    ("darks_-10C_300s", FrameType.DARK),
    ("Master Darks", FrameType.DARK),
    ("Flats Ha", FrameType.FLAT),
    ("Bias", FrameType.BIAS),
    ("Offsets", FrameType.BIAS),
    ("FlatDarks", FrameType.DARK_FLAT),
])
def test_directory_classifies(dirname, expected):
    result = classify_frame_type(None, f"/data/mount1/{dirname}/img_0001.fits")
    assert result.frame_type == expected
    assert result.source == "PATH"


@pytest.mark.parametrize("dirname", [
    "Dark Sky Site",
    "Darkside",
    "Flatiron Park",
    "Light Pollution Tests",
    "Lights Out Nebula",
])
def test_directory_false_positive_guard(dirname):
    result = classify_frame_type(None, f"/data/mount1/{dirname}/img_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "DEFAULT"


def test_nearest_directory_wins():
    # Farther-up "Darks" should be ignored because a nearer "Lights"
    # directory exists closer to the file.
    result = classify_frame_type(None, "/data/mount1/Darks/Lights/M31/img_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "PATH"


def test_directory_stops_at_mount_root():
    # "mount1" itself is not a classifiable segment even if it happened to
    # contain a keyword, and we never walk above the configured mount root.
    result = classify_frame_type(None, "/data/mount1/M31/img_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "DEFAULT"


def test_header_beats_path():
    result = classify_frame_type({"IMAGETYP": "LIGHT"}, "/data/mount1/Darks/img_0001.fits")
    assert result.frame_type == FrameType.LIGHT
    assert result.source == "HEADER"


# ---------------------------------------------------------------------------
# Step 4: default
# ---------------------------------------------------------------------------

def test_default_light():
    result = classify_frame_type(None, "/data/mount1/M31/random_name_0001.fits")
    assert result == FrameTypeResult(frame_type=FrameType.LIGHT, source="DEFAULT", is_master_hint=False)


def test_never_raises_on_garbage_input():
    # Non-dict header, empty path -- should not raise.
    result = classify_frame_type("not-a-dict", "")
    assert result.frame_type == FrameType.LIGHT
