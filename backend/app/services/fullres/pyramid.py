"""
Deep Zoom (DZI) pyramid writer (V1, docs/design/20261001-V1-full-resolution-viewer.md §4.2).

The pyramid is written to a temp dir inside the cache root and renamed into place, so a
half-written pyramid is never served.
"""

import json
import os
import shutil
import time
import uuid
from typing import Callable, Optional

import numpy as np

from app.services.fullres import cache

try:
    import pyvips
except (ImportError, OSError):  # OSError: libvips shared library missing
    pyvips = None

TILE_SIZE = 510
OVERLAP = 1
JPEG_SUFFIX = ".jpg[Q=88,strip]"


def write_dzi(array: np.ndarray, dest_dir, progress: Optional[Callable[[float], None]] = None):
    """Write `array` (uint8, HxW or HxWx3) as <dest_dir>/image.dzi + image_files/."""
    if pyvips is None:
        raise RuntimeError("pyvips/libvips is not available")
    array = np.ascontiguousarray(array)
    height, width = array.shape[:2]
    bands = 1 if array.ndim == 2 else array.shape[2]
    image = pyvips.Image.new_from_memory(array.data, width, height, bands, "uchar")
    if progress:
        image.set_progress(True)
        last = [-1]

        def on_eval(_image, prog):
            pct = int(prog.percent)
            if pct != last[0]:
                last[0] = pct
                progress(pct)

        image.signal_connect("eval", on_eval)
    os.makedirs(dest_dir, exist_ok=True)
    image.dzsave(os.path.join(str(dest_dir), "image"), layout="dz", tile_size=TILE_SIZE,
                 overlap=OVERLAP, suffix=JPEG_SUFFIX)


def build(array: np.ndarray, key: str, manifest: dict,
          progress: Optional[Callable[[float], None]] = None) -> dict:
    """Write the pyramid for `key` into the cache and return the final manifest.

    `manifest` carries the render metadata (image_id, preset, scale, ...); width, height, tile
    geometry, byte size and build time are filled in here.
    """
    root = cache.cache_root()
    root.mkdir(parents=True, exist_ok=True)
    final = cache.key_dir(key)
    tmp = root / f".tmp-{key}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    try:
        write_dzi(array, tmp, progress)
        height, width = array.shape[:2]
        manifest = {
            **manifest,
            "key": key,
            "type": "dzi",
            "width": int(width),
            "height": int(height),
            "tile_size": TILE_SIZE,
            "overlap": OVERLAP,
            "tiling_ms": int((time.monotonic() - started) * 1000),
            "built_at": time.time(),
        }
        manifest["bytes"] = cache.dir_size(tmp)
        with open(tmp / cache.MANIFEST_NAME, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        if final.exists():
            # lost a race with a concurrent build of the same key; theirs is equivalent
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest
