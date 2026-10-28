"""Pure helpers of build_runtime.py. Run: python -m pytest desktop/runtime -q"""

from pathlib import Path

import pytest

import build_runtime as b

REQUIREMENTS = Path(__file__).resolve().parents[2] / "backend" / "requirements.txt"

# Every package build_runtime.py pip-installs into the desktop runtime, checked by hand.
# Pure-Python packages (any version installs anywhere):
PURE_PYTHON = {
    "fastapi", "uvicorn", "python-multipart", "alembic", "geoalchemy2", "celery", "redis",
    "exifread", "xisf", "tifffile", "tzdata", "concurrent-log-handler", "python-dotenv",
    "pydantic", "pydantic-settings", "pyjwt", "passlib", "slowapi", "httpx", "pytest",
    "pytest-asyncio",
}
# Packages with native code, at the pinned version that was checked to have cp312 wheels for
# both win_amd64 and macosx_arm64 (pip must never fall back to compiling on a user's build).
NATIVE_WHEELS = {
    "sqlalchemy": "2.0.25", "asyncpg": "0.29.0", "psycopg2-binary": "2.9.9", "astropy": "6.0.0",
    "pillow": "11.0.0", "rawpy": "0.19.1", "numpy": "1.26.4", "sep": "1.4.1", "scipy": "1.13.1",
    "opencv-python-headless": "4.10.0.84", "bcrypt": "4.0.1",
}


def _pinned(text: str) -> dict:
    """{name: version or None} for each requirement line."""
    out = {}
    for line in text.splitlines():
        spec = line.split("#")[0].strip()
        if not spec:
            continue
        name, _, version = spec.partition("==")
        out[name.split("[")[0].strip().lower()] = version.strip() or None
    return out


def test_requirements_drop_only_conda_provided_packages():
    text = "fastapi==0.115.0\npyvips==2.2.3   # deep zoom\nPyVips>=2\nnumpy==1.26.4\n# comment\n"
    assert b.filtered_requirements(text) == "fastapi==0.115.0\nnumpy==1.26.4\n# comment\n"


def test_postgis_extension_files_keep_install_and_same_minor_upgrades(tmp_path):
    ext = tmp_path
    (ext / "postgis.control").write_text("comment = 'PostGIS'\ndefault_version = '3.6.2'\n")
    for name in ["postgis--3.6.2.sql", "postgis--ANY--3.6.2.sql", "postgis--3.6.0--ANY.sql",
                 "postgis--3.6.1--ANY.sql", "postgis--3.5.4--ANY.sql", "postgis--2.0.0--ANY.sql",
                 "postgis_raster--3.6.2.sql", "postgis--unpackaged--3.6.2.sql"]:
        (ext / name).write_text("")
    kept = sorted(p.name for p in b.postgis_extension_files(ext))
    assert kept == ["postgis--3.6.0--ANY.sql", "postgis--3.6.1--ANY.sql", "postgis--3.6.2.sql",
                    "postgis--ANY--3.6.2.sql", "postgis.control"]


def test_postgis_extension_files_fail_loudly_when_install_script_missing(tmp_path):
    (tmp_path / "postgis.control").write_text("default_version = '3.6.2'\n")
    with pytest.raises(SystemExit):
        b.postgis_extension_files(tmp_path)


def test_every_runtime_requirement_installs_from_a_wheel_or_conda():
    """
    A new requirement with C extensions (or a version bump of one) must be checked against the
    desktop runtime build: it needs cp312 wheels for win_amd64 and macosx_arm64, or a conda-forge
    package added to environment.yml and b.CONDA_PROVIDED. Check PyPI's "Download files" page,
    then add it above. CI's desktop-smoke job builds the real runtime and will catch a miss.
    """
    pins = _pinned(b.filtered_requirements(REQUIREMENTS.read_text(encoding="utf-8")))
    assert not set(pins) & b.CONDA_PROVIDED
    unchecked = sorted(n for n in pins if n not in PURE_PYTHON and n not in NATIVE_WHEELS)
    assert not unchecked, f"new requirements not checked for the desktop runtime: {unchecked} (see docstring)"
    moved = sorted(f"{n} {NATIVE_WHEELS[n]} -> {pins[n]}" for n in pins
                   if n in NATIVE_WHEELS and pins[n] != NATIVE_WHEELS[n])
    assert not moved, f"native requirements changed version; re-check their wheels: {moved} (see docstring)"
    stale = sorted((PURE_PYTHON | set(NATIVE_WHEELS)) - set(pins))
    assert not stale, f"no longer in requirements.txt, drop from this test: {stale}"
