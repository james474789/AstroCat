"""
Tests for app.utils.filter_names.normalize_filter (F2).

Covers every row of the normalization table in
docs/design/F2-target-integration.md §3.4.
"""

import pytest

from app.utils.filter_names import normalize_filter


@pytest.mark.parametrize("raw,expected", [
    # L
    ("L", "L"),
    ("Lum", "L"),
    ("Luminance", "L"),
    ("Clear", "L"),
    ("UV/IR", "L"),
    ("UVIR", "L"),
    ("L-Pro", "L"),
    # R/G/B
    ("R", "R"),
    ("Red", "R"),
    ("G", "G"),
    ("Green", "G"),
    ("B", "B"),
    ("Blue", "B"),
    # Ha
    ("Ha", "Ha"),
    ("H-a", "Ha"),
    ("H-alpha", "Ha"),
    ("Halpha", "Ha"),
    ("HA", "Ha"),
    ("H_Alpha", "Ha"),
    ("Hα", "Ha"),
    ("Ha 7nm", "Ha"),
    ("Astrodon Ha 3nm", "Ha"),
    # OIII
    ("OIII", "OIII"),
    ("O3", "OIII"),
    ("O-III", "OIII"),
    ("Oiii", "OIII"),
    # SII
    ("SII", "SII"),
    ("S2", "SII"),
    ("S-II", "SII"),
    # Hb
    ("Hb", "Hb"),
    ("H-beta", "Hb"),
    # Duo / multiband
    ("L-eNhance", "Duo"),
    ("L-eXtreme", "Duo"),
    ("L-Ultimate", "Duo"),
    ("ALP-T", "Duo"),
    ("NBZ", "Duo"),
    ("Triad", "Duo"),
    ("Quad", "Duo"),
    ("Duo", "Duo"),
    ("Dual", "Duo"),
    ("Tri-band", "Duo"),
    ("CLS", "Duo"),
    # None
    (None, "None"),
    ("", "None"),
    ("NoFilter", "None"),
    ("OSC", "None"),
    ("   ", "None"),
])
def test_normalize_filter_table(raw, expected):
    assert normalize_filter(raw) == expected


def test_normalize_filter_other_keeps_original():
    assert normalize_filter("Weird Custom Filter") == "Other:Weird Custom Filter"


def test_normalize_filter_case_insensitive():
    assert normalize_filter("ha") == "Ha"
    assert normalize_filter("HA") == "Ha"
    assert normalize_filter("oiii") == "OIII"
