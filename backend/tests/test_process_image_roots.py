from app.utils.path_security import is_under_any_root


def test_under_posix_root():
    assert is_under_any_root("/lib/A/x.fits", ["/lib/A"])
    assert is_under_any_root("/lib/A", ["/lib/A/"])


def test_sibling_prefix_is_not_under_root():
    assert not is_under_any_root("/lib/AB/x.fits", ["/lib/A"])


def test_windows_root_and_wildcard_chars():
    assert is_under_any_root("D:\Astro\m31.fit", ["D:\Astro"])
    assert is_under_any_root("/lib/50%_off/x.fit", ["/lib/50%_off"])
    assert not is_under_any_root("/lib/50x/x.fit", ["/lib/50%_off"])
