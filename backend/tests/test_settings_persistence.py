import json

from app.api import settings as settings_api


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def _patch(monkeypatch, redis, db):
    monkeypatch.setattr(settings_api, "get_redis_client", lambda: redis)
    monkeypatch.setattr(settings_api, "_db_load", lambda: db.get("v"))
    monkeypatch.setattr(settings_api, "_db_save", lambda raw: db.__setitem__("v", raw))


DOC = json.dumps({"astrometry_provider": "nova", "mount_friendly_names": {"/data/mount2": "finals"}})


def test_restore_repopulates_redis_from_postgres(monkeypatch):
    redis, db = FakeRedis(), {"v": DOC}
    _patch(monkeypatch, redis, db)
    assert settings_api.restore_settings_cache() == DOC
    assert redis.data[settings_api.SETTINGS_KEY] == DOC


def test_restore_imports_legacy_redis_value_into_postgres(monkeypatch):
    redis, db = FakeRedis(), {}
    redis.data[settings_api.SETTINGS_KEY] = DOC
    _patch(monkeypatch, redis, db)
    settings_api.restore_settings_cache()
    assert db["v"] == DOC


def test_get_settings_survives_redis_wipe(monkeypatch):
    redis, db = FakeRedis(), {"v": DOC}
    _patch(monkeypatch, redis, db)
    assert settings_api.get_settings().mount_friendly_names == {"/data/mount2": "finals"}


def test_update_writes_postgres_and_redis(monkeypatch):
    redis, db = FakeRedis(), {}
    _patch(monkeypatch, redis, db)
    new = settings_api.SystemSettings(astrometry_provider="nova", mount_friendly_names={"/a": "A"})
    settings_api.update_settings(new)
    assert json.loads(db["v"])["mount_friendly_names"] == {"/a": "A"}
    assert redis.data[settings_api.SETTINGS_KEY] == db["v"]
