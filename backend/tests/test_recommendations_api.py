"""
R1 §7: /api/recommendations (no database): response shape against the
contract, rig fallback, 404s, the result cache and the replay report.
"""

import asyncio
import json
import sys
import types
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
from app.schemas.recommendations import RecommendationsResponse, ReplayReport, TargetExplanation  # noqa: E402
from app.services.recommend import Params, recommend, result_to_dict  # noqa: E402
from app.services.recommend import loader  # noqa: E402

from _recommend_helpers import NB_RIG, OSC_RIG, SITE, make_inputs, ngc7000_rows  # noqa: E402

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
    assert set(body["excluded_counts"]) == {"BELOW_HORIZON", "TOO_SMALL", "TOO_BIG", "MOON", "TIER"}
    hero = body["hero"]
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
    assert body["context"]["rigs"][0] == {"id": 1, "name": "Mono 200mm", "classes": ["HA", "SII", "OIII", "BB"]}
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
    assert any(k.startswith("recs:result:1:mounted:2026-09-26:6:v1") for k in redis.data)
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
    assert by_rig[2]["pick"] is None and by_rig[2]["excluded_reason"] in ("MOON", "TIER")
    assert "usable_hours" in by_rig[2]["details"]

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
