"""S1 §5 scoring: curves, weights, model combination, windows, grade / checklist / verdict. Pure, no I/O."""

import numpy as np
import pytest

import _seeing_helpers  # noqa: F401  (environment)
from app.services.seeing import scoring as sc


# ---- factor curves at each knot (design §5.1) --------------------------------------------------------

@pytest.mark.parametrize("wind,expected", [(0, 1.0), (1, 1.0), (3, 0.35), (5, 0.1), (8, 0.0), (20, 0.0), (2, 0.675)])
def test_surface_wind_knots(wind, expected):
    assert sc.f_surface_wind(wind) == pytest.approx(expected)


def test_surface_wind_uses_half_gust():
    assert sc.surface_wind_speed(0.5, 4.0) == 2.0
    assert sc.f_surface_wind(0.5, 4.0) == pytest.approx(0.675)
    assert sc.surface_wind_speed(3.0, 2.0) == 3.0      # gust half below the mean wind: the wind wins
    assert sc.f_surface_wind(None, 4.0) is None


@pytest.mark.parametrize("idx,expected", [(1, 0.0), (2, 0.15), (3, 0.35), (4, 0.85), (5, 1.0), (3.5, 0.6)])
def test_seeing_index_knots(idx, expected):
    assert sc.f_seeing_index(idx) == pytest.approx(expected)


def test_stability_knots_and_bonus():
    assert sc.f_stability(1, 90) == pytest.approx(1.0)
    assert sc.f_stability(5, 80) == pytest.approx((0.4 + 0.55) / 2)
    assert sc.f_stability(10, 70) == pytest.approx((0.1 + 0.2) / 2)
    assert sc.f_stability(5, 80, 200) == pytest.approx((0.4 + 0.55) / 2 + 0.1)   # BLH <= 300 m bonus
    assert sc.f_stability(5, 80, 400) == pytest.approx((0.4 + 0.55) / 2)
    assert sc.f_stability(0, 95, 100) == 1.0                                       # capped at 1
    assert sc.f_stability(5, None) == pytest.approx(0.4)                          # one input is enough
    assert sc.f_stability(None, None, 100) is None


@pytest.mark.parametrize("mid,high,expected", [(0, 0, 1.0), (20, 0, 0.55), (0, 50, 0.2), (80, 10, 0.0), (10, 0, 0.775)])
def test_upper_cloud_knots(mid, high, expected):
    assert sc.f_upper_cloud(mid, high) == pytest.approx(expected)


def test_upper_cloud_uses_whichever_is_known():
    assert sc.f_upper_cloud(None, 20) == pytest.approx(0.55)
    assert sc.f_upper_cloud(None, None) is None


@pytest.mark.parametrize("v,expected", [(10, 1.0), (15, 1.0), (20, 0.85), (35, 0.55), (50, 0.3), (70, 0.1), (100, 0.1),
                                        (27.5, 0.7)])
def test_jet_knots(v, expected):
    assert sc.f_jet(v) == pytest.approx(expected)


@pytest.mark.parametrize("w850,w10,expected", [(5, 0, 1.0), (3, 3, 1.0), (10, 0, 0.6), (20, 0, 0.2), (7.5, 0, 0.8)])
def test_ground_shear_knots(w850, w10, expected):
    assert sc.f_ground_shear(w850, w10) == pytest.approx(expected)
    assert sc.f_ground_shear(None, 1) is None


def test_clear_fraction():
    assert sc.clear_fraction(0, 0) == 1.0
    assert sc.clear_fraction(30, 70) == pytest.approx(0.3)
    assert sc.clear_fraction(None, None) == 1.0
    assert sc.clear_fraction(150, 0) == 0.0


def test_weights_sum_and_zero_weight_factors():
    assert sum(sc.WEIGHTS.values()) == pytest.approx(1.0)
    assert sc.WEIGHTS["arcsec"] == 0 and sc.WEIGHTS["computed_seeing"] == 0     # §2: no signal / uncalibrated
    assert sc.SCORING_VERSION == 1


# ---- weight renormalisation -----------------------------------------------------------------------

def test_missing_factors_renormalise():
    assert sc.atmosphere_score({"surface_wind": (0.5, 1.0)}) == pytest.approx(1.0)
    both = {"surface_wind": (0.5, 1.0), "jet": (60, 0.0)}
    assert sc.atmosphere_score(both) == pytest.approx(0.25 / 0.40)
    assert sc.atmosphere_score({"surface_wind": (None, None)}) is None
    # a shown-only factor never moves the score
    assert sc.atmosphere_score({**both, "arcsec": (0.5, 1.0), "computed_seeing": (3.0, 0.0)}) == pytest.approx(0.625)


def test_no_meteoblue_key_does_not_lower_the_score():
    v = {"wind_speed_10m": 0.5, "temperature_2m": 0, "relative_humidity_2m": 92, "cloud_cover_mid": 0,
         "cloud_cover_high": 0, "wind_speed_250hPa": 10, "wind_speed_850hPa": 1}
    without = sc.atmosphere_score(sc.hour_factors(v, None))
    with_mb = sc.atmosphere_score(sc.hour_factors(v, {"seeing_index1": 5, "jet_stream": 10, "seeing_arcsec": 1.0}))
    assert without == pytest.approx(1.0)
    assert with_mb == pytest.approx(1.0)
    assert sc.hour_factors(v, None)["seeing_index"] == (None, None)


def test_meteoblue_index_and_jet_enter_the_score():
    v = {"wind_speed_10m": 0.5, "wind_speed_250hPa": 10}
    base = sc.atmosphere_score(sc.hour_factors(v, None))
    bad = sc.atmosphere_score(sc.hour_factors(v, {"seeing_index1": 1, "jet_stream": 60}))
    assert bad < base
    f = sc.hour_factors(v, {"seeing_index1": 4, "jet_stream": 60, "seeing_arcsec": 1.3})
    assert f["jet"][0] == 60                          # meteoblue jet replaces the model's
    assert f["arcsec"] == (1.3, None)                 # shown only


# ---- multi-model combination and confidence (§5.3) -----------------------------------------------

def test_blend_primary_weighting():
    assert sc.blend({"a": 0.8, "b": 0.6, "c": 0.4}, "a") == pytest.approx(0.6 * 0.8 + 0.4 * 0.5)
    assert sc.blend({"a": None, "b": 0.6, "c": 0.4}, "a") == pytest.approx(0.5)
    assert sc.blend({"a": 0.7}, "a") == pytest.approx(0.7)
    assert sc.blend({"a": None}, "a") is None


def test_combine_spread_and_confidence():
    a, conf = sc.combine({"a": 0.8, "b": 0.6, "c": 0.4}, "a", lead_h=0)
    assert a == pytest.approx(0.68)
    assert conf == pytest.approx(1 - 0.4 / 0.5)
    assert sc.combine({"a": 0.9, "b": 0.9}, "a")[1] == pytest.approx(1.0)
    assert sc.combine({"a": 1.0, "b": 0.0}, "a")[1] == 0.0                        # spread >= 0.5 clamps to 0
    assert sc.combine({"a": None, "b": None}, "a") == (None, 0.0)


@pytest.mark.parametrize("lead,factor", [(0, 1.0), (24, 1.0), (24.1, 0.85), (48, 0.85), (60, 0.7), (72, 0.7), (100, 0.5),
                                         (-3, 1.0)])
def test_lead_time_factor(lead, factor):
    assert sc.lead_factor(lead) == factor
    assert sc.combine({"a": 0.5}, "a", lead)[1] == pytest.approx(factor)


# ---- altitude, windows (§5.4) -------------------------------------------------------------------

def test_altitude_factor_knots_and_horizon():
    alt = np.array([10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 80.0])
    f = sc.altitude_factor(alt, np.full(7, 15.0))
    assert f.tolist() == pytest.approx([0.0, 0.0, 0.25, 0.6, 0.85, 1.0, 1.0])
    # a 45 degree horizon zeroes everything below it, whatever the curve says
    f2 = sc.altitude_factor(alt, np.full(7, 45.0))
    assert f2.tolist() == pytest.approx([0, 0, 0, 0, 0, 1.0, 1.0])
    # the planetary floor applies even over a lower horizon
    assert sc.altitude_factor(np.array([12.0]), np.array([0.0]))[0] == 0.0


def test_body_scores_gates():
    n = 4
    a, alt, lim = np.ones(n), np.full(n, 60.0), np.full(n, 15.0)
    allowed = np.array([True, True, False, True])
    clear = np.array([1.0, 0.5, 1.0, 0.2])                # last hour under the 0.3 cloud gate
    p = sc.body_scores(a, clear, alt, lim, allowed)
    assert p.tolist() == pytest.approx([1.0, 0.5, 0.0, 0.0])


def test_best_window_contiguous_and_highest_mean():
    p = np.array([0, 0, .2, .8, .9, .85, .3, 0, 0, .7, .7, .7, 0])
    assert sc.best_window(p, 15) == (3, 5)


def test_best_window_prefers_min_length_over_a_short_high_span():
    p = np.array([0, .9, .9, 0, .5, .5, .5, .5, 0])       # 30 min at .9, then 60 min at .5
    assert sc.best_window(p, 15, 45) == (4, 7)
    assert sc.best_window(p, 15, 20) == (1, 2)


def test_best_window_short_fallback_and_drop():
    assert sc.best_window(np.array([0, .9, 0]), 15, 45) == (1, 1)         # 15 min: kept as a short window
    assert sc.best_window(np.array([0, .9, 0]), 5, 45) is None            # 5 min: dropped
    assert sc.best_window(np.zeros(6), 15) is None


def test_best_window_respects_half_of_peak():
    p = np.array([.2, .2, .2, 1.0, 1.0, 1.0, .2, .2])
    assert sc.best_window(p, 15) == (3, 5)                # the .2 shoulders are under 0.5 x peak


def test_best_window_zero_gaps_split_spans():
    p = np.array([.8, .8, .8, 0, .9, .9, .9, .9])
    assert sc.best_window(p, 15) == (4, 7)


# ---- warnings, grade, checklist, verdict (§5.4, §5.5) ---------------------------------------------

def test_window_warnings():
    none = dict(rh_max=80, dew_spread_min=5, gust_max=2, peak_alt=60, jet_max=10, confidence=0.9)
    assert sc.window_warnings(**none) == []
    assert sc.window_warnings(**{**none, "rh_max": 95}) == ["dew_risk"]
    assert sc.window_warnings(**{**none, "dew_spread_min": 1.0}) == ["dew_risk"]
    assert sc.window_warnings(**{**none, "gust_max": 6}) == ["gusty"]
    assert sc.window_warnings(**{**none, "peak_alt": 29}) == ["low_dispersion"]
    assert sc.window_warnings(**{**none, "jet_max": 36}) == ["jet_overhead"]
    assert sc.window_warnings(**{**none, "confidence": 0.4}) == ["models_disagree"]
    assert sc.window_warnings(rh_max=None, dew_spread_min=None, gust_max=None, peak_alt=None, jet_max=None,
                              confidence=None) == []


@pytest.mark.parametrize("score,grade", [(0.0, "VVP"), (0.149, "VVP"), (0.15, "VP"), (0.299, "VP"), (0.3, "P"),
                                         (0.449, "P"), (0.45, "A"), (0.599, "A"), (0.6, "G"), (0.749, "G"),
                                         (0.75, "VG"), (1.0, "VG"), (None, "VVP")])
def test_grade_mapping(score, grade):
    assert sc.grade_for(score) == grade


def _check(**kw):
    base = dict(wind=0.8, temp=0.0, rh=92.0, blh=None, upper_cloud=5.0, seeing_index=None, peak_alt=52.0)
    return sc.checklist(**{**base, **kw})


def test_checklist_all_pass_with_no_key():
    items = _check()
    by = {i["key"]: i["pass"] for i in items}
    assert by == {"surface_wind": True, "temp_humidity": True, "upper_cloud": True, "seeing_index": None,
                  "planet_altitude": True}
    assert sc.checklist_passes(items)


@pytest.mark.parametrize("kw,key", [({"wind": 1.6}, "surface_wind"), ({"temp": 3.1}, "temp_humidity"),
                                    ({"rh": 80.0}, "temp_humidity"), ({"upper_cloud": 21.0}, "upper_cloud"),
                                    ({"seeing_index": 3.0}, "seeing_index"), ({"peak_alt": 34.0}, "planet_altitude")])
def test_checklist_each_item_can_fail(kw, key):
    items = _check(**kw)
    assert {i["key"]: i["pass"] for i in items}[key] is False
    assert not sc.checklist_passes(items)


def test_checklist_boundaries_and_blh_alternative():
    assert {i["key"]: i["pass"] for i in _check(wind=1.5, temp=3.0, rh=85.0, upper_cloud=20.0, seeing_index=4.0,
                                               peak_alt=35.0)} == {k: True for k in
                                                                   ("surface_wind", "temp_humidity", "upper_cloud",
                                                                    "seeing_index", "planet_altitude")}
    warm_dry_but_shallow = {i["key"]: i["pass"] for i in _check(temp=8.0, rh=60.0, blh=250.0)}
    assert warm_dry_but_shallow["temp_humidity"] is True                  # "or BLH <= 300 m"


def test_verdict_go_maybe_no_go_cloud():
    v = sc.verdict_for
    assert v("A", 60, any_body=True, all_cloud_blocked=False) == "GO"
    assert v("VG", 120, any_body=True, all_cloud_blocked=False) == "GO"
    assert v("A", 45, any_body=True, all_cloud_blocked=False) == "MAYBE"       # checklist short of 1 h
    assert v("P", 0, any_body=True, all_cloud_blocked=False) == "MAYBE"        # grade >= P
    assert v("VP", 60, any_body=True, all_cloud_blocked=False) == "MAYBE"      # checklist passes
    assert v("VP", 30, any_body=True, all_cloud_blocked=False) == "NO_GO"
    assert v("VVP", 0, any_body=True, all_cloud_blocked=False) == "NO_GO"
    assert v("VG", 240, any_body=True, all_cloud_blocked=True) == "CLOUD"
    assert v("VVP", 0, any_body=False, all_cloud_blocked=True) == "NO_GO"      # nothing up at all is not "cloud"


# ---- experimental computed seeing (§5.2) ---------------------------------------------------------

def _levels(shear=0.0):
    return {850: {"speed": 5.0, "dir": 270.0, "temp": 5.0},
            700: {"speed": 8.0 + shear, "dir": 270.0, "temp": -5.0},
            500: {"speed": 15.0 + 2 * shear, "dir": 270.0, "temp": -20.0},
            300: {"speed": 30.0 + 4 * shear, "dir": 270.0, "temp": -45.0},
            250: {"speed": 35.0 + 6 * shear, "dir": 270.0, "temp": -52.0}}


def test_computed_seeing_none_without_data_and_rises_with_shear():
    assert sc.computed_seeing({}) is None
    assert sc.computed_seeing({850: {"speed": None, "dir": 0, "temp": 0}, 700: {"speed": 1, "dir": 0, "temp": 0}}) is None
    calm = sc.computed_seeing({p: {**lv, "speed": 5.0} for p, lv in _levels().items()})
    shear = sc.computed_seeing(_levels(shear=15.0))
    assert calm == pytest.approx(sc.COMPUTED_SEEING_BASE)        # no shear, stable: base only
    assert shear >= calm
