"""
Build latest.json, the manifest the desktop app's updater reads (D3 5.2).

release.yml's publish job runs this over the downloaded desktop artifacts, then attaches
latest.json to the GitHub Release; the app fetches
https://github.com/<repo>/releases/latest/download/latest.json.

    python desktop/scripts/updater_manifest.py --tag v20261028.10 --repo owner/name --dist dist
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from version import to_semver  # noqa: E402

# Tauri platform key -> update artifact name for a release. The Windows updater installs the NSIS
# setup exe itself; the macOS updater installs the .app tarball.
PLATFORMS = {
    "windows-x86_64": "AstroCat_{semver}_x64-setup.exe",
    "darwin-aarch64": "AstroCat.app.tar.gz",
}


def build_manifest(semver: str, tag: str, repo: str, signatures: dict, pub_date: str) -> dict:
    """`signatures` maps platform key -> the contents of that artifact's .sig file."""
    missing = sorted(set(PLATFORMS) - set(signatures))
    if missing:
        raise ValueError(f"no signature for {', '.join(missing)}")
    base = f"https://github.com/{repo}/releases/download/{tag}"
    return {
        "version": semver,
        "notes": "See the release notes.",
        "pub_date": pub_date,
        "platforms": {
            platform: {
                "url": f"{base}/{name.format(semver=semver)}",
                "signature": signatures[platform].strip(),
            }
            for platform, name in PLATFORMS.items()
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="the release tag, v<VERSION>")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--dist", required=True, help="folder holding the update artifacts and .sig files")
    parser.add_argument("--out", help="default: <dist>/latest.json")
    args = parser.parse_args()

    semver = to_semver(args.tag.removeprefix("v"))
    dist = Path(args.dist)
    signatures = {}
    for platform, name in PLATFORMS.items():
        sig = dist / f"{name.format(semver=semver)}.sig"
        if not sig.is_file():
            sys.exit(f"missing {sig}")
        signatures[platform] = sig.read_text(encoding="utf-8")
    pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build_manifest(semver, args.tag, args.repo, signatures, pub_date)
    out = Path(args.out) if args.out else dist / "latest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} for {semver}")


if __name__ == "__main__":
    main()
