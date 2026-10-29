import json

from app.config import settings
from app.services.fullres import cache


def test_adopt_repoints_manifest_to_new_image_id(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "fullres_cache_path", str(tmp_path))
    key = "0123456789abcdef"
    (tmp_path / key).mkdir()
    (tmp_path / key / cache.MANIFEST_NAME).write_text(json.dumps({"image_id": 438, "type": "dzi"}))

    cache.adopt(key, 961)

    assert cache.read_manifest(key)["image_id"] == 961
    assert cache.read_manifest(key)["type"] == "dzi"


def test_adopt_ignores_missing_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "fullres_cache_path", str(tmp_path))
    cache.adopt("0123456789abcdef", 1)
