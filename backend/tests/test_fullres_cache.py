"""
V1 full-resolution viewer: cache key, eviction and the pyramid writer.
docs/design/20261001-V1-full-resolution-viewer.md §4.2-4.3 and §8.
"""

import json
import os

import numpy as np
import pytest

from app.config import settings
from app.services.fullres import cache, pyramid
from _fullres_helpers import FakeRedis, write_manifest_dir

KEY_A = "a" * 16
KEY_B = "b" * 16
KEY_C = "c" * 16


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "fullres_cache_path", str(tmp_path / "fullres"))
    (tmp_path / "fullres").mkdir()
    return tmp_path / "fullres"


@pytest.fixture
def redis():
    return FakeRedis()


# --- key -------------------------------------------------------------------

def test_key_is_stable_and_well_formed():
    key = cache.compute_key("/data/a.fits", 123, 456, "auto")
    assert key == cache.compute_key("/data/a.fits", 123, 456, "auto")
    assert cache.KEY_RE.match(key)


@pytest.mark.parametrize("changed", [
    ("/data/b.fits", 123, 456, "auto"),     # path
    ("/data/a.fits", 124, 456, "auto"),     # mtime
    ("/data/a.fits", 123, 457, "auto"),     # size
    ("/data/a.fits", 123, 456, "strong"),   # preset
])
def test_key_changes_with_each_input(changed):
    assert cache.compute_key(*changed) != cache.compute_key("/data/a.fits", 123, 456, "auto")


def test_key_changes_with_render_version(monkeypatch):
    before = cache.compute_key("/data/a.fits", 123, 456, "auto")
    monkeypatch.setattr(cache, "RENDER_VERSION", cache.RENDER_VERSION + 1)
    assert cache.compute_key("/data/a.fits", 123, 456, "auto") != before


def test_key_changes_with_pixel_affecting_settings(monkeypatch):
    before = cache.compute_key("/data/a.fits", 123, 456, "auto")
    monkeypatch.setattr(settings, "fullres_debayer", not settings.fullres_debayer)
    assert cache.compute_key("/data/a.fits", 123, 456, "auto") != before


def test_key_dir_rejects_malformed_keys(root):
    for bad in ("../etc", "A" * 16, "g" * 16, "a" * 15, "a" * 17, ""):
        with pytest.raises(ValueError):
            cache.key_dir(bad)


# --- status and lock ---------------------------------------------------------

def test_build_lock_is_exclusive(redis):
    assert cache.acquire_build_lock(KEY_A, redis) is True
    assert cache.acquire_build_lock(KEY_A, redis) is False
    cache.release_build_lock(KEY_A, redis)
    assert cache.acquire_build_lock(KEY_A, redis) is True


def test_status_round_trip(redis):
    assert cache.get_status(KEY_A, redis) is None
    cache.set_status(KEY_A, "tiling", 40, r=redis)
    assert cache.get_status(KEY_A, redis) == {"state": "tiling", "pct": 40, "error": None}
    cache.clear_status(KEY_A, redis)
    assert cache.get_status(KEY_A, redis) is None


# --- eviction ----------------------------------------------------------------

def test_evicts_oldest_first_down_to_the_cap(root, redis):
    now = 1_000_000.0
    for key, last_access in ((KEY_A, now - 5000), (KEY_B, now - 3000), (KEY_C, now - 1000)):
        write_manifest_dir(root, key, nbytes=1000, built_at=now - 6000)
        cache.touch(key, redis, now=last_access)
    result = cache.evict(cap_bytes=1500, now=now, r=redis)
    assert result["evicted"] == 2 and result["bytes"] == 1000 and result["count"] == 1
    assert not (root / KEY_A).exists() and not (root / KEY_B).exists()
    assert (root / KEY_C).exists()
    assert KEY_A not in redis.zsets[cache.LRU_KEY]


def test_recent_access_protects_an_old_build(root, redis):
    now = 1_000_000.0
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=now - 9000)
    write_manifest_dir(root, KEY_B, nbytes=1000, built_at=now - 8000)
    cache.touch(KEY_A, redis, now=now - 10)       # built long ago but viewed just now
    cache.touch(KEY_B, redis, now=now - 7000)
    cache.evict(cap_bytes=1000, now=now, r=redis)
    assert (root / KEY_A).exists() and not (root / KEY_B).exists()


def test_never_evicts_a_build_younger_than_ten_minutes(root, redis):
    now = 1_000_000.0
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=now - 100)      # 100 s old
    write_manifest_dir(root, KEY_B, nbytes=1000, built_at=now - 5000)
    cache.touch(KEY_A, redis, now=now - 100)
    cache.touch(KEY_B, redis, now=now - 100)
    result = cache.evict(cap_bytes=0, now=now, r=redis)
    assert result["evicted"] == 1
    assert (root / KEY_A).exists() and not (root / KEY_B).exists()


def test_under_the_cap_nothing_is_evicted(root, redis):
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=0)
    assert cache.evict(cap_bytes=10_000, now=1e9, r=redis)["evicted"] == 0


def test_flushed_redis_is_rebuilt_from_disk_manifests(root, redis):
    now = 1_000_000.0
    write_manifest_dir(root, KEY_A, nbytes=1000, built_at=now - 9000)    # older
    write_manifest_dir(root, KEY_B, nbytes=1000, built_at=now - 8000)
    assert not redis.zsets                                                # Redis knows nothing
    cache.evict(cap_bytes=1000, now=now, r=redis)
    assert not (root / KEY_A).exists() and (root / KEY_B).exists()       # build time stood in for last access
    assert set(redis.zsets[cache.LRU_KEY]) == {KEY_B}


def test_pyramids_missing_from_disk_are_dropped_from_redis(root, redis):
    cache.record_built(KEY_A, 1000, redis)
    cache.reconcile(redis)
    assert KEY_A not in redis.zsets.get(cache.LRU_KEY, {})


def test_stats_and_clear(root, redis):
    write_manifest_dir(root, KEY_A, nbytes=300, built_at=1)
    write_manifest_dir(root, KEY_B, nbytes=700, built_at=1)
    s = cache.stats(redis)
    assert (s["count"], s["bytes"]) == (2, 1000) and s["max_bytes"] == cache.max_bytes()
    assert cache.clear(redis) == {"deleted": 2, "freed_bytes": 1000}
    assert cache.stats(redis)["count"] == 0


def test_stale_temp_dirs_are_swept(root, redis):
    stale = root / ".tmp-aaaaaaaaaaaaaaaa-deadbeef"
    fresh = root / ".tmp-bbbbbbbbbbbbbbbb-deadbeef"
    stale.mkdir()
    fresh.mkdir()
    os.utime(stale, (1, 1))
    cache.evict(cap_bytes=10**9, r=redis)
    assert not stale.exists() and fresh.exists()


# --- pyramid writer ----------------------------------------------------------

@pytest.mark.skipif(pyramid.pyvips is None, reason="libvips not installed")
class TestPyramid:
    def test_builds_a_dzi_and_renames_it_into_place(self, root):
        array = np.random.default_rng(0).integers(0, 256, (700, 900, 3), dtype=np.uint8)
        ticks = []
        manifest = pyramid.build(array, KEY_A, {"image_id": 7, "preset": "auto", "scale": 1}, progress=ticks.append)
        final = root / KEY_A
        assert (final / "image.dzi").is_file() and (final / "image_files").is_dir()
        assert (final / "image_files" / "0" / "0_0.jpg").is_file()
        # 900 px wide with 510 px tiles: two columns at the top level
        top = max(int(p.name) for p in (final / "image_files").iterdir() if p.name.isdigit())
        assert (final / "image_files" / str(top) / "1_0.jpg").is_file()
        assert manifest["width"] == 900 and manifest["height"] == 700 and manifest["type"] == "dzi"
        assert json.loads((final / "manifest.json").read_text())["image_id"] == 7
        assert manifest["bytes"] > 0 and ticks
        assert not [p for p in root.iterdir() if p.name.startswith(".tmp-")]

    def test_mono_pyramid(self, root):
        array = np.random.default_rng(1).integers(0, 256, (300, 400), dtype=np.uint8)
        pyramid.build(array, KEY_B, {"image_id": 1})
        assert (root / KEY_B / "image.dzi").is_file()

    def test_failed_build_leaves_nothing_behind(self, root, monkeypatch):
        def boom(*a, **k):
            os.makedirs(a[1], exist_ok=True)
            raise RuntimeError("tiling failed")
        monkeypatch.setattr(pyramid, "write_dzi", boom)
        with pytest.raises(RuntimeError):
            pyramid.build(np.zeros((10, 10), dtype=np.uint8), KEY_C, {"image_id": 1})
        assert list(root.iterdir()) == []
