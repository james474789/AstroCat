"""D1: scan/batch targets are checked against the configured image roots, not a /data/ prefix."""

import os

import pytest

from app.utils.path_security import is_within_roots


@pytest.mark.parametrize("target,roots,expected", [
    ("/data/images", ["/data/images"], True),
    ("/data/images/2025/M31", ["/data/images"], True),
    ("/data/mount3/x", ["/data/images", "/data/mount3"], True),
    ("/data/images-backup/x", ["/data/images"], False),   # sibling prefix is not inside
    ("/data/images/../etc", ["/data/images"], False),     # traversal
    ("/tmp/x", ["/data/images"], False),
    ("", ["/data/images"], False),
    (None, ["/data/images"], False),
    ("/data/images/x", [], False),
])
def test_posix_style_roots(target, roots, expected):
    assert is_within_roots(target, roots) is expected


@pytest.mark.skipif(os.name != "nt", reason="drive-letter semantics only on Windows")
def test_windows_drive_roots_are_case_insensitive():
    roots = [r"D:\Astro\Lights"]
    assert is_within_roots(r"d:\astro\lights\M42", roots)
    assert is_within_roots("D:/Astro/Lights/M42", roots)
    assert not is_within_roots(r"E:\Astro\Lights\M42", roots)
    assert not is_within_roots(r"D:\Astro\Lights\..\Darks", roots)
