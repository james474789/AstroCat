from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import redis
import json
from app.config import settings
from typing import Optional, Dict

router = APIRouter()

class SystemSettings(BaseModel):
    astrometry_provider: str # "nova" or "local"
    astrometry_max_submissions: int = 8
    mount_friendly_names: Dict[str, str] = {}

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

@router.get("/", response_model=SystemSettings)
def get_settings():
    """Get current system settings."""
    r = get_redis_client()
    data = r.get(SETTINGS_KEY)
    
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

    r = get_redis_client()
    r.set(SETTINGS_KEY, new_settings.model_dump_json())
    return new_settings
