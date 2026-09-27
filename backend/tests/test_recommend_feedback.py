"""R2a §4 and §9: per-user feedback applied after scoring (pure; no database)."""

import json
from datetime import timedelta

import pytest

from app.services.recommend import Params, recommend, render_payload, result_payload, result_to_dict
from app.services.recommend.feedback import (
    DISMISS, IMAGED, PIN, SNOOZE, UNDISMISS, UNPIN, UNSNOOZE, FeedbackError, FeedbackState, TargetState,
    apply_feedback, assemble_lanes, choose_hero, event_payload, impression_rows, transition, validate_action,
)
from app.services.recommend.lanes import LANE_OTHER, LANE_PINNED
from app.schemas.recommendations import RecommendationsResponse

from _recommend_helpers import FULL_MOON_NIGHT, NB_RIG, OSC_RIG, make_inputs, ngc7000_rows
from test_recommend_lanes import pick

NIGHT = FULL_MOON_NIGHT


@pytest.fixture(scope="module")
def result():
    return recommend(make_inputs(rows=ngc7000_rows(), rigs=[NB_RIG, OSC_RIG]), Params(rig_mode="ALL"))


@pytest.fixture(scope="module")
def payload(result):
    # Through JSON, exactly as the Redis cache stores it.
    return json.loads(json.dumps(result_payload(result), default=str))


def _lane_keys(body):
    return {lane["id"]: [p["target_key"] for p in lane["items"]] for lane in body["lanes"]}


def _all_keys(body):
    return [p["target_key"] for lane in body["lanes"] for p in lane["items"]]


# --- state ------------------------------------------------------------------------

def test_snooze_hides_until_the_until_night():
    fb = FeedbackState(snoozed={"M42": NIGHT + timedelta(days=7)})
    for d in range(7):
        assert fb.hidden_reason("M42", NIGHT + timedelta(days=d)) == "SNOOZED"
    assert fb.hidden_reason("M42", NIGHT + timedelta(days=7)) is None
    assert fb.counts(NIGHT) == {"pinned": 0, "snoozed": 1, "dismissed": 0}
    assert fb.counts(NIGHT + timedelta(days=7))["snoozed"] == 0


def test_dismiss_hides_every_night():
    fb = FeedbackState(dismissed={"M42": "NOT_MY_TYPE"})
    for d in (-400, 0, 1, 30, 4000):
        assert fb.hidden_reason("M42", NIGHT + timedelta(days=d)) == "DISMISSED"
    assert fb.target_feedback("M42") == {"pinned": False, "snoozed_until": None, "dismissed": True,
                                         "dismiss_reason": "NOT_MY_TYPE"}


def test_from_rows_accepts_mappings_and_objects():
    from types import SimpleNamespace

    rows = [{"target_key": "A", "pinned": True, "snoozed_until": None, "dismissed": False, "dismiss_reason": None},
            SimpleNamespace(target_key="B", pinned=False, snoozed_until=NIGHT, dismissed=True, dismiss_reason="DONE")]
    fb = FeedbackState.from_rows(rows)
    assert fb.pinned == frozenset({"A"}) and fb.snoozed == {"B": NIGHT} and fb.dismissed == {"B": "DONE"}


# --- rendering the cached payload -------------------------------------------------

def test_empty_state_reproduces_r1(result, payload):
    body = render_payload(payload, FeedbackState.empty(), result.params.per_lane)
    RecommendationsResponse.model_validate(body)
    assert body["hero"]["target_key"] == result.hero.key
    assert [(lane["id"], [p.key for p in lane["items"]]) for lane in result.lanes] == \
        [(lane["id"], [p["target_key"] for p in lane["items"]]) for lane in body["lanes"]]
    assert (body["verdict"]["level"], body["verdict"]["reasons"]) == (result.verdict[0], result.verdict[1])
    assert body["pinned_unavailable"] == [] and body["excluded_counts"]["SNOOZED"] == 0
    # result_to_dict takes the same path; curves are rebuilt from the shared parts.
    direct = result_to_dict(result)
    assert direct["lanes"] == body["lanes"] and direct["hero"] == body["hero"]
    assert direct["hero"]["curve"]["t_utc"] and len(direct["hero"]["curve"]["alt"]) == len(
        direct["hero"]["curve"]["t_utc"])


def test_payload_holds_every_feasible_pick(result):
    small = recommend(make_inputs(rows=ngc7000_rows(), rigs=[NB_RIG, OSC_RIG]), Params(rig_mode="ALL", per_lane=1))
    p = result_payload(small)
    shown = sum(len(lane["items"]) for lane in small.lanes)
    assert len(p["ranked"]) == len(small.ranked) > shown
    assert set(p["excluded"]) == set(small.excluded)
    # Rendering at another per_lane gives that lane size (the cache is per_lane-independent).
    wide = render_payload(p, None, per_lane=6)
    assert _lane_keys(wide) == _lane_keys(result_to_dict(result))


def test_render_does_not_mutate_the_payload(payload):
    before = json.dumps(payload, sort_keys=True)
    render_payload(payload, FeedbackState(pinned=frozenset({"IC1396"}), dismissed={"NGC7000": None}), 6)
    assert json.dumps(payload, sort_keys=True) == before


def test_snoozed_and_dismissed_leave_the_lanes(payload):
    fb = FeedbackState(snoozed={"IC1805": NIGHT + timedelta(days=1)}, dismissed={"M45": "DONE"})
    body = render_payload(payload, fb, 6)
    keys = _all_keys(body)
    assert "IC1805" not in keys and "M45" not in keys
    assert body["excluded_counts"]["SNOOZED"] == 1 and body["excluded_counts"]["DISMISSED"] == 1
    assert body["excluded_counts"]["MOON"] == 2          # engine counts unchanged
    assert body["context"]["feedback_counts"] == {"pinned": 0, "snoozed": 1, "dismissed": 1}
    # On the until-night the snoozed target is back.
    later = dict(payload, context=dict(payload["context"], night=(NIGHT + timedelta(days=1)).isoformat()))
    assert "IC1805" in _all_keys(render_payload(later, fb, 6))


def test_pinned_feasible_pick_gets_its_own_lane_first(payload):
    fb = FeedbackState(pinned=frozenset({"M81", "IC1396"}))
    body = render_payload(payload, fb, 6)
    RecommendationsResponse.model_validate(body)
    lanes = _lane_keys(body)
    assert body["lanes"][0]["id"] == LANE_PINNED and body["lanes"][0]["title"] == "Your pins"
    assert lanes[LANE_PINNED] == ["IC1396", "M81"]                # by score
    assert all(k not in lanes[lane] for lane in lanes if lane != LANE_PINNED for k in ("M81", "IC1396"))
    keys = _all_keys(body)
    assert len(keys) == len(set(keys))
    pinned_pick = body["lanes"][0]["items"][0]
    assert pinned_pick["lane"] == LANE_PINNED and pinned_pick["feedback"] == {"pinned": True, "snoozed_until": None}
    assert body["context"]["feedback_counts"]["pinned"] == 2
    # Pins don't boost scores: NGC7000 is still the hero.
    assert body["hero"]["target_key"] == "NGC7000"


def test_pinned_infeasible_pick_is_listed_with_its_reason(payload):
    fb = FeedbackState(pinned=frozenset({"M31", "IC1805", "NOPE1"}), snoozed={"IC1805": NIGHT + timedelta(days=30)})
    body = render_payload(payload, fb, 6)
    assert body["pinned_unavailable"] == [
        {"target_key": "IC1805", "name": "Heart Nebula", "excluded_reason": "SNOOZED"},
        {"target_key": "M31", "name": "Andromeda Galaxy", "excluded_reason": "MOON"},
        {"target_key": "NOPE1", "name": "NOPE1", "excluded_reason": "NOT_IN_POOL"},
    ]
    assert all(lane["id"] != LANE_PINNED for lane in body["lanes"])


def test_hero_tie_break_prefers_a_pin(payload):
    # Without NGC7000 the best is IC1805 (0.497); IC1396 (0.482) is within 0.02, NGC6960 (0.464) isn't.
    base = {"dismissed": {"NGC7000": None}}
    assert render_payload(payload, FeedbackState(**base), 6)["hero"]["target_key"] == "IC1805"
    tie = render_payload(payload, FeedbackState(pinned=frozenset({"IC1396"}), **base), 6)
    assert tie["hero"]["target_key"] == "IC1396" and tie["hero"]["lane"] == LANE_PINNED
    far = render_payload(payload, FeedbackState(pinned=frozenset({"NGC6960"}), **base), 6)
    assert far["hero"]["target_key"] == "IC1805"


def test_choose_hero_and_apply_feedback_on_plain_picks():
    a, b, c = pick("A", score=0.80), pick("B", score=0.79), pick("C", score=0.70)
    assert choose_hero([a, b, c], frozenset()) is a
    assert choose_hero([a, b, c], frozenset({"B"})) is b
    assert choose_hero([a, b, c], frozenset({"C"})) is a
    assert choose_hero([], frozenset({"C"})) is None
    kept, hidden, unavailable = apply_feedback([a, b, c], {"D": "TOO_SMALL"},
                                               FeedbackState(pinned=frozenset({"D"}), dismissed={"B": None}), NIGHT,
                                               names={"D": "Dee"})
    assert [p.key for p in kept] == ["A", "C"] and hidden == {"SNOOZED": 0, "DISMISSED": 1}
    assert unavailable == [{"target_key": "D", "name": "Dee", "excluded_reason": "TOO_SMALL"}]
    assert apply_feedback([a], {}, FeedbackState(pinned=frozenset({"Z"})), NIGHT, no_rigs=True)[2][0][
        "excluded_reason"] == "NO_RIGS"


def test_lane_rebuild_keeps_caps_and_diversity():
    # 30 broadband picks on a line: `other` is capped at 10, other lanes at per_lane, crowding is pushed down.
    picks = [pick(f"K{i:02d}", ra=(i * 3) % 360, dec=0, score=1 - i / 100, mode="BB") for i in range(30)]
    lanes = assemble_lanes(picks, frozenset(), 0.0, 0.0, per_lane=6)
    assert [lane["id"] for lane in lanes] == [LANE_OTHER] and len(lanes[0]["items"]) == 10
    top = [p.key for p in lanes[0]["items"]]
    assert top[:2] == ["K00", "K01"] and top[2] != "K02"          # K02 is within 10 deg of K00 and K01
    # Filtering then rebuilding refills the lane from further down.
    kept, _, _ = apply_feedback(picks, {}, FeedbackState(dismissed={p.key: None for p in picks[:5]}), NIGHT)
    lanes = assemble_lanes(kept, frozenset({"K29"}), 0.0, 0.0, per_lane=6)
    assert [lane["id"] for lane in lanes] == [LANE_PINNED, LANE_OTHER]
    assert [p.key for p in lanes[0]["items"]] == ["K29"]
    assert len(lanes[1]["items"]) == 10 and "K29" not in [p.key for p in lanes[1]["items"]]
    # Pinned lane: every pin, by score, never diversified or capped.
    many = frozenset(p.key for p in picks[:12])
    lanes = assemble_lanes(picks, many, 0.0, 0.0, per_lane=2)
    assert [p.key for p in lanes[0]["items"]] == [p.key for p in picks[:12]]


# --- replay -----------------------------------------------------------------------

def test_replay_with_empty_feedback_is_unchanged():
    from test_recommend_replay import _data, _history_rows
    from app.services.recommend.replay import build_report, replay_nights, run_replay

    data = _data(_history_rows(), rigs=(NB_RIG, OSC_RIG))
    nights = replay_nights(data.rows)[:6]
    plain = run_replay(nights, data.inputs_for)
    empty = run_replay(nights, data.inputs_for, feedback=FeedbackState.empty())
    assert [o.ranked for o in plain] == [o.ranked for o in empty]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("generated_at", "runtime_seconds")}  # noqa: E731
    assert strip(build_report(plain, Params())) == strip(build_report(empty, Params()))
    # A non-empty state does filter (proves the hook is live).
    key = next(k for o in plain for k in o.ranked)
    hidden = run_replay(nights, data.inputs_for, feedback=FeedbackState(dismissed={key: None}))
    assert all(key not in o.ranked for o in hidden)


# --- state machine ---------------------------------------------------------------

def test_every_action_and_its_inverse():
    s0 = TargetState()
    s1, ch = transition(s0, PIN, NIGHT)
    assert ch and s1.pinned
    assert transition(s1, PIN, NIGHT) == (s1, False)                 # no-op
    assert transition(s1, UNPIN, NIGHT) == (s0, True)
    s2, ch = transition(s0, SNOOZE, NIGHT, nights=7)
    assert ch and s2.snoozed_until == NIGHT + timedelta(days=7)
    assert transition(s0, SNOOZE, NIGHT, nights=1)[0].snoozed_until == NIGHT + timedelta(days=1)
    assert transition(s0, SNOOZE, NIGHT, nights=30)[0].snoozed_until == NIGHT + timedelta(days=30)
    assert transition(s2, UNSNOOZE, NIGHT) == (s0, True)
    s3, ch = transition(s0, DISMISS, NIGHT, reason="TOO_HARD", note="needs a longer scope")
    assert ch and s3.dismissed and s3.dismiss_reason == "TOO_HARD" and s3.note == "needs a longer scope"
    s4, ch = transition(s3, UNDISMISS, NIGHT)
    assert ch and not s4.dismissed and s4.dismiss_reason is None
    assert transition(s1, IMAGED, NIGHT, note="x") == (s1, False)
    assert not TargetState().active and s1.active and s2.active and s3.active


def test_validate_action_rejects_bad_input():
    assert validate_action("pin") == "PIN"
    for bad in (dict(action="BOOST"), dict(action=None), dict(action="SNOOZE"), dict(action="SNOOZE", nights=3),
                dict(action="DISMISS", reason="BORING"), dict(action="PIN", note="x" * 201)):
        with pytest.raises(FeedbackError) as e:
            validate_action(**bad)
        assert e.value.status == 400
    assert validate_action("SNOOZE", nights=30) == "SNOOZE"
    assert validate_action("DISMISS") == "DISMISS"


def test_event_payload():
    assert event_payload(SNOOZE, nights=7) == {"nights": 7}
    assert event_payload(DISMISS, reason="NOT_MY_TYPE") == {"reason": "NOT_MY_TYPE"}
    assert event_payload(PIN) is None


# --- impressions ------------------------------------------------------------------

def test_impression_rows_hero_first_one_per_key(payload):
    body = render_payload(payload, FeedbackState(pinned=frozenset({"M81"})), 6)
    rows = impression_rows(body, per_lane=1)
    keys = [r["target_key"] for r in rows]
    assert keys[0] == body["hero"]["target_key"] and rows[0]["is_hero"] and rows[0]["rank"] == 1
    assert len(keys) == len(set(keys)) and [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert keys == ["NGC7000", "M81", "IC1805", "M45"]         # hero, then the first item of each lane
    assert rows[1]["lane"] == LANE_PINNED and not rows[1]["is_hero"]
    assert all(r["night"] == NIGHT.isoformat() and r["site_id"] == 1 and r["rig_mode"] == "ALL" for r in rows)
    assert rows[0]["rig_id"] == NB_RIG.id and rows[0]["score"] == body["hero"]["score"]
