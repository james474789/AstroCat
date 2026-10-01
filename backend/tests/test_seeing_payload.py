"""S1 §5/§7: payload assembly, planet tracks, caching and degradation, meteoblue (phase 2), no coordinates anywhere."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import _seeing_helpers as h
from app.services.seeing import SCORING_VERSION, build_forecast
from app.services.seeing import planets, service
from app.services.seeing.sources import SourceError
from app.services.seeing.sources import meteoblue as mb_src
from app.services.seeing.sources import open_meteo as om_src

FIX = Path(__file__).parent / "fixtures" / "seeing"
GLOBALS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]
SRC = [{"id": "open_meteo", "label": "Windy-equivalent models", "status": "ok"},
       {"id": "meteoblue", "label": "meteoblue", "status": "no_key"}]


def _build(frames=None, tracks=None, **kw):
    return build_forecast(h.SENTINEL_SITE, h.NIGHT, frames or h.make_frames(), "ecmwf_ifs025", now=h.NOW,
                          tracks=tracks or h.make_tracks(), sources=SRC, **kw)


def _om_raw():
    return json.loads((FIX / "open_meteo_sanitized.json").read_text(encoding="utf-8"))


# ---- payload shape (§7.1) --------------------------------------------------------------------------

def test_payload_shape_for_a_calm_clear_night():
    p = _build()
    assert p["available"] is True
    assert p["night"] == "2030-01-01" and p["site"] == {"id": 7, "timezone": "UTC"}
    assert p["scoring_version"] == SCORING_VERSION == 1 and p["stale"] is False
    o = p["overall"]
    assert o["grade"] == "VG" and o["verdict"] == "GO" and o["best_body"] == "jupiter"
    assert 0.0 <= o["confidence"] <= 1.0
    keys = {c["key"]: c["pass"] for c in o["checklist"]}
    assert keys["surface_wind"] is True and keys["seeing_index"] is None      # no meteoblue key
    body = p["bodies"][0]
    assert body["body"] == "jupiter" and body["diameter_arcsec"] == 44.0
    w = body["window"]
    assert set(w) == {"start_utc", "end_utc", "peak_utc", "peak_alt", "score"} and w["peak_alt"] > 70
    assert w["start_utc"].endswith("Z") and body["track"][0].keys() == {"t", "alt", "score"}
    assert len(p["hourly"]) == 24
    hr = p["hourly"][5]
    assert set(hr) == {"t", "score", "confidence", "clear", "factors", "per_model"}
    assert set(hr["per_model"]) == set(GLOBALS)
    assert set(hr["factors"]) == {"surface_wind", "seeing_index", "stability", "upper_cloud", "jet", "ground_shear",
                                  "arcsec", "computed_seeing"}
    assert hr["factors"]["surface_wind"]["score"] == pytest.approx(1.0)
    assert hr["factors"]["seeing_index"] == {"value": None, "score": None}
    json.dumps(p)                                                             # JSON-serialisable


def test_cloud_gate_gives_cloud_verdict_and_no_window():
    p = _build(frames=h.make_frames(cloud_cover=100.0, cloud_cover_low=100.0))
    assert p["overall"]["verdict"] == "CLOUD"
    assert p["bodies"][0]["window"] is None and p["bodies"][0]["cloud_blocked"] is True


def test_windy_night_scores_lower_and_is_not_go():
    calm = _build()["overall"]
    windy = _build(frames=h.make_frames(wind_speed_10m=7.0, wind_gusts_10m=12.0))
    assert windy["overall"]["score"] < calm["score"]
    assert windy["overall"]["verdict"] != "GO"
    wind_item = next(c for c in windy["overall"]["checklist"] if c["key"] == "surface_wind")
    assert wind_item["pass"] is False
    assert "gusty" in windy["bodies"][0]["warnings"]


def test_warnings_dew_risk_and_low_dispersion():
    p = _build(frames=h.make_frames(relative_humidity_2m=97.0), tracks=h.make_tracks(peak_alt=25.0))
    w = p["bodies"][0]["warnings"]
    assert "dew_risk" in w and "low_dispersion" in w


def test_models_disagree_warning_and_confidence():
    frames = {"ecmwf_ifs025": h.make_frame(wind_speed_10m=0.5, wind_gusts_10m=0.5),
              "icon_seamless": h.make_frame(wind_speed_10m=6.0, wind_gusts_10m=6.0),
              "gfs_seamless": h.make_frame(wind_speed_10m=6.0, wind_gusts_10m=6.0)}
    p = _build(frames=frames)
    assert p["overall"]["confidence"] < 0.5
    assert "models_disagree" in p["bodies"][0]["warnings"]
    mid = p["hourly"][10]
    assert mid["per_model"]["ecmwf_ifs025"] > mid["per_model"]["icon_seamless"]


def test_bodies_sorted_by_window_score_and_low_planet_listed_last():
    tracks = h.make_tracks(bodies=("saturn", "jupiter"), peak_alt=80.0)
    low = h.make_tracks(bodies=("saturn",), peak_alt=32.0).bodies["saturn"]
    tracks.bodies["saturn"] = low
    p = _build(tracks=tracks)
    assert [b["body"] for b in p["bodies"]] == ["jupiter", "saturn"]
    assert p["bodies"][0]["window"]["score"] > p["bodies"][1]["window"]["score"]


def test_body_up_only_in_twilight_has_no_window_but_is_not_listed_when_never_allowed():
    tracks = h.make_tracks(bodies=("mars",))
    tracks.bodies["mars"].allowed[:] = False                     # Sun never low enough
    p = _build(tracks=tracks)
    assert p["bodies"] == [] and p["overall"]["verdict"] == "NO_GO" and p["overall"]["best_body"] is None


def test_venus_daylight_allowed_with_a_note():
    tracks = h.make_tracks(bodies=("venus", "jupiter"), daylight=True)
    tracks.sun_alt[:] = 20.0                                       # the Sun is up all "night"
    for tk in tracks.bodies.values():
        tk.allowed[:] = tk.body == "venus"
        tk.daylight[:] = tk.body == "venus"
    p = _build(tracks=tracks)
    assert [b["body"] for b in p["bodies"]] == ["venus"]
    assert p["bodies"][0]["window"] is not None and "imaging_in_daylight" in p["bodies"][0]["notes"]


def test_window_respects_the_horizon_through_planet_limit():
    tracks = h.make_tracks(peak_alt=60.0)
    tk = tracks.bodies["jupiter"]
    tk.limit[:] = 70.0                                          # a tall obstruction: the body never clears it
    p = _build(tracks=tracks)
    assert p["bodies"] == [] or p["bodies"][0]["window"] is None


def test_out_of_range_night_is_unavailable():
    frames = {m: h.make_frame(start=datetime(2030, 3, 1), n=48) for m in GLOBALS}
    p = _build(frames=frames)
    assert p["available"] is False and p["reason"] == "out_of_range"
    assert build_forecast(h.SENTINEL_SITE, h.NIGHT, {}, "ecmwf_ifs025", now=h.NOW, tracks=h.make_tracks(),
                          sources=SRC)["reason"] == "no_data"


def test_longer_lead_times_reduce_confidence():
    near = _build()["overall"]["confidence"]
    far = build_forecast(h.SENTINEL_SITE, h.NIGHT, h.make_frames(), "ecmwf_ifs025", now=h.NOW - timedelta(days=5),
                         tracks=h.make_tracks(), sources=SRC)["overall"]["confidence"]
    assert far < near


# ---- privacy: no coordinates in the payload (§11) ---------------------------------------------------

def test_payload_contains_no_sentinel_coordinates():
    p = _build(tracks=None)           # real ephemeris for the sentinel site
    p2 = build_forecast(h.SENTINEL_SITE, h.NIGHT, h.make_frames(), "ecmwf_ifs025", now=h.NOW, sources=SRC)
    for payload in (p, p2):
        text = json.dumps(payload)
        assert not any(s in text for s in h.SENTINEL_STRINGS)
        assert "latitude" not in text and "longitude" not in text and '"lat"' not in text and '"lon"' not in text


def test_service_payload_cache_and_logs_contain_no_coordinates(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    r = h.FakeRedis()
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    monkeypatch.setattr(om_src, "fetch_open_meteo", lambda lat, lon, models, client=None: _om_raw())
    payload = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    assert payload["night"] == "2030-01-01"
    blob = json.dumps(payload) + "".join(r.store) + "".join(map(str, r.store.values())) + caplog.text
    assert not any(s in blob for s in h.SENTINEL_STRINGS)
    assert any(k.startswith("seeing:raw:7:open_meteo") for k in r.store)
    assert any(k.startswith("seeing:view:7:2030-01-01:1") for k in r.store)
    # the raw cache entry is the stripped response
    raw = json.loads(r.store["seeing:raw:7:open_meteo"])
    assert "latitude" not in raw["payload"] and raw["models"] == GLOBALS


# ---- request-level service: caching, fallback ------------------------------------------------------

def _patch_om(monkeypatch, calls, fail=False):
    def fetch(lat, lon, models, client=None):
        calls.append(list(models))
        if fail:
            raise SourceError("Open-Meteo HTTP 503")
        return _om_raw()
    monkeypatch.setattr(om_src, "fetch_open_meteo", fetch)


def test_view_cache_avoids_refetch_and_marks_source_ok(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    _patch_om(monkeypatch, calls)
    a = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    b = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW + timedelta(minutes=5))
    assert a == b and len(calls) == 1
    s = a["sources"][0]
    assert s["id"] == "open_meteo" and s["status"] == "ok" and s["label"] == "Windy-equivalent models"
    assert s["primary"] == "ecmwf_ifs025" and set(s["models"]) == set(GLOBALS)
    assert a["sources"][1] == {"id": "meteoblue", "label": "meteoblue", "status": "no_key", "message": None}
    assert a["available"] is True and a["stale"] is False


def test_fetch_failure_falls_back_to_stale_cache(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    _patch_om(monkeypatch, calls)
    first = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    _patch_om(monkeypatch, calls, fail=True)
    later = h.NOW + timedelta(hours=4)
    p = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=later, use_cache=False)
    assert p["available"] is True and p["stale"] is True
    assert p["fetched_at"] == first["fetched_at"]
    assert p["sources"][0]["status"] == "error" and "503" in p["sources"][0]["message"]


def test_no_cache_and_failure_is_unavailable_with_error(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    _patch_om(monkeypatch, calls, fail=True)
    p = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    assert p["available"] is False and p["reason"] == "no_data"
    assert p["sources"][0]["status"] == "error"


def test_regional_model_failure_retries_with_global_models(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    box = service.pick_models.__globals__["REGIONAL_BOXES"][0]
    site = h.SENTINEL_SITE.__class__(id=3, name="Box", latitude=box.centre[0], longitude=box.centre[1], timezone="UTC")

    def fetch(lat, lon, models, client=None):
        calls.append(list(models))
        if models[0] == box.model:
            raise SourceError("Open-Meteo HTTP 400 No data is available for this location")
        return _om_raw()

    monkeypatch.setattr(om_src, "fetch_open_meteo", fetch)
    res = service.get_open_meteo(site, r, h.NOW)
    assert calls[0][0] == box.model and calls[1] == GLOBALS
    assert res["primary"] == "ecmwf_ifs025" and res["source"]["status"] == "ok"


def test_date_beyond_seven_days_is_unavailable(monkeypatch):
    r = h.FakeRedis()
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    far = (h.NOW + timedelta(days=9)).date()
    p = service.forecast_view(h.SENTINEL_SITE, far, r, [], now=h.NOW)
    assert p["available"] is False and p["reason"] == "out_of_range"


def test_disabled_flag_returns_unavailable(monkeypatch):
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(seeing_enabled=False))
    assert service.forecast_for_request(None, None) == {"available": False, "reason": "disabled"}


def test_viewed_marker_and_invalidation():
    r = h.FakeRedis()
    assert service.was_viewed(r, 7) is False
    service.mark_viewed(r, 7)
    assert service.was_viewed(r, 7) is True and service.was_viewed(r, 8) is False
    r.store["seeing:view:7:2030-01-01:1"] = "{}"
    r.store["seeing:view:8:2030-01-01:1"] = "{}"
    service.invalidate_views(r, 7)
    assert "seeing:view:7:2030-01-01:1" not in r.store and "seeing:view:8:2030-01-01:1" in r.store


def test_refresh_site_now_summary_has_no_coordinates(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg())
    _patch_om(monkeypatch, calls)
    out = service.refresh_site_now(h.SENTINEL_SITE, r, [], now=h.NOW)
    assert out["site_id"] == 7 and out["available"] is True and out["sources"] == {"open_meteo": "ok",
                                                                                   "meteoblue": "no_key"}
    assert not any(s in json.dumps(out) for s in h.SENTINEL_STRINGS)
    service.refresh_site_now(h.SENTINEL_SITE, r, [], now=h.NOW)
    assert len(calls) == 2                                      # force refresh always refetches


# ---- meteoblue (phase 2): key gating, scoring, credit throttle --------------------------------------

def _mb_raw():
    """Synthetic meteoblue response (ASSUMED keys) covering the whole night."""
    start = datetime(2030, 1, 1, 12, 0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M") for i in range(30)]
    n = len(times)
    return {"data_1h": {"time": times, "seeing_arcsec": [1.2] * n, "seeing1": [5] * n, "seeing2": [4] * n,
                        "jetstream": [12.0] * n, "lowclouds": [0] * n, "midclouds": [0] * n, "highclouds": [0] * n}}


def test_no_key_status_and_unchanged_score_shape(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(meteoblue_api_key=None))
    _patch_om(monkeypatch, calls)
    monkeypatch.setattr(mb_src, "fetch_meteoblue", lambda *a, **k: pytest.fail("must not be called without a key"))
    p = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    assert p["sources"][1]["status"] == "no_key"
    assert p["hourly"][5]["factors"]["seeing_index"]["score"] is None
    assert not any(k.startswith("seeing:raw:7:meteoblue") for k in r.store)


def test_with_key_index_jet_and_arcsec_appear_and_enter_the_score(monkeypatch):
    r = h.FakeRedis()
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(meteoblue_api_key="KEY123"))
    monkeypatch.setattr(mb_src, "fetch_meteoblue", lambda lat, lon, key, client=None: _mb_raw())
    frames = h.make_frames(wind_speed_10m=3.0, wind_gusts_10m=3.0)      # mediocre surface so the index can matter
    with_mb = build_forecast(h.SENTINEL_SITE, h.NIGHT, frames, "ecmwf_ifs025", now=h.NOW, tracks=h.make_tracks(),
                             mb=mb_src.parse_meteoblue(_mb_raw()), sources=SRC)
    without = build_forecast(h.SENTINEL_SITE, h.NIGHT, frames, "ecmwf_ifs025", now=h.NOW, tracks=h.make_tracks(),
                             sources=SRC)
    f = with_mb["hourly"][6]["factors"]
    assert f["seeing_index"]["value"] == 5 and f["seeing_index"]["score"] == pytest.approx(1.0)
    assert f["arcsec"]["value"] == 1.2 and f["arcsec"]["score"] is None
    assert f["jet"]["value"] == 12.0
    assert with_mb["hourly"][6]["score"] > without["hourly"][6]["score"]
    item = next(c for c in with_mb["overall"]["checklist"] if c["key"] == "seeing_index")
    assert item["pass"] is True

    _patch = lambda lat, lon, models, client=None: _om_raw()                         # noqa: E731
    monkeypatch.setattr(om_src, "fetch_open_meteo", _patch)
    p = service.forecast_view(h.SENTINEL_SITE, None, r, [], now=h.NOW)
    assert p["sources"][1]["status"] == "ok" and p["sources"][1]["id"] == "meteoblue"
    assert "KEY123" not in json.dumps(p) + "".join(map(str, r.store.values()))


def test_meteoblue_throttle_respects_min_interval(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(meteoblue_api_key="KEY123", meteoblue_min_interval_h=6))

    def fetch(lat, lon, key, client=None):
        calls.append(1)
        return _mb_raw()

    monkeypatch.setattr(mb_src, "fetch_meteoblue", fetch)
    service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW)
    service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW + timedelta(hours=5, minutes=59))
    assert len(calls) == 1
    service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW + timedelta(hours=6, minutes=1))
    assert len(calls) == 2
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(meteoblue_api_key="KEY123", meteoblue_min_interval_h=1))
    service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW + timedelta(hours=7, minutes=30))
    assert len(calls) == 3


def test_meteoblue_error_is_reported_and_not_retried_every_request(monkeypatch):
    r, calls = h.FakeRedis(), []
    monkeypatch.setattr(service, "_cfg", lambda: h.cfg(meteoblue_api_key="KEY123"))

    def fetch(lat, lon, key, client=None):
        calls.append(1)
        raise SourceError("meteoblue HTTP 402 out of credits")

    monkeypatch.setattr(mb_src, "fetch_meteoblue", fetch)
    a = service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW)
    b = service.get_meteoblue(h.SENTINEL_SITE, r, h.NOW + timedelta(minutes=10))
    assert a["source"]["status"] == "error" and "402" in a["source"]["message"] and a["frame"] is None
    assert b["source"]["status"] == "error" and len(calls) == 1             # failure window: no credit burn
    # the panel still works: open-meteo-only score
    monkeypatch.setattr(om_src, "fetch_open_meteo", lambda lat, lon, models, client=None: _om_raw())
    p = service.forecast_view(h.SENTINEL_SITE, None, h.FakeRedis(), [], now=h.NOW)
    assert p["available"] is True and p["sources"][1]["status"] == "error"


# ---- planet tracks (astropy, synthetic location) ----------------------------------------------------

def test_planet_tracks_are_sane_for_a_synthetic_site():
    t = planets.planet_tracks(0.0, 0.0, h.NIGHT)
    assert len(t.times) == 96 and t.step_min == 15
    assert set(t.bodies) <= set(planets.BODIES) and {"jupiter", "saturn", "moon", "venus"} <= set(t.bodies)
    assert "mercury" not in t.bodies
    ranges = {"jupiter": (28, 52), "saturn": (13, 22), "mars": (3, 26), "venus": (9, 67), "uranus": (3, 4.2),
              "neptune": (2, 2.5), "moon": (1700, 2100)}
    for name, tk in t.bodies.items():
        lo, hi = ranges[name]
        assert lo <= tk.diameter_arcsec <= hi, (name, tk.diameter_arcsec)
        assert tk.alt.shape == (96,) and np.all(np.abs(tk.alt) <= 90)
        assert tk.limit.shape == (96,) and np.all(tk.limit >= 15.0)
    for name in ("venus", "mars", "moon"):
        assert 0.0 <= t.bodies[name].illum <= 1.0
    assert t.bodies["jupiter"].illum is None
    assert t.bodies["venus"].allowed.all() and not t.bodies["jupiter"].allowed.all()
    assert np.array_equal(t.bodies["jupiter"].allowed, t.sun_alt < -6.0)
    # something has to rise and set over 24 h from the equator
    assert t.bodies["jupiter"].alt.max() > 60 and t.bodies["jupiter"].alt.min() < 0


def test_planet_tracks_apply_the_site_horizon_profile():
    t = planets.planet_tracks(0.0, 0.0, h.NIGHT, horizon_points=[[0, 40], [180, 40]], bodies=("jupiter",))
    assert np.allclose(t.bodies["jupiter"].limit, 40.0)
    flat = planets.planet_tracks(0.0, 0.0, h.NIGHT, bodies=("jupiter",))
    assert np.allclose(flat.bodies["jupiter"].limit, 15.0)
    assert t.bodies["jupiter"].alt.shape == flat.bodies["jupiter"].alt.shape


def test_end_to_end_with_real_ephemeris_and_synthetic_weather():
    p = build_forecast(h.ZERO_SITE, h.NIGHT, h.make_frames(), "ecmwf_ifs025", now=h.NOW, sources=SRC)
    assert p["available"] is True and len(p["hourly"]) == 24
    scores = [b["window"]["score"] if b["window"] else 0 for b in p["bodies"]]
    assert scores == sorted(scores, reverse=True)
    assert p["bodies"], "at least one body should be listed from a 24 h night"


# ---- wiring ----------------------------------------------------------------------------------------

def test_worker_beat_and_routing():
    from celery.schedules import crontab
    from app.worker import celery_app

    entry = celery_app.conf.beat_schedule["refresh-seeing-forecasts"]
    assert entry["task"] == "app.tasks.seeing.refresh_forecasts"
    assert entry["schedule"] == crontab(minute=10, hour="*/3")
    assert celery_app.conf.task_routes["app.tasks.seeing.*"] == {"queue": "celery"}
    assert "app.tasks.seeing" in celery_app.conf.include


def test_task_registered_and_router_mounted():
    import app.tasks.seeing as t
    from app.api import seeing as api

    assert t.refresh_forecasts.name == "app.tasks.seeing.refresh_forecasts"
    assert t.refresh_site.name == "app.tasks.seeing.refresh_site"
    assert any(route.path == "/forecast" for route in api.router.routes)


def test_config_fields_default():
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["meteoblue_api_key"].default is None
    assert fields["meteoblue_min_interval_h"].default == 6
    assert fields["seeing_coord_decimals"].default == 2
    assert fields["seeing_enabled"].default is True
