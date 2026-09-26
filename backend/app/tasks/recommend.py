"""
Recommendation tasks (R1 spec §6).

`precompute_tonight` runs daily at 12:00 UTC (Celery beat): it computes
tonight for the default site with rig=mounted and warms the Redis result
cache, so the Tonight page's first request of the day is a cache hit.
"""

import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)

TASK_NAME = "app.tasks.recommend.precompute_tonight"


@celery_app.task(name=TASK_NAME, soft_time_limit=600, time_limit=900)
def precompute_tonight():
    from app.services.recommend.loader import RecommendationError, get_recommendations

    try:
        body = get_recommendations(rig="mounted", use_cache=False)
    except RecommendationError as e:
        logger.info(f"Recommendations pre-compute skipped: {e.detail}")
        return {"status": "skipped", "reason": e.detail}
    except Exception as e:
        logger.error(f"Recommendations pre-compute failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    ctx = body.get("context", {})
    hero = body.get("hero") or {}
    return {
        "status": "completed",
        "night": ctx.get("night"),
        "site_id": (ctx.get("site") or {}).get("id"),
        "tier": ctx.get("tier"),
        "rig_mode": ctx.get("rig_mode"),
        "hero": hero.get("target_key"),
        "verdict": (body.get("verdict") or {}).get("level"),
    }
