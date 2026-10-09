"""
Online catalog overlay layers (O1): VizieR/SkyBoT parsing, query planning, cache and
throttle, de-duplication against local catalogs, settings gate. No network: upstream
answers are VOTables / JSON recorded from the live services in tests/fixtures/online.
"""

import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import settings as settings_api
from app.services import sky_overlay as so
from app.services.online_catalogs import service, skybot, vizier
from app.services.online_catalogs.registry import BY_KEY, REGISTRY
from app.services.online_catalogs.vizier import OnlineCatalogError

FIX = Path(__file__).parent / "fixtures" / "online"


def _vo(name):
    return (FIX / f"{name}.xml").read_bytes()


def _rows(key, fixture):
    return vizier.to_rows(BY_KEY[key], vizier.parse_votable(_vo(fixture)))


# ---- VizieR parsing ------------------------------------------------------------------------

def test_pgc_rows_sizes_and_aliases():
    rows = {r["designation"]: r for r in _rows("PGC", "pgc_m31")}
    m31 = rows["PGC 2557"]
    assert m31["ra"] == pytest.approx(10.6846, abs=1e-3)
    assert m31["dec"] == pytest.approx(41.2694, abs=1e-3)
    assert m31["major"] == pytest.approx(0.1 * 10 ** 3.3, rel=1e-3)          # logD25 3.3 -> ~199.5'
    assert m31["minor"] == pytest.approx(m31["major"] / 10 ** 0.45, rel=1e-3)
    assert m31["pa"] == pytest.approx(35.0)
    assert "NGC224" in m31["aliases"]
    assert m31["object_type"].startswith("Galaxy")
    assert m31["url"].endswith("PGC2557")


def test_ldn_area_becomes_equal_area_diameter():
    rows = _rows("LDN", "ldn_taurus")
    assert rows and all(r["designation"].startswith("LDN ") for r in rows)
    r = rows[0]
    assert r["major"] > 0 and r["minor"] is None and r["pa"] is None
    assert "Dark nebula" in r["object_type"]


def test_abell_reads_both_tables_and_skips_empty_one():
    rows = _rows("ABELL", "abell_coma")
    names = [r["designation"] for r in rows]
    assert "Abell 1656" in names                                             # Coma cluster
    coma = next(r for r in rows if r["designation"] == "Abell 1656")
    assert "z=0.023" in coma["object_type"]
    assert coma["url"].endswith("ACO%201656")


def test_pn_uses_common_name_as_designation():
    rows = _rows("PN", "pn_m57")
    assert rows[0]["designation"] == "NGC 6720"
    assert rows[0]["aliases"] == ["PN G063.1+13.9"]


def test_lbn_rows_have_diameters():
    rows = _rows("LBN", "lbn_orion")
    assert rows and rows[0]["designation"].startswith("LBN ")
    assert rows[0]["major"] and rows[0]["minor"]


def test_empty_result_is_no_rows():
    assert _rows("ARP", "empty") == []


def test_vizier_error_status_raises():
    bad = b'<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">RA(lon) outside range</INFO></VOTABLE>'
    with pytest.raises(OnlineCatalogError):
        vizier.parse_votable(bad)


def test_vizier_coordinates_always_decimal():
    params = vizier.query_params(BY_KEY["LDN"], 68, 26, 1.5)
    assert params["-c"] == "68.000000 +26.000000"                             # '68 +26' is sexagesimal to VizieR
    assert params["-out.add"] == "_RAJ,_DEJ"


# ---- SkyBoT --------------------------------------------------------------------------------

def _skybot_records():
    return json.loads((FIX / "skybot.json").read_text())


def test_skybot_rows_labels_motion_and_limit():
    rows = {r["designation"]: r for r in skybot.to_rows(_skybot_records(), mag_limit=18.0)}
    assert "(580362) 2015 BX319" not in rows                                  # V 23.2, fainter than the limit
    eros = rows["(433) Eros"]
    assert eros["ra"] == pytest.approx(150.05, abs=1e-6)
    assert eros["dec"] == pytest.approx(12 + 5 / 60, abs=1e-6)
    assert eros["motion_arcsec_h"] == pytest.approx(math.hypot(120, 50), abs=0.1)
    assert eros["url"].endswith("sstr=433")
    comet = rows["C/2023 A3 (Tsuchinshan-ATLAS)"]
    assert comet["is_comet"] is True and not eros["is_comet"]
    assert rows["2020 QB52"]["dec"] == pytest.approx(-0.5)                    # '-00 30' keeps its sign


def test_skybot_error_object_raises():
    with pytest.raises(OnlineCatalogError):
        skybot.to_rows({"flag": -1, "message": "computeEphemeris: Connection closed"}, None)


def test_skybot_epoch_is_mid_exposure():
    start = datetime(2024, 10, 14, 0, 0, 0)
    mid = skybot.epoch_for(start, 300)
    assert (mid - start).total_seconds() == 150
    assert skybot.julian_date(datetime(2000, 1, 1, 12)) == pytest.approx(2451545.0)
    assert skybot.epoch_for(None, 300) is None


# ---- planning ------------------------------------------------------------------------------

def _frame(scale_deg=2.0 / 3600, w=4000, h=3000, ra=150.0, dec=12.0):
    hdr = {"WCSAXES": 2, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": ra, "CRVAL2": dec,
           "CRPIX1": w / 2, "CRPIX2": h / 2, "CD1_1": -scale_deg, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": scale_deg,
           "IMAGEW": w, "IMAGEH": h, "NAXIS": 0}
    img = SimpleNamespace(wcs_header=hdr, raw_header=None, width_pixels=w, height_pixels=h, file_format="FITS",
                          pixel_scale_arcsec=None)
    return so.resolve_frame(img)


def test_pgc_auto_size_limit_follows_plate_scale():
    frame = _frame(scale_deg=2.0 / 3600)                                      # 2"/px -> 8 px = 16" = 0.27'
    plan = service.plan_query(BY_KEY["PGC"], frame, None)
    assert plan["limit"] == pytest.approx(0.27, abs=0.01)
    assert plan["constraints"]["logD25"] == f">={math.log10(plan['limit'] * 10):.3f}"
    explicit = service.plan_query(BY_KEY["PGC"], frame, 1.0)
    assert explicit["constraints"]["logD25"] == ">=1.000"


def test_skybot_plan_needs_capture_time_and_small_field():
    frame = _frame()
    no_date = SimpleNamespace(capture_date_utc=None, exposure_time_seconds=60)
    assert service.plan_query(BY_KEY["SKYBOT"], frame, None, no_date)["reason"] == service.REASON_NO_CAPTURE_TIME
    dated = SimpleNamespace(capture_date_utc=datetime(2024, 10, 14), exposure_time_seconds=60)
    plan = service.plan_query(BY_KEY["SKYBOT"], frame, None, dated)
    assert plan["limit"] == 18.0 and plan["jd"] == pytest.approx(skybot.julian_date(datetime(2024, 10, 14, 0, 0, 30)))
    wide = _frame(scale_deg=20.0 / 3600)                                      # ~13.9 deg diagonal
    assert service.plan_query(BY_KEY["SKYBOT"], wide, None, dated)["reason"] == service.REASON_FIELD_TOO_WIDE


def test_cache_key_depends_on_query_not_image():
    frame = _frame()
    a = service.plan_query(BY_KEY["LDN"], frame, None)
    b = service.plan_query(BY_KEY["LDN"], frame, None)
    assert service.cache_key(BY_KEY["LDN"], a) == service.cache_key(BY_KEY["LDN"], b)
    assert service.cache_key(BY_KEY["LDN"], a) != service.cache_key(BY_KEY["LBN"], a)


# ---- cache and throttle --------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.data = {}

    async def mget(self, *keys):
        return [self.data.get(k) for k in keys]

    async def setex(self, k, ttl, v):
        self.data[k] = v

    async def close(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()

    async def _get():
        return r
    monkeypatch.setattr(service, "_redis", _get)
    return r


def test_cache_hit_skips_upstream(fake_redis, monkeypatch):
    calls = []

    async def cone(spec, ra, dec, radius, constraints):
        calls.append(1)
        return [{"designation": "LDN 1"}]
    monkeypatch.setattr(vizier, "cone", cone)
    plan = {"ra": 1.0, "dec": 2.0, "radius": 1.0, "constraints": {}, "jd": None, "limit": None}
    assert asyncio.run(service.fetch_rows(BY_KEY["LDN"], plan)) == [{"designation": "LDN 1"}]
    assert asyncio.run(service.fetch_rows(BY_KEY["LDN"], plan)) == [{"designation": "LDN 1"}]
    assert len(calls) == 1


def test_failure_is_throttled(fake_redis, monkeypatch):
    calls = []

    async def cone(spec, ra, dec, radius, constraints):
        calls.append(1)
        raise OnlineCatalogError("VizieR unavailable: boom")
    monkeypatch.setattr(vizier, "cone", cone)
    plan = {"ra": 1.0, "dec": 2.0, "radius": 1.0, "constraints": {}, "jd": None, "limit": None}
    with pytest.raises(OnlineCatalogError):
        asyncio.run(service.fetch_rows(BY_KEY["LDN"], plan))
    with pytest.raises(service.ThrottledError):
        asyncio.run(service.fetch_rows(BY_KEY["LDN"], plan))
    assert len(calls) == 1
    with pytest.raises(OnlineCatalogError):                                   # the admin test bypasses it
        asyncio.run(service.fetch_rows(BY_KEY["LDN"], plan, use_cache=False))
    assert len(calls) == 2


# ---- de-duplication and objects ------------------------------------------------------------

def _local(catalog, designation, ra, dec, aliases=()):
    return {"catalog": catalog, "designation": designation, "aliases": list(aliases), "ra": ra, "dec": dec}


def test_dedup_by_alias_and_position():
    rows = _rows("PGC", "pgc_m31")
    local = [_local("MESSIER", "M31", 10.6847, 41.2690, aliases=["NGC 224"]),
             _local("NGC", "NGC0221", 10.6742, 40.8652)]                     # M32, by position only
    kept = {r["designation"] for r in service.dedup_against_local(rows, local)}
    assert "PGC 2557" not in kept and "PGC 2555" not in kept
    assert kept                                                               # others survive


def test_named_stars_do_not_suppress_online_objects():
    row = {"catalog": "VDB", "designation": "vdB 139", "aliases": [], "ra": 315.4, "dec": 68.16}
    star = _local("NAMED_STAR", "HD 200775", 315.4, 68.16)
    assert service.dedup_against_local([row], [star]) == [row]


def test_layer_objects_carry_url_and_motion():
    frame = _frame()
    rows = skybot.to_rows(_skybot_records(), mag_limit=None)
    objects = {o["label"]: o for o in service.build_layer_objects(frame, rows)}
    eros = objects["(433) Eros"]
    assert eros["catalog"] == "SKYBOT" and eros["catalogs"] == ["SKYBOT"]
    assert eros["url"].endswith("sstr=433") and eros["motion_arcsec_h"] > 0
    assert eros["ellipse"] is None
    assert objects["C/2023 A3 (Tsuchinshan-ATLAS)"]["is_comet"] is True


def test_online_catalogs_rank_after_local_ones():
    assert so.catalog_rank("NAMED_STAR") < so.catalog_rank("PGC") == len(so.CATALOG_PRIORITY)


def test_notice_for_stacks_and_file_times():
    master = SimpleNamespace(subtype=SimpleNamespace(value="INTEGRATION_MASTER"), capture_time_source="FITS_UTC")
    assert service.notice_for(BY_KEY["SKYBOT"], master) == service.NOTICE_STACK
    sub = SimpleNamespace(subtype="SUB_FRAME", capture_time_source="FILE_MTIME")
    assert service.notice_for(BY_KEY["SKYBOT"], sub) == service.NOTICE_FILE_TIME
    assert service.notice_for(BY_KEY["PGC"], master) is None


# ---- settings ------------------------------------------------------------------------------

def test_all_catalogs_default_off(monkeypatch):
    from app.services import quality_settings
    monkeypatch.setattr(quality_settings, "runtime_settings", lambda: {})
    cfg = service.catalog_settings()
    assert set(cfg) == {s.key for s in REGISTRY}
    assert not any(c["enabled"] for c in cfg.values())
    assert service.enabled_specs() == []
    assert settings_api.SystemSettings(astrometry_provider="nova").online_catalogs == {}


def test_enabled_setting_and_limit_are_read(monkeypatch):
    from app.services import quality_settings
    monkeypatch.setattr(quality_settings, "runtime_settings",
                        lambda: {"online_catalogs": {"PGC": {"enabled": True, "limit": 0.5}, "LDN": {"enabled": "yes"}}})
    cfg = service.catalog_settings()
    assert cfg["PGC"] == {"enabled": True, "limit": 0.5}
    assert cfg["LDN"]["enabled"] is False                                     # only a real true enables
    assert [s.key for s in service.enabled_specs()] == ["PGC"]


def test_settings_reject_unknown_catalog(monkeypatch):
    monkeypatch.setattr(settings_api, "_db_save", lambda raw: None)
    new = settings_api.SystemSettings(astrometry_provider="nova", online_catalogs={"NOPE": {"enabled": True}})
    with pytest.raises(HTTPException) as e:
        settings_api.update_settings(new)
    assert e.value.status_code == 400


def test_disabled_layer_is_404(monkeypatch):
    from app.api import sky_online
    from app.services import quality_settings
    monkeypatch.setattr(quality_settings, "runtime_settings", lambda: {})
    with pytest.raises(HTTPException) as e:
        asyncio.run(sky_online.get_online_layer(1, "PGC", db=None))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(sky_online.get_online_layer(1, "NOPE", db=None))
    assert e.value.status_code == 404


def test_admin_endpoints_require_admin():
    from app.api import sky_online
    from app.api.dependencies import require_admin
    for route in sky_online.router.routes:
        deps = {d.call for d in route.dependant.dependencies}
        assert (require_admin in deps) == (route.path != "/online-catalogs"), route.path
