"""
Exit 0 when one desktop semver (YY.MMDD.NN, from version.py) is newer than another, 1 otherwise.

release.yml uses it so a late-finishing older run never overwrites the beta channel's
latest.json, and promote.yml so a stable release never goes backwards by accident (RC1,
docs/design/20261101-RC1-release-channels.md). Parts compare as integers: 26.1101.13 is newer
than 26.1101.9, which a string comparison gets wrong. An empty or unreadable CURRENT counts as
older than anything, so the first beta or stable release always passes.

    python desktop/scripts/semver_newer.py NEW CURRENT
"""

import sys


def parse(semver: str) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(p) for p in semver.strip().split("."))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None


def is_newer(new: str, current: str) -> bool:
    new_parts = parse(new)
    if new_parts is None:
        raise ValueError(f"{new!r} is not a YY.MMDD.NN version")
    current_parts = parse(current)
    return current_parts is None or new_parts > current_parts


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(0 if is_newer(sys.argv[1], sys.argv[2]) else 1)


if __name__ == "__main__":
    main()
