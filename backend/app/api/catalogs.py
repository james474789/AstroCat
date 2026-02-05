"""
Catalogs API
Endpoints for browsing Messier and NGC catalogs.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.catalog import MessierCatalog, NGCCatalog, NamedStarCatalog
from app.schemas.catalog import MessierSchema, NGCSchema, NamedStarSchema
from app.schemas.common import PaginatedResponse

router = APIRouter()


@router.get("/messier", response_model=PaginatedResponse[MessierSchema])
async def list_messier(
    page: int = Query(1, ge=1),
    page_size: int = Query(110, ge=1), # Default to showing all
    q: Optional[str] = Query(None),
    has_images: bool = Query(False),
    sort_by: str = Query("default"),
    sort_order: str = Query("asc"),
    db: AsyncSession = Depends(get_db)
):
    """List all Messier objects."""
    from app.models.matches import ImageCatalogMatch
    from app.models.image import Image
    from sqlalchemy import select, func, desc

    # Build stats subquery by computing stats from image_catalog_matches
    # This works whether or not the materialized view exists
    stats_subquery = select(
        ImageCatalogMatch.catalog_designation,
        func.coalesce(func.sum(Image.exposure_time_seconds), 0).label("cumulative_exposure_seconds"),
        func.count(func.distinct(ImageCatalogMatch.image_id)).label("image_count"),
        func.coalesce(func.max(ImageCatalogMatch.angular_separation_degrees), 0).label("max_separation_degrees")
    ).join(Image, Image.id == ImageCatalogMatch.image_id).where(
        ImageCatalogMatch.catalog_type == "MESSIER"
    ).group_by(ImageCatalogMatch.catalog_designation).subquery()

    # Base statement
    base_stmt = select(
        MessierCatalog,
        func.coalesce(stats_subquery.c.cumulative_exposure_seconds, 0.0).label("cumulative_exposure_seconds"),
        func.coalesce(stats_subquery.c.image_count, 0).label("image_count"),
        func.coalesce(stats_subquery.c.max_separation_degrees, 0.0).label("max_separation_degrees")
    ).outerjoin(
        stats_subquery, 
        MessierCatalog.designation == stats_subquery.c.catalog_designation
    )
    
    if q:
        normalized_q = q.replace(" ", "")
        base_stmt = base_stmt.where(
            (func.replace(MessierCatalog.designation, ' ', '').ilike(normalized_q)) |
            (MessierCatalog.common_name.ilike(f"%{q}%")) |
            (MessierCatalog.constellation.ilike(f"%{q}%"))
        )

    if has_images:
        base_stmt = base_stmt.where(stats_subquery.c.image_count > 0)

    # Count total
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Sorting
    if sort_by == "exposure":
        order_col = "cumulative_exposure_seconds"
    elif sort_by == "ra":
        order_col = MessierCatalog.ra_degrees
    else:
        order_col = MessierCatalog.messier_number

    if sort_order == "desc":
        stmt = base_stmt.order_by(desc(order_col))
    else:
        stmt = base_stmt.order_by(order_col)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(stmt)
    # result.all() returns rows with (CatalogObject, exposure, count, max_separation)
    items = []
    for row in result.all():
        obj = row[0]
        obj.cumulative_exposure_seconds = row[1]
        obj.image_count = row[2]
        obj.max_separation_degrees = row[3]
        items.append(obj)
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


@router.get("/ngc", response_model=PaginatedResponse[NGCSchema])
async def list_ngc(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1),
    constellation: str = None,
    q: Optional[str] = Query(None),
    catalog: Optional[str] = Query(None, description="Catalog filter (e.g. NGC, IC)"),
    has_images: bool = Query(False),
    sort_by: str = Query("default"),
    sort_order: str = Query("asc"),
    db: AsyncSession = Depends(get_db)
):
    """List NGC objects with filtering."""
    from app.models.matches import ImageCatalogMatch
    from app.models.image import Image
    from sqlalchemy import select, func, desc

    # Build stats subquery by computing stats from image_catalog_matches
    # This works whether or not the materialized view exists
    stats_subquery = select(
        ImageCatalogMatch.catalog_designation,
        func.coalesce(func.sum(Image.exposure_time_seconds), 0).label("cumulative_exposure_seconds"),
        func.count(func.distinct(ImageCatalogMatch.image_id)).label("image_count"),
        func.coalesce(func.max(ImageCatalogMatch.angular_separation_degrees), 0).label("max_separation_degrees")
    ).join(Image, Image.id == ImageCatalogMatch.image_id).where(
        ImageCatalogMatch.catalog_type == "NGC"
    ).group_by(ImageCatalogMatch.catalog_designation).subquery()

    # Base statement for both count and data
    base_stmt = select(
        NGCCatalog,
        func.coalesce(stats_subquery.c.cumulative_exposure_seconds, 0.0).label("cumulative_exposure_seconds"),
        func.coalesce(stats_subquery.c.image_count, 0).label("image_count"),
        func.coalesce(stats_subquery.c.max_separation_degrees, 0.0).label("max_separation_degrees")
    ).outerjoin(
        stats_subquery, 
        NGCCatalog.designation == stats_subquery.c.catalog_designation
    )

    if constellation:
        base_stmt = base_stmt.where(NGCCatalog.constellation == constellation)
    if catalog:
        base_stmt = base_stmt.where(NGCCatalog.designation.ilike(f"{catalog}%"))
    if q:
        normalized_q = q.replace(" ", "")
        base_stmt = base_stmt.where(
            (func.replace(NGCCatalog.designation, ' ', '').ilike(normalized_q)) |
            (NGCCatalog.common_name.ilike(f"%{q}%")) |
            (NGCCatalog.constellation.ilike(f"%{q}%"))
        )
    
    if has_images:
        base_stmt = base_stmt.where(stats_subquery.c.image_count > 0)

    # Count total
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Sorting
    if sort_by == "exposure":
        order_col = "cumulative_exposure_seconds"
    elif sort_by == "ra":
        order_col = NGCCatalog.ra_degrees
    else:
        order_col = NGCCatalog.ngc_number

    if sort_order == "desc":
        stmt = base_stmt.order_by(desc(order_col))
    else:
        stmt = base_stmt.order_by(order_col)

    # Get page items
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(stmt)
    # result.all() returns rows with (CatalogObject, exposure, count, max_separation)
    items = []
    for row in result.all():
        obj = row[0]
        obj.cumulative_exposure_seconds = row[1]
        obj.image_count = row[2]
        obj.max_separation_degrees = row[3]
        items.append(obj)
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


@router.get("/messier/{designation}", response_model=MessierSchema)
async def get_messier(designation: str, db: AsyncSession = Depends(get_db)):
    """Get specific Messier object (e.g., M31)."""
    stmt = select(MessierCatalog).where(MessierCatalog.designation.ilike(designation))
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()
    
    if not item:
        raise HTTPException(status_code=404, detail="Object not found")
    return item


@router.get("/named_stars", response_model=PaginatedResponse[NamedStarSchema])
async def list_named_stars(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1),
    q: Optional[str] = Query(None),
    has_images: bool = Query(False),
    sort_by: str = Query("default"),
    sort_order: str = Query("asc"),
    db: AsyncSession = Depends(get_db)
):
    """List Named Stars."""
    from app.models.catalog import NamedStarCatalog
    from app.schemas.catalog import NamedStarSchema
    from app.models.matches import ImageCatalogMatch
    from app.models.image import Image
    from app.models.matches import CatalogType
    from sqlalchemy import select, func, desc, or_

    # Build stats subquery by computing stats from image_catalog_matches
    # This works whether or not the materialized view exists
    stats_subquery = select(
        ImageCatalogMatch.catalog_designation,
        func.coalesce(func.sum(Image.exposure_time_seconds), 0).label("cumulative_exposure_seconds"),
        func.count(func.distinct(ImageCatalogMatch.image_id)).label("image_count"),
        func.coalesce(func.max(ImageCatalogMatch.angular_separation_degrees), 0).label("max_separation_degrees")
    ).join(Image, Image.id == ImageCatalogMatch.image_id).where(
        ImageCatalogMatch.catalog_type == "NAMED_STAR"
    ).group_by(ImageCatalogMatch.catalog_designation).subquery()

    base_stmt = select(
        NamedStarCatalog,
        func.coalesce(stats_subquery.c.cumulative_exposure_seconds, 0.0).label("cumulative_exposure_seconds"),
        func.coalesce(stats_subquery.c.image_count, 0).label("image_count"),
        func.coalesce(stats_subquery.c.max_separation_degrees, 0.0).label("max_separation_degrees")
    ).outerjoin(
        stats_subquery, 
        NamedStarCatalog.designation == stats_subquery.c.catalog_designation
    )

    if q:
        normalized_q = q.replace(" ", "")
        base_stmt = base_stmt.where(
            (func.replace(NamedStarCatalog.designation, ' ', '').ilike(normalized_q)) |
            (NamedStarCatalog.common_name.ilike(f"%{q}%"))
        )

    if has_images:
        base_stmt = base_stmt.where(stats_subquery.c.image_count > 0)

    # Count total
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Sorting
    if sort_by == "exposure":
        order_col = "cumulative_exposure_seconds"
    elif sort_by == "mag":
        order_col = NamedStarCatalog.magnitude
    elif sort_by == "ra":
        order_col = NamedStarCatalog.ra_degrees
    else:
        order_col = NamedStarCatalog.designation

    if sort_order == "desc":
        stmt = base_stmt.order_by(desc(order_col))
    else:
        stmt = base_stmt.order_by(order_col)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    
    items = []
    for row in result.all():
        obj = row[0]
        obj.cumulative_exposure_seconds = row[1]
        obj.image_count = row[2]
        obj.max_separation_degrees = row[3]
        items.append(obj)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1
    }
