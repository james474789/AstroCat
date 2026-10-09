from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import redis
import json
from app.config import settings
from typing import Optional, Dict, Literal

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
