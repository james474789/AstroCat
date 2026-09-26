"""R1 §5: the pure replay loop and its metrics."""

import json
import random
from datetime import date, timedelta

import pytest

from app.services.recommend import Params
from app.services.recommend.history import HistoryRow, build_history, sort_rows
from app.services.recommend.replay import (
    NOT_IN_POOL, NightOutcome, ReplayData, ReplayNight, aggregate, all_metrics, build_report, format_table,
    grid_search, moon_bucket, rank_metrics, random_metrics, replay_nights, rig_nights_index, rigs_near, run_replay,
    weight_grid,
)
from app.services.recommend.scoring import Weights

from _recommend_helpers import BB_RIG, FLAT, NB_RIG, OSC_RIG, SITE, standard_pool


def row(key, night, secs=3600.0, flt="Ha", rig=1, site=1):
    return HistoryRow(key, night, flt, secs, rig, False, site)


# --- nights ----------------------------------------------------------------------

def test_replay_nights_thresholds_and_majorities():
    n1, n2, n3 = date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3)
    rows = [row("NGC7000", n1, 2400), row("M31", n1, 600, rig=2, site=None),
            row("M31", n2, 1000), row("M33", n2, 1000, rig=None),      # >= 30 min total, no target >= 30 min
            row("IC1396", n3, 1200, rig=None, site=None), row("IC1396", n3, 1500, rig=4, site=None)]
    nights = replay_nights(rows)
    assert [n.night for n in nights] == [n1, n3]
    first = nights[0]
    assert first.actual == frozenset({"NGC7000"}) and first.rig_id == 1 and first.site_id == 1
    third = nights[1]
    assert third.actual == frozenset({"IC1396"}) and third.rig_id == 4 and third.site_id is None
    assert replay_nights(rows, since=n2) == [third]
    assert replay_nights(rows, until=n2) == [first]


def test_rigs_near_window():
    rows = [row("A", date(2024, 1, 1), rig=1), row("A", date(2026, 1, 1), rig=2), row("A", date(2025, 6, 1), rig=3)]
    idx = rig_nights_index(rows)
    assert rigs_near(idx, date(2024, 6, 1)) == [1, 3]
    assert rigs_near(idx, date(2027, 6, 1)) == []


# --- as-of honesty ------------------------------------------------------------

def test_as_of_truncation_never_leaks_the_night_itself():
    rows = sort_rows([row("NGC7000", date(2025, 9, d)) for d in range(1, 11)])
    for cut in (date(2025, 9, 1), date(2025, 9, 5), date(2025, 9, 11)):
        for presorted in (True, False):
            h = build_history(rows, as_of=cut, presorted=presorted)
            nights = h["NGC7000"].nights if "NGC7000" in h else set()
            assert all(n < cut for n in nights)
            assert len(nights) == (cut - date(2025, 9, 1)).days


def _data(rows, rigs=(NB_RIG, OSC_RIG)):
    return ReplayData(pool=standard_pool(), rows=sort_rows(rows), masters={}, goal_rows=[], sites={1: SITE},
                      default_site_id=1, horizons={1: FLAT}, rig_specs={r.id: r for r in rigs},
                      active_rig_ids=[r.id for r in rigs])


def test_inputs_for_uses_only_earlier_nights_and_the_nights_rig():
    rows = [row("NGC7000", date(2025, 9, d), rig=1) for d in range(1, 6)] + [row("M31", date(2025, 9, 5), rig=2)]
    data = _data(rows)
    nights = replay_nights(data.rows)
    target = next(n for n in nights if n.night == date(2025, 9, 5))
    inputs, mode, known = data.inputs_for(target)
    assert all(n < target.night for h in inputs.history.values() for n in h.nights)
    assert "M31" not in inputs.history
    assert inputs.site.id == 1
    # Majority rig: NGC7000 and M31 tie (1 h each): no majority -> rigs used within +/- 365 days.
    assert mode == "ALL" and not known
    first = data.inputs_for(nights[0])
    assert first[1] == "SINGLE" and first[2] is True and first[0].rigs == [NB_RIG]


def test_goals_created_after_the_night_are_ignored():
    from datetime import datetime
    from app.services.recommend.history import GoalRow

    data = _data([row("NGC7000", date(2025, 9, 1)), row("NGC7000", date(2025, 9, 3))])
    data.goal_rows = [GoalRow("NGC7000", "ANY", 20 * 3600.0, datetime(2025, 9, 2, 10, 0))]
    n1, n3 = replay_nights(data.rows)
    assert data.inputs_for(n1)[0].goals.goal_for("NGC7000", "EMISSION")[1] != "SET"
    assert data.inputs_for(n3)[0].goals.goal_for("NGC7000", "EMISSION") == (20.0, "SET")


# --- metrics ---------------------------------------------------------------------

def _outcome(night, actual, ranked, in_pool=None, tier="ASTRO", illum=0.1, known=True, last=None, usable=None):
    return NightOutcome(night=night, tier=tier, moon_illum=illum, rig_known=known, actual=frozenset(actual),
                        in_pool=frozenset(in_pool if in_pool is not None else actual), ranked=list(ranked),
                        last_night=last or {k: None for k in ranked}, usable_h=usable or {k: 1.0 for k in ranked},
                        misses=[])


def test_rank_metrics():
    m = rank_metrics(["A", "B", "C", "D"], {"C"})
    assert (m["hit@1"], m["hit@3"], m["hit@5"], m["hit@10"], m["mrr"]) == (0.0, 1.0, 1.0, 1.0, pytest.approx(1 / 3))
    assert rank_metrics(["A"], {"Z"}) == {"hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "hit@10": 0.0, "mrr": 0.0}


def test_metrics_on_three_nights():
    outcomes = [
        _outcome(date(2025, 1, 1), {"A"}, ["A", "B", "C"]),                 # rank 1
        _outcome(date(2025, 1, 2), {"C", "X"}, ["A", "B", "C"]),            # rank 3; X feasible? no
        _outcome(date(2025, 1, 3), {"Z"}, ["A", "B"], in_pool={"Z"}),       # miss (Z infeasible)
    ]
    m = aggregate(outcomes, lambda o: o.ranked)
    assert m["hit@1"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["hit@3"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)
    # in-pool actual pairs: A, C, X, Z -> feasible A, C
    assert m["feasible_recall"] == pytest.approx(0.5)
    assert m["nights"] == 3


def test_baselines_are_deterministic_with_a_seed():
    last = {"A": date(2024, 1, 1), "B": date(2025, 6, 1), "C": None}
    usable = {"A": 2.0, "B": 1.0, "C": 7.0}
    o = _outcome(date(2025, 7, 1), {"C"}, ["A", "B", "C"], last=last, usable=usable)
    assert o.baseline("recency") == ["B", "A", "C"]
    assert o.baseline("altitude") == ["C", "A", "B"]
    assert o.baseline("random", random.Random(3)) == o.baseline("random", random.Random(3))
    outs = [o, _outcome(date(2025, 7, 2), {"A"}, ["A", "B", "C", "D", "E"])]
    assert random_metrics(outs, seed=7) == random_metrics(outs, seed=7)
    assert all_metrics(outs, seed=1)["altitude"]["hit@1"] == 1.0
    assert all_metrics(outs, seed=1)["recency"]["hit@1"] == 0.5


def test_report_shape_matches_contract():
    outs = [_outcome(date(2025, 1, 1), {"A"}, ["A", "B"], illum=0.9),
            _outcome(date(2026, 1, 2), {"B"}, ["A", "B"], tier="NAUTICAL", known=False)]
    outs[1].misses.append({"night": "2026-01-02", "target_key": "Q", "reason": NOT_IN_POOL})
    rep = build_report(outs, Params())
    keys = {"hit@1", "hit@3", "hit@5", "hit@10", "mrr", "feasible_recall"}
    assert {"generated_at", "nights", "params", "metrics", "baselines", "breakdown", "misses"} <= set(rep)
    assert set(rep["metrics"]) == keys
    assert set(rep["baselines"]) == {"recency", "altitude", "random"}
    assert all(set(v) == keys for v in rep["baselines"].values())
    assert set(rep["breakdown"]) == {"tier", "moon", "year", "rig_known", "site_class", "source"}
    assert set(rep["breakdown"]["moon"]) == {"<0.25", ">0.75"}
    assert rep["misses"] == [{"night": "2026-01-02", "target_key": "Q", "reason": NOT_IN_POOL}]
    assert rep["params"]["weights"]["observability"] == 0.25
    json.dumps(rep)
    assert "engine" in format_table(rep)
    assert moon_bucket(0.5) == "0.25-0.75"


# --- end to end (pure engine) ------------------------------------------------------

def _history_rows():
    rows = []
    start = date(2025, 9, 1)
    for d in range(0, 30, 3):
        rows.append(row("NGC7000", start + timedelta(days=d), 3 * 3600.0))
    for d in range(1, 30, 5):
        rows.append(row("IC1396", start + timedelta(days=d), 2 * 3600.0))
    rows.append(row("M31", date(2025, 10, 20), 2 * 3600.0, flt="L", rig=4))
    rows.append(row("OBJ:COMET", date(2025, 9, 20), 2 * 3600.0))
    return rows


def test_run_replay_end_to_end_and_grid():
    data = _data(_history_rows(), rigs=(NB_RIG, OSC_RIG, BB_RIG))
    nights = replay_nights(data.rows)
    assert len(nights) >= 10
    outcomes = run_replay(nights, data.inputs_for)
    assert len(outcomes) == len(nights)
    rep = build_report(outcomes, Params())
    assert 0.0 <= rep["metrics"]["hit@5"] <= 1.0
    assert any(m["reason"] == NOT_IN_POOL and m["target_key"] == "OBJ:COMET" for m in rep["misses"])
    assert rep["pool_coverage"] < 1.0
    for o in outcomes:
        assert set(o.ranked) >= (o.in_pool & set(o.ranked))

    grid = weight_grid(Weights(), factors=(1.0, 2.0), keys=("momentum",))
    assert len(grid) == 2 and grid[1].momentum == 0.3
    res = grid_search(nights[:4], data.inputs_for, Params(), weights=grid, top=2)
    top = res["top"]
    assert len(top) == 2 and res["evaluated"] == 2 and res["moon_stage"] == []
    assert top[0]["metrics"]["hit@5"] >= top[1]["metrics"]["hit@5"]
    assert set(top[0]["recency"]) >= {"hit@5", "mrr"}


# --- tuning round: headline set, pair classes, miss details, grid ---------------------

from app.services.recommend.replay import (  # noqa: E402
    HOME, REMOTE, SOURCE_HEADER, SOURCE_MATCH, UNKNOWN_SITE, PairRow, classify_pairs, headline, moon_bb_grid,
    pair_counts, tuning_grid, TUNING_SPACE,
)


def test_classify_pairs_home_remote_unknown_and_source():
    n = date(2025, 3, 1)
    rows = [PairRow("M42", n, 56.0, "HEADER", 3000), PairRow("M42", n, None, "MATCH", 1000),
            PairRow("NGC3372", n, -30.5, "MATCH", 3600),
            PairRow("M81", n, None, "HEADER_RAW", 3600), PairRow("M81", n, 56.1, "HEADER", 100),
            PairRow("OBJ:IC13961", n, 55.4, "MANUAL", 1800)]
    info = classify_pairs(rows, [56.0], key_map={"OBJ:IC13961": "IC1396"})
    assert info[("M42", n)] == (HOME, SOURCE_HEADER)
    assert info[("NGC3372", n)] == (REMOTE, SOURCE_MATCH)
    assert info[("M81", n)] == (UNKNOWN_SITE, SOURCE_HEADER)
    assert info[("IC1396", n)] == (HOME, SOURCE_HEADER)       # folded key, within 1.5 deg


def test_headline_excludes_remote_pairs():
    o_home = _outcome(date(2025, 1, 1), {"A"}, ["A", "B"])
    o_home.pair_class = {"A": HOME}
    o_mixed = _outcome(date(2025, 1, 2), {"B", "Z"}, ["B"], in_pool={"B", "Z"})
    o_mixed.pair_class = {"B": UNKNOWN_SITE, "Z": REMOTE}
    o_remote = _outcome(date(2025, 1, 3), {"Q"}, ["A"], in_pool={"Q"})
    o_remote.pair_class = {"Q": REMOTE}
    outs = [o_home, o_mixed, o_remote]
    m = aggregate(outs, lambda o: o.ranked, headline)
    assert m["nights"] == 2 and m["hit@1"] == 1.0 and m["feasible_recall"] == 1.0
    r = aggregate(outs, lambda o: o.ranked, lambda o, k: o.pair_class.get(k) == REMOTE)
    assert r["nights"] == 2 and r["hit@10"] == 0.0 and r["feasible_recall"] == 0.0
    rep = build_report(outs, Params())
    assert rep["nights"] == 2 and rep["nights_total"] == 3
    assert rep["remote"]["nights"] == 2
    assert pair_counts(outs)["site_class"] == {HOME: 1, REMOTE: 2, UNKNOWN_SITE: 1}
    assert set(rep["breakdown"]["site_class"]) == {HOME, REMOTE, UNKNOWN_SITE}


def test_misses_carry_details_and_classes():
    from app.services.recommend.candidates import CandidatePool
    from _recommend_helpers import cand

    pool = CandidatePool([cand("NGC7000", 314.7, 44.3, 120.0, "EMISSION"),
                          cand("SOUTH", 100.0, -60.0, 30.0, "GALAXY")])
    rows = [row("NGC7000", date(2025, 9, 1)), row("SOUTH", date(2025, 9, 2)), row("NGC7000", date(2025, 9, 2))]
    data = ReplayData(pool=pool, rows=sort_rows(rows), masters={}, goal_rows=[], sites={1: SITE},
                      default_site_id=1, horizons={1: FLAT}, rig_specs={1: NB_RIG}, active_rig_ids=[1],
                      pair_info={("SOUTH", date(2025, 9, 2)): (REMOTE, SOURCE_MATCH)})
    outcomes = run_replay(replay_nights(data.rows), data.inputs_for, pair_info=data.pair_info)
    miss = next(m for o in outcomes for m in o.misses if m["target_key"] == "SOUTH")
    assert miss["reason"] == "BELOW_HORIZON" and miss["site_class"] == REMOTE and miss["source"] == SOURCE_MATCH
    d = miss["details"]
    assert d["rig_id"] == 1 and (d["max_alt_deg"] is None or d["max_alt_deg"] < 15)
    assert {"mode", "moon_sep_min_deg", "required_sep_deg", "usable_hours", "target_px"} <= set(d)
    rep = build_report(outcomes, Params())
    assert rep["miss_reasons"].get("BELOW_HORIZON") is None          # remote misses leave the headline
    assert rep["remote"]["miss_reasons"] == {"BELOW_HORIZON": 1}


def test_tuning_grid_space_and_sampling():
    combos = tuning_grid()
    assert len(combos) == 288
    taus = {tau for _, tau in combos}
    assert taus == set(TUNING_SPACE["momentum_tau_days"])
    w, tau = combos[0]
    assert w.urgency == 0.075 and w.momentum in TUNING_SPACE["momentum"]
    sample = tuning_grid(max_combos=20, seed=3)
    assert len(sample) == 20 and sample[0] == combos[0] and sample == tuning_grid(max_combos=20, seed=3)


def test_moon_bb_grid():
    rules = moon_bb_grid()
    assert [r["BB"] for r in rules] == [(60.0, 14.0), (90.0, 14.0), (120.0, 14.0)]
    assert all(r["OSC"] == r["BB"] and r["HA"] == (40.0, 10.0) for r in rules)


def test_grid_with_moon_stage():
    data = _data(_history_rows(), rigs=(NB_RIG, OSC_RIG, BB_RIG))
    nights = replay_nights(data.rows)[:3]
    combos = tuning_grid(max_combos=2)
    res = grid_search(nights, data.inputs_for, Params(), combos=combos, moon_rules=moon_bb_grid(), top=5)
    assert len(res["moon_stage"]) == 3 and res["evaluated"] == 2 and len(res["top"]) == 2
    assert {row["moon_rules"]["BB"]["D"] for row in res["moon_stage"]} == {60.0, 90.0, 120.0}


def test_grid_warm_keep_matches_cold():
    data = _data(_history_rows(), rigs=(NB_RIG, OSC_RIG, BB_RIG))
    nights = replay_nights(data.rows)[:3]
    combos = tuning_grid(max_combos=3)
    cold = grid_search(nights, data.inputs_for, Params(), combos=combos)
    keep = {}
    run_replay(nights, data.inputs_for, keep=keep)
    calls = []

    def counting(nr):
        calls.append(nr)
        return data.inputs_for(nr)

    warm = grid_search(nights, counting, Params(), combos=combos, warm_keep=keep)
    assert calls == []
    assert [r["metrics"] for r in warm["top"]] == [r["metrics"] for r in cold["top"]]
