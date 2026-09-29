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


# --- R0c: exact id filter (rig_bucket) --------------------------------------

import asyncio  # noqa: E402
import inspect  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402


def _pg(stmt):
    return stmt.compile(dialect=postgresql.dialect())


def test_image_ids_is_one_array_bind():
    compiled = _pg(api._build_image_query(image_ids=[3, 5]))
    sql = str(compiled).lower()
    assert "images.id = any" in sql
    id_binds = [k for k in compiled.params if "image_ids" in k]
    assert id_binds == ["image_ids"]
    assert compiled.params["image_ids"] == [3, 5]


def test_empty_image_ids_matches_nothing():
    sql = str(_pg(api._build_image_query(image_ids=[]))).lower()
    assert "where false" in sql or "1 != 1" in sql


def test_image_ids_none_is_no_filter():
    compiled = _pg(api._build_image_query(image_ids=None))
    assert "= any" not in str(compiled).lower()
    assert "image_ids" not in compiled.params
    assert str(compiled) == str(_pg(api._build_image_query()))


def test_image_ids_bind_survives_count_subquery():
    stmt = api._build_image_query(image_ids=[3, 5])
    compiled = _pg(select(func.count()).select_from(stmt.subquery()))
    assert "= any" in str(compiled).lower()
    assert compiled.params["image_ids"] == [3, 5]


def test_every_rig_id_endpoint_also_takes_rig_bucket():
    checked = 0
    for route in api.router.routes:
        params = inspect.signature(route.endpoint).parameters
        if "rig_id" in params:
            assert "rig_bucket" in params, route.path
            checked += 1
    assert checked >= 7


class _Untouchable:
    def __getattribute__(self, name):
        raise AssertionError(f"db.{name} was accessed")


def test_rig_bucket_ids_without_key_skips_db():
    assert asyncio.run(api._rig_bucket_ids(_Untouchable(), None)) is None
    assert asyncio.run(api._rig_bucket_ids(_Untouchable(), "")) is None


def test_rig_bucket_ids_forwards_key(monkeypatch):
    from app.api import equipment

    seen = []

    async def fake(db, key):
        seen.append((db, key))
        return [4, 2]

    monkeypatch.setattr(equipment, "bucket_image_ids", fake)
    db = object()
    assert asyncio.run(api._rig_bucket_ids(db, "ZWO ASI2600MM Pro|b1|-")) == [4, 2]
    assert seen == [(db, "ZWO ASI2600MM Pro|b1|-")]
