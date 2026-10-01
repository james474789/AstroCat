"""Q1 star quality presentation helpers and runtime settings (docs/design/20260927-Q1-star-quality.md §7)."""

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

    def test_implausible_image_scale_uses_rig(self):
        assert resolve_scale(72.0, 2.27) == (2.27, "RIG_OVERRIDE")
        assert resolve_scale(0.5, 2.27) == (2.27, "RIG_OVERRIDE")

    def test_binned_image_scale_is_kept(self):
        assert resolve_scale(4.54, 2.27) == (4.54, "IMAGE")

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


class TestSummarizeSamples:
    def test_empty(self):
        from app.utils.star_quality import summarize_samples
        assert summarize_samples([]) is None and summarize_samples(None) is None

    def test_mixed_scales_use_each_subs_own_scale(self):
        from app.utils.star_quality import summarize_samples
        # fwhm_px, hfr_px, ecc, stars, scale
        samples = [[3.0, 1.8, 0.4, 1000, 1.0], [4.0, 2.4, 0.5, 900, 1.0], [2.0, 1.2, 0.3, 1100, 2.0],
                   [5.0, 3.0, 0.6, 800, None]]
        q = summarize_samples(samples)
        assert q["measured"] == 4 and q["with_scale"] == 3
        assert q["median_fwhm_px"] == 3.5
        assert q["median_fwhm_arcsec"] == 4.0          # [3, 4, 4] arcsec
        assert q["best_fwhm_arcsec"] == 3.2            # p10 of [3, 4, 4]
        assert q["median_ecc"] == 0.45 and q["median_stars"] == 950.0

    def test_percentile_matches_postgres_percentile_cont(self):
        from app.utils.star_quality import _percentile
        assert _percentile([1, 2, 3, 4], 0.5) == 2.5
        assert _percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.1) == 1.9
        assert _percentile([7], 0.9) == 7.0


class TestFilterRigSamples:
    def _bucket(self, filt, camera, scale, subs, samples, rig_id=None):
        b = {"filter": filt, "camera": camera, "pixel_scale": scale, "pixel_size_um": 3.76,
             "subs": subs, "seconds": subs * 300.0, "rig_id": rig_id}
        if samples is not None:
            b["quality_samples"] = samples
        return b

    def test_samples_concatenate_when_buckets_fold(self):
        from app.utils.rig_optics import build_filter_rig_rows, QUALITY_SAMPLES_KEY
        rows = build_filter_rig_rows([
            self._bucket("Ha", "ASI2600MM", 1.20, 2, [[3.0, 1.8, 0.4, 900, 1.2]]),
            self._bucket("Ha", "ASI2600MM", 1.21, 3, [[3.4, 2.0, 0.4, 950, 1.21], [3.2, 1.9, 0.4, 920, 1.21]]),
        ])
        assert len(rows) == 1 and rows[0]["subs"] == 5
        assert len(rows[0][QUALITY_SAMPLES_KEY]) == 3

    def test_declared_rig_rows_carry_samples(self):
        from app.utils.rig_optics import build_filter_rig_rows_with_rigs, QUALITY_SAMPLES_KEY
        rigs = {7: {"name": "C8", "camera": "ASI2600MM", "focal_length": 1280}}
        rows = build_filter_rig_rows_with_rigs([
            self._bucket("L", "ASI2600MM", 0.62, 4, [[4.0, 2.5, 0.5, 700, 0.62]], rig_id=7),
            self._bucket("L", "ASI2600MM", 0.61, 1, [], rig_id=7),
        ], rigs)
        assert rows[0]["rig_name"] == "C8" and rows[0][QUALITY_SAMPLES_KEY] == [[4.0, 2.5, 0.5, 700, 0.62]]

    def test_no_samples_key_when_not_requested(self):
        from app.utils.rig_optics import build_filter_rig_rows, QUALITY_SAMPLES_KEY
        rows = build_filter_rig_rows([self._bucket("R", "ASI294MM", 2.0, 2, None)])
        assert QUALITY_SAMPLES_KEY not in rows[0]


class TestFoldQualityParts:
    def test_single_part_is_exact(self):
        from app.api.targets import _fold_quality_parts
        part = {"measured": 5, "median_fwhm_px": 3.0, "median_hfr_px": 1.8, "median_fwhm_arcsec": 2.4, "median_hfr_arcsec": 1.4}
        assert _fold_quality_parts([part]) is part and _fold_quality_parts(None) is None

    def test_weighted_by_measured(self):
        from app.api.targets import _fold_quality_parts
        parts = [{"measured": 30, "median_fwhm_px": 3.0, "median_hfr_px": 1.8, "median_fwhm_arcsec": 2.4, "median_hfr_arcsec": None},
                 {"measured": 5, "median_fwhm_px": 4.0, "median_hfr_px": 2.2, "median_fwhm_arcsec": None, "median_hfr_arcsec": None}]
        q = _fold_quality_parts(parts)
        assert q["measured"] == 35 and q["median_fwhm_px"] == 3.0 and q["median_fwhm_arcsec"] == 2.4
        assert q["median_hfr_arcsec"] is None
