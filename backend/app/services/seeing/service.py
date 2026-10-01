"""
Fetching, caching and request-level service for the seeing forecast (design §4). Not pure.

Redis (no coordinates anywhere in a key or value; sites are referred to by id only):
  seeing:raw:{site_id}:open_meteo   stripped provider response + fetched_at   (fresh for 3 h)
  seeing:raw:{site_id}:meteoblue    same                                      (fresh for METEOBLUE_MIN_INTERVAL_H)
  seeing:mbfail:{site_id}           last meteoblue error message, 1 h (credit throttle: no refetch while set)
  seeing:view:{site_id}:{night}:{v} the computed payload, 30 min (5 min when stale / a source errored)
  seeing:viewed:{site_id}           set by the API on read; keeps the site in the beat refresh for 7 days
Raw entries live 24 h so a failed fetch can fall back to stale data; freshness is judged from fetched_at.

Coordinates are read from the Site row (a SiteSpec in memory) and only go out through round_coords.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.seeing import SCORING_VERSION, build_forecast
from app.services.seeing.models import REFERENCE_MODEL, pick_models
from app.services.seeing.privacy import quiet_http_loggers, redact_url
from app.services.seeing.sources import HourlyFrame, SourceError
from app.services.seeing.sources import meteoblue as mb_src
from app.services.seeing.sources import open_meteo as om_src

quiet_http_loggers()
logger = logging.getLogger(__name__)

RAW_KEY = "seeing:raw:{site}:{source}"
MBFAIL_KEY = "seeing:mbfail:{site}"
VIEW_KEY = "seeing:view:{site}:{night}:{version}"
VIEWED_KEY = "seeing:viewed:{site}"

RAW_TTL_S = 24 * 3600
OM_FRESH_S = 3 * 3600
MB_FAIL_TTL_S = 3600
VIEW_TTL_S = 30 * 60
VIEW_TTL_DEGRADED_S = 5 * 60
VIEWED_TTL_S = 7 * 24 * 3600
FORECAST_HORIZON_DAYS = 7

OM_LABEL = "Windy-equivalent models"
MB_LABEL = "meteoblue"


def _cfg():
    """Settings, imported lazily so the pure parts of this package import without the app environment."""
    from app.config import settings
    return settings


# ---------------------------------------------------------------------------
# Redis helpers (all tolerate Redis being down)
# ---------------------------------------------------------------------------

def redis_client():
    try:
        from app.services.data_migrations import redis_client as rc
        return rc()
    except Exception:
        return None


def _get(r, key: str):
    if r is None:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"Seeing cache read failed: {redact_url(type(e).__name__)}")
        return None


def _set(r, key: str, value, ttl: int) -> None:
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning(f"Seeing cache write failed: {redact_url(type(e).__name__)}")


def mark_viewed(r, site_id: int) -> None:
    if r is None:
        return
    try:
        r.setex(VIEWED_KEY.format(site=site_id), VIEWED_TTL_S, "1")
    except Exception:
        pass


def was_viewed(r, site_id: int) -> bool:
    if r is None:
        return False
    try:
        return bool(r.exists(VIEWED_KEY.format(site=site_id)))
    except Exception:
        return False


def invalidate_views(r, site_id: int) -> None:
    if r is None:
        return
    try:
        keys = list(r.scan_iter(match=f"seeing:view:{site_id}:*"))
        if keys:
            r.delete(*keys)
    except Exception:
        pass


def _age_s(entry: Optional[dict], now: datetime) -> Optional[float]:
    if not entry or not entry.get("fetched_at"):
        return None
    return (now - datetime.fromisoformat(entry["fetched_at"])).total_seconds()


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _fetch_open_meteo_models(site, models: List[str]) -> Tuple[dict, List[str]]:
    """Fetch with the regional model; if the provider has no data for it, retry with the global models only."""
    try:
        return om_src.fetch_open_meteo(site.latitude, site.longitude, models), models
    except SourceError:
        from app.services.seeing.models import GLOBAL_MODELS
        if models == GLOBAL_MODELS:
            raise
        return om_src.fetch_open_meteo(site.latitude, site.longitude, GLOBAL_MODELS), list(GLOBAL_MODELS)


def get_open_meteo(site, r, now: datetime, force: bool = False) -> Dict[str, Any]:
    """{frames, primary, fetched_at, stale, source} for the always-on source."""
    entry = _get(r, RAW_KEY.format(site=site.id, source="open_meteo"))
    age = _age_s(entry, now)
    message = None
    status = "ok"
    stale = False
    if force or entry is None or age is None or age >= OM_FRESH_S:
        models, primary = pick_models(site.latitude, site.longitude)
        try:
            raw, used = _fetch_open_meteo_models(site, models)
            if primary not in used:
                primary = REFERENCE_MODEL
            entry = {"fetched_at": now.replace(microsecond=0).isoformat(), "models": used, "primary": primary,
                     "payload": raw}
            _set(r, RAW_KEY.format(site=site.id, source="open_meteo"), entry, RAW_TTL_S)
        except SourceError as e:
            message = str(e)
            status = "error"
            logger.warning(f"Seeing: Open-Meteo fetch failed for site {site.id}: {message}")
            if entry is not None:
                age = _age_s(entry, now)
                stale = age is None or age >= OM_FRESH_S
    frames: Dict[str, HourlyFrame] = {}
    primary = REFERENCE_MODEL
    fetched_at = None
    if entry is not None:
        primary = entry.get("primary", REFERENCE_MODEL)
        frames = om_src.parse_open_meteo(entry["payload"], entry["models"])
        om_src.fill_upper_levels(frames, REFERENCE_MODEL)
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
    source = {"id": "open_meteo", "label": OM_LABEL, "models": list(frames), "primary": primary if frames else None,
              "status": status, "message": message}
    return {"frames": frames, "primary": primary, "fetched_at": fetched_at, "stale": stale, "source": source}


def get_meteoblue(site, r, now: datetime, force: bool = False) -> Dict[str, Any]:
    """{frame, source}. Without a key: status no_key and nothing else changes."""
    settings = _cfg()
    key = settings.meteoblue_api_key
    if not key:
        return {"frame": None, "source": {"id": "meteoblue", "label": MB_LABEL, "status": "no_key", "message": None}}
    rk = RAW_KEY.format(site=site.id, source="meteoblue")
    entry = _get(r, rk)
    age = _age_s(entry, now)
    min_age = max(1, int(settings.meteoblue_min_interval_h)) * 3600
    failed = _get(r, MBFAIL_KEY.format(site=site.id))
    status, message = "ok", None
    due = entry is None or age is None or age >= min_age
    if failed:                       # credit throttle: after an error, wait out the failure window
        due = False
        status, message = "error", failed.get("message")
    elif due:
        try:
            raw = mb_src.fetch_meteoblue(site.latitude, site.longitude, key)
            entry = {"fetched_at": now.replace(microsecond=0).isoformat(), "payload": raw}
            _set(r, rk, entry, RAW_TTL_S)
        except SourceError as e:
            status, message = "error", str(e).replace(key, "<key>")
            _set(r, MBFAIL_KEY.format(site=site.id), {"message": message}, MB_FAIL_TTL_S)
            logger.warning(f"Seeing: meteoblue fetch failed for site {site.id}: {message}")
    frame = mb_src.parse_meteoblue(entry["payload"]) if entry is not None else None
    fetched = entry["fetched_at"] if entry else None
    return {"frame": frame if frame is not None and frame.has_data() else None,
            "source": {"id": "meteoblue", "label": MB_LABEL, "status": status, "message": message,
                       "fetched_at": (fetched + "Z") if fetched else None}}


# ---------------------------------------------------------------------------
# Payload and views
# ---------------------------------------------------------------------------

def compute_payload(site, night: date, r, horizon_points, now: Optional[datetime] = None,
                    force: bool = False) -> dict:
    now = now or datetime.utcnow()
    om = get_open_meteo(site, r, now, force)
    mb = get_meteoblue(site, r, now, force)
    return build_forecast(site, night, om["frames"], om["primary"], now=now, mb=mb["frame"],
                          sources=[om["source"], mb["source"]], horizon_points=horizon_points,
                          fetched_at=om["fetched_at"], stale=om["stale"])


def _degraded(payload: dict) -> bool:
    return bool(payload.get("stale")) or any(s.get("status") == "error" for s in payload.get("sources", [])) \
        or not payload.get("available", False)


def forecast_view(site, night: Optional[date], r, horizon_points, now: Optional[datetime] = None,
                  use_cache: bool = True) -> dict:
    """Cached payload for (site, night). `night` defaults to tonight at the site. No site name inside."""
    from app.services.recommend.loader import default_night

    now = now or datetime.utcnow()
    night = night or default_night(now, site.longitude)
    delta = (night - now.date()).days
    if delta > FORECAST_HORIZON_DAYS or delta < -1:
        return {"available": False, "reason": "out_of_range", "night": night.isoformat(),
                "site": {"id": site.id, "timezone": site.timezone}, "scoring_version": SCORING_VERSION,
                "sources": []}
    key = VIEW_KEY.format(site=site.id, night=night.isoformat(), version=SCORING_VERSION)
    if use_cache:
        hit = _get(r, key)
        if hit is not None:
            return hit
    payload = compute_payload(site, night, r, horizon_points, now)
    _set(r, key, payload, VIEW_TTL_DEGRADED_S if _degraded(payload) else VIEW_TTL_S)
    return payload


def refresh_site_now(site, r, horizon_points, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Force-refresh both sources for a site and warm tonight's view. Returns a summary without coordinates."""
    from app.services.recommend.loader import default_night

    now = now or datetime.utcnow()
    night = default_night(now, site.longitude)
    payload = compute_payload(site, night, r, horizon_points, now, force=True)
    invalidate_views(r, site.id)
    _set(r, VIEW_KEY.format(site=site.id, night=night.isoformat(), version=SCORING_VERSION), payload,
         VIEW_TTL_DEGRADED_S if _degraded(payload) else VIEW_TTL_S)
    return {"site_id": site.id, "night": night.isoformat(), "available": payload.get("available", False),
            "stale": payload.get("stale", False),
            "sources": {s["id"]: s.get("status") for s in payload.get("sources", [])}}


def forecast_for_request(site_id: Optional[int], night: Optional[date], session=None) -> dict:
    """GET /api/seeing/forecast body. Raises RecommendationError (404) for an unknown site / no site."""
    from app.services.recommend import loader

    if not _cfg().seeing_enabled:
        return {"available": False, "reason": "disabled"}
    r = redis_client()
    with loader._session_scope(session) as s:
        site = loader.pick_site(loader.load_sites(s), site_id)
        mark_viewed(r, site.id)
        try:
            horizon_points = loader.site_horizon(s, site, r).points
        except Exception:
            horizon_points = []
        name = site.name
    payload = forecast_view(site, night, r, horizon_points)
    out = dict(payload)
    out["site"] = {**(payload.get("site") or {"id": site.id, "timezone": site.timezone}), "name": name}
    return out
