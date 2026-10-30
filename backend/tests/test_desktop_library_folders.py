"""
QA-30: the desktop-only library folder (mount point) routes in api/settings.py. Adding appends one
folder, never changes the others, has no upper limit, and refuses with the actual reason.
No database: a minimal app with the settings router and a faked current user.
"""

import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies, settings as settings_api
from app.config import settings

URL = "/api/settings/desktop-library-folders"


class _User:
    def __init__(self, is_admin):
        self.is_admin = is_admin


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for name in ("first", "a", "b", "c", "d", "e", "f"):
        (root / name / "sub").mkdir(parents=True)
    return root


@pytest.fixture
def config_file(tmp_path, library, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "secret_key": "s" * 64, "image_paths": [str(library / "first")],
        "api_keys": {"ASTROMETRY_API_KEY": "abcd1234efgh"},
    }))
    monkeypatch.setattr(settings, "desktop_mode", True)
    monkeypatch.setattr(settings, "desktop_config_path", str(path))
    return path


def _client(is_admin=True):
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/settings")
    app.dependency_overrides[dependencies.get_current_user] = lambda: _User(is_admin)
    return TestClient(app)


def _saved(config_file):
    return json.loads(config_file.read_text())


def _add(path):
    return _client().post(URL, json={"path": str(path)})


def _remove(path):
    return _client().request("DELETE", URL, json={"path": str(path)})


def test_adding_six_folders_keeps_every_one_and_the_rest_of_the_config(config_file, library):
    for name in ("a", "b", "c", "d", "e", "f"):
        r = _add(library / name)
        assert r.status_code == 200, r.text
    expected = [str(library / n) for n in ("first", "a", "b", "c", "d", "e", "f")]
    saved = _saved(config_file)
    assert saved["image_paths"] == expected
    assert saved["api_keys"] == {"ASTROMETRY_API_KEY": "abcd1234efgh"} and saved["secret_key"] == "s" * 64
    assert _client().get(URL).json() == {"folders": expected}
    assert not [p for p in config_file.parent.iterdir() if p.name.endswith(".tmp")]


def test_surrounding_quotes_and_spaces_are_ignored(config_file, library):
    assert _add(f'  "{library / "a"}"  ').status_code == 200
    assert _saved(config_file)["image_paths"][-1] == str(library / "a")


@pytest.mark.parametrize("make_path, status, words", [
    (lambda lib: "", 400, "Enter the full path"),
    (lambda lib: "relative/folder", 400, "not a full path"),
    (lambda lib: lib / "missing", 400, "Folder not found"),
    (lambda lib: lib / "first", 409, "already a library folder"),
    (lambda lib: lib / "first" / "sub", 409, "inside the library folder"),
    (lambda lib: lib, 409, "contains the library folder"),
])
def test_refusals_say_why_and_change_nothing(config_file, library, make_path, status, words):
    before = config_file.read_text()
    r = _add(make_path(library))
    assert r.status_code == status
    assert words in r.json()["detail"]
    assert config_file.read_text() == before


def test_a_file_is_not_a_folder(config_file, library):
    (library / "note.txt").write_text("x")
    r = _add(library / "note.txt")
    assert r.status_code == 400 and "not a folder" in r.json()["detail"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths ignore case")
def test_duplicates_ignore_case_on_windows(config_file, library):
    r = _add(str(library / "first").upper())
    assert r.status_code == 409


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_unreadable_folder_is_refused_with_the_reason(config_file, library):
    locked = library / "a"
    locked.chmod(0)
    try:
        r = _add(locked)
    finally:
        locked.chmod(0o755)
    assert r.status_code == 400 and "can't read" in r.json()["detail"]


def test_remove_drops_only_that_folder(config_file, library):
    _add(library / "a")
    _add(library / "b")
    r = _remove(library / "a")
    assert r.status_code == 200
    assert _saved(config_file)["image_paths"] == [str(library / "first"), str(library / "b")]
    assert (library / "a" / "sub").is_dir()  # files on disk are never touched


def test_removing_an_unknown_folder_is_a_404(config_file, library):
    r = _remove(library / "a")
    assert r.status_code == 404 and "not a library folder" in r.json()["detail"]


def test_routes_do_not_exist_outside_desktop_mode(config_file, library, monkeypatch):
    monkeypatch.setattr(settings, "desktop_mode", False)
    assert _client().get(URL).status_code == 404
    assert _add(library / "a").status_code == 404
    assert _remove(library / "first").status_code == 404


def test_non_admins_are_refused(config_file, library):
    assert _client(is_admin=False).post(URL, json={"path": str(library / "a")}).status_code == 403
    assert _saved(config_file)["image_paths"] == [str(library / "first")]


def test_unreadable_config_is_a_clean_error_not_a_wipe(config_file, library):
    config_file.write_text("{ not json")
    assert _add(library / "a").status_code == 500
    assert config_file.read_text() == "{ not json"
