"""
Tests for app.utils.optics (R0 rig maths, spec §4.2 / §6).
"""

import pytest

from app.utils.optics import (
    effective_focal_mm, focal_ratio, fov_deg, pixel_scale, rig_optics_summary, sampling, scale_check,
)


def test_pixel_scale_known_rigs():
    assert pixel_scale(4.63, 2800) == pytest.approx(0.341, abs=0.001)       # C11 + ASI294MM
    assert pixel_scale(3.8, 346) == pytest.approx(2.265, abs=0.001)         # ZS73 + ASI1600MM
    assert pixel_scale(2.315, 200) == pytest.approx(2.387, abs=0.001)       # EF200 + ASI294 unlocked
    assert pixel_scale(3.2, 105) == pytest.approx(6.29, abs=0.01)           # Sigma 105 + R7


def test_reducer_and_binning():
    base = pixel_scale(3.8, 430)
    assert pixel_scale(3.8, 430, modifier_factor=0.8) == pytest.approx(base * 1.25)
    assert pixel_scale(3.8, 430, binning=2) == pytest.approx(base * 2)
    assert effective_focal_mm(430, 0.8) == pytest.approx(344)
    assert effective_focal_mm(1000, 2.0) == pytest.approx(2000)


def test_missing_inputs_return_none():
    assert pixel_scale(None, 400) is None
    assert pixel_scale(3.8, None) is None
    assert pixel_scale(3.8, 0) is None
    assert fov_deg(None, 100, 1.0) is None
    assert focal_ratio(400, None) is None


def test_fov_and_focal_ratio():
    w, h = fov_deg(4656, 3520, 2.265)
    assert w == pytest.approx(2.93, abs=0.01)
    assert h == pytest.approx(2.21, abs=0.01)
    assert focal_ratio(2800, 280) == pytest.approx(10.0)
    assert focal_ratio(430, 73, 0.8) == pytest.approx(4.71, abs=0.01)


def test_sampling():
    verdict, ratio = sampling(0.34, 2.5)
    assert verdict == "over" and ratio == pytest.approx(7.35, abs=0.01)
    assert sampling(1.0, 2.5)[0] == "ok"
    assert sampling(6.5, 2.5)[0] == "under"
    assert sampling(None, 2.5) is None


def test_scale_check():
    check = scale_check(2.46, 2.27)
    assert check["delta_pct"] == pytest.approx(8.4, abs=0.05)
    assert check["verdict"] == "mismatch"
    assert scale_check(2.28, 2.27)["verdict"] == "ok"
    assert scale_check(None, 2.27) == {"declared": None, "measured": 2.27, "delta_pct": None, "verdict": "unknown"}


def test_rig_optics_summary_shape():
    s = rig_optics_summary(pixel_um=4.63, width_px=4144, height_px=2822, focal_mm=2800, aperture_mm=280,
                           measured_scale=0.34, seeing_arcsec=2.5)
    assert set(s) == {"scale", "fov_deg", "focal_ratio", "effective_focal_mm", "sampling", "scale_check"}
    assert s["scale"] == pytest.approx(0.341, abs=0.001)
    assert s["sampling"]["verdict"] == "over"
    assert s["sampling"]["seeing_arcsec"] == 2.5
    assert s["scale_check"]["verdict"] == "ok"
    assert s["focal_ratio"] == pytest.approx(10.0)

    unknown = rig_optics_summary(pixel_um=None, width_px=None, height_px=None, focal_mm=200, aperture_mm=None)
    assert unknown["scale"] is None and unknown["fov_deg"] is None and unknown["sampling"] is None
    assert unknown["focal_ratio"] is None and unknown["effective_focal_mm"] == 200.0
    assert unknown["scale_check"]["verdict"] == "unknown"
