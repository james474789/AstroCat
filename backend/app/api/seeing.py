"""
Planetary seeing forecast API (S1, docs/design/20261001-S1-planetary-seeing-forecast.md §7.1).

- GET /api/seeing/forecast?site_id=&date=   hourly score, per-planet windows, verdict and source status

Needs a logged-in user (router dependency in main.py). The response never contains coordinates. The engine
does blocking HTTP and CPU work, so it runs in a threadpool.
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/forecast")
async def get_seeing_forecast(
    site_id: Optional[int] = Query(None, description="Site id; default the default site"),
    date_: Optional[date] = Query(None, alias="date", description="Observing night (YYYY-MM-DD); default tonight"),
):
    from app.services.recommend.loader import RecommendationError
    from app.services.seeing import service

    try:
        return await run_in_threadpool(service.forecast_for_request, site_id, date_)
    except RecommendationError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
