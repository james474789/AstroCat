"""
Recommendations API (R1, docs/design/R1-recommendation-engine.md §7).

- GET /api/recommendations                  tonight's ranked picks, lanes and hero
- GET /api/recommendations/target/{key}     "why / why not" for one target, per rig
- GET /api/recommendations/replay/latest    the last replay report (admin)

Every endpoint needs a logged-in user (router dependency in main.py). There
are no writes in R1. The engine is CPU-bound and uses a sync session, so it
runs in a threadpool.
"""

import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _raise(e) -> None:
    raise HTTPException(status_code=e.status, detail=e.detail)


@router.get("")
@router.get("/", include_in_schema=False)
async def get_recommendations(
    date_: Optional[date] = Query(None, alias="date", description="Observing night (YYYY-MM-DD); default tonight"),
    site_id: Optional[int] = Query(None),
    rig: str = Query("mounted", description="mounted | all | <rig id>"),
    per_lane: int = Query(6, ge=1, le=20),
):
    from app.services.recommend import loader

    try:
        return await run_in_threadpool(loader.get_recommendations, date_, site_id, rig, per_lane)
    except loader.RecommendationError as e:
        _raise(e)


@router.get("/replay/latest", dependencies=[Depends(require_admin)])
async def get_replay_latest():
    from app.services.recommend.loader import replay_latest_path

    path = replay_latest_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No replay report has been saved yet")
    try:
        return json.loads(await run_in_threadpool(path.read_text, encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Replay report unreadable: {e}")
        raise HTTPException(status_code=500, detail="The saved replay report is unreadable")


@router.get("/target/{key}")
async def explain_target(
    key: str,
    date_: Optional[date] = Query(None, alias="date"),
    site_id: Optional[int] = Query(None),
    rig: str = Query("mounted"),
):
    from app.services.recommend import loader

    try:
        return await run_in_threadpool(loader.get_target_explanation, key, date_, site_id, rig)
    except loader.RecommendationError as e:
        _raise(e)
