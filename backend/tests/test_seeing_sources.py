"""S1 §8 privacy guards, §3 source parsers and model selection. No network (httpx MockTransport)."""

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
import pytest

import _seeing_helpers as h
from app.services.seeing import models as mdl
from app.services.seeing import privacy
from app.services.seeing.sources import SourceError
from app.services.seeing.sources import meteoblue as mb
from app.services.seeing.sources import open_meteo as om

FIX = Path(__file__).parent / "fixtures" / "seeing"
GLOBALS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]


def _fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _has_location_key(obj) -> bool:
    if isinstance(obj, dict):
        return any(privacy._is_location_key(k) or _has_location_key(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_location_key(v) for v in obj)
    return False


# ---- privacy helpers -------------------------------------------------------------------------------

def test_round_coords_default_and_explicit():
    assert privacy.round_coords(h.SENTINEL_LAT, h.SENTINEL_LON, 2) == (12.35, -65.43)
    assert privacy.round_coords(h.SENTINEL_LAT, h.SENTINEL_LON, 0) == (12.0, -65.0)
    assert privacy.round_coords(0.0, 0.0) == (0.0, 0.0)
    assert privacy.round_coords(h.SENTINEL_LAT, h.SENTINEL_LON) == (12.35, -65.43)   # default 2 decimals


def test_strip_location_removes_provider_echoes():
    raw = {"latitude": h.SENTINEL_LAT, "longitude": h.SENTINEL_LON, "elevation": 12.0, "generationtime_ms": 1.2,
           "location_lat": 1, "site_lon": 2, "lat": 3, "lng": 4,
           "hourly": {"time": ["2030-01-01T00:00"], "relative_humidity_2m": [90], "wind_speed_10m": [1.0]},
           "nested": [{"Latitude": 5, "cloud_cover": [1]}]}
    out = privacy.strip_location(raw)
    assert out == {"generationtime_ms": 1.2,
                   "hourly": {"time": ["2030-01-01T00:00"], "relative_humidity_2m": [90], "wind_speed_10m": [1.0]},
                   "nested": [{"cloud_cover": [1]}]}
    assert "relative_humidity_2m" in out["hourly"]          # "relative" contains "lat" but is not a location key
    assert "latitude" in raw                                 # input untouched
    assert not any(s in json.dumps(out) for s in h.SENTINEL_STRINGS)


def test_redact_url_drops_query_and_coordinate_pairs():
    msg = "Client error for url 'https://api.example.org/v1/forecast?latitude=12.3456&longitude=-65.4321&apikey=SECRETKEY'"
    out = privacy.redact_url(msg)
    assert "12.3456" not in out and "65.4321" not in out and "SECRETKEY" not in out
    assert "https://api.example.org/v1/forecast" in out
    assert privacy.redact_url("failed at latitude=12.3456 longitude=-65.4321") == \
        "failed at latitude=<redacted> longitude=<redacted>"
    assert privacy.redact_url("plain message") == "plain message"
    assert "SECRETKEY" not in privacy.redact_url("GET https://x.org/p?apikey=SECRETKEY HTTP/1.1")


def test_http_loggers_are_quiet():
    import app.services.seeing  # noqa: F401  (import applies the guard)
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


# ---- pick_models -----------------------------------------------------------------------------------

def test_pick_models_outside_every_box_is_global_with_ecmwf_primary():
    models, primary = mdl.pick_models(0.0, 0.0)
    assert models == GLOBALS and primary == "ecmwf_ifs025"


@pytest.mark.parametrize("box", mdl.REGIONAL_BOXES, ids=lambda b: b.model)
def test_pick_models_inside_each_box(box):
    lat, lon = box.centre                              # computed from the box itself, never a real site
    assert box.contains(lat, lon)
    models, primary = mdl.pick_models(lat, lon)
    containing = [b for b in mdl.REGIONAL_BOXES if b.contains(lat, lon)]
    assert primary == min(containing, key=lambda b: b.area).model
    assert models[0] == primary and models[1:] == GLOBALS


@pytest.mark.parametrize("box", mdl.REGIONAL_BOXES, ids=lambda b: b.model)
def test_pick_models_just_outside_each_box(box):
    for lat, lon in [(box.lat_max + 0.01, box.centre[1]), (box.lat_min - 0.01, box.centre[1]),
                     (box.centre[0], box.lon_min - 0.01), (box.centre[0], box.lon_max + 0.01)]:
        models, primary = mdl.pick_models(lat, lon)
        assert box.model != primary or any(b.model == box.model and b.contains(lat, lon) for b in mdl.REGIONAL_BOXES)
        assert models[-3:] == GLOBALS


def test_pick_models_edges_are_inclusive_and_smallest_box_wins():
    b = mdl.REGIONAL_BOXES[0]
    assert mdl.regional_model(b.lat_min, b.lon_min) is not None
    overlaps = [(x, y) for x in mdl.REGIONAL_BOXES for y in mdl.REGIONAL_BOXES if x is not y and
                x.contains(*y.centre)]
    for x, y in overlaps:
        assert mdl.regional_model(*y.centre) == min([bb for bb in mdl.REGIONAL_BOXES if bb.contains(*y.centre)],
                                                    key=lambda bb: bb.area).model


# ---- Open-Meteo: fetch (mock transport) ------------------------------------------------------------

def _om_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _echoing_response(request):
    body = _fixture("open_meteo_sanitized.json")
    body.update(latitude=h.SENTINEL_LAT, longitude=h.SENTINEL_LON, elevation=33.0)   # providers echo these back
    return httpx.Response(200, json=body)


def test_fetch_open_meteo_rounds_coords_strips_response_and_uses_known_variables():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return _echoing_response(request)

    raw = om.fetch_open_meteo(h.SENTINEL_LAT, h.SENTINEL_LON, GLOBALS, client=_om_client(handler))
    p = seen["params"]
    assert p["latitude"] == "12.35" and p["longitude"] == "-65.43"
    assert not any(s in seen["url"] for s in h.SENTINEL_STRINGS)
    assert p["models"] == ",".join(GLOBALS) and p["timezone"] == "UTC" and p["wind_speed_unit"] == "ms"
    assert p["forecast_days"] == "7"
    assert set(p["hourly"].split(",")) == set(om.HOURLY_VARS)
    for var in ("wind_speed_10m", "wind_gusts_10m", "boundary_layer_height", "wind_speed_250hPa",
                "wind_direction_850hPa", "temperature_700hPa", "cloud_cover_high", "dew_point_2m"):
        assert var in om.HOURLY_VARS
    assert not _has_location_key(raw)
    assert not any(s in json.dumps(raw) for s in h.SENTINEL_STRINGS)


def test_fetch_errors_are_redacted_and_have_no_chained_url(caplog):
    def http_500(request):
        return httpx.Response(500, json={"reason": "boom"})

    with pytest.raises(SourceError) as ei:
        om.fetch_open_meteo(h.SENTINEL_LAT, h.SENTINEL_LON, GLOBALS, client=_om_client(http_500))
    assert "500" in str(ei.value) and not any(s in str(ei.value) for s in h.SENTINEL_STRINGS)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(SourceError) as ei:
        om.fetch_open_meteo(h.SENTINEL_LAT, h.SENTINEL_LON, GLOBALS, client=_om_client(boom))
    assert not any(s in str(ei.value) for s in h.SENTINEL_STRINGS)


def test_fetching_logs_no_coordinates(caplog):
    caplog.set_level(logging.DEBUG)
    om.fetch_open_meteo(h.SENTINEL_LAT, h.SENTINEL_LON, GLOBALS, client=_om_client(_echoing_response))
    assert not any(s in caplog.text for s in h.SENTINEL_STRINGS)
    assert "latitude=" not in caplog.text


def test_fetch_rejects_unexpected_shape():
    with pytest.raises(SourceError):
        om.fetch_open_meteo(0.0, 0.0, GLOBALS, client=_om_client(lambda r: httpx.Response(200, json={"x": 1})))


# ---- fixtures and parsers --------------------------------------------------------------------------

def test_fixtures_contain_no_location_keys_or_real_dates():
    for f in FIX.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert not _has_location_key(data), f.name
    om_fix = _fixture("open_meteo_sanitized.json")
    assert all(t.startswith("2030-01-0") for t in om_fix["hourly"]["time"])          # shifted to a fake date
    assert om_fix["hourly"]["time"][0] == "2030-01-01T00:00"
    text = (FIX / "open_meteo_sanitized.json").read_text(encoding="utf-8")
    assert not any(s in text for s in h.SENTINEL_STRINGS)


def test_parse_open_meteo_multi_model_fixture():
    frames = om.parse_open_meteo(_fixture("open_meteo_sanitized.json"), GLOBALS)
    assert set(frames) == set(GLOBALS)
    f = frames["ecmwf_ifs025"]
    assert len(f.times) == 72 and f.times[0] == datetime(2030, 1, 1, 0, 0)
    assert len(f.values["wind_speed_10m"]) == 72
    assert f.at("wind_speed_10m", datetime(2030, 1, 1, 3)) == f.values["wind_speed_10m"][3]
    assert f.at("wind_speed_10m", datetime(1999, 1, 1)) is None and f.at("nope", datetime(2030, 1, 1)) is None
    assert any(v is not None for v in f.values["wind_speed_250hPa"])
    # a variable a model does not provide is simply None, not an error
    assert all(v is None for v in f.values.get("boundary_layer_height", [None]))


def test_parse_open_meteo_single_model_has_unsuffixed_keys():
    raw = {"hourly": {"time": ["2030-01-01T00:00", "2030-01-01T01:00"], "wind_speed_10m": [1.0, None],
                      "cloud_cover": [10, 20]}}
    frames = om.parse_open_meteo(raw, ["ecmwf_ifs025"])
    assert frames["ecmwf_ifs025"].values["wind_speed_10m"] == [1.0, None]
    assert frames["ecmwf_ifs025"].values["cloud_cover"] == [10.0, 20.0]


def test_parse_open_meteo_drops_models_without_data():
    raw = {"hourly": {"time": ["2030-01-01T00:00"], "wind_speed_10m_a": [1.0], "wind_speed_10m_b": [None]}}
    assert set(om.parse_open_meteo(raw, ["a", "b"])) == {"a"}


def test_upper_levels_filled_from_reference_model():
    frames = om.parse_open_meteo(_fixture("open_meteo_sanitized.json"), GLOBALS)
    icon = frames["icon_seamless"]
    for var in om.UPPER_VARS:
        icon.values.pop(var, None)
    om.fill_upper_levels(frames, "ecmwf_ifs025")
    assert icon.values["wind_speed_250hPa"] == frames["ecmwf_ifs025"].values["wind_speed_250hPa"]
    assert icon.values["temperature_500hPa"] == frames["ecmwf_ifs025"].values["temperature_500hPa"]
    # surface variables are never overwritten
    assert icon.values["wind_speed_10m"] != [None] * 72


def test_parse_meteoblue_fixture():
    f = mb.parse_meteoblue(_fixture("meteoblue_synthetic.json"))
    assert f.times[0] == datetime(2030, 1, 1, 0, 0) and len(f.times) == 4
    assert f.values["seeing_index1"] == [4.0, 5.0, 4.0, 2.0]
    assert f.values["jet_stream"][3] == 41.0
    assert f.values["seeing_arcsec"][0] == 1.6
    assert f.values["badlayer_bottom"][2] is None
    assert f.at("seeing_index1", datetime(2030, 1, 1, 1)) == 5.0
    assert mb.parse_meteoblue({}).times == []


def test_fetch_meteoblue_errors_never_contain_the_key_or_coordinates():
    def status(code):
        return lambda request: httpx.Response(code, json={})

    for code, hint in ((401, "invalid key"), (402, "out of credits"), (429, "out of credits")):
        with pytest.raises(SourceError) as ei:
            mb.fetch_meteoblue(h.SENTINEL_LAT, h.SENTINEL_LON, "KEY123", client=_om_client(status(code)))
        msg = str(ei.value)
        assert hint in msg and "KEY123" not in msg and not any(s in msg for s in h.SENTINEL_STRINGS)

    seen = {}

    def ok(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={**_fixture("meteoblue_synthetic.json"), "latitude": h.SENTINEL_LAT})

    raw = mb.fetch_meteoblue(h.SENTINEL_LAT, h.SENTINEL_LON, "KEY123", client=_om_client(ok))
    assert seen["lat"] == "12.35" and seen["lon"] == "-65.43"
    assert "latitude" not in raw
