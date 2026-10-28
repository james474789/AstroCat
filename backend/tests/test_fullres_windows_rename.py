"""
D2 A2: full-res cache deletes and the pyramid rename on Windows.

Windows refuses to rename a directory while a file inside it is open (uvicorn serving a
tile), and os.rename onto an existing directory raises FileExistsError there. These tests
simulate both on any OS by monkeypatching os.rename.
"""

import errno
import os

import numpy as np
import pytest

from app.config import settings
from app.services.fullres import cache, pyramid
from _fullres_helpers import FakeRedis, write_manifest_dir

KEY_A = "a" * 16
KEY_B = "b" * 16


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "fullres_cache_path", str(tmp_path / "fullres"))
    monkeypatch.setattr(cache, "RENAME_RETRY_DELAYS_S", (0, 0, 0))
    (tmp_path / "fullres").mkdir()
    return tmp_path / "fullres"


@pytest.fixture
def redis():
    return FakeRedis()


def _locked_rename(calls):
    def rename(src, dst):
        calls.append((src, dst))
        raise PermissionError(errno.EACCES, "The process cannot access the file")
    return rename


def test_rename_refused_falls_back_to_in_place_delete(root, redis, monkeypatch):
    now = 1_000_000.0
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=now - 9000)
    write_manifest_dir(root, KEY_B, nbytes=1000, built_at=now - 8000)
    calls = []
    monkeypatch.setattr(cache.os, "rename", _locked_rename(calls))

    result = cache.evict(cap_bytes=1000, now=now, r=redis)

    assert len(calls) == 1 + len(cache.RENAME_RETRY_DELAYS_S)
    assert not (root / KEY_A).exists() and (root / KEY_B).exists()
    assert result["evicted"] == 1 and result["freed_bytes"] == 1000 and result["bytes"] == 1000
    assert KEY_A not in redis.zsets[cache.LRU_KEY]


def test_undeletable_pyramid_is_not_counted_as_freed(root, redis, monkeypatch):
    now = 1_000_000.0
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=now - 9000)
    monkeypatch.setattr(cache.os, "rename", _locked_rename([]))
    monkeypatch.setattr(cache.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(cache.os, "remove", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))

    assert cache._rmtree_atomic(root / KEY_A) is False
    result = cache.evict(cap_bytes=0, now=now, r=redis)

    assert result["evicted"] == 0 and result["freed_bytes"] == 0 and result["bytes"] == 1000
    assert (root / KEY_A).exists()
    assert KEY_A in redis.zsets[cache.LRU_KEY]


def test_partial_fallback_delete_is_a_cache_miss(root, monkeypatch):
    write_manifest_dir(root, KEY_A)
    monkeypatch.setattr(cache.os, "rename", _locked_rename([]))
    monkeypatch.setattr(cache.shutil, "rmtree", lambda *a, **k: None)   # tiles stay locked

    assert cache._rmtree_atomic(root / KEY_A) is False
    assert cache.read_manifest(KEY_A) is None


def test_missing_dir_counts_as_deleted(root):
    assert cache._rmtree_atomic(root / KEY_A) is True


def test_pyramid_rename_race_on_windows_is_harmless(root, monkeypatch):
    def fake_write_dzi(array, dest_dir, progress=None):
        os.makedirs(dest_dir / "image_files", exist_ok=True)
        (dest_dir / "image.dzi").write_text("<Image/>")

    def rename_onto_existing(src, dst):
        raise FileExistsError(errno.EEXIST, "Cannot create a file when that file already exists")

    monkeypatch.setattr(pyramid, "write_dzi", fake_write_dzi)
    monkeypatch.setattr(pyramid.os, "rename", rename_onto_existing)

    manifest = pyramid.build(np.zeros((10, 20), dtype=np.uint8), KEY_A, {"image_id": 3})

    assert manifest["image_id"] == 3 and manifest["width"] == 20 and manifest["height"] == 10
    assert not [p for p in root.iterdir() if p.name.startswith(".tmp-")]


def test_pyramid_rename_other_errors_still_raise(root, monkeypatch):
    monkeypatch.setattr(pyramid, "write_dzi", lambda a, d, p=None: os.makedirs(d, exist_ok=True))
    monkeypatch.setattr(pyramid.os, "rename",
                        lambda s, d: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")))

    with pytest.raises(OSError):
        pyramid.build(np.zeros((10, 10), dtype=np.uint8), KEY_A, {"image_id": 1})
    assert list(root.iterdir()) == []
