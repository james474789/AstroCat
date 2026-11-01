"""QA-47: a hand-edited config.json saved with a UTF-8 BOM must still be read."""
import json

from app.api.settings import _read_desktop_config
from app.desktop.paths import DesktopPaths, load_config


def _write_bom(path, data):
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))


def test_settings_reader_accepts_bom(tmp_path):
    f = tmp_path / "config.json"
    _write_bom(f, {"api_keys": {"x": "1"}})
    assert _read_desktop_config(f)["api_keys"] == {"x": "1"}


def test_load_config_accepts_bom(tmp_path):
    paths = DesktopPaths(root=tmp_path)
    _write_bom(paths.config_file, {"image_paths": ["D:/Astro"]})
    assert load_config(paths)["image_paths"] == ["D:/Astro"]
