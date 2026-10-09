"""
Online catalog overlay layers (O1, docs/design/20261009-O1-online-catalog-overlays.md).

/api/sky/online-catalogs                     enabled layers, for the viewer's legend
/api/sky/online-catalogs/admin               every catalog with its setting (admin)
/api/sky/online-catalogs/{key}/test          live probe of the upstream service (admin)
/api/images/{id}/sky-overlay/online/{key}    one layer for one image
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.database import get_db
from app.models.image import Image
from app.schemas.image import OnlineSkyOverlayResponse
from app.services import sky_overlay
from app.services.online_catalogs import service
from app.services.online_catalogs.registry import REGISTRY
from app.services.online_catalogs.vizier import OnlineCatalogError

logger = logging.getLogger(__name__)

router = APIRouter()
images_router = APIRouter()

# Fields that reliably return rows, for the admin Test button
_TEST_CONES = {
    "SKYBOT": {"ra": 150.0, "dec": 12.0, "radius": 0.3, "jd": 2460600.5, "limit": None},
    "PGC": {"ra": 194.95, "dec": 27.98, "radius": 0.5, "constraints": {"logD25": ">=0.5"}},
    "ARP": {"ra": 35.37, "dec": 39.37, "radius": 1.0},
    "ABELL": {"ra": 194.95, "dec": 27.98, "radius": 1.0},
    "LDN": {"ra": 68.0, "dec": 26.0, "radius": 2.0},
    "LBN": {"ra": 83.8, "dec": -5.4, "radius": 2.0},
    "BARNARD": {"ra": 85.2, "dec": -2.5, "radius": 2.0},
    "VDB": {"ra": 315.4, "dec": 68.16, "radius": 1.0},
    "PN": {"ra": 283.4, "dec": 33.03, "radius": 1.0},
}


def _spec_or_404(key: str):
    spec = service.get_spec(key)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown online catalog: {key}")
    return spec


@router.get("/online-catalogs")
def list_enabled_catalogs():
    """Online catalogs an admin has enabled: {key, label, group}, in display order."""
    return [s.public() for s in service.enabled_specs()]


@router.get("/online-catalogs/admin", dependencies=[Depends(require_admin)])
def list_all_catalogs():
    """Every online catalog with its source, limit semantics and current setting."""
    cfg = service.catalog_settings()
    return [{**s.describe(), **cfg[s.key]} for s in REGISTRY]


@router.post("/online-catalogs/{key}/test", dependencies=[Depends(require_admin)])
async def test_catalog(key: str):
    """Query the catalog's service on a known field (uncached, even when disabled)."""
    spec = _spec_or_404(key)
    cone = _TEST_CONES[spec.key]
    plan = {"constraints": {}, "jd": None, "limit": None, **cone}
    started = time.monotonic()
    try:
        rows = await service.fetch_rows(spec, plan, use_cache=False)
    except OnlineCatalogError as e:
        return {"ok": False, "error": str(e), "seconds": round(time.monotonic() - started, 1)}
    return {"ok": True, "rows": len(rows), "seconds": round(time.monotonic() - started, 1),
            "sample": [r["designation"] for r in rows[:5]]}


@images_router.get("/{image_id}/sky-overlay/online/{key}", response_model=OnlineSkyOverlayResponse)
async def get_online_layer(image_id: int, key: str, db: AsyncSession = Depends(get_db)):
    """
    One online catalog's objects in this image's field, projected like /sky-overlay.
    404 when the catalog is unknown or disabled; 502 when its service fails (503 while a
    recent failure of the same query is being backed off).
    """
    spec = _spec_or_404(key)
    setting = service.catalog_settings()[spec.key]
    if not setting["enabled"]:
        raise HTTPException(status_code=404, detail=f"{spec.label} is not enabled")
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    frame = sky_overlay.resolve_frame(image, allow_pointing=True)
    if frame is None:
        return OnlineSkyOverlayResponse(catalog=spec.key, reason=service.REASON_NO_WCS)
    plan = service.plan_query(spec, frame, setting["limit"], image)
    base = dict(catalog=spec.key, source=frame.source, accuracy_warning=frame.accuracy_warning,
                width=frame.width, height=frame.height)
    if plan.get("reason"):
        return OnlineSkyOverlayResponse(**base, reason=plan["reason"])

    try:
        rows = await service.fetch_rows(spec, plan)
    except service.ThrottledError as e:
        raise HTTPException(status_code=503, detail=f"{spec.label}: service failed recently, retrying later ({e})")
    except OnlineCatalogError as e:
        raise HTTPException(status_code=502, detail=f"{spec.label}: {e}")

    if spec.dedup_local and rows:
        from app.api.images import _load_sky_catalog_rows
        ra, dec, radius = frame.field_circle()
        local = await _load_sky_catalog_rows(db, ra, dec, radius + sky_overlay.QUERY_MARGIN_DEG)
        rows = service.dedup_against_local(rows, local)

    return OnlineSkyOverlayResponse(
        **base,
        objects=service.build_layer_objects(frame, rows),
        notice=service.notice_for(spec, image),
        limit=plan.get("limit"),
    )
