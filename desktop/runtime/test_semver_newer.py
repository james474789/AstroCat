"""Release-channel version comparison (desktop/scripts/semver_newer.py). Run: python -m pytest desktop/runtime -q"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import semver_newer as s  # noqa: E402


@pytest.mark.parametrize(
    "new, current",
    [
        ("26.1101.13", "26.1101.9"),  # a string comparison gets this one wrong
        ("26.1102.1", "26.1101.13"),
        ("27.101.1", "26.1231.40"),
        ("26.1101.1", ""),  # no manifest yet
        ("26.1101.1", "null"),  # jq's output for a manifest without a version
    ],
)
def test_newer(new, current):
    assert s.is_newer(new, current)


@pytest.mark.parametrize("new, current", [("26.1101.9", "26.1101.13"), ("26.1101.13", "26.1101.13")])
def test_not_newer(new, current):
    assert not s.is_newer(new, current)


def test_bad_new_version_fails():
    with pytest.raises(ValueError):
        s.is_newer("v20261101.13", "26.1101.1")


def test_cli_exit_codes():
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "semver_newer.py"), *a]).returncode
    assert run("26.1101.13", "26.1101.9") == 0
    assert run("26.1101.9", "26.1101.13") == 1
