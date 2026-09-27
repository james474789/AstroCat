"""Q1 star quality presentation helpers and runtime settings (docs/design/Q1-star-quality.md §7)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import quality_settings as qs
from app.utils.star_quality import night_context, quality_summary, resolve_scale, sampling, to_arcsec


def _image(**overrides):
    base = dict(star_metrics_status="OK", hfr_px=1.9, fwhm_px=3.2, eccentricity=0.41, star_count=1200,
                pixel_scale_arcsec=0.8, star_metrics_version=1, star_metrics_at=datetime(2026, 9, 27, 8),
                star_metrics={"source": "MEASURED", "fwhm_method": "MOFFAT",
                              "grid_hfr": [[2.0, 1.9, None], [1.8, 1.8, 1.9], [2.1, 2.0, 2.2]],
                              "hints": {"HFR": 1.85}})
    base.update(overrides)
    return SimpleNamespace(**base)


class TestScale:
    def test_image_scale_wins_over_rig(self):
        assert resolve_scale(0.8, 1.2) == (0.8, "IMAGE")

    def test_rig_scale_fallback(self):
        assert resolve_scale(None, 1.2) == (1.2, "RIG")

    @pytest.mark.parametrize("bad", [None, 0, -1, "x", 5000])
    def test_no_usable_scale(self, bad):
        assert resolve_scale(bad, None) == (None, None)

    def test_to_arcsec(self):
        assert to_arcsec(3.2, 0.8) == 2.56
        assert to_arcsec(3.2, None) is None and to_arcsec(None, 0.8) is None


class TestSampling:
    @pytest.mark.parametrize("fwhm,expected", [(0.8, "UNDER"), (1.0, "OK"), (2.5, "OK"), (3.0, "OK"), (4.2, "OVER"), (None, None)])
    def test_bands(self, fwhm, expected):
        assert sampling(fwhm) == expected


class TestNightContext:
    def test_needs_three_peers(self):
        assert night_context(3.0, 2.0, [{"fwhm_px": 3.0, "hfr_px": 2.0}] * 2, 0.8, "2026-09-26") is None

    def test_delta_against_median(self):
        peers = [{"fwhm_px": v, "hfr_px": v / 1.6} for v in (3.0, 3.2, 4.0)]
        ctx = night_context(4.0, 2.5, peers, 0.8, "2026-09-26")
        assert ctx["median_fwhm_px"] == 3.2
        assert ctx["median_fwhm_arcsec"] == 2.56
        assert ctx["fwhm_delta_pct"] == 25.0
        assert ctx["subs"] == 3 and ctx["night"] == "2026-09-26"


class TestSummary:
    def test_both_units_and_grid(self):
        q = quality_summary(_image())
        assert q["measured"] is True
        assert (q["fwhm_px"], q["fwhm_arcsec"], q["hfr_arcsec"]) == (3.2, 2.56, 1.52)
        assert q["scale_source"] == "IMAGE"
        assert q["sampling"] == "OVER"
        assert q["grid_hfr_arcsec"][0] == [1.6, 1.52, None]
        assert q["hints"] == {"HFR": 1.85}

    def test_unsolved_image_uses_rig_scale(self):
        q = quality_summary(_image(pixel_scale_arcsec=None), rig_scale=2.0)
        assert q["scale_source"] == "RIG" and q["fwhm_arcsec"] == 6.4

    def test_no_scale_keeps_pixels(self):
        q = quality_summary(_image(pixel_scale_arcsec=None))
        assert q["fwhm_arcsec"] is None and q["grid_hfr_arcsec"] is None and q["fwhm_px"] == 3.2

    def test_hint_rows_are_not_measured(self):
        q = quality_summary(_image(star_metrics_status="HINT", fwhm_px=None, star_metrics={"source": "HINT"}))
        assert q["measured"] is False and q["sampling"] is None and q["source"] == "HINT"

    def test_never_measured(self):
        q = quality_summary(_image(star_metrics_status=None, hfr_px=None, fwhm_px=None, eccentricity=None,
                                   star_count=None, star_metrics=None, star_metrics_at=None))
        assert q["status"] is None and q["grid_hfr_px"] is None and q["measured_at"] is None


class TestRuntimeSettings:
    def setup_method(self):
        qs.clear_cache()

    def teardown_method(self):
        qs.clear_cache()

    def test_defaults_without_overrides(self):
        with patch.object(qs, "_runtime_overrides", return_value={}):
            assert qs.quality_settings() == {"quality_units": "ARCSEC", "star_metrics_enabled": True,
                                             "star_metrics_backfill": True}

    def test_runtime_off_switch(self):
        with patch.object(qs, "_runtime_overrides", return_value={"star_metrics_backfill": False, "quality_units": "px"}):
            s = qs.quality_settings()
            assert s["star_metrics_backfill"] is False and s["quality_units"] == "PX"
            assert qs.backfill_enabled() is False and qs.measuring_enabled() is True

    def test_env_off_wins(self):
        with patch.object(qs, "_runtime_overrides", return_value={"star_metrics_enabled": True}), \
                patch.object(qs.settings, "star_metrics_enabled", False):
            assert qs.measuring_enabled() is False and qs.backfill_enabled() is False

    def test_unknown_units_fall_back(self):
        with patch.object(qs, "_runtime_overrides", return_value={"quality_units": "furlongs"}):
            assert qs.quality_settings()["quality_units"] == "ARCSEC"
