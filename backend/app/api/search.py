"""
Search API
Endpoints for searching images by coordinates and catalog objects.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.image import Image
from app.models.matches import ImageCatalogMatch, CatalogType
from app.schemas.image import ImageList

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("/coordinates", response_model=List[ImageList])
@limiter.limit("30/minute")
async def search_by_coordinates(
    request: Request,
    ra: float = Query(..., description="Right Ascension in degrees (0-360)"),
    dec: float = Query(..., description="Declination in degrees (-90 to +90)"),
    radius: float = Query(1.0, description="Search radius in degrees"),
    db: AsyncSession = Depends(get_db)
):
    """
    Search for images covering a specific point in the sky.
    Uses PostGIS ST_DWithin on the center_location column.
    Rate limited to prevent DoS through expensive spatial queries.
    """
    # Note: We are using simple distance from center for now. 
    # A more accurate check would be ST_Intersects(image.field_boundary, point)
    # but we need to ensure field_boundary is populated.
    
    # Using raw SQL for PostGIS functions
    # 1 degree ~ 111320 meters approx (at equator)
    radius_meters = radius * 111320
    
    stmt = select(Image).options(selectinload(Image.catalog_matches)).where(
        text("""
        ST_DWithin(
            center_location, 
            ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
            :radius_meters
        )
        """)
    ).params(ra=ra, dec=dec, radius_meters=radius_meters)
    
    # Limit results
    stmt = stmt.limit(50)
    
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/catalog/{catalog_type}/{designation}", response_model=List[ImageList])
async def search_by_catalog_object(
    catalog_type: CatalogType,
    designation: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Find images that contain a specific catalog object.
    e.g., /catalog/MESSIER/M31
    """
    # Join ImageCatalogMatch
    stmt = select(Image).join(Image.catalog_matches).options(
        selectinload(Image.catalog_matches)
    ).where(
        ImageCatalogMatch.catalog_type == catalog_type,
        ImageCatalogMatch.catalog_designation == designation
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()
