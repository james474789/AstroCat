import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import redis
import json
from app.config import settings
from typing import Optional, Dict, Literal

from app.api.dependencies import require_admin

router = APIRouter()


class OnlineCatalogSetting(BaseModel):
    """One online overlay catalog (O1): off unless enabled; limit is a magnitude or arcmin, per catalog."""
    enabled: bool = False
    limit: Optional[float] = None


class SystemSettings(BaseModel):
    astrometry_provider: str # "nova" or "local"
    astrometry_max_submissions: int = 8
    mount_friendly_names: Dict[str, str] = {}
    # Star quality (Q1): default display units for FWHM/HFR (each viewer can
    # toggle), and runtime switches for measuring / library backfill. The env
    # STAR_METRICS_* settings still win when they are off.
    quality_units: Literal["ARCSEC", "PX"] = "ARCSEC"
    star_metrics_enabled: bool = True
    star_metrics_backfill: bool = True
    # Online overlay catalogs (O1), keyed by app.services.online_catalogs.registry key.
    # A missing key means off: nothing is fetched from outside until an admin opts in.
    online_catalogs: Dict[str, OnlineCatalogSetting] = {}

    class Config:
        json_schema_extra = {
            "example": {
                "astrometry_provider": "nova",
                "astrometry_max_submissions": 8
            }
        }

def get_redis_client():
    return redis.from_url(settings.redis_url, decode_responses=True)

SETTINGS_KEY = "system_settings"


def _db_load() -> Optional[str]:
    from app.database import SessionLocal
    from app.models.system_setting import SystemSetting
    with SessionLocal() as session:
        row = session.get(SystemSetting, SETTINGS_KEY)
        return row.value if row else None


def _db_save(raw: str) -> None:
    from app.database import SessionLocal
    from app.models.system_setting import SystemSetting
    with SessionLocal() as session:
        row = session.get(SystemSetting, SETTINGS_KEY)
        if row:
            row.value = raw
        else:
            session.add(SystemSetting(key=SETTINGS_KEY, value=raw))
        session.commit()


def restore_settings_cache() -> Optional[str]:
    """Reconcile Postgres (source of truth) and the Redis cache.

    Postgres wins: its value is copied into Redis if Redis is empty. If only
    Redis has a value (install that predates the table), it is imported into
    Postgres. Returns the effective JSON document, or None if neither has one.
    Never raises: a down store just leaves things as they were.
    """
    try:
        r = get_redis_client()
        cached = r.get(SETTINGS_KEY)
    except Exception:
        r, cached = None, None
    try:
        stored = _db_load()
    except Exception:
        return cached
    if stored:
        if r is not None and not cached:
            try:
                r.set(SETTINGS_KEY, stored)
            except Exception:
                pass
        return cached or stored
    if cached:
        try:
            _db_save(cached)
        except Exception:
            pass
    return cached


@router.get("/", response_model=SystemSettings)
def get_settings():
    """Get current system settings."""
    data = None
    try:
        data = get_redis_client().get(SETTINGS_KEY)
    except Exception:
        pass
    if not data:
        data = restore_settings_cache()

    if not data:
        # Default settings
        return SystemSettings(astrometry_provider="nova")

    return SystemSettings(**json.loads(data))

@router.post("/", response_model=SystemSettings)
def update_settings(new_settings: SystemSettings):
    """Update system settings."""
    # Validation: if choosing local, ensure local config exists
    if new_settings.astrometry_provider == "local":
        if not settings.local_astrometry_url or not settings.local_astrometry_api_key:
            raise HTTPException(
                status_code=400, 
                detail="Cannot switch to Local Astrometry: Configuration (URL/Key) is missing."
            )
    from app.services.online_catalogs.registry import BY_KEY
    unknown = sorted(set(new_settings.online_catalogs) - set(BY_KEY))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown online catalog(s): {', '.join(unknown)}")

    raw = new_settings.model_dump_json()
    # Postgres first: it is the durable copy, so a failure here must surface.
    try:
        _db_save(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist settings: {e}")
    get_redis_client().set(SETTINGS_KEY, raw)
    from app.services.quality_settings import clear_cache
    clear_cache()
    return new_settings


# --- Desktop app: integration keys (D3 5.3) ---------------------------------------------------
# The desktop app has no .env: its keys live under "api_keys" in config.json. The launcher
# watches that file and restarts the app processes when it changes. Docker keeps using .env, so
# these routes don't exist there.

DESKTOP_URL_VARS = ("LOCAL_ASTROMETRY_URL",)  # not secrets: returned in full
DESKTOP_KEY_MAX_LEN = 500


def _desktop_config_path() -> Path:
    if not settings.desktop_mode:
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.desktop_config_path:
        raise HTTPException(status_code=500, detail="The desktop config path is not set")
    return Path(settings.desktop_config_path)


def _read_desktop_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Could not read config.json: {e}")


def _desktop_keys_view(cfg: dict) -> dict:
    from app.desktop.launcher import API_KEY_VARS
    stored = cfg.get("api_keys") or {}
    out = {}
    for name in API_KEY_VARS:
        value = str(stored.get(name) or "")
        entry = {"set": bool(value), "hint": ("..." + value[-4:]) if value and name not in DESKTOP_URL_VARS else ""}
        if name in DESKTOP_URL_VARS:
            entry["value"] = value
        out[name] = entry
    return out


def _write_json_atomic(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.get("/desktop-keys")
def get_desktop_keys(_admin=Depends(require_admin)):
    """Which integration keys are set (never their values, except the server URL)."""
    path = _desktop_config_path()
    return _desktop_keys_view(_read_desktop_config(path))


@router.put("/desktop-keys")
def put_desktop_keys(changes: Dict[str, Optional[str]], _admin=Depends(require_admin)):
    """Set (a string) or remove (null or empty) keys. Other names and other config.json keys are kept."""
    from app.desktop.launcher import API_KEY_VARS
    path = _desktop_config_path()
    unknown = sorted(set(changes) - set(API_KEY_VARS))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown key(s): {', '.join(unknown)}")
    too_long = [n for n, v in changes.items() if v and len(v) > DESKTOP_KEY_MAX_LEN]
    if too_long:
        raise HTTPException(status_code=400, detail=f"Value too long for: {', '.join(sorted(too_long))}")
    cfg = _read_desktop_config(path)
    keys = dict(cfg.get("api_keys") or {})
    for name, value in changes.items():
        value = (value or "").strip()
        if value:
            keys[name] = value
        else:
            keys.pop(name, None)
    cfg["api_keys"] = keys
    try:
        _write_json_atomic(path, cfg)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save config.json: {e}")
    return _desktop_keys_view(cfg)
