"""
API-level tests for the R0 Equipment & Sites routes (no database): response
shapes against the R0 API contract, the Telescopius key handling, horizon
export and the ImageDetail/PUT rig override schema.
"""

import asyncio
import sys
import types
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.api import equipment as api  # noqa: E402

NOW = datetime(2026, 9, 26, 12, 0)

CAMERA_KEYS = {"id", "name", "maker", "sensor_width_px", "sensor_height_px", "pixel_size_um", "is_color",
               "is_cooled", "match_patterns", "source", "external_ref", "notes", "rig_count", "created_at",
               "updated_at"}
OPTIC_KEYS = {"id", "name", "kind", "aperture_mm", "focal_length_mm", "source", "external_ref", "notes",
              "rig_count", "created_at", "updated_at"}
FILTER_KEYS = {"id", "name", "band", "bandwidth_nm", "match_patterns", "source", "external_ref", "created_at",
               "updated_at"}
RIG_KEYS = {"id", "name", "camera_id", "optic_id", "camera_name", "optic_name", "modifier_name",
            "modifier_factor", "binning", "is_active", "is_mounted", "mount_name", "filter_ids", "filters",
            "measured_scale_arcsec", "measured_count", "scale", "fov_deg", "focal_ratio", "effective_focal_mm",
            "sampling", "scale_check", "image_count", "last_used", "created_at", "updated_at",
            "sampling_seeing_source", "delivered_fwhm"}  # Q1d
SITE_KEYS = {"id", "name", "latitude", "longitude", "elevation_m", "timezone", "bortle", "sqm",
             "typical_seeing_arcsec", "is_default", "horizon", "horizon_source", "image_count", "created_at",
             "updated_at", "measured_seeing"}  # Q1d


def _camera(**kw):
    base = dict(id=1, name="ZWO ASI294MM Pro", maker="ZWO", sensor_width_px=4144, sensor_height_px=2822,
                pixel_size_um=4.63, is_color=False, is_cooled=True, match_patterns=["zwo asi294mm pro"],
                source="DETECTED", external_ref=None, notes=None, created_at=NOW, updated_at=NOW)
    return SimpleNamespace(**{**base, **kw})


def _optic(**kw):
    base = dict(id=2, name="C11 EdgeHD", kind="TELESCOPE", aperture_mm=280.0, focal_length_mm=2800.0,
                source="TELESCOPIUS", external_ref="telescopius:1", notes=None, created_at=NOW, updated_at=NOW)
    return SimpleNamespace(**{**base, **kw})


def _site(**kw):
    base = dict(id=3, name="Backyard", latitude=51.48, longitude=0.0, elevation_m=None, timezone="Europe/London",
                bortle=5, sqm=None, typical_seeing_arcsec=2.5, is_default=True, horizon=[[0.0, 20.0], [180.0, 25.5]],
                horizon_source="IMPORTED", created_at=NOW, updated_at=NOW)
    return SimpleNamespace(**{**base, **kw})


def test_camera_optic_filter_shapes():
    assert set(api.camera_dict(_camera(), 2)) == CAMERA_KEYS
    assert set(api.optic_dict(_optic(), 1)) == OPTIC_KEYS
    flt = SimpleNamespace(id=5, name="Ha 7nm", band="Ha", bandwidth_nm=7.0, match_patterns=["ha"], source="MANUAL",
                          external_ref=None, created_at=NOW, updated_at=NOW)
    assert set(api.filter_dict(flt)) == FILTER_KEYS
    assert api.camera_dict(_camera(match_patterns=None))["match_patterns"] == []


def test_rig_shape_and_computed_fields():
    flt = SimpleNamespace(id=5, name="Ha 7nm", band="Ha")
    rig = SimpleNamespace(id=7, name="C11 + ASI294", camera_id=1, optic_id=2, camera=_camera(), optic=_optic(),
                          modifier_name=None, modifier_factor=1.0, binning=1, is_active=True, is_mounted=True,
                          mount_name="EQMod", filters=[flt], measured_scale_arcsec=0.34, measured_count=850,
                          created_at=NOW, updated_at=NOW)
    d = api.rig_dict(rig, (850, NOW), 2.5)
    assert set(d) == RIG_KEYS
    assert d["filter_ids"] == [5] and d["filters"] == [{"id": 5, "name": "Ha 7nm", "band": "Ha"}]
    assert d["scale"] == pytest.approx(0.341, abs=0.001)
    assert d["fov_deg"] == [pytest.approx(0.393, abs=0.001), pytest.approx(0.267, abs=0.001)]
    assert d["focal_ratio"] == 10.0 and d["effective_focal_mm"] == 2800.0
    assert d["sampling"]["verdict"] == "over"
    assert d["scale_check"]["verdict"] == "ok"
    assert d["image_count"] == 850 and d["last_used"] == NOW.isoformat()

    unknown = api.rig_dict(SimpleNamespace(**{**rig.__dict__, "camera": _camera(pixel_size_um=None),
                                              "measured_scale_arcsec": None}), None)
    assert unknown["scale"] is None and unknown["fov_deg"] is None and unknown["sampling"] is None
    assert unknown["scale_check"]["verdict"] == "unknown"
    assert unknown["image_count"] == 0 and unknown["last_used"] is None


def test_rig_sampling_uses_measured_fwhm_with_enough_subs():
    rig = SimpleNamespace(id=7, name="C11", camera_id=1, optic_id=2, camera=_camera(), optic=_optic(),
                          modifier_name=None, modifier_factor=1.0, binning=1, is_active=True, is_mounted=True,
                          mount_name=None, filters=[], measured_scale_arcsec=0.34, measured_count=850,
                          created_at=NOW, updated_at=NOW)
    few = api.rig_dict(rig, None, 2.5, {"n": 10, "median_arcsec": 1.8})
    assert few["sampling_seeing_source"] == "SITE" and few["sampling"]["seeing_arcsec"] == 2.5
    many = api.rig_dict(rig, None, 2.5, {"n": 386, "median_arcsec": 2.63})
    assert many["sampling_seeing_source"] == "MEASURED" and many["sampling"]["seeing_arcsec"] == 2.63
    assert many["delivered_fwhm"]["n"] == 386


def test_site_shape():
    d = api.site_dict(_site(), 1234)
    assert set(d) == SITE_KEYS
    assert d["image_count"] == 1234 and d["horizon_source"] == "IMPORTED"


def test_telescopius_import_without_key_is_400(monkeypatch):
    monkeypatch.setattr(api.settings, "telescopius_api_key", None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.import_telescopius(db=None))
    assert exc.value.status_code == 400
    assert exc.value.detail == "Telescopius API key not configured"


def test_telescopius_upstream_failure_is_502_without_key(monkeypatch):
    secret = "sk-not-a-real-key-456"
    monkeypatch.setattr(api.settings, "telescopius_api_key", secret)
    from app.services import telescopius

    def fail(key, timeout=20.0):
        raise telescopius.TelescopiusError("Telescopius returned HTTP 500")

    monkeypatch.setattr(telescopius, "fetch_equipment", fail)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.import_telescopius(db=None))
    assert exc.value.status_code == 502 and secret not in exc.value.detail


class _FakeDB:
    def __init__(self, obj):
        self.obj = obj

    async def get(self, model, obj_id):
        return self.obj if self.obj is not None and self.obj.id == obj_id else None


def test_horizon_export_is_hrz_attachment():
    resp = asyncio.run(api.export_horizon(3, db=_FakeDB(_site(name="Back yard / 2"))))
    assert resp.headers["content-disposition"] == 'attachment; filename="Back_yard_2.hrz"'
    body = resp.body.decode()
    assert body.splitlines()[-2:] == ["0 20", "180 25.5"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.export_horizon(3, db=_FakeDB(_site(horizon=None))))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.export_horizon(99, db=_FakeDB(None)))
    assert exc.value.status_code == 404


def test_timezone_validation():
    assert api._valid_timezone("Europe/London") == "Europe/London"
    with pytest.raises(HTTPException) as exc:
        api._valid_timezone("Mars/Olympus_Mons")
    assert exc.value.status_code == 400


def test_writes_require_admin():
    from app.api.dependencies import require_admin

    for router in (api.router, api.sites_router):
        for route in router.routes:
            deps = {d.call for d in route.dependant.dependencies}
            if route.methods & {"POST", "PUT", "DELETE"}:
                assert require_admin in deps, route.path
            else:
                assert require_admin not in deps, route.path


def test_update_image_request_distinguishes_absent_and_null():
    from app.schemas.image import ImageDetail, UpdateImageRequest

    assert "rig_id" not in UpdateImageRequest(rating=3).model_fields_set
    explicit_null = UpdateImageRequest.model_validate({"rig_id": None})
    assert "rig_id" in explicit_null.model_fields_set and explicit_null.rig_id is None
    assert UpdateImageRequest.model_validate({"rig_id": 4}).rig_id == 4
    for field in ("rig_id", "rig_name", "rig_source", "site_id", "site_name"):
        assert field in ImageDetail.model_fields


def test_unassigned_assign_validation():
    from pydantic import ValidationError
    from app.schemas.equipment import UnassignedAssign

    body = UnassignedAssign(key="SUB_FRAME|zwo asi2600mm pro|6248x4176|none|1.4600", rig_id=3)
    assert body.rig_id == 3
    with pytest.raises(ValidationError):
        UnassignedAssign(key="", rig_id=3)
    with pytest.raises(ValidationError):
        UnassignedAssign.model_validate({"key": "SUB_FRAME|x|1x1|none|-"})
    with pytest.raises(ValidationError):
        UnassignedAssign(key="k" * 401, rig_id=3)


def test_unassigned_routes_are_registered():
    paths = {(r.path, m) for r in api.router.routes for m in r.methods}
    assert ("/unassigned", "GET") in paths
    assert ("/unassigned/assign", "POST") in paths
