"""
Recommendations API (R1, docs/design/R1-recommendation-engine.md §7;
R2a, docs/design/R2a-feedback-dashboard.md §5-§6).

- GET  /api/recommendations                 tonight's ranked picks, lanes and hero (with the user's feedback)
- GET  /api/recommendations/target/{key}    "why / why not" for one target, per rig
- GET  /api/recommendations/replay/latest   the last replay report (admin)
- POST /api/recommendations/feedback        pin / snooze / dismiss / imaged (+ inverses), per user
- GET  /api/recommendations/feedback        the user's active feedback (hidden-items manager)
- GET  /api/recommendations/outcomes        advice outcomes over the user's impressions

Every endpoint needs a logged-in user (router dependency in main.py);
feedback and outcomes are the user's own, so they aren't admin-only. The
engine is CPU-bound and uses a sync session, so it runs in a threadpool.
Impressions are written after the response (BackgroundTasks), only when the
night viewed is tonight's default night.
"""

import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user, require_admin
from app.models.user import User
from app.schemas.recommendations import FeedbackRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _raise(e) -> None:
    raise HTTPException(status_code=e.status, detail=e.detail)


def _user_id(user) -> Optional[int]:
    return getattr(user, "id", None)


@router.get("")
@router.get("/", include_in_schema=False)
async def get_recommendations(
    date_: Optional[date] = Query(None, alias="date", description="Observing night (YYYY-MM-DD); default tonight"),
    site_id: Optional[int] = Query(None),
    rig: str = Query("mounted", description="mounted | all | <rig id>"),
    per_lane: int = Query(6, ge=1, le=20),
    background_tasks: BackgroundTasks = None,
    user: User = Depends(get_current_user),
):
    from app.services.recommend import loader
    from app.services.recommend.feedback import impression_rows

    uid = _user_id(user)
    try:
        body, is_tonight = await run_in_threadpool(loader.recommendations_view, date_, site_id, rig, per_lane,
                                                   True, None, uid)
    except loader.RecommendationError as e:
        _raise(e)
    if is_tonight and uid is not None and background_tasks is not None:
        rows = impression_rows(body, per_lane)
        if rows:
            background_tasks.add_task(loader.record_impressions, uid, rows)
    return body


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
    user: User = Depends(get_current_user),
):
    from app.services.recommend import loader

    try:
        return await run_in_threadpool(loader.get_target_explanation, key, date_, site_id, rig, None,
                                       _user_id(user))
    except loader.RecommendationError as e:
        _raise(e)


@router.post("/feedback")
async def post_feedback(body: FeedbackRequest, user: User = Depends(get_current_user)):
    from app.services.recommend import loader

    ctx = body.context.model_dump() if body.context is not None else None
    try:
        return await run_in_threadpool(loader.post_feedback, user.id, body.target_key, body.action, body.nights,
                                       body.reason, body.note, ctx)
    except loader.RecommendationError as e:
        _raise(e)


@router.get("/feedback")
async def list_feedback(user: User = Depends(get_current_user)):
    from app.services.recommend import loader

    return await run_in_threadpool(loader.list_feedback, user.id)


@router.get("/outcomes")
async def get_outcomes(days: int = Query(90, ge=1, le=730), user: User = Depends(get_current_user)):
    from app.services.recommend import loader

    return await run_in_threadpool(loader.get_outcomes, user.id, days)
