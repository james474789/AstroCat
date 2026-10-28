"""
Map the repo VERSION (YYYYMMDD.NN) to the semver Tauri and the installers need.

    20261027.10  ->  26.1027.10

Monotonic, like VERSION itself (year, then month+day, then the build of the day), and each part
stays within the 16-bit limits Windows version resources impose. Writes
desktop/src-tauri/tauri.version.json, passed to `tauri build --config`.

    python desktop/scripts/version.py [--check-tag v20261027.10]
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "desktop" / "src-tauri" / "tauri.version.json"
VERSION_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.(\d{1,3})$")


def to_semver(version: str) -> str:
    m = VERSION_RE.match(version.strip())
    if not m:
        raise ValueError(f"VERSION {version!r} is not YYYYMMDD.NN")
    year, month, day, build = (int(g) for g in m.groups())
    if not (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
        raise ValueError(f"VERSION {version!r} has an impossible date")
    return f"{year - 2000}.{month * 100 + day}.{build}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-tag", help="fail unless this git tag is v<VERSION>")
    args = parser.parse_args()

    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    if args.check_tag and args.check_tag != f"v{version}":
        sys.exit(f"tag {args.check_tag} does not match VERSION {version}")
    semver = to_semver(version)
    OUT.write_text(json.dumps({"version": semver}) + "\n", encoding="utf-8")
    print(semver)


if __name__ == "__main__":
    main()
