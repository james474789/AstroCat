"""latest.json builder (desktop/scripts/updater_manifest.py). Run: python -m pytest desktop/runtime -q"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import updater_manifest as m  # noqa: E402

SIGS = {"windows-x86_64": "sigW\n", "darwin-aarch64": "sigM\n"}


def test_manifest_shape_matches_what_tauri_reads():
    manifest = m.build_manifest("26.1028.10", "v20261028.10", "o/r", SIGS, "2026-10-28T10:00:00Z")
    assert manifest["version"] == "26.1028.10"
    assert manifest["pub_date"] == "2026-10-28T10:00:00Z"
    win = manifest["platforms"]["windows-x86_64"]
    assert win == {
        "url": "https://github.com/o/r/releases/download/v20261028.10/AstroCat_26.1028.10_x64-setup.exe",
        "signature": "sigW",
    }
    mac = manifest["platforms"]["darwin-aarch64"]
    assert mac["url"].endswith("/v20261028.10/AstroCat.app.tar.gz")
    assert mac["signature"] == "sigM"


def test_missing_platform_signature_fails():
    with pytest.raises(ValueError, match="darwin-aarch64"):
        m.build_manifest("26.1028.10", "v20261028.10", "o/r", {"windows-x86_64": "x"}, "now")


def test_cli_reads_sig_files_and_writes_latest_json(tmp_path):
    (tmp_path / "AstroCat_26.1028.10_x64-setup.exe.sig").write_text("sigW\n")
    (tmp_path / "AstroCat.app.tar.gz.sig").write_text("sigM\n")
    subprocess.run(
        [sys.executable, str(SCRIPTS / "updater_manifest.py"), "--tag", "v20261028.10",
         "--repo", "o/r", "--dist", str(tmp_path)],
        check=True,
    )
    manifest = json.loads((tmp_path / "latest.json").read_text())
    assert manifest["version"] == "26.1028.10"
    assert manifest["platforms"]["darwin-aarch64"]["signature"] == "sigM"


def test_cli_fails_when_an_artifact_signature_is_missing(tmp_path):
    (tmp_path / "AstroCat_26.1028.10_x64-setup.exe.sig").write_text("sigW\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "updater_manifest.py"), "--tag", "v20261028.10",
         "--repo", "o/r", "--dist", str(tmp_path)],
        capture_output=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "latest.json").exists()
