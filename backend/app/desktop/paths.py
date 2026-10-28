"""
Per-user data locations and the desktop config file.

Everything the desktop app writes lives under one folder:
    Windows  %LOCALAPPDATA%\\AstroCat
    macOS    ~/Library/Application Support/AstroCat
(ASTROCAT_DATA_DIR overrides it, which the CI smoke test uses.)
"""

import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "AstroCat"
PG_MAJOR = 18  # bundled server (osx-arm64 PostGIS 3.6 needs it); another major needs pg_upgrade


def default_data_root() -> Path:
    override = os.environ.get("ASTROCAT_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


@dataclass(frozen=True)
class DesktopPaths:
    root: Path

    @property
    def pgdata(self) -> Path:
        return self.root / "pgdata"

    @property
    def redis(self) -> Path:
        return self.root / "redis"

    @property
    def thumbnails(self) -> Path:
        return self.root / "thumbnails"

    @property
    def fullres(self) -> Path:
        return self.root / "fullres"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def children_file(self) -> Path:
        """PIDs of the running children, so a crashed launcher's orphans can be reaped."""
        return self.temp / "children.json"

    def ensure(self):
        # pgdata is left to initdb, which wants to create it itself
        for d in (self.redis, self.thumbnails, self.fullres, self.logs, self.backups, self.temp):
            d.mkdir(parents=True, exist_ok=True)


def _defaults() -> dict:
    return {
        # hex only, so SECRET_KEY can never trip config.py's insecure-word check
        "secret_key": secrets.token_hex(32),
        "pg_password": secrets.token_urlsafe(24),
        "redis_password": secrets.token_hex(16),  # hex: goes into redis:// URLs unescaped
        "pg_major": PG_MAJOR,
        "image_paths": [],
        "api_keys": {},  # e.g. {"ASTROMETRY_API_KEY": "..."}; see launcher.API_KEY_VARS
        "celery_workers": 4,
        "last_backup": 0,
    }


def load_config(paths: DesktopPaths) -> dict:
    """Read config.json, creating it (with fresh secrets) on first run and filling new keys."""
    paths.root.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if paths.config_file.exists():
        cfg = json.loads(paths.config_file.read_text(encoding="utf-8"))
    merged = {**_defaults(), **cfg}
    if merged != cfg:
        save_config(paths, merged)
    return merged


def save_config(paths: DesktopPaths, cfg: dict):
    tmp = paths.config_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, paths.config_file)
