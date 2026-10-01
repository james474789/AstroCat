"""
Planetary seeing forecast tasks (S1, docs/design/20261001-S1-planetary-seeing-forecast.md §4).

`refresh_forecasts` runs every 3 h at minute 10 (Celery beat, default queue): it refreshes the default site
plus any site viewed in the last 7 days. Idempotent. Results and logs carry site ids only, never coordinates.
"""

import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)

REFRESH_ALL = "app.tasks.seeing.refresh_forecasts"
REFRESH_ONE = "app.tasks.seeing.refresh_site"


def _refresh(site, session, r):
    from app.services.recommend import loader
    from app.services.seeing import service

    try:
        points = loader.site_horizon(session, site, r).points
    except Exception:
        points = []
    return service.refresh_site_now(site, r, points)


@celery_app.task(name=REFRESH_ALL, soft_time_limit=600, time_limit=900)
def refresh_forecasts():
    from app.config import settings

    if not settings.seeing_enabled:
        return {"status": "disabled"}
    from app.database import SessionLocal
    from app.services.recommend import loader
    from app.services.seeing import service

    r = service.redis_client()
    results = []
    with SessionLocal() as s:
        for site in loader.load_sites(s):
            if not (site.is_default or service.was_viewed(r, site.id)):
                continue
            try:
                results.append(_refresh(site, s, r))
            except Exception as e:
                logger.error(f"Seeing refresh failed for site {site.id}: {type(e).__name__}")
                results.append({"site_id": site.id, "error": type(e).__name__})
    return {"status": "completed", "sites": results}


@celery_app.task(name=REFRESH_ONE, soft_time_limit=300, time_limit=600)
def refresh_site(site_id: int):
    from app.config import settings

    if not settings.seeing_enabled:
        return {"status": "disabled"}
    from app.database import SessionLocal
    from app.services.recommend import loader
    from app.services.seeing import service

    r = service.redis_client()
    with SessionLocal() as s:
        try:
            site = loader.pick_site(loader.load_sites(s), site_id)
        except loader.RecommendationError as e:
            return {"status": "skipped", "reason": e.detail}
        return {"status": "completed", **_refresh(site, s, r)}
