"""Shared helpers for the V1 full-resolution viewer tests: a fake Redis and fixture writers."""

import json
import time
from types import SimpleNamespace

import numpy as np

from app.models.image import ImageSubtype


class FakeRedis:
    """The handful of Redis commands the full-res cache uses, in memory."""

    def __init__(self):
        self.kv, self.zsets, self.hashes = {}, {}, {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)

    def zadd(self, name, mapping):
        self.zsets.setdefault(name, {}).update(mapping)

    def zrange(self, name, start, stop, withscores=False):
        items = sorted(self.zsets.get(name, {}).items(), key=lambda kv: kv[1])
        items = [(k.encode(), s) for k, s in items]
        return items if withscores else [k for k, _ in items]

    def zrem(self, name, key):
        self.zsets.get(name, {}).pop(key, None)

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value

    def hdel(self, name, key):
        self.hashes.get(name, {}).pop(key, None)


def make_image(path, image_id=1, subtype=ImageSubtype.SUB_FRAME, raw_header=None):
    return SimpleNamespace(id=image_id, file_path=str(path), subtype=subtype, raw_header=raw_header or {},
                           file_name=getattr(path, "name", str(path)), width_pixels=None, height_pixels=None)


def scene(height=240, width=320, seed=0):
    """Asymmetric greyscale test scene: a gradient, a bright blob top-left and a dim one bottom-right."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    img = 800 + 2.0 * xx + 1.0 * yy + rng.normal(0, 4, (height, width))
    img[10:60, 20:90] += 30000
    img[height - 40:height - 10, width - 60:width - 30] += 6000
    return img.astype(np.uint16)


def write_fits(path, data, **header):
    from astropy.io import fits
    hdu = fits.PrimaryHDU(data)
    for key, value in header.items():
        hdu.header[key] = value
    hdu.writeto(str(path), overwrite=True)
    return path


def write_manifest_dir(root, key, image_id=1, nbytes=1000, built_at=None):
    """A fake finished pyramid directory (manifest only) in the cache root."""
    d = root / key
    (d / "image_files").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"image_id": image_id, "bytes": nbytes, "built_at": built_at if built_at is not None else time.time()}))
    return d
