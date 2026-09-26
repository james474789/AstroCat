"""
Tests for app.utils.rig_optics (Target detail "By Filter & Rig" table).
"""

import pytest

from app.utils.rig_optics import (
    build_filter_rig_rows,
    valid_pixel_scale,
    parse_pixel_size,
    known_pixel_size,
    binning_factor,
    focal_length_mm,
)


def _b(scale, pix=3.8, subs=10, seconds=600.0, filt="Ha", camera="ZWO ASI1600MM Pro"):
    return {"filter": filt, "camera": camera, "pixel_scale": scale,
            "pixel_size_um": pix, "subs": subs, "seconds": seconds}


@pytest.mark.parametrize("raw,expected", [
    (1.31, 1.31), (None, None), (0.0, None), (72.0, None), (500.0, None), ("x", None),
])
def test_valid_pixel_scale(raw, expected):
    assert valid_pixel_scale(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("3.8", 3.8), (4.63, 4.63), ("3.79999995231628", pytest.approx(3.8)),
    ("3.76 um", 3.76), (None, None), ("", None), ("0", None),
])
def test_parse_pixel_size(raw, expected):
    assert parse_pixel_size(raw) == expected


def test_known_pixel_size_and_binning():
    assert known_pixel_size("Canon Canon EOS 6D") == 6.54
    assert known_pixel_size("ZWO ASI294MM Pro") == 4.63
    assert known_pixel_size("Mystery Cam") is None
    assert binning_factor("2x2") == 2
    assert binning_factor("3") == 3
    assert binning_factor(None) == 1


def test_focal_length():
    # 3.8um at 0.871"/px -> ~900mm (ASI1600 on a 200P)
    assert round(focal_length_mm(3.8, 0.871)) == 900
    assert focal_length_mm(None, 1.0) is None
    assert focal_length_mm(3.8, None) is None


def test_nearby_scales_merge_into_one_rig():
    rows = build_filter_rig_rows([_b(1.30, subs=5), _b(1.31, subs=20), _b(1.32, subs=5)])
    assert len(rows) == 1
    r = rows[0]
    assert r["subs"] == 30
    assert r["seconds"] == 1800.0
    assert r["pixel_scale"] == 1.31
    assert r["focal_length"] == round(206.265 * 3.8 / 1.31)


def test_distinct_scales_split_into_rigs():
    rows = build_filter_rig_rows([_b(0.87), _b(1.76), _b(2.16)])
    assert sorted(r["pixel_scale"] for r in rows) == [0.87, 1.76, 2.16]


def test_unsolved_subs_form_unknown_row():
    rows = build_filter_rig_rows([_b(1.31), _b(None, subs=4)])
    unknown = [r for r in rows if r["pixel_scale"] is None]
    assert len(unknown) == 1
    assert unknown[0]["subs"] == 4
    assert unknown[0]["focal_length"] is None


def test_missing_pixel_size_leaves_focal_length_empty():
    rows = build_filter_rig_rows([_b(1.31, pix=None)])
    assert rows[0]["pixel_scale"] == 1.31
    assert rows[0]["focal_length"] is None


def test_filters_and_cameras_kept_separate():
    rows = build_filter_rig_rows([
        _b(1.31, filt="Ha"), _b(1.31, filt="OIII"), _b(1.31, camera="Other"),
    ])
    assert len(rows) == 3
