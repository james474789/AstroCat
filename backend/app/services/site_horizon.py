"""
Learned site horizon, shared by `GET /api/sites/{id}/horizon/learned` (async)
and the recommendation engine (sync) (R0 §4.7, R1 §4.3).

Both read and write the same Redis key, so the profile is computed once per
site per TTL and the two can't disagree.
"""

import json
import logging
from typing import Any, Dict, Iterable, Mapping, Optional

from sqlalchemy import text

from app.utils.horizon import horizon_samples, learn_horizon_profile

logger = logging.getLogger(__name__)

HORIZON_CACHE_KEY = "cache:equipment:horizon:{site_id}"
HORIZON_CACHE_TTL = 24 * 3600

HORIZON_SAMPLES_SQL = text("""
    SELECT capture_date_utc AS utc,
           raw_header->'CENTALT' AS centalt, raw_header->'CENTAZ' AS centaz,
           ra_center_degrees AS ra, dec_center_degrees AS dec,
           raw_header->'OBJCTRA' AS objctra, raw_header->'OBJCTDEC' AS objctdec
    FROM images
    WHERE site_id = :site_id AND frame_type = 'LIGHT' AND subtype = 'SUB_FRAME'
      AND capture_date_utc IS NOT NULL
""")


def profile_from_rows(rows: Iterable[Mapping[str, Any]], lat: float, lon: float) -> Dict[str, Any]:
    """{"points", "floor_deg", "sample_count"} from horizon sample rows."""
    profile = learn_horizon_profile(horizon_samples([dict(r) for r in rows], lat, lon))
    return {"points": profile["points"], "floor_deg": profile["floor_deg"], "sample_count": profile["sample_count"]}


def learned_horizon_sync(session, site_id: int, lat: float, lon: float, redis=None) -> Dict[str, Any]:
    """The learned profile for a site (Redis read-through, same key as the API)."""
    key = HORIZON_CACHE_KEY.format(site_id=site_id)
    if redis is not None:
        try:
            raw = redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Horizon cache read failed: {e}")
    rows = session.execute(HORIZON_SAMPLES_SQL, {"site_id": site_id}).mappings().all()
    result = profile_from_rows(rows, lat, lon)
    if redis is not None:
        try:
            redis.setex(key, HORIZON_CACHE_TTL, json.dumps(result))
        except Exception as e:
            logger.warning(f"Horizon cache write failed: {e}")
    return result
