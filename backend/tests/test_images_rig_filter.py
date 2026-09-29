"""
R0b §7.3: the images `rig_id` filter accepts an integer id or 'none' (no DB).
"""

import sys
import types

import pytest
from fastapi import HTTPException

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.api import images as api  # noqa: E402


def _sql(**kw) -> str:
    return str(api._build_image_query(**kw).compile()).lower()


def test_rig_none_selects_rigless_images():
    assert "images.rig_id is null" in _sql(rig_id="none")


def test_rig_id_string_is_an_equality_filter():
    sql = _sql(rig_id="7")
    assert "images.rig_id =" in sql
    assert "rig_id is null" not in sql


def test_empty_rig_id_is_no_filter():
    assert _sql(rig_id="") == _sql(rig_id=None) == _sql()


def test_bad_rig_id_is_400():
    with pytest.raises(HTTPException) as exc:
        api._build_image_query(rig_id="abc")
    assert exc.value.status_code == 400


def test_no_endpoint_types_rig_id_as_int():
    for route in api.router.routes:
        for param in route.dependant.query_params:
            if param.name == "rig_id":
                assert param.field_info.annotation == (str | None), route.path


def test_bulk_rig_route_exists():
    routes = {(r.path, m) for r in api.router.routes for m in r.methods}
    assert ("/bulk/rig", "PUT") in routes
