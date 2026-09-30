"""
R1 §7: /api/recommendations (no database): response shape against the
contract, rig fallback, 404s, the result cache and the replay report.
"""

import asyncio
import json
import sys
import types
from dataclasses import replace
from datetime import date

import pytest
from fastapi import HTTPException

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.api import recommendations as api  # noqa: E402
from app.schemas.recommendations import (  # noqa: E402
    OutcomesResponse, RecommendationsResponse, ReplayReport, TargetExplanation,
)
from app.services.recommend import Params, recommend, result_to_dict  # noqa: E402
from app.services.recommend import loader  # noqa: E402

from app.services.recommend import assign_rig_plan  # noqa: E402

from app.services.recommend.candidates import CandidatePool  # noqa: E402
from _recommend_helpers import (  # noqa: E402
    BB_RIG, FULL_MOON_NIGHT, NB_RIG, OSC_RIG, SITE, cand, make_inputs, ngc7000_rows,
)

PICK_KEYS = {"target_key", "name", "kind", "ra_deg", "dec_deg", "size_arcmin", "rig", "alternatives", "mode", "score",
             "components", "usable_hours", "available_hours", "best_time_utc", "max_alt_deg", "moon_sep_min_deg",
             "fill_ratio", "have_hours", "have_by_filter", "nights", "last_imaged", "goal_hours", "goal_source",
             "reasons", "curve"}
CONTEXT_KEYS = {"night", "site", "tier", "tier_note", "dark_start_utc", "dark_end_utc", "moon", "horizon_source",
                "floor_deg", "rig_mode", "rigs", "weights"}


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value

    def scan_iter(self, match="*"):
        prefix = match.rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix)]

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


def _body(**kw):
    inputs = make_inputs(rows=ngc7000_rows(), rigs=[NB_RIG, OSC_RIG], **kw)
    return result_to_dict(recommend(inputs, Params(rig_mode="ALL_FALLBACK")))


def test_response_shape_matches_contract():
    body = _body()
    RecommendationsResponse.model_validate(body)
    assert {"generated_at", "cached", "context", "hero", "verdict", "lanes", "excluded_counts",
            "skipped_rigs"} <= set(body)
    assert CONTEXT_KEYS <= set(body["context"])
    assert set(body["context"]["moon"]) >= {"illumination", "age_days", "up_fraction"}
    assert set(body["excluded_counts"]) == {"BELOW_HORIZON", "TOO_SMALL", "TOO_BIG", "MOON", "TIER",
                                            "SNOOZED", "DISMISSED"}     # R2a: additive
    assert body["excluded_counts"]["SNOOZED"] == 0 and body["excluded_counts"]["DISMISSED"] == 0
    assert body["pinned_unavailable"] == []
    assert body["context"]["feedback_counts"] == {"pinned": 0, "snoozed": 0, "dismissed": 0}
    hero = body["hero"]
    assert hero["feedback"] == {"pinned": False, "snoozed_until": None}
    assert PICK_KEYS <= set(hero)
    assert hero["target_key"] == "NGC7000" and hero["rig"] == {"id": NB_RIG.id, "name": NB_RIG.name}
    assert set(hero["curve"]) == {"t_utc", "alt", "moon_alt", "limit", "dark"}
    n = len(hero["curve"]["t_utc"])
    assert n > 20 and all(len(v) == n for v in hero["curve"].values())
    assert hero["curve"]["t_utc"][0].endswith("Z") and hero["best_time_utc"].endswith("Z")
    assert body["verdict"]["level"] in ("GO", "MARGINAL", "DONT_BOTHER")
    for lane in body["lanes"]:
        assert set(lane) >= {"id", "title", "items"}
        for p in lane["items"]:
            assert PICK_KEYS <= set(p)
            assert all(set(r) == {"code", "text"} for r in p["reasons"])
    assert body["context"]["rig_mode"] == "ALL_FALLBACK"
    assert body["context"]["rigs"][0] == {"id": 1, "name": "Mono 200mm", "classes": ["HA", "SII", "OIII", "BB"],
                                          "size_window_arcmin": [0.1, 926.4]}
    json.dumps(body)


def test_tier_none_body_validates():
    # 70N at midsummer: the Sun never gets below -9.
    from app.services.recommend.context import SiteSpec

    inputs = make_inputs(rigs=[NB_RIG], night=date(2026, 6, 21))
    inputs.site = SiteSpec(id=9, name="Far north", latitude=70.0, longitude=20.0, timezone="Europe/Oslo")
    body = result_to_dict(recommend(inputs, Params()))
    RecommendationsResponse.model_validate(body)
    assert body["context"]["tier"] == "NONE" and body["hero"] is None and body["lanes"] == []
    assert body["verdict"]["level"] == "DONT_BOTHER" and body["context"]["tier_note"]


# --- several mounted rigs: the per-rig plan -------------------------------------------

def test_rig_plan_gives_each_mounted_rig_distinct_targets():
    inputs = make_inputs(rigs=[NB_RIG, BB_RIG, OSC_RIG], night=date(2026, 10, 10))
    body = result_to_dict(recommend(inputs, Params(rig_mode="MOUNTED")))
    RecommendationsResponse.model_validate(body)
    plan = body["rig_plan"]
    assert [e["rig"]["id"] for e in plan] == [NB_RIG.id, BB_RIG.id, OSC_RIG.id]
    keys = [p["target_key"] for e in plan for p in e["items"]]
    assert keys and len(keys) == len(set(keys))
    for entry in plan:
        assert 1 <= len(entry["items"]) <= 3
        for p in entry["items"]:
            assert p["rig"]["id"] == entry["rig"]["id"]
            assert all(a["rig_id"] != p["rig"]["id"] for a in p["alternatives"])
            if not p["best_rig"]:
                assert p["alternatives"] and p["alternatives"][0]["score"] >= p["score"]
                framing = [r["text"] for r in p["reasons"] if r["code"] == "FRAMING"]
                assert all(entry["rig"]["name"] in t for t in framing)
    json.dumps(body)


def test_rig_plan_only_for_several_mounted_rigs():
    inputs = make_inputs(rigs=[NB_RIG, BB_RIG], night=date(2026, 10, 10))
    assert result_to_dict(recommend(inputs, Params(rig_mode="ALL")))["rig_plan"] == []
    one = make_inputs(rigs=[NB_RIG], night=date(2026, 10, 10))
    assert result_to_dict(recommend(one, Params(rig_mode="MOUNTED")))["rig_plan"] == []


class _P:
    """A cached pick for assign_rig_plan: alts are (rig, score[, hours]); hours default to `hours`."""

    def __init__(self, key, rig, score, alts=(), hours=5.0):
        self.key, self.score, self.available_hours = key, score, hours
        self.data = {"rig": {"id": rig}, "alternatives": [
            {"rig_id": a[0], "rig_name": str(a[0]), "score": a[1], "available_hours": a[2] if len(a) > 2 else hours}
            for a in alts]}


def test_assign_rig_plan_primaries_first_then_backups_and_pins_first():
    # Rig 1 is best for everything; rig 2 must still get a primary before rig 1 gets a backup.
    picks = [_P("A", 1, 0.9, [(2, 0.5)]), _P("B", 1, 0.8, [(2, 0.7)]), _P("C", 1, 0.6, [(2, 0.2)])]
    plan = assign_rig_plan(picks, [1, 2], per_rig=2)
    assert [p.key for p, _ in plan[1]] == ["A", "C"]
    assert [(p.key, alt["rig_id"]) for p, alt in plan[2]] == [("B", 2)]
    plan = assign_rig_plan(picks, [1, 2], pinned=frozenset({"C"}), per_rig=1)
    assert [p.key for p, _ in plan[1]] == ["C"] and [p.key for p, _ in plan[2]] == ["B"]


def test_assign_rig_plan_leaves_a_rig_empty_when_no_pair_has_1_5_hours():
    # Rig 2 only offers 0.8 h pairs: it gets nothing, and rig 1 is unaffected.
    picks = [_P("A", 1, 0.9, [(2, 0.8, 0.8)]), _P("B", 1, 0.8, [(2, 0.7, 0.8)])]
    plan = assign_rig_plan(picks, [1, 2], per_rig=2)
    assert [p.key for p, _ in plan[1]] == ["A", "B"] and plan[2] == []
    # A target that is short on its best rig can still be planned on the other rig.
    plan = assign_rig_plan([_P("A", 1, 0.9, [(2, 0.5, 4.0)], hours=0.6)], [1, 2])
    assert plan[1] == [] and [(p.key, a["rig_id"]) for p, a in plan[2]] == [("A", 2)]


def test_rig_plan_entries_carry_window_verdict_and_note():
    from app.services.recommend import PAYLOAD_FORMAT

    assert PAYLOAD_FORMAT == 4
    inputs = make_inputs(rigs=[NB_RIG, OSC_RIG], night=date(2026, 10, 10))
    body = result_to_dict(recommend(inputs, Params(rig_mode="MOUNTED")))
    RecommendationsResponse.model_validate(body)
    for entry in body["rig_plan"]:
        assert entry["verdict"]["level"] in ("GO", "MARGINAL", "DONT_BOTHER")
        assert len(entry["size_window_arcmin"]) == 2
        assert (entry["note"] is None) == bool(entry["items"])


def test_empty_rig_gets_a_note_naming_the_moon():
    # The full-Moon night: a OSC rig whose window holds only WF-like big targets has no 1.5 h pair.
    big = [cand("BIGMW", 314.7, 44.3, 600.0, "EMISSION", "Wide field"), cand("BIGM31", 10.7, 41.3, 500.0, "GALAXY")]
    inputs = make_inputs(pool=CandidatePool(big), rigs=[NB_RIG, OSC_RIG], night=FULL_MOON_NIGHT)
    body = result_to_dict(recommend(inputs, Params(rig_mode="MOUNTED")))
    osc = next(e for e in body["rig_plan"] if e["rig"]["id"] == OSC_RIG.id)
    assert not osc["items"]
    assert "Moon" in osc["note"] and "broadband/OSC needs 120°" in osc["note"]
    assert osc["verdict"] == {"level": "DONT_BOTHER", "reasons": [{"code": "NOTHING_GOOD", "text": osc["note"]}]}


def test_empty_rig_note_for_a_size_window_with_nothing_in_it():
    tiny = [cand("SMALL", 314.7, 44.3, 4.0, "GALAXY")]
    rig = replace(OSC_RIG, min_target_arcmin=180.0, max_target_arcmin=960.0)
    inputs = make_inputs(pool=CandidatePool(tiny), rigs=[NB_RIG, rig], night=date(2026, 10, 10))
    body = result_to_dict(recommend(inputs, Params(rig_mode="MOUNTED")))
    entry = next(e for e in body["rig_plan"] if e["rig"]["id"] == rig.id)
    assert entry["items"] == [] and entry["note"] == "No target between 3°–16° is in the candidate list"
    assert entry["size_window_arcmin"] == [180.0, 960.0]


# --- service with a fake DB layer ---------------------------------------------------

@pytest.fixture
def fake_loader(monkeypatch):
    redis = FakeRedis()
    calls = {"build": 0}
    monkeypatch.setattr(loader, "_redis", lambda: redis)
    monkeypatch.setattr(loader, "load_sites", lambda s: [SITE])
    monkeypatch.setattr(loader, "hist_version", lambda s: "v1")

    def build_inputs(session, site, night, rig, version=None, r=None, as_of=None):
        calls["build"] += 1
        specs, mode, skipped = loader.select_rigs(
            [loader.RigRecord(1, NB_RIG.name, True, False, NB_RIG, None),
             loader.RigRecord(2, OSC_RIG.name, True, False, OSC_RIG, None)], rig)
        inputs = make_inputs(rows=ngc7000_rows(), rigs=specs, night=night)
        inputs.skipped_rigs = skipped
        return inputs, mode

    monkeypatch.setattr(loader, "build_inputs", build_inputs)
    return redis, calls


def test_mounted_without_mounted_rig_falls_back_to_all(fake_loader):
    body = loader.get_recommendations(date(2026, 9, 26), None, "mounted", 6, session=object())
    assert body["context"]["rig_mode"] == "ALL_FALLBACK"
    assert [r["id"] for r in body["context"]["rigs"]] == [1, 2]


def test_cache_hit_sets_cached_true(fake_loader):
    redis, calls = fake_loader
    first = loader.get_recommendations(date(2026, 9, 26), None, "mounted", 6, session=object())
    assert first["cached"] is False and calls["build"] == 1
    # R2a: the cached payload is per_lane-independent (lanes are rebuilt per request).
    assert any(k.startswith("recs:result:v2:1:mounted:2026-09-26:v1") for k in redis.data)
    second = loader.get_recommendations(date(2026, 9, 26), None, "mounted", 6, session=object())
    assert second["cached"] is True and calls["build"] == 1
    assert second["hero"]["target_key"] == first["hero"]["target_key"]
    assert loader.invalidate_recommendations_cache(redis) == 1
    third = loader.get_recommendations(date(2026, 9, 26), None, "mounted", 6, session=object())
    assert third["cached"] is False and calls["build"] == 2


def test_unknown_site_is_404(fake_loader):
    with pytest.raises(loader.RecommendationError) as e:
        loader.get_recommendations(date(2026, 9, 26), 99, "mounted", 6, session=object())
    assert e.value.status == 404
    with pytest.raises(loader.RecommendationError) as e:
        loader.pick_site([], None)
    assert e.value.status == 404


def test_target_endpoint_shape_and_unknown_key_404(fake_loader, monkeypatch):
    import app.services.targets as targets

    monkeypatch.setattr(targets, "get_alias_index_sync",
                        lambda s: types.SimpleNamespace(resolve=lambda text: "M31" if text == "Andromeda" else None))
    monkeypatch.setattr(loader, "_session_scope", lambda session: _Ctx())
    body = asyncio.run(api.explain_target("NGC7000", date(2026, 9, 26), None, "mounted"))
    TargetExplanation.model_validate(body)
    assert body["target_key"] == "NGC7000" and body["night"] == "2026-09-26"
    by_rig = {r["rig_id"]: r for r in body["results"]}
    assert by_rig[1]["pick"] is not None and by_rig[1]["excluded_reason"] is None
    # The OSC rig survives the generous hard Moon filter but has no Moon-clear hours.
    assert by_rig[2]["pick"] is not None and by_rig[2]["pick"]["available_hours"] < 0.5
    d = by_rig[2]["details"]
    assert {"usable_hours", "usable_hours_hard", "available_hours_hard", "required_sep_deg", "target_px",
            "max_alt_deg", "moon_sep_min_deg", "mode"} <= set(d)

    alias = asyncio.run(api.explain_target("Andromeda", date(2026, 9, 26), None, "mounted"))
    assert alias["target_key"] == "M31"

    with pytest.raises(HTTPException) as e:
        asyncio.run(api.explain_target("NOPE123", date(2026, 9, 26), None, "mounted"))
    assert e.value.status_code == 404


class _Ctx:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


def test_get_endpoint_maps_errors(fake_loader, monkeypatch):
    monkeypatch.setattr(loader, "_session_scope", lambda session: _Ctx())
    body = asyncio.run(api.get_recommendations(date(2026, 9, 26), None, "mounted", 6))
    RecommendationsResponse.model_validate(body)
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_recommendations(date(2026, 9, 26), 42, "mounted", 6))
    assert e.value.status_code == 404


def test_replay_latest(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.get_replay_latest())
    assert e.value.status_code == 404
    report = {"generated_at": "2026-09-27T10:00:00Z", "nights": 3, "params": {},
              "metrics": {"hit@1": 0.1, "hit@3": 0.2, "hit@5": 0.3, "hit@10": 0.4, "mrr": 0.2, "feasible_recall": 0.9},
              "baselines": {"recency": {}, "altitude": {}, "random": {}}, "breakdown": {}, "misses": []}
    (tmp_path / "replay_latest.json").write_text(json.dumps(report), encoding="utf-8")
    got = asyncio.run(api.get_replay_latest())
    assert got == report
    ReplayReport.model_validate(got)


def test_default_night():
    from datetime import datetime

    # lon -3: local solar time = UTC - 12 min.
    assert loader.default_night(datetime(2026, 9, 26, 12, 0), -3.0) == date(2026, 9, 26)   # 11:48 local: tonight
    assert loader.default_night(datetime(2026, 9, 26, 6, 0), -3.0) == date(2026, 9, 25)    # 05:48 local: last night
    assert loader.default_night(datetime(2026, 9, 26, 9, 0), -3.0) == date(2026, 9, 26)    # morning: tonight
    assert loader.default_night(datetime(2026, 9, 26, 14, 0), -3.0) == date(2026, 9, 26)
    assert loader.default_night(datetime(2026, 9, 27, 2, 0), -3.0) == date(2026, 9, 26)    # mid-session


# --- R2a: feedback, impressions and outcomes (SQLite stands in for PostgreSQL) -------

from contextlib import nullcontext  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from _recommend_helpers import FULL_MOON_NIGHT, standard_pool  # noqa: E402


class _User:
    def __init__(self, uid):
        self.id = uid
        self.is_admin = False


@pytest.fixture
def r2a(fake_loader, monkeypatch):
    """A minimal app with the router, a SQLite DB for the R2a tables, and a switchable current user."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.services.targets as targets
    from app.api.dependencies import get_current_user
    from app.database import Base
    from app.models.recommendation import RecommendationEvent, RecommendationImpression, RecommendationTargetState
    from app.models.user import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, RecommendationTargetState.__table__,
                                             RecommendationEvent.__table__, RecommendationImpression.__table__])
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add_all([User(id=1, email="a@example.test", hashed_password="x"),
                   User(id=2, email="b@example.test", hashed_password="x")])
        s.commit()

    monkeypatch.setattr(loader, "_session_scope", lambda session: nullcontext(session) if session else Session())
    monkeypatch.setattr(loader, "default_night", lambda now, lon: FULL_MOON_NIGHT)
    monkeypatch.setattr(loader, "_pool_for_keys",
                        lambda s, r=None: SimpleNamespace(pool=standard_pool(), key_map={"OBJ:M81LUM": "M81"}))
    aliases = {"NGC3031": "M81", "BODESGALAXY": "M81"}
    monkeypatch.setattr(targets, "get_alias_index_sync",
                        lambda s: SimpleNamespace(resolve=lambda t: aliases.get((t or "").upper().replace(" ", ""))))

    current = {"user": _User(1)}
    app = FastAPI()
    app.include_router(api.router, prefix="/api/recommendations")
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    def as_user(uid):
        current["user"] = _User(uid)

    def rows(model):
        with Session() as s:
            return s.query(model).all()

    ns = SimpleNamespace(client=client, as_user=as_user, rows=rows, Session=Session, redis=fake_loader[0],
                         calls=fake_loader[1], State=RecommendationTargetState, Event=RecommendationEvent,
                         Impression=RecommendationImpression)
    yield ns
    client.close()


def _post(ns, key, action, **kw):
    return ns.client.post("/api/recommendations/feedback", json={"target_key": key, "action": action, **kw})


def test_feedback_every_action_and_inverse_one_event_per_change(r2a):
    ctx = {"night": "2026-09-26", "lane": "other", "rank": 4, "score": 0.51, "rig_id": 1}
    steps = [("PIN", {}), ("PIN", {}), ("UNPIN", {}), ("SNOOZE", {"nights": 7}), ("UNSNOOZE", {}),
             ("DISMISS", {"reason": "NOT_MY_TYPE", "note": "meh"}), ("UNDISMISS", {}), ("IMAGED", {})]
    for action, kw in steps:
        res = _post(r2a, "M81", action, context=ctx, **kw)
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body) == {"target_key", "name", "pinned", "snoozed_until", "dismissed", "dismiss_reason", "note",
                             "updated_at"}
        assert body["target_key"] == "M81" and body["name"] == "Bode's Galaxy"
        if action == "PIN":
            assert body["pinned"] is True
        if action == "SNOOZE":
            assert body["snoozed_until"] == "2026-10-03"
        if action == "DISMISS":
            assert body["dismissed"] is True and body["dismiss_reason"] == "NOT_MY_TYPE" and body["note"] == "meh"
    events = r2a.rows(r2a.Event)
    # The second PIN changed nothing, so no event; IMAGED always logs one.
    assert [e.action for e in events] == ["PIN", "UNPIN", "SNOOZE", "UNSNOOZE", "DISMISS", "UNDISMISS", "IMAGED"]
    assert all(e.user_id == 1 and e.target_key == "M81" and e.night == FULL_MOON_NIGHT and e.lane == "other"
               and e.rank == 4 and e.rig_id == 1 for e in events)
    assert events[2].payload == {"nights": 7} and events[4].payload == {"reason": "NOT_MY_TYPE", "note": "meh"}
    (state,) = r2a.rows(r2a.State)
    assert not state.pinned and state.snoozed_until is None and not state.dismissed


def test_feedback_resolves_aliases_to_the_canonical_key(r2a):
    assert _post(r2a, "NGC3031", "PIN").json()["target_key"] == "M81"
    assert _post(r2a, "Bodes Galaxy", "UNPIN").json()["target_key"] == "M81"
    assert _post(r2a, "OBJ:M81LUM", "PIN").json()["target_key"] == "M81"       # R1 §14.1 stray-key fold
    assert {s.target_key for s in r2a.rows(r2a.State)} == {"M81"}
    assert {e.target_key for e in r2a.rows(r2a.Event)} == {"M81"}


def test_feedback_errors(r2a):
    assert _post(r2a, "NOPE123", "PIN").status_code == 404
    assert _post(r2a, "M81", "BOOST").status_code == 400
    assert _post(r2a, "M81", "SNOOZE").status_code == 400
    assert _post(r2a, "M81", "SNOOZE", nights=3).status_code == 400
    assert _post(r2a, "M81", "DISMISS", reason="BORING").status_code == 400
    assert _post(r2a, "NOPE123", "BOOST").status_code == 400        # the action is checked first
    assert r2a.rows(r2a.Event) == [] and r2a.rows(r2a.State) == []


def test_snooze_without_context_uses_tonight(r2a):
    assert _post(r2a, "M45", "SNOOZE", nights=1).json()["snoozed_until"] == "2026-09-27"


def test_feedback_list_and_explain(r2a):
    _post(r2a, "M81", "PIN")
    _post(r2a, "IC1805", "SNOOZE", nights=30, context={"night": "2026-09-26"})
    _post(r2a, "M45", "DISMISS", reason="DONE")
    _post(r2a, "M31", "PIN")
    _post(r2a, "M31", "UNPIN")                                   # no active state: not listed
    items = r2a.client.get("/api/recommendations/feedback").json()["items"]
    by_key = {i["target_key"]: i for i in items}
    assert set(by_key) == {"M81", "IC1805", "M45"}
    assert by_key["IC1805"]["snoozed_until"] == "2026-10-26" and by_key["IC1805"]["name"] == "Heart Nebula"
    assert by_key["M45"]["dismiss_reason"] == "DONE"

    body = r2a.client.get("/api/recommendations/target/IC1805", params={"date": "2026-09-26"}).json()
    TargetExplanation.model_validate(body)
    assert body["feedback"] == {"pinned": False, "snoozed_until": "2026-10-26", "dismissed": False,
                                "dismiss_reason": None}
    assert all(r["excluded_reason"] == "SNOOZED" and r["pick"] is None and r["details"]["until"] == "2026-10-26"
               for r in body["results"])
    body = r2a.client.get("/api/recommendations/target/M45", params={"date": "2026-09-26"}).json()
    assert all(r["excluded_reason"] == "DISMISSED" and r["details"]["reason"] == "DONE" for r in body["results"])
    body = r2a.client.get("/api/recommendations/target/NGC3031", params={"date": "2026-09-26"}).json()
    assert body["target_key"] == "M81" and body["feedback"]["pinned"] is True
    assert any(r["pick"] is not None for r in body["results"])


def test_feedback_is_per_user_and_applied_to_a_cached_result(r2a):
    first = r2a.client.get("/api/recommendations").json()
    assert first["cached"] is False and r2a.calls["build"] == 1
    _post(r2a, "M81", "PIN")
    _post(r2a, "IC1805", "DISMISS")
    body = r2a.client.get("/api/recommendations").json()
    RecommendationsResponse.model_validate(body)
    assert body["cached"] is True and r2a.calls["build"] == 1          # feedback writes invalidate nothing
    assert body["lanes"][0]["id"] == "pinned" and [p["target_key"] for p in body["lanes"][0]["items"]] == ["M81"]
    assert "IC1805" not in [p["target_key"] for lane in body["lanes"] for p in lane["items"]]
    assert body["excluded_counts"]["DISMISSED"] == 1
    assert body["context"]["feedback_counts"] == {"pinned": 1, "snoozed": 0, "dismissed": 1}

    r2a.as_user(2)
    other = r2a.client.get("/api/recommendations").json()
    assert other["cached"] is True
    assert other["lanes"][0]["id"] != "pinned" and other["excluded_counts"]["DISMISSED"] == 0
    assert _lane_ids(other) == _lane_ids(first)
    assert r2a.client.get("/api/recommendations/feedback").json() == {"items": []}


def _lane_ids(body):
    return [(lane["id"], [p["target_key"] for p in lane["items"]]) for lane in body["lanes"]]


def test_impressions_only_for_tonight_and_never_duplicated(r2a):
    body = r2a.client.get("/api/recommendations", params={"per_lane": 2}).json()
    first = r2a.rows(r2a.Impression)
    assert first and first[0].is_hero and first[0].rank == 1 and first[0].target_key == body["hero"]["target_key"]
    assert all(i.user_id == 1 and i.night == FULL_MOON_NIGHT and i.site_id == 1 and i.rig_mode == "ALL_FALLBACK"
               for i in first)
    shown = {p["target_key"] for lane in body["lanes"] for p in lane["items"][:2]} | {body["hero"]["target_key"]}
    assert {i.target_key for i in first} == shown
    stamp = {i.target_key: (i.lane, i.rank, i.first_shown_at) for i in first}

    # Same night again, with a pin that changes lanes: the first showing wins, nothing is added twice.
    _post(r2a, "M81", "PIN")
    r2a.client.get("/api/recommendations", params={"per_lane": 2})
    again = r2a.rows(r2a.Impression)
    assert {i.target_key: (i.lane, i.rank, i.first_shown_at) for i in again if i.target_key in stamp} == stamp
    keys = [(i.night, i.target_key) for i in again]
    assert len(keys) == len(set(keys))

    # Browsing another date writes nothing.
    n = len(again)
    r2a.client.get("/api/recommendations", params={"date": "2026-10-10"})
    assert len(r2a.rows(r2a.Impression)) == n

    # Another user gets their own rows.
    r2a.as_user(2)
    r2a.client.get("/api/recommendations")
    assert {i.user_id for i in r2a.rows(r2a.Impression)} == {1, 2}


def test_outcomes_endpoint(r2a, monkeypatch):
    from sqlalchemy import text as sql_text

    # The PostgreSQL night expression can't run on SQLite: stand in for the library query.
    monkeypatch.setattr(loader, "IMAGED_SQL", sql_text(
        "SELECT 'OBJ:M81LUM' AS key, '2026-09-26' AS night WHERE :since_ts IS NOT NULL"))
    empty = r2a.client.get("/api/recommendations/outcomes").json()
    assert empty["nights_with_impressions"] == 0 and empty["hero"] == {"shown": 0, "acted": 0, "rate": 0.0}

    with r2a.Session() as s:
        s.add_all([r2a.Impression(user_id=1, night=FULL_MOON_NIGHT, target_key="M81", lane="pinned", rank=1,
                                  score=0.5, is_hero=True, rig_mode="MOUNTED"),
                   r2a.Impression(user_id=1, night=FULL_MOON_NIGHT, target_key="M31", lane="other", rank=2,
                                  score=0.4, is_hero=False, rig_mode="MOUNTED"),
                   r2a.Impression(user_id=2, night=FULL_MOON_NIGHT, target_key="M31", lane="other", rank=1,
                                  score=0.4, is_hero=True, rig_mode="MOUNTED")])
        s.commit()
    _post(r2a, "M81", "IMAGED", context={"night": "2026-09-26"})
    monkeypatch.setattr(loader, "get_outcomes", _with_today(loader.get_outcomes, date(2026, 9, 28)))
    out = r2a.client.get("/api/recommendations/outcomes", params={"days": 30}).json()
    OutcomesResponse.model_validate(out)
    assert out["nights_with_impressions"] == 1 and out["nights_imaged"] == 1
    assert out["hero"] == {"shown": 1, "acted": 1, "rate": 1.0}           # OBJ:M81LUM folds into M81
    assert out["any"]["shown"] == 2 and out["any"]["acted"] == 1
    assert out["by_lane"] == {"other": {"shown": 1, "acted": 0, "rate": 0.0},
                              "pinned": {"shown": 1, "acted": 1, "rate": 1.0}}
    assert out["self_reported"] == {"imaged_events": 1, "confirmed_by_library": 1}
    assert r2a.client.get("/api/recommendations/outcomes", params={"days": 0}).status_code == 422


def _with_today(fn, today):
    def wrapped(user_id, days=90, session=None):
        return fn(user_id, days, session, today=today)
    return wrapped
