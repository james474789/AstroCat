"""
Effective star-quality settings (Q1, docs/design/20260927-Q1-star-quality.md §7.9).

Env settings (app.config) are the install-level switch; the Settings page
stores runtime overrides in the Redis "system_settings" document
(app/api/settings.py). A feature is on only when both allow it. Read on hot
paths (every indexed image), so the Redis value is cached briefly.
"""

import json
import logging
import time
from typing import Any, Dict

from app.config import settings

logger = logging.getLogger(__name__)

SETTINGS_KEY = "system_settings"   # app/api/settings.py
UNITS = ("ARCSEC", "PX")
DEFAULTS = {"quality_units": "ARCSEC", "star_metrics_enabled": True, "star_metrics_backfill": True}
_CACHE_SECONDS = 60.0
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _runtime_overrides() -> Dict[str, Any]:
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["value"]
    value: Dict[str, Any] = {}
    try:
        import redis
        raw = redis.from_url(settings.redis_url, decode_responses=True).get(SETTINGS_KEY)
        if raw:
            value = json.loads(raw)
    except Exception as e:  # Redis down: fall back to env/defaults, never block indexing
        logger.debug(f"Could not read runtime settings: {e}")
    _cache.update(at=now, value=value)
    return value


def runtime_settings() -> Dict[str, Any]:
    """The whole runtime settings document (cached briefly; {} when Redis is down)."""
    return _runtime_overrides()


def clear_cache() -> None:
    _cache.update(at=0.0, value=None)


def quality_settings() -> Dict[str, Any]:
    runtime = _runtime_overrides()
    units = str(runtime.get("quality_units") or DEFAULTS["quality_units"]).upper()
    return {
        "quality_units": units if units in UNITS else DEFAULTS["quality_units"],
        "star_metrics_enabled": bool(settings.star_metrics_enabled
                                     and runtime.get("star_metrics_enabled", True) is not False),
        "star_metrics_backfill": bool(settings.star_metrics_backfill
                                      and runtime.get("star_metrics_backfill", True) is not False),
    }


def measuring_enabled() -> bool:
    return quality_settings()["star_metrics_enabled"]


def backfill_enabled() -> bool:
    s = quality_settings()
    return s["star_metrics_enabled"] and s["star_metrics_backfill"]
