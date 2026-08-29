"""
AstroCat Version Utility
Reads the application version from the root VERSION file or environment.
"""

import os
from pathlib import Path

DEFAULT_VERSION = "0.1.0"


def get_version() -> str:
    # 1. Check environment variable override
    if os.environ.get("APP_VERSION"):
        return os.environ["APP_VERSION"].strip()

    # 2. Look for VERSION file in root or parent directories
    current_dir = Path(__file__).resolve().parent
    potential_paths = [
        current_dir.parent.parent / "VERSION",  # When running in repo: <repo>/VERSION
        current_dir.parent / "VERSION",         # <backend>/VERSION
        Path("/app/VERSION"),                   # Inside Docker container if copied
        Path("VERSION"),
    ]

    for p in potential_paths:
        if p.is_file():
            try:
                version = p.read_text(encoding="utf-8").strip()
                if version:
                    return version
            except Exception:
                pass

    return DEFAULT_VERSION


__version__ = get_version()
