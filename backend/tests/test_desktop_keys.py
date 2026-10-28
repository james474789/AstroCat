"""
D3 5.3: the desktop-only API keys routes (api/settings.py) and the launcher's config.json watcher.
No database: a minimal app with the settings router and a faked current user.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies, settings as settings_api
from app.config import settings
from app.desktop import launcher
from app.desktop.launcher import ConfigWatcher


class _User:
    def __init__(self, is_admin):
        self.is_admin = is_admin


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "secret_key": "s" * 64, "image_paths": ["D:/Astro"],
        "api_keys": {"ASTROMETRY_API_KEY": "abcd1234efgh", "METEOBLUE_API_KEY": "mb-9999"},
    }))
    monkeypatch.setattr(settings, "desktop_mode", True)
    monkeypatch.setattr(settings, "desktop_config_path", str(path))
    return path


def _client(is_admin=True):
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/settings")
    app.dependency_overrides[dependencies.get_current_user] = lambda: _User(is_admin)
    return TestClient(app)


def test_get_masks_values_and_reports_what_is_set(config_file):
    body = _client().get("/api/settings/desktop-keys").json()
    assert set(body) == set(launcher.API_KEY_VARS)
    assert body["ASTROMETRY_API_KEY"] == {"set": True, "hint": "...efgh"}
    assert body["TELESCOPIUS_API_KEY"] == {"set": False, "hint": ""}
    assert "abcd1234" not in json.dumps(body)


def test_server_url_is_returned_in_full_but_keys_never(config_file):
    _client().put("/api/settings/desktop-keys", json={"LOCAL_ASTROMETRY_URL": "http://pi:8080/api"})
    body = _client().get("/api/settings/desktop-keys").json()
    assert body["LOCAL_ASTROMETRY_URL"]["value"] == "http://pi:8080/api"
    assert "value" not in body["METEOBLUE_API_KEY"]


def test_routes_do_not_exist_outside_desktop_mode(config_file, monkeypatch):
    monkeypatch.setattr(settings, "desktop_mode", False)
    assert _client().get("/api/settings/desktop-keys").status_code == 404
    assert _client().put("/api/settings/desktop-keys", json={}).status_code == 404


def test_non_admins_are_refused(config_file):
    assert _client(is_admin=False).get("/api/settings/desktop-keys").status_code == 403
    assert _client(is_admin=False).put("/api/settings/desktop-keys", json={"ASTROMETRY_API_KEY": "x"}).status_code == 403
    assert json.loads(config_file.read_text())["api_keys"]["ASTROMETRY_API_KEY"] == "abcd1234efgh"


def test_put_sets_and_removes_keys_and_keeps_everything_else(config_file):
    r = _client().put("/api/settings/desktop-keys", json={"TELESCOPIUS_API_KEY": " new-key-1234 ",
                                                          "METEOBLUE_API_KEY": None})
    assert r.status_code == 200
    saved = json.loads(config_file.read_text())
    assert saved["api_keys"] == {"ASTROMETRY_API_KEY": "abcd1234efgh", "TELESCOPIUS_API_KEY": "new-key-1234"}
    assert saved["secret_key"] == "s" * 64 and saved["image_paths"] == ["D:/Astro"]
    assert r.json()["METEOBLUE_API_KEY"]["set"] is False
    assert not [p for p in config_file.parent.iterdir() if p.name != "config.json"]  # no temp file left


def test_empty_string_also_removes_and_unknown_names_are_rejected(config_file):
    assert _client().put("/api/settings/desktop-keys", json={"ASTROMETRY_API_KEY": ""}).status_code == 200
    assert "ASTROMETRY_API_KEY" not in json.loads(config_file.read_text())["api_keys"]
    r = _client().put("/api/settings/desktop-keys", json={"SECRET_KEY": "x"})
    assert r.status_code == 400
    assert "SECRET_KEY" not in json.loads(config_file.read_text())["api_keys"]


def test_unreadable_config_is_a_clean_error_not_a_wipe(config_file):
    config_file.write_text("{ not json")
    assert _client().put("/api/settings/desktop-keys", json={"ASTROMETRY_API_KEY": "x"}).status_code == 500
    assert config_file.read_text() == "{ not json"


# --- launcher: config.json watcher ---------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


_mtime_ns = [1_700_000_000_000_000_000]


def _bump(path, text):
    """Write the file and give it a mtime no earlier write had (filesystem clocks can be coarse)."""
    import os
    path.write_text(text)
    _mtime_ns[0] += 1_000_000_000
    os.utime(path, ns=(_mtime_ns[0], _mtime_ns[0]))


def test_watcher_waits_for_the_file_to_settle(tmp_path):
    f = tmp_path / "config.json"
    f.write_text("{}")
    clock = _Clock()
    w = ConfigWatcher(f, debounce=1.0, clock=clock)
    assert not w.poll()
    _bump(f, '{"a": 1}')
    assert not w.poll()                 # change seen, debounce starts
    clock.now = 0.5
    _bump(f, '{"a": 2}')
    assert not w.poll()                 # another write restarts the debounce
    clock.now = 1.2
    assert not w.poll()
    clock.now = 1.6
    assert w.poll()                     # quiet for 1.1 s: fires once
    clock.now = 5
    assert not w.poll()


def test_watcher_survives_a_missing_file(tmp_path):
    w = ConfigWatcher(tmp_path / "gone.json", clock=_Clock())
    assert not w.poll()


@pytest.fixture
def lau(paths_for_launcher, monkeypatch):
    obj = launcher.Launcher.__new__(launcher.Launcher)
    obj.paths = paths_for_launcher
    obj.cfg = launcher.load_config(paths_for_launcher)
    obj.config_watcher = ConfigWatcher(paths_for_launcher.config_file, debounce=0, clock=_Clock())
    obj.reloads = 0
    monkeypatch.setattr(launcher.Launcher, "reload", lambda self: setattr(self, "reloads", self.reloads + 1))
    return obj


@pytest.fixture
def paths_for_launcher(tmp_path):
    from app.desktop.paths import DesktopPaths
    p = DesktopPaths(tmp_path / "AstroCat")
    p.ensure()
    return p


def test_changed_api_keys_trigger_one_reload(lau):
    cfg = json.loads(lau.paths.config_file.read_text())
    cfg["api_keys"] = {"ASTROMETRY_API_KEY": "k"}
    _bump(lau.paths.config_file, json.dumps(cfg))
    assert not lau.reload_if_config_changed()   # first poll only notices the change
    assert lau.reload_if_config_changed()
    assert lau.reloads == 1


def test_a_change_the_launcher_already_applied_is_not_reloaded_twice(lau):
    """The tray writes image_paths then sends "restart": reload() ran, so the file matches lau.cfg."""
    cfg = json.loads(lau.paths.config_file.read_text())
    cfg["image_paths"] = ["D:/Astro"]
    _bump(lau.paths.config_file, json.dumps(cfg))
    lau.cfg = dict(cfg)                          # what reload() does
    assert not lau.reload_if_config_changed()
    assert not lau.reload_if_config_changed()
    assert lau.reloads == 0


def test_launcher_bookkeeping_writes_do_not_reload(lau):
    cfg = json.loads(lau.paths.config_file.read_text())
    cfg["last_backup"] = 123.0
    _bump(lau.paths.config_file, json.dumps(cfg))
    lau.reload_if_config_changed()
    assert not lau.reload_if_config_changed()
    assert lau.reloads == 0
