"""Q1c session timeline (docs/design/20260927-Q1-star-quality.md §6, §7.4): events, flags, drift, altitude."""

from datetime import datetime, timedelta

import pytest

from app.services.session_quality import (
    add_altitudes, airmass, build_timeline, flag_points, infer_events, summarize, theil_sen_slope,
)

T0 = datetime(2026, 2, 13, 20, 0, 0)


def _p(i, minutes, **kw):
    base = dict(id=i, t=T0 + timedelta(minutes=minutes), exposure_s=300, filter="Ha", target_key="NGC2392",
                rig_id=1, ra=112.3, dec=20.9, lat=51.5, lon=-0.1, rotation=115.0, status="OK",
                fwhm_px=8.0, hfr_px=4.8, ecc=0.5, stars=300, scale=0.34, bkg=900.0, hints={})
    base.update(kw)
    return base


class TestEvents:
    def test_autofocus_filter_target_gap_and_flip(self):
        pts = [
            _p(1, 0, hints={"FOCPOS": 122312}),
            _p(2, 5.5, hints={"FOCPOS": 122312}),
            _p(3, 11, hints={"FOCPOS": 122500}),                       # autofocus
            _p(4, 16.5, filter="OIII", hints={"FOCPOS": 122500}),      # filter change
            _p(5, 60, filter="OIII", hints={"FOCPOS": 122500}),        # 38 min gap
            _p(6, 65.5, filter="OIII", rotation=295.3),                # meridian flip (rotation +180)
            _p(61, 71, filter="OIII", rotation=295.3),
            _p(62, 76.5, filter="OIII", rotation=295.3),
            _p(63, 82, filter="OIII", rotation=295.3),
            _p(7, 87.5, filter="OIII", target_key="M42", rotation=295.3),  # target change
        ]
        types = [(e["type"], e["image_id"]) for e in infer_events(pts)]
        assert ("AUTOFOCUS", 3) in types
        assert ("FILTER_CHANGE", 4) in types
        assert ("GAP", 5) in types
        assert ("MERIDIAN_FLIP", 6) in types
        assert ("TARGET_CHANGE", 7) in types
        assert not any(t == "GAP" for t, i in types if i != 5)

    def test_alternating_solver_rotation_is_not_a_flip(self):
        # Real R7 night: header rotation alternated -87.6 / 92.0 between consecutive subs.
        pts = [_p(i, i * 0.6, exposure_s=30, rotation=(-87.6 if i % 2 else 92.0)) for i in range(12)]
        assert [e for e in infer_events(pts) if e["type"] == "MERIDIAN_FLIP"] == []

    def test_flip_survives_one_noisy_solve(self):
        rot = [115.2] * 6 + [295.5, 295.5, 115.2, 295.5, 295.5, 295.5]
        pts = [_p(i, i * 5.5, rotation=r) for i, r in enumerate(rot)]
        flips = [e["image_id"] for e in infer_events(pts) if e["type"] == "MERIDIAN_FLIP"]
        assert flips == [6]

    def test_sustained_rotation_change_is_one_flip(self):
        pts = [_p(i, i * 5.5, rotation=115.2) for i in range(6)] + [_p(10 + i, 33 + i * 5.5, rotation=295.5) for i in range(6)]
        flips = [e for e in infer_events(pts) if e["type"] == "MERIDIAN_FLIP"]
        assert len(flips) == 1 and flips[0]["image_id"] == 10

    def test_pierside_change_is_a_flip(self):
        pts = [_p(1, 0, hints={"PIERSIDE": "EAST"}), _p(2, 5.5, hints={"PIERSIDE": "WEST"})]
        assert [e["type"] for e in infer_events(pts)] == ["MERIDIAN_FLIP"]

    def test_events_are_per_rig(self):
        pts = [_p(1, 0, rig_id=1), _p(2, 1, rig_id=2, filter="L"), _p(3, 5.5, rig_id=1), _p(4, 6, rig_id=2, filter="L")]
        assert infer_events(pts) == []


class TestFlags:
    def test_soft_cloud_trailed(self):
        pts = [_p(i, i * 5.5) for i in range(1, 9)]
        pts[2]["fwhm_px"] = 11.0        # > 1.3 x 8
        pts[4]["stars"] = 100           # < 0.5 x 300
        pts[6]["ecc"] = 0.8             # > max(0.6, 0.5 + 0.15)
        flag_points(pts)
        assert [p["flag"] for p in pts] == [None, None, "SOFT", None, "CLOUD", None, "TRAILED", None]

    def test_trailed_is_relative_for_elongated_rigs(self):
        # A rig whose stars are always ~0.75 eccentric (lens aberration) is not flagged wholesale.
        pts = [_p(i, i * 5.5, ecc=0.74 + 0.01 * (i % 3)) for i in range(1, 9)]
        flag_points(pts)
        assert all(p["flag"] is None for p in pts)

    def test_small_groups_are_not_flagged(self):
        pts = [_p(1, 0), _p(2, 5.5, fwhm_px=20.0)]
        flag_points(pts)
        assert pts[1]["flag"] is None

    def test_groups_are_per_filter(self):
        pts = [_p(i, i * 5.5) for i in range(1, 7)] + [_p(10 + i, 40 + i * 5.5, filter="OIII", fwhm_px=10.0) for i in range(6)]
        flag_points(pts)
        assert all(p["flag"] is None for p in pts)


class TestDrift:
    def test_theil_sen_ignores_an_outlier(self):
        xs = [0, 1, 2, 3, 4, 5, 6, 7]
        ys = [2.0, 2.1, 2.2, 9.0, 2.4, 2.5, 2.6, 2.7]
        assert theil_sen_slope(xs, ys) == pytest.approx(0.1, abs=0.01)

    def test_too_few_points(self):
        assert theil_sen_slope([0, 1, 2], [1, 2, 3]) is None

    def test_no_drift_for_short_runs(self):
        pts = [_p(i, i * 2, fwhm_px=8.0 + i) for i in range(10)]   # 18 minutes
        assert summarize(pts)[0]["drift_px_per_hour"] is None

    def test_summary_drift_per_hour_in_both_units(self):
        # FWHM grows 1 px per hour over two hours: focus drift.
        pts = [_p(i, i * 10, fwhm_px=8.0 + i * 10 / 60.0) for i in range(13)]
        s = summarize(pts)[0]
        assert s["measured"] == 13
        assert s["drift_px_per_hour"] == pytest.approx(1.0, abs=0.01)
        assert s["drift_arcsec_per_hour"] == pytest.approx(0.34, abs=0.01)
        assert s["best_fwhm_px"] < s["median_fwhm_px"] < s["worst_fwhm_px"]


class TestAltitude:
    def test_airmass(self):
        assert airmass(90) == pytest.approx(1.0, abs=0.001)
        assert airmass(30) == pytest.approx(1.995, abs=0.01)
        assert airmass(-5) is None and airmass(None) is None

    def test_unsolved_sub_borrows_target_pointing(self):
        pts = [_p(1, 0), _p(2, 5.5, ra=None, dec=None)]
        add_altitudes(pts)
        assert pts[1]["alt_deg"] is not None
        assert abs(pts[1]["alt_deg"] - pts[0]["alt_deg"]) < 2

    def test_no_site_no_altitude(self):
        pts = [_p(1, 0, lat=None, lon=None)]
        add_altitudes(pts)
        assert pts[0]["alt_deg"] is None and pts[0]["airmass"] is None


class TestBuildTimeline:
    def test_shape(self):
        pts = [_p(i, i * 5.5, hints={"FOCPOS": 100 if i < 4 else 120}) for i in range(8)]
        pts[5]["status"], pts[5]["fwhm_px"] = None, None   # not measured yet
        tl = build_timeline(pts, "2026-02-13")
        assert tl["night"] == "2026-02-13" and len(tl["points"]) == 8
        p0 = tl["points"][0]
        assert p0["fwhm_arcsec"] == pytest.approx(2.72) and p0["focuser_pos"] == 100
        assert tl["points"][5]["fwhm_px"] is None
        assert any(e["type"] == "AUTOFOCUS" for e in tl["events"])
        assert tl["summary"][0]["subs"] == 8 and tl["summary"][0]["measured"] == 7
        assert tl["dark"] is not None and tl["dark"]["start"] is not None   # February evening in London
        # The whole dark period, not clipped to the session: ends before dawn next morning.
        assert tl["dark"]["end"].startswith("2026-02-14T0")
