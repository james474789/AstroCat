"""
Full-resolution pyramid cache (V1, docs/design/20261001-V1-full-resolution-viewer.md §4.3).

Layout: <FULLRES_CACHE_PATH>/<key>/{manifest.json, image.dzi, image_files/...}.
The key is content-addressed, so a file edit or a renderer change makes a new key and nothing
needs invalidating. Redis only holds ephemeral bookkeeping (LRU order, build status, build
locks); the disk is the source of truth and Redis is rebuilt from it when it is flushed.
"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from hashlib import sha1
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Bump when the renderer's output changes, to retire every existing pyramid.
RENDER_VERSION = 1

KEY_RE = re.compile(r"^[0-9a-f]{16}$")
MANIFEST_NAME = "manifest.json"

LRU_KEY = "fullres:lru"
BYTES_KEY = "fullres:bytes"
STATUS_PREFIX = "fullres:status:"
LOCK_PREFIX = "fullres:build:"

LOCK_TTL_S = 15 * 60
STATUS_TTL_S = 60 * 60
MIN_EVICT_AGE_S = 10 * 60
STALE_TMP_AGE_S = 60 * 60

STATES_ACTIVE = ("queued", "loading", "debayering", "stretching", "tiling")


def redis_client():
    import redis
    return redis.from_url(settings.redis_url)


def cache_root() -> Path:
    return Path(settings.fullres_cache_path)


def compute_key(file_path, mtime_ns, size, preset) -> str:
    """16-hex-char cache key. Includes the settings that change the rendered pixels."""
    parts = (file_path, mtime_ns, size, preset, RENDER_VERSION,
             int(bool(settings.fullres_debayer)), settings.fullres_max_megapixels)
    return sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def key_for_file(file_path, preset, st=None) -> str:
    st = st or os.stat(file_path)
    return compute_key(file_path, st.st_mtime_ns, st.st_size, preset)


def key_dir(key) -> Path:
    if not KEY_RE.match(key):
        raise ValueError("invalid cache key")
    return cache_root() / key


def read_manifest(key) -> Optional[dict]:
    try:
        with open(key_dir(key) / MANIFEST_NAME, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------------
# Status and build lock
# ----------------------------------------------------------------------------

def set_status(key, state, pct=None, error=None, r=None):
    r = r or redis_client()
    r.set(STATUS_PREFIX + key, json.dumps({"state": state, "pct": pct, "error": error}), ex=STATUS_TTL_S)


def get_status(key, r=None) -> Optional[dict]:
    r = r or redis_client()
    raw = r.get(STATUS_PREFIX + key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear_status(key, r=None):
    (r or redis_client()).delete(STATUS_PREFIX + key)


def acquire_build_lock(key, r=None) -> bool:
    """True if this caller now owns the build for `key` (SET NX), so exactly one request enqueues it."""
    r = r or redis_client()
    return bool(r.set(LOCK_PREFIX + key, "1", nx=True, ex=LOCK_TTL_S))


def release_build_lock(key, r=None):
    (r or redis_client()).delete(LOCK_PREFIX + key)


# ----------------------------------------------------------------------------
# LRU bookkeeping
# ----------------------------------------------------------------------------

def touch(key, r=None, now=None):
    """Record an access (called on status/manifest fetches, not per tile)."""
    try:
        (r or redis_client()).zadd(LRU_KEY, {key: now if now is not None else time.time()})
    except Exception:
        logger.debug("fullres LRU touch failed", exc_info=True)


def record_built(key, size_bytes, r=None, now=None):
    r = r or redis_client()
    r.zadd(LRU_KEY, {key: now if now is not None else time.time()})
    r.hset(BYTES_KEY, key, int(size_bytes))


def dir_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def scan_disk():
    """[{key, bytes, built_at}] for every complete pyramid on disk (manifest present)."""
    root = cache_root()
    entries = []
    if not root.is_dir():
        return entries
    for child in root.iterdir():
        if not KEY_RE.match(child.name) or not child.is_dir():
            continue
        manifest = read_manifest(child.name)
        if manifest is None:
            continue
        try:
            built_at = float(manifest.get("built_at") or child.stat().st_mtime)
        except (TypeError, ValueError, OSError):
            built_at = 0.0
        entries.append({"key": child.name, "bytes": int(manifest.get("bytes") or 0), "built_at": built_at})
    return entries


def reconcile(r=None):
    """Bring the Redis LRU/byte index in line with the disk, returning entries with `last_access`.

    A key on disk but missing from Redis (Redis was flushed) is added with its build time as a proxy
    for its last access; a key in Redis whose pyramid is gone is dropped.
    """
    entries = scan_disk()
    try:
        r = r or redis_client()
        scores = {(k.decode() if isinstance(k, bytes) else k): s for k, s in r.zrange(LRU_KEY, 0, -1, withscores=True)}
        on_disk = {e["key"] for e in entries}
        for key in set(scores) - on_disk:
            r.zrem(LRU_KEY, key)
            r.hdel(BYTES_KEY, key)
        for e in entries:
            if e["key"] not in scores:
                r.zadd(LRU_KEY, {e["key"]: e["built_at"]})
                scores[e["key"]] = e["built_at"]
            r.hset(BYTES_KEY, e["key"], e["bytes"])
        for e in entries:
            e["last_access"] = scores.get(e["key"], e["built_at"])
    except Exception:
        logger.warning("fullres Redis index unavailable; using build time as last access", exc_info=True)
        for e in entries:
            e["last_access"] = e["built_at"]
    return entries


# ----------------------------------------------------------------------------
# Deletion, eviction, stats
# ----------------------------------------------------------------------------

def _rmtree_atomic(path: Path):
    """Rename out of the way first so a reader never sees a half-deleted pyramid."""
    doomed = path.with_name(f".del-{path.name}-{uuid.uuid4().hex[:8]}")
    try:
        os.rename(path, doomed)
    except OSError:
        return
    shutil.rmtree(doomed, ignore_errors=True)


def delete_key(key, r=None):
    _rmtree_atomic(key_dir(key))
    try:
        r = r or redis_client()
        r.zrem(LRU_KEY, key)
        r.hdel(BYTES_KEY, key)
        r.delete(STATUS_PREFIX + key)
    except Exception:
        logger.debug("fullres Redis cleanup failed", exc_info=True)


def sweep_stale_tmp(now=None):
    """Remove temp dirs left behind by builds that died (older than an hour)."""
    root = cache_root()
    if not root.is_dir():
        return
    now = now if now is not None else time.time()
    for child in root.iterdir():
        if child.name.startswith((".tmp-", ".del-")):
            try:
                if now - child.stat().st_mtime > STALE_TMP_AGE_S:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass


def max_bytes() -> int:
    return int(float(settings.fullres_cache_max_gb) * 1024 ** 3)


def evict(cap_bytes=None, now=None, min_age=MIN_EVICT_AGE_S, r=None) -> dict:
    """Delete least-recently-used pyramids until the cache fits `cap_bytes` (default FULLRES_CACHE_MAX_GB).

    A pyramid built within `min_age` seconds is never evicted, so a build that just finished is
    always there for the request that triggered it.
    """
    cap_bytes = max_bytes() if cap_bytes is None else cap_bytes
    now = now if now is not None else time.time()
    sweep_stale_tmp(now)
    entries = reconcile(r)
    total = sum(e["bytes"] for e in entries)
    evicted, freed = 0, 0
    for e in sorted(entries, key=lambda e: e["last_access"]):
        if total <= cap_bytes:
            break
        if now - e["built_at"] < min_age:
            continue
        delete_key(e["key"], r)
        total -= e["bytes"]
        freed += e["bytes"]
        evicted += 1
    return {"evicted": evicted, "freed_bytes": freed, "count": len(entries) - evicted, "bytes": total}


def stats(r=None) -> dict:
    entries = reconcile(r)
    return {
        "count": len(entries),
        "bytes": sum(e["bytes"] for e in entries),
        "max_bytes": max_bytes(),
    }


def clear(r=None) -> dict:
    entries = scan_disk()
    for e in entries:
        delete_key(e["key"], r)
    return {"deleted": len(entries), "freed_bytes": sum(e["bytes"] for e in entries)}
