"""
Tests for app.services.telescopius (R0 optional import, spec §4.4 / §6).

The fixture is a sanitised /v2.2/equipment/user payload (field names as
verified 2026-09-26; no key; ids kept).
"""

import json
from pathlib import Path

import pytest

from app.services.telescopius import (
    TelescopiusError, fetch_equipment, map_telescopius, merge_plan,
)

FIXTURE = Path(__file__).parent / "fixtures" / "telescopius_equipment_user.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_optics_mapping_and_lens_heuristic(payload):
    plan = map_telescopius(payload)
    optics = {o["name"]: o for o in plan["optics"]}
    assert set(optics) == {"William Optics Zenithstar 73", "Celestron C11 EdgeHD", "EF 200mm f/2.8L II",
                           "Sigma 105mm F1.4 DG HSM Art", "EF 70-200mm f/4L"}
    assert optics["William Optics Zenithstar 73"]["kind"] == "TELESCOPE"
    assert optics["Celestron C11 EdgeHD"]["kind"] == "TELESCOPE"
    assert optics["Celestron C11 EdgeHD"]["focal_length_mm"] == 2800
    assert optics["Celestron C11 EdgeHD"]["aperture_mm"] == 280
    assert optics["EF 200mm f/2.8L II"]["kind"] == "LENS"          # custom_brand Canon
    assert optics["Sigma 105mm F1.4 DG HSM Art"]["kind"] == "LENS"  # brand from the label
    zoom = optics["EF 70-200mm f/4L"]
    assert zoom["focal_length_mm"] == 200 and "Zoom 70-200 mm" in zoom["notes"]
    assert optics["William Optics Zenithstar 73"]["external_ref"] == "telescopius:50601"
    assert plan["skipped"]["optics"] == 1                           # no focal length


def test_camera_mapping_derives_pixel_size(payload):
    plan = map_telescopius(payload)
    cams = {c["name"]: c for c in plan["cameras"]}
    assert cams["ZWO ASI1600MM Pro"]["pixel_size_um"] == pytest.approx(3.80, abs=0.01)
    assert cams["ZWO ASI294MM Pro"]["pixel_size_um"] == pytest.approx(4.63, rel=0.02)
    assert cams["ZWO ASI294MM Pro (unlocked)"]["pixel_size_um"] == pytest.approx(2.315, rel=0.02)
    assert cams["ZWO ASI1600MM Pro"]["is_color"] is None            # always null upstream
    assert cams["ZWO ASI1600MM Pro"]["is_cooled"] is True
    assert cams["ZWO ASI1600MM Pro"]["external_ref"] == "telescopius:50600"
    assert cams["ZWO ASI1600MM Pro"]["match_patterns"] == ["zwo asi1600mm pro"]
    assert "approximate" in cams["ZWO ASI1600MM Pro"]["notes"]


def test_filters_and_mounts(payload):
    plan = map_telescopius(payload)
    assert [(f["name"], f["band"]) for f in plan["filters"]] == [
        ("Astrodon Ha 5nm", "Ha"), ("Astrodon OIII 3nm", "OIII"), ("Astrodon SII 5nm", "SII")]
    assert plan["mount_suggestions"] == ["CEM60"]


def _as_rows(specs, start_id, source="TELESCOPIUS"):
    return [{**s, "id": start_id + i, "source": source} for i, s in enumerate(specs)]


def test_import_is_idempotent(payload):
    plan = map_telescopius(payload)
    first = merge_plan(plan, {})
    assert len(first["cameras"]["create"]) == 3
    assert len(first["optics"]["create"]) == 5
    assert len(first["filters"]["create"]) == 3

    existing = {k: _as_rows(first[k]["create"], 100) for k in ("cameras", "optics", "filters")}
    second = merge_plan(map_telescopius(payload), existing)
    for kind in ("cameras", "optics", "filters"):
        assert second[kind]["create"] == []
        assert second[kind]["update"] == []
    assert second["cameras"]["skipped"] == 3
    assert second["optics"]["skipped"] == 5 + 1   # + the broken entry


def test_reimport_updates_changed_rows_and_respects_detected_pixel_size(payload):
    plan = map_telescopius(payload)
    existing = {
        "cameras": [
            # Detected earlier from XPIXSZ, same name: link it but keep the measured pixel size.
            {"id": 1, "name": "ZWO ASI1600MM Pro", "source": "DETECTED", "external_ref": None,
             "pixel_size_um": 3.8, "sensor_width_px": 4656, "sensor_height_px": 3520},
        ],
        "optics": [
            {"id": 2, "name": "Celestron C11 EdgeHD", "source": "TELESCOPIUS", "external_ref": "telescopius:50602",
             "kind": "TELESCOPE", "aperture_mm": 280, "focal_length_mm": 2700, "notes": None},
            {"id": 3, "name": "Sigma 105 (mine)", "source": "MANUAL", "external_ref": "telescopius:50604",
             "kind": "LENS", "aperture_mm": 70, "focal_length_mm": 105, "notes": "my notes"},
        ],
    }
    merged = merge_plan(plan, existing)
    cam_updates = dict(merged["cameras"]["update"])
    assert cam_updates[1]["external_ref"] == "telescopius:50600"
    assert "pixel_size_um" not in cam_updates[1]
    opt_updates = dict(merged["optics"]["update"])
    assert opt_updates[2] == {"focal_length_mm": 2800}
    assert 3 not in opt_updates                                    # MANUAL values are kept


def test_fetch_requires_key_and_never_echoes_it(monkeypatch):
    with pytest.raises(TelescopiusError):
        fetch_equipment("")

    import httpx

    class Resp:
        status_code = 401

        def json(self):
            return {}

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    secret = "sk-not-a-real-key-123"
    with pytest.raises(TelescopiusError) as exc:
        fetch_equipment(secret)
    assert secret not in str(exc.value)
    assert seen["headers"]["Authorization"] == f"Key {secret}"
    assert seen["url"].endswith("/v2.2/equipment/user")
    assert seen["timeout"] == 20.0

    def boom(url, headers=None, timeout=None):
        raise httpx.ConnectError(f"cannot connect with {headers['Authorization']}")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(TelescopiusError) as exc:
        fetch_equipment(secret)
    assert secret not in str(exc.value)
