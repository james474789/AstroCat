"""R1 §4.7: lanes, diversity, hero verdict, reasons."""

from datetime import date, datetime

from app.services.recommend import Params, recommend
from app.services.recommend.lanes import (
    LANE_ACTIVE, LANE_CONTINUE, LANE_LAST_CHANCE, LANE_MOON_PROOF, LANE_OTHER, Pick, build_lanes, diversify,
    lane_for, pick_reasons, verdict,
)

from _recommend_helpers import NB_RIG, OSC_RIG, cand, make_inputs, ngc7000_rows


def pick(key="X", ra=10.0, dec=10.0, score=0.5, mode="HA", momentum=0.0, project=0.0, urgency=0.0,
         committed=False, have=0.0, goal=10.0, avail=5.0, **kw):
    base = dict(
        candidate=cand(key, ra, dec), rig_id=1, rig_name="Mono 200mm", mode=mode, score=score,
        components={"observability": 0.8, "framing": 0.9, "project": project, "momentum": momentum,
                    "urgency": urgency, "prior": 0.55},
        usable_hours=6.0, available_hours=avail, best_time_utc=datetime(2026, 9, 26, 22, 0), max_alt_deg=70.0,
        moon_sep_min_deg=59.2, fill_ratio=0.46, have_hours=have, have_by_filter={}, nights=11 if have else 0,
        last_imaged=None, goal_hours=goal, goal_source="INFERRED", committed=committed, days_since=None,
        weeks_left=12.0,
    )
    base.update(kw)
    return Pick(**base)


def test_lane_priority():
    full = dict(moon_up_dark_frac=1.0, moon_illum=0.99)
    assert lane_for(pick(momentum=0.5, committed=True, project=0.9, urgency=0.9, have=2), **full) == LANE_ACTIVE
    assert lane_for(pick(momentum=0.5, committed=False, project=0.9, urgency=0.9), **full) == LANE_LAST_CHANCE
    assert lane_for(pick(momentum=0.2, committed=True, project=0.3, have=4, urgency=0.9), **full) == LANE_CONTINUE
    assert lane_for(pick(committed=True, project=0.3, have=12, goal=10, urgency=0.9), **full) == LANE_LAST_CHANCE
    assert lane_for(pick(mode="HA"), **full) == LANE_MOON_PROOF
    assert lane_for(pick(mode="BB"), **full) == LANE_OTHER
    assert lane_for(pick(mode="HA"), moon_up_dark_frac=0.3, moon_illum=0.99) == LANE_OTHER


def test_each_target_in_exactly_one_lane():
    res = recommend(make_inputs(rows=ngc7000_rows(), rigs=[NB_RIG, OSC_RIG]), Params(per_lane=20))
    seen = [p.key for lane in res.lanes for p in lane["items"]]
    assert len(seen) == len(set(seen))
    assert res.lanes[0]["id"] == LANE_ACTIVE and res.lanes[0]["items"][0].key == "NGC7000"
    ids = [lane["id"] for lane in res.lanes]
    assert ids == sorted(ids, key=[LANE_ACTIVE, LANE_CONTINUE, LANE_LAST_CHANCE, LANE_MOON_PROOF, LANE_OTHER].index)
    assert all(lane["items"] for lane in res.lanes)


def test_diversity_pushes_a_third_crowded_pick_down():
    a = pick("A", 310, 44, 0.9)
    b = pick("B", 312, 44, 0.8)
    c = pick("C", 314, 45, 0.7)       # within 10 deg of A and B
    d = pick("D", 100, 20, 0.6)       # far away
    e = pick("E", 316, 43, 0.5)
    assert [p.key for p in diversify([a, b, c, d, e])] == ["A", "B", "D", "C", "E"]
    assert [p.key for p in diversify([a, b, d])] == ["A", "B", "D"]


def test_lane_limits():
    picks = [pick(f"K{i}", ra=i * 30 % 360, dec=0, score=1 - i / 100, mode="BB") for i in range(30)]
    lanes = build_lanes(picks, 0.0, 0.0, per_lane=6)
    assert [lane["id"] for lane in lanes] == [LANE_OTHER]
    assert len(lanes[0]["items"]) == 10


def test_verdict_thresholds():
    assert verdict(pick(avail=3.5), "ASTRO")[0] == "GO"
    assert verdict(pick(avail=3.5), "NAUTICAL")[0] == "GO"
    assert verdict(pick(avail=3.5), "BRIGHT")[0] == "MARGINAL"
    assert verdict(pick(avail=2.0), "ASTRO")[0] == "MARGINAL"
    assert verdict(pick(avail=1.0), "ASTRO")[0] == "DONT_BOTHER"
    assert verdict(pick(avail=1.0), "BRIGHT")[0] == "MARGINAL"
    assert verdict(None, "ASTRO") == ("DONT_BOTHER", [{"code": "NO_PICKS",
                                                       "text": "No target passes the feasibility checks tonight"}])
    level, reasons = verdict(None, "NONE")
    assert level == "DONT_BOTHER" and reasons[0]["code"] == "TIER"
    level, reasons = verdict(pick(avail=5.8), "NAUTICAL")
    assert [r["code"] for r in reasons] == ["MOON_CLEAR", "TIER"]


def test_reason_texts():
    p = pick(have=6.4, goal=15.0, momentum=0.49, days_since=32, committed=True, urgency=0.6, weeks_left=5.0,
             avail=5.8, moon_limited=("OIII", 59.2), nights=11)
    reasons = pick_reasons(p, "NAUTICAL")
    texts = {r["code"]: r["text"] for r in reasons}
    assert texts["MOON_CLEAR"] == "5.8 h clear of Moon (Ha)"
    assert texts["ACTIVE"] == "last imaged 32 days ago"
    assert texts["HAVE"] == "6.4 h over 11 nights; goal ≈ 15 h (inferred)"
    assert texts["WINDOW"] == "window closes in ~5 wk"
    assert texts["MOON_MARGINAL"] == "Moon 59° away; OIII limited"
    assert texts["FRAMING"] == "fills 46% of Mono 200mm"
    assert texts["TIER"] == "nautical darkness only"
    assert reasons[0]["code"] == "MOON_CLEAR"


def test_no_rigs_gives_dont_bother():
    res = recommend(make_inputs(rigs=[]), Params())
    assert res.hero is None and res.lanes == [] and res.verdict[0] == "DONT_BOTHER"
    assert res.verdict[1][0]["code"] == "NO_RIGS"
