"""
D1 desktop app: nonce sign-in gating and FastAPI serving the built frontend.
docs/design/20261027-D1-desktop-apps.md. No database: a fake session stands in for Postgres.
"""

import asyncio
import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import auth
from app.config import settings
from app.models.user import User
from app.services import auth_service

NONCE = "n0nce-for-this-launch-7f3a9c"


class _Result:
    def __init__(self, user):
        self._user = user

    def scalars(self):
        return self

    def first(self):
        return self._user


class FakeSession:
    def __init__(self, user=None):
        self.user = user
        self.added = []

    async def execute(self, _stmt):
        return _Result(self.user)

    def add(self, obj):
        self.added.append(obj)
        self.user = obj

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _request(host="127.0.0.1"):
    return Request({
        "type": "http", "method": "GET", "path": "/api/auth/desktop-login",
        "headers": [], "query_string": b"", "client": (host, 50123),
    })


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setattr(settings, "desktop_mode", True)
    monkeypatch.setattr(settings, "desktop_launch_nonce", NONCE)
    monkeypatch.setattr(auth_service, "get_password_hash", lambda p: "hashed")


def _login(db, nonce=NONCE, host="127.0.0.1"):
    return asyncio.run(auth.desktop_login(_request(host), nonce=nonce, db=db))


def test_desktop_login_creates_local_admin_and_sets_cookies(desktop):
    db = FakeSession()
    response = _login(db)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookies = response.headers.getlist("set-cookie")
    assert any(c.startswith("access_token=") for c in cookies)
    assert any(c.startswith(f"{settings.csrf_cookie_name}=") for c in cookies)
    [user] = db.added
    assert user.email == auth.DESKTOP_USER_EMAIL and user.is_admin and user.is_active


def test_desktop_login_reuses_existing_admin(desktop):
    existing = User(email="me@example.com", hashed_password="x", is_admin=True, is_active=True)
    db = FakeSession(existing)
    _login(db)
    assert db.added == []


@pytest.mark.parametrize("nonce,host", [
    ("wrong", "127.0.0.1"),
    ("", "127.0.0.1"),
    (NONCE, "192.168.1.20"),   # right nonce, but not from this machine
])
def test_desktop_login_rejects_bad_nonce_or_remote_client(desktop, nonce, host):
    with pytest.raises(HTTPException) as exc:
        _login(FakeSession(), nonce=nonce, host=host)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("mode,nonce", [(False, NONCE), (True, "")])
def test_desktop_login_absent_outside_desktop_mode(monkeypatch, mode, nonce):
    monkeypatch.setattr(settings, "desktop_mode", mode)
    monkeypatch.setattr(settings, "desktop_launch_nonce", nonce)
    with pytest.raises(HTTPException) as exc:
        _login(FakeSession(), nonce=NONCE)
    assert exc.value.status_code == 404


def test_admin_sign_up_blocked_in_desktop_mode(desktop):
    # slowapi's decorator needs a real Request; call the undecorated function
    fn = auth.admin_sign_up.__wrapped__
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fn(_request(), user_data=None, response=None, db=FakeSession()))
    assert exc.value.status_code == 403


def test_setup_status_reports_desktop_mode(desktop):
    result = asyncio.run(auth.get_setup_status(db=FakeSession()))
    assert result == {"setup_complete": False, "desktop_mode": True}


# ----------------------------------------------------------------------------
# Built frontend served by FastAPI
# ----------------------------------------------------------------------------

@pytest.fixture
def frontend_app(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>astrocat-index</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('bundle')", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("nope", encoding="utf-8")

    import app.main as main
    monkeypatch.setattr(settings, "frontend_dist_dir", str(tmp_path))
    main = importlib.reload(main)
    try:
        yield TestClient(main.app)
    finally:
        monkeypatch.setattr(settings, "frontend_dist_dir", "")
        importlib.reload(main)


def test_frontend_served_with_spa_fallback(frontend_app):
    root = frontend_app.get("/")
    assert root.status_code == 200 and "astrocat-index" in root.text
    assert root.headers["cache-control"] == "no-cache"

    asset = frontend_app.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and "bundle" in asset.text

    deep_link = frontend_app.get("/images/42")
    assert deep_link.status_code == 200 and "astrocat-index" in deep_link.text


def test_frontend_never_shadows_api_or_escapes_dist(frontend_app):
    assert frontend_app.get("/api/health").json()["status"] == "healthy"
    assert frontend_app.get("/api/no-such-route").status_code == 404
    escaped = frontend_app.get("/%2e%2e/secret.txt")
    assert "nope" not in escaped.text


def test_root_is_api_info_without_frontend():
    import app.main as main
    assert settings.frontend_dist_dir == ""
    assert TestClient(main.app).get("/").json()["name"] == "AstroCat API"
