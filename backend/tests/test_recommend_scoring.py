"""R1 §4.5-4.6: Moon rules, framing, feasibility, components, rig specs."""

import math
from datetime import date, timedelta

import numpy as np
import pytest

from app.services.recommend import Params, recommend
from app.services.recommend.candidates import CandidatePool
from app.services.recommend.history import HistoryRow, build_history, infer_goals
from app.services.recommend.loader import RecommendationError, RigRecord, rig_spec, select_rigs
from app.services.recommend.scoring import (
    CLASS_INDEX, EXCL_TOO_BIG, EXCL_TOO_SMALL, HistoryArrays, MOON_RULES, NightFeatures, RigSpec, Weights,
    evaluate_rig, framing, history_arrays, moon_factor, momentum_score, required_separation, tier_class_mask,
    urgency_scores, useful_mask,
)

from _recommend_helpers import (
    BB_RIG, FULL_MOON_NIGHT, LONG_RIG, NB_RIG, OSC_RIG, cand, make_inputs, ngc7000_rows, standard_pool,
)


# --- Moon ------------------------------------------------------------------------

def test_required_separation_lorentzian():
    assert required_separation(29.53 / 2, 120, 14) == pytest.approx(120, abs=0.1)
    assert required_separation(0.0, 120, 14) < 60
    assert required_separation(0.0, 40, 10) < required_separation(7.0, 40, 10) < 40


def test_soft_moon_just_short_is_half():
    req = 59.3
    f = moon_factor(np.array([req - 0.1, req + 20, req - 20]), np.array([True, True, True]), req)
    assert f[0] == pytest.approx(0.5, abs=0.02)
    assert f[1] > 0.98 and f[2] < 0.02
    assert moon_factor(np.array([1.0]), np.array([False]), req)[0] == 1.0


def test_emission_on_narrowband_rig_beats_broadband_at_full_moon():
    """The hard MOON filter is generous (half the required distance); the full rule drives hours and score."""
    pool = CandidatePool([cand("NGC7000", 314.7, 44.3, 120.0, "EMISSION"),
                          cand("GAL", 314.7, 44.3, 60.0, "GALAXY")])
    res = recommend(make_inputs(pool=pool, rigs=[NB_RIG]), Params())
    keys = {p.key: p for p in res.ranked}
    assert "NGC7000" in keys and keys["NGC7000"].mode in ("HA", "SII")
    assert keys["NGC7000"].available_hours > 3
    gal = keys["GAL"]                       # 59 deg from a full Moon: kept, but no clear hours
    assert gal.mode == "BB" and gal.available_hours < 0.5 and gal.score < keys["NGC7000"].score


def test_moon_hard_exclusion_only_close_to_the_moon():
    # 2026-09-26 full Moon near RA 5 h... use a target a few degrees from the Moon's position at midnight.
    from app.services.recommend import prepare_night
    inputs = make_inputs(rigs=[OSC_RIG])
    prep = prepare_night(inputs, Params())
    eph = prep.ctx.eph
    mid = int(np.flatnonzero(prep.ctx.dark_mask)[len(np.flatnonzero(prep.ctx.dark_mask)) // 2])
    near = CandidatePool([cand("NEAR", float(eph.moon_ra[mid]), float(eph.moon_dec[mid]) + 5.0, 60.0, "GALAXY")])
    res = recommend(make_inputs(pool=near, rigs=[OSC_RIG]), Params())
    assert res.excluded.get("NEAR") in ("MOON", "BELOW_HORIZON")


def test_osc_rig_at_full_moon_near_moon_picks_have_no_clear_hours():
    res = recommend(make_inputs(rigs=[OSC_RIG]), Params())
    for p in res.ranked:
        assert p.mode == "OSC"
        if p.moon_sep_min_deg is not None and p.moon_sep_min_deg < 60:
            assert p.available_hours < 0.5


def test_bright_tier_penalises_broadband():
    ok = tier_class_mask("BRIGHT")
    assert ok[CLASS_INDEX["BB"]] and ok[CLASS_INDEX["OSC"]] and ok[CLASS_INDEX["HA"]]
    assert not tier_class_mask("NONE").any()
    res = recommend(make_inputs(rigs=[NB_RIG, OSC_RIG], night=date(2026, 6, 21)), Params())
    assert res.context.tier in ("BRIGHT", "NAUTICAL")
    if res.context.tier == "BRIGHT":
        assert res.ranked
        bb = [p for p in res.ranked if p.mode in ("BB", "OSC")]
        assert bb and all(p.components["observability"] <= 0.3 + 1e-9 for p in bb)
        assert all(any(r["code"] == "TIER" for r in p.reasons) for p in res.lanes[0]["items"])
    assert res.context.tier_note


# --- Framing ------------------------------------------------------------------

def test_framing_small_big_unknown():
    size = np.array([1.0, 1000.0, np.nan, 0.5 * NB_RIG.short_side_arcmin])
    fit, ratio, px = framing(size, NB_RIG)
    assert px[0] < 40                              # too small
    assert ratio[1] > 3                            # too big
    assert fit[2] == 0.4 and np.isnan(ratio[2])    # unknown size
    assert fit[3] == pytest.approx(1.0)            # ideal fill

    pool = CandidatePool([cand("TINY", 314.7, 44.3, 0.3, "GALAXY"), cand("HUGE", 314.7, 44.3, 2000.0, "EMISSION")])
    res = recommend(make_inputs(pool=pool, rigs=[NB_RIG], night=date(2026, 10, 10)), Params())
    assert res.excluded == {"TINY": "TOO_SMALL", "HUGE": "TOO_BIG"}
    assert res.excluded_counts["TOO_SMALL"] == 1 and res.excluded_counts["TOO_BIG"] == 1


# --- Project / momentum / urgency ------------------------------------------------

def _hist(rows, night=FULL_MOON_NIGHT, pool=None, masters=None):
    pool = pool or standard_pool()
    history = build_history(rows, masters=masters)
    goals = infer_goals(history, {c.key: c.kind for c in pool.candidates})
    return pool, history_arrays(pool, history, goals, night)


def _eval(pool, hist, avail=5.0):
    n = len(pool)
    feats = NightFeatures(usable_h=np.full(n, 6.0), moon_ok_h=np.full((n, 5), avail, dtype=np.float32),
                          weighted_h=np.full((n, 5), 4.0, dtype=np.float32), max_alt=np.full(n, 60.0),
                          best_idx=np.zeros(n, dtype=int), sep_min=np.full(n, np.nan), limit=None, required={},
                          step_h=5 / 60)
    return evaluate_rig(NB_RIG, pool, feats, hist, np.zeros(n), "ASTRO", Weights(), useful_mask(pool))


def test_project_one_old_short_test_is_not_a_project():
    old = FULL_MOON_NIGHT - timedelta(days=400)
    pool, hist = _hist([HistoryRow("NGC7000", old, "Ha", 1200.0)])
    i = pool.index["NGC7000"]
    assert not hist.committed[i]
    assert _eval(pool, hist).components["project"][i] == 0.0


def test_project_two_nights_is_a_project():
    old = FULL_MOON_NIGHT - timedelta(days=400)
    pool, hist = _hist([HistoryRow("NGC7000", old, "Ha", 1200.0),
                        HistoryRow("NGC7000", old + timedelta(days=1), "Ha", 1200.0)])
    i = pool.index["NGC7000"]
    assert hist.committed[i]
    assert _eval(pool, hist).components["project"][i] > 0.0


def test_project_zero_when_well_over_goal():
    rows = [HistoryRow("NGC7000", FULL_MOON_NIGHT - timedelta(days=d), "Ha", 3600.0 * 5) for d in range(10, 14)]
    pool, hist = _hist(rows)             # 20 h vs the 10 h default goal
    i = pool.index["NGC7000"]
    assert hist.goal_h[i] == 10.0 and hist.total_h[i] == 20.0
    assert _eval(pool, hist).components["project"][i] == 0.0

    rows = [HistoryRow("NGC7000", FULL_MOON_NIGHT - timedelta(days=d), "Ha", 3600.0 * 3) for d in range(10, 14)]
    pool, hist = _hist(rows)             # 12 h: over goal but < 1.5x -> reduced, not zero
    p = _eval(pool, hist).components["project"][i]
    assert 0.0 < p < 0.5


def test_momentum_decay():
    m = momentum_score([0, 45, 135, 136, np.nan])
    assert m[0] == 1.0
    assert m[1] == pytest.approx(math.exp(-1), abs=1e-6)
    assert m[2] == pytest.approx(math.exp(-3), abs=1e-6)
    assert m[3] == 0.0 and m[4] == 0.0            # zero beyond 3 x tau
    m = momentum_score([10, 30, 31], tau_days=10)
    assert m[0] == pytest.approx(math.exp(-1)) and m[1] > 0 and m[2] == 0.0


def test_recency_rank():
    from app.services.recommend.scoring import recency_rank_score

    r = recency_rank_score([5, np.nan, 1, 5, 40])
    assert r.tolist() == [pytest.approx(1 / 2), 0.0, 1.0, pytest.approx(1 / 2), pytest.approx(1 / 4)]


def test_urgency():
    usable = np.array([6.0, 6.0, 2.0])
    future = np.array([[6.0] * 3 + [0.0] * 9, [6.0] * 12, [6.0] * 3 + [0.0] * 9])
    urgency, weeks = urgency_scores(usable, future)
    assert weeks.tolist() == [4.0, 12.0, 4.0]
    assert urgency[0] == pytest.approx(1 - 4 / 12)
    assert urgency[1] == 0.0
    assert urgency[2] == 0.0          # tonight well below the coming weeks: no urgency


def test_active_project_regression_full_moon():
    """§11 item 3 in miniature: NGC7000 tops a full-Moon night on a narrowband rig, with an ACTIVE reason."""
    res = recommend(make_inputs(rows=ngc7000_rows(), rigs=[NB_RIG, OSC_RIG, LONG_RIG]), Params())
    top3 = [p.key for p in res.ranked[:3]]
    assert "NGC7000" in top3
    hero = res.ranked[0]
    assert hero.key == "NGC7000" and hero.mode in ("HA", "SII")
    assert any(r["code"] == "ACTIVE" for r in hero.reasons)
    for p in res.ranked:
        if p.mode in ("BB", "OSC") and p.moon_sep_min_deg is not None and p.moon_sep_min_deg < 60:
            assert p.available_hours < 0.5 and p.score < hero.score


def test_osc_only_full_moon_is_empty_or_dont_bother():
    res = recommend(make_inputs(rows=ngc7000_rows(), rigs=[OSC_RIG]), Params())
    assert not res.ranked or res.verdict[0] == "DONT_BOTHER"


def test_best_rig_and_alternatives():
    res = recommend(make_inputs(rigs=[NB_RIG, BB_RIG], night=date(2026, 10, 10)), Params())
    p = next(p for p in res.ranked if p.key == "NGC7000")
    assert p.rig_id in (NB_RIG.id, BB_RIG.id)
    for alt in p.alternatives:
        assert alt["score"] <= p.score + 1e-9 and alt["rig_id"] != p.rig_id


# --- Rig specs and selection ------------------------------------------------------------

def test_rig_spec_classes_and_scale():
    spec, reason = rig_spec(1, "OSC", pixel_um=3.2, width_px=6960, height_px=4640, focal_mm=105, is_color=True)
    assert reason is None and spec.classes == {"OSC"} and spec.scale_arcsec == pytest.approx(6.29, abs=0.01)
    spec, _ = rig_spec(2, "Mono", pixel_um=3.8, width_px=4656, height_px=3520, focal_mm=346, is_color=False)
    assert spec.classes == {"BB"}
    spec, _ = rig_spec(2, "Mono", pixel_um=3.8, width_px=4656, height_px=3520, focal_mm=346, is_color=False,
                       seen_classes=["HA", "OIII"])
    assert spec.classes == {"HA", "OIII"}
    spec, _ = rig_spec(3, "Duo", pixel_um=3.8, width_px=4656, height_px=3520, focal_mm=346, is_color=True,
                       filter_bands=["Duo", "L"])
    assert spec.classes == {"HA", "OIII", "BB"}
    spec, _ = rig_spec(4, "Measured", pixel_um=None, width_px=4144, height_px=2822, focal_mm=None,
                       measured_scale=0.68, binning=2)
    assert spec.scale_arcsec == 0.68 and spec.fov_w_deg == pytest.approx(4144 / 2 * 0.68 / 3600)
    assert rig_spec(5, "No scale", pixel_um=None, width_px=100, height_px=100, focal_mm=None) == (None, "no pixel scale")
    assert rig_spec(6, "No size", pixel_um=3.8, width_px=None, height_px=None, focal_mm=346)[1].startswith("no sensor")


def test_select_rigs_modes():
    recs = [RigRecord(1, "A", True, False, NB_RIG, None), RigRecord(2, "B", True, False, OSC_RIG, None),
            RigRecord(3, "C", False, False, LONG_RIG, None), RigRecord(7, "D", True, False, None, "no pixel scale")]
    specs, mode, skipped = select_rigs(recs, "mounted")
    assert mode == "ALL_FALLBACK" and [s.id for s in specs] == [1, 2]
    assert skipped == [{"id": 7, "name": "D", "reason": "no pixel scale"}]
    recs[1].is_mounted = True
    specs, mode, _ = select_rigs(recs, "mounted")
    assert mode == "MOUNTED" and [s.id for s in specs] == [2]
    assert select_rigs(recs, "all")[1] == "ALL"
    specs, mode, _ = select_rigs(recs, "3")
    assert mode == "SINGLE" and [s.id for s in specs] == [3]
    with pytest.raises(RecommendationError) as e:
        select_rigs(recs, "99")
    assert e.value.status == 404
    with pytest.raises(RecommendationError):
        select_rigs(recs, "nonsense")


def test_moon_rules_defaults_match_spec():
    assert MOON_RULES["BB"] == (120.0, 14.0) and MOON_RULES["HA"] == (40.0, 10.0)
    assert MOON_RULES["SII"] == (40.0, 10.0) and MOON_RULES["OIII"] == (70.0, 10.0)
    w = Weights().as_dict()
    assert w == {"observability": 0.25, "framing": 0.20, "project": 0.20, "momentum": 0.15, "urgency": 0.15,
                 "prior": 0.10, "recency_rank": 0.0}
