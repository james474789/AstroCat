"""
V1 full-resolution viewer: API flow, dedupe, security and the native shortcut.
docs/design/20261001-V1-full-resolution-viewer.md §4.5 and §8.

No database: a fake session serves the Image rows, Redis is in memory and the Celery task is
run inline.
"""

import sys
import types

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.api import fullres as api  # noqa: E402
from app.api.dependencies import require_admin  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.image import ImageSubtype  # noqa: E402
from app.services.fullres import cache, pyramid  # noqa: E402
from app.tasks import fullres as tasks  # noqa: E402
from _fullres_helpers import FakeRedis, make_image, scene, write_fits  # noqa: E402

pytestmark = pytest.mark.skipif(pyramid.pyvips is None, reason="libvips not installed")


class FakeSession:
    def __init__(self, images):
        self.images = {i.id: i for i in images}

    async def get(self, _model, image_id):
        return self.images.get(image_id)


class Env:
    """A TestClient over the full-res router, with the build task run inline (or just counted)."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.redis = FakeRedis()
        self.images = []
        self.enqueued = []
        self.run_inline = True
        monkeypatch.setattr(settings, "fullres_cache_path", str(tmp_path / "cache"))
        monkeypatch.setattr(settings, "image_paths", str(tmp_path / "lib"))
        (tmp_path / "cache").mkdir()
        (tmp_path / "lib").mkdir()
        monkeypatch.setattr(cache, "redis_client", lambda: self.redis)
        monkeypatch.setattr(api, "_OWNERS", {})
        monkeypatch.setattr(tasks.build_pyramid, "delay", self._delay, raising=False)

        app = FastAPI()
        app.include_router(api.router, prefix="/api/images")
        app.dependency_overrides[get_db] = lambda: FakeSession(self.images)
        app.dependency_overrides[require_admin] = lambda: object()
        self.client = TestClient(app)

    def _delay(self, image_id, preset):
        self.enqueued.append((image_id, preset))
        if not self.run_inline:
            return
        image = next(i for i in self.images if i.id == image_id)
        key = cache.key_for_file(image.file_path, preset)
        try:
            tasks.build_image(image, preset, r=self.redis)
        except Exception as e:  # what the Celery task body does
            cache.set_status(key, "error", error=str(e), r=self.redis)
        finally:
            cache.release_build_lock(key, self.redis)

    def add_fits(self, image_id=1, data=None, name="a.fits", subtype=ImageSubtype.SUB_FRAME, **header):
        path = write_fits(self.tmp_path / "lib" / name, scene() if data is None else data, **header)
        image = make_image(path, image_id, subtype)
        self.images.append(image)
        return image

    def add_png(self, image_id=2, size=(64, 48), name="p.png", subtype=ImageSubtype.PLANETARY):
        path = self.tmp_path / "lib" / name
        PILImage.fromarray(np.random.default_rng(0).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)).save(path)
        image = make_image(path, image_id, subtype)
        self.images.append(image)
        return image

    def get(self, url, **kw):
        return self.client.get(f"/api/images{url}", **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_202_then_ready_then_tiles(env):
    env.add_fits(1)
    first = env.get("/1/fullres")
    assert first.status_code == 202 and first.json()["state"] == "queued"
    assert env.enqueued == [(1, "auto")]                       # sub-frame default

    ready = env.get("/1/fullres")
    body = ready.json()
    assert ready.status_code == 200 and body["state"] == "ready" and body["preset"] == "auto"
    assert body["presets"] == ["linear", "auto", "strong"]     # mono: no 'unlinked'
    manifest, key = body["manifest"], body["key"]
    assert manifest["type"] == "dzi" and (manifest["width"], manifest["height"]) == (320, 240)
    assert body["dzi_url"] == f"/api/images/1/fullres/{key}/image.dzi"
    assert cache.get_status(key, env.redis)["state"] == "ready"

    dzi = env.get(f"/1/fullres/{key}/image.dzi")
    assert dzi.status_code == 200 and b"<Image" in dzi.content
    assert "immutable" in dzi.headers["cache-control"]
    top = max(int(p.name) for p in (env.tmp_path / "cache" / key / "image_files").iterdir() if p.name.isdigit())
    tile = env.get(f"/1/fullres/{key}/image_files/{top}/0_0.jpg")
    assert tile.status_code == 200 and tile.headers["content-type"] == "image/jpeg"
    assert "immutable" in tile.headers["cache-control"] and tile.content[:2] == b"\xff\xd8"


def test_second_request_does_not_double_enqueue(env):
    env.run_inline = False                                      # the build stays queued
    env.add_fits(1)
    assert env.get("/1/fullres").status_code == 202
    again = env.get("/1/fullres")
    assert again.status_code == 202 and again.json()["state"] == "queued"
    assert len(env.enqueued) == 1


def test_lock_alone_prevents_a_second_enqueue(env):
    env.run_inline = False
    image = env.add_fits(1)
    key = cache.key_for_file(image.file_path, "auto")
    assert cache.acquire_build_lock(key, env.redis)             # another request already owns the build
    assert env.get("/1/fullres").status_code == 202
    assert env.enqueued == []


def test_preset_selects_a_separate_cache_entry(env):
    env.add_fits(1)
    env.get("/1/fullres?preset=linear")
    linear = env.get("/1/fullres?preset=linear").json()
    env.get("/1/fullres?preset=strong")
    strong = env.get("/1/fullres?preset=strong").json()
    assert linear["key"] != strong["key"]
    assert (linear["preset"], strong["preset"]) == ("linear", "strong")


def test_non_subframe_defaults_to_linear(env):
    env.add_fits(1, subtype=ImageSubtype.INTEGRATION_MASTER)
    assert env.get("/1/fullres").json()["preset"] == "linear"


def test_unlinked_on_mono_is_409(env):
    env.add_fits(1)
    assert env.get("/1/fullres?preset=unlinked").status_code == 409
    assert env.enqueued == []


def test_unknown_preset_is_rejected(env):
    env.add_fits(1)
    assert env.get("/1/fullres?preset=sepia").status_code == 422


def test_unlinked_is_offered_for_debayered_data(env):
    env.add_fits(1, BAYERPAT="RGGB")
    body = env.get("/1/fullres").json()
    assert "unlinked" in body["presets"]
    assert env.get("/1/fullres?preset=unlinked").status_code == 202


def test_unknown_image_and_missing_source_are_404(env):
    assert env.get("/99/fullres").status_code == 404
    image = env.add_fits(1)
    import os
    os.remove(image.file_path)
    assert env.get("/1/fullres").status_code == 404


def test_source_outside_the_library_is_refused(env, tmp_path):
    outside = write_fits(tmp_path / "outside.fits", scene())
    env.images.append(make_image(outside, 3))
    assert env.get("/3/fullres").status_code == 403


def test_failed_build_is_reported_and_retry_requeues(env, monkeypatch):
    from app.services.fullres import render

    def boom(*a, **k):
        raise RuntimeError("out of memory")
    monkeypatch.setattr(render, "render_full", boom)
    env.add_fits(1)
    assert env.get("/1/fullres").status_code == 202             # the inline build fails
    failed = env.get("/1/fullres")
    assert failed.status_code == 202 and failed.json()["state"] == "error"
    assert "out of memory" in failed.json()["error"]
    assert len(env.enqueued) == 1                                # an error is not silently retried
    env.get("/1/fullres?retry=true")
    assert len(env.enqueued) == 2


def test_unreadable_source_is_422(env):
    env.add_fits(1)
    (env.tmp_path / "lib" / "a.fits").write_bytes(b"not a fits file")
    assert env.get("/1/fullres").status_code == 422
    assert env.enqueued == []


def test_rebuild_drops_and_requeues(env):
    env.add_fits(1)
    env.get("/1/fullres")
    key = env.get("/1/fullres").json()["key"]
    stamp = (env.tmp_path / "cache" / key / "manifest.json").stat().st_mtime_ns
    resp = env.client.post("/api/images/1/fullres/rebuild")
    assert resp.status_code == 202 and len(env.enqueued) == 2
    assert (env.tmp_path / "cache" / key / "manifest.json").stat().st_mtime_ns >= stamp


# --- security ---------------------------------------------------------------

@pytest.fixture
def built(env):
    env.add_fits(1)
    env.get("/1/fullres")
    return env, env.get("/1/fullres").json()["key"]


def test_tile_key_must_be_16_lowercase_hex(built):
    env, _key = built
    for bad in ("zzzzzzzzzzzzzzzz", "ABCDEF0123456789", "abc", "0" * 17, "..%2f..%2fetc"):
        resp = env.get(f"/1/fullres/{bad}/image_files/0/0_0.jpg")
        assert resp.status_code in (400, 404), bad


def test_tile_path_traversal_is_rejected(built):
    env, key = built
    for tile in ("..%2f..%2fmanifest.json", "0_0.jpg%00", "0_0.png", "a_b.jpg", "0_0.jpg/../x", ".._0.jpg"):
        resp = env.get(f"/1/fullres/{key}/image_files/0/{tile}")
        assert resp.status_code in (400, 404, 422), tile
    assert env.get(f"/1/fullres/{key}/image_files/../manifest.json").status_code in (400, 404, 422)
    assert env.get(f"/1/fullres/{key}/image_files/abc/0_0.jpg").status_code == 422     # level must be an int
    assert env.get(f"/1/fullres/{key}/image_files/-1/0_0.jpg").status_code == 422


def test_key_of_another_image_is_not_served(env):
    env.add_fits(1, name="a.fits")
    env.add_fits(2, name="b.fits")
    env.get("/1/fullres")
    key = env.get("/1/fullres").json()["key"]
    assert env.get(f"/1/fullres/{key}/image.dzi").status_code == 200
    assert env.get(f"/2/fullres/{key}/image.dzi").status_code == 404
    assert env.get(f"/2/fullres/{key}/image_files/0/0_0.jpg").status_code == 404


def test_unknown_key_and_missing_tile_are_404(built):
    env, key = built
    assert env.get(f"/1/fullres/{'0' * 16}/image.dzi").status_code == 404
    assert env.get(f"/1/fullres/{key}/image_files/0/9_9.jpg").status_code == 404


def test_symlinked_tile_is_not_followed(built):
    env, key = built
    outside = env.tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    link = env.tmp_path / "cache" / key / "image_files" / "0" / "5_5.jpg"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert env.get(f"/1/fullres/{key}/image_files/0/5_5.jpg").status_code == 404


# --- native shortcut --------------------------------------------------------

def test_small_png_uses_the_native_shortcut(env):
    image = env.add_png(2)
    resp = env.get("/2/fullres")
    body = resp.json()
    assert resp.status_code == 200 and body["manifest"]["type"] == "image" and body["dzi_url"] is None
    assert body["source_url"] == "/api/images/2/fullres/source"
    assert env.enqueued == []
    src = env.get("/2/fullres/source")
    assert src.status_code == 200 and src.headers["content-type"] == "image/png"
    assert "attachment" not in src.headers.get("content-disposition", "")
    assert src.content == open(image.file_path, "rb").read()


def test_native_shortcut_only_for_linear_and_small_images(env):
    env.add_png(2)
    env.add_png(3, size=(5000, 20), name="wide.png")
    assert env.get("/3/fullres").status_code == 202                               # > 4096 px: needs a pyramid
    assert env.get("/2/fullres?preset=auto").status_code == 202                   # stretched: needs a render


def test_source_endpoint_refuses_other_formats(env):
    env.add_fits(1)
    assert env.get("/1/fullres/source").status_code == 404
