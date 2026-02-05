"""
Stats API
Endpoints for dashboard statistics and analytics.
"""

import json
from functools import wraps
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.database import get_db
from app.models.image import Image, ImageSubtype
from app.models.matches import ImageCatalogMatch
from app.config import settings

router = APIRouter()

# Simple Cache Helper
def cache_response(ttl_seconds=300):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                r = redis.from_url(settings.redis_url, decode_responses=True)
                key = f"cache:stats:{func.__name__}"
                cached = await r.get(key)
                if cached:
                    await r.close()
                    return json.loads(cached)
            except Exception as e:
                print(f"Cache read error: {e}")

            # Execute function
            result = await func(*args, **kwargs)

            try:
                # Re-connect
                r = redis.from_url(settings.redis_url, decode_responses=True)
                await r.setex(key, ttl_seconds, json.dumps(result))
                await r.close()
            except Exception as e:
                print(f"Cache write error: {e}")
                
            return result
        return wrapper
    return decorator


@router.get("/overview")
@cache_response(ttl_seconds=300)
async def get_stats_overview(db: AsyncSession = Depends(get_db)):
    """Get overview statistics for the dashboard."""
    # Total images
    total_images = (await db.execute(select(func.count(Image.id)))).scalar() or 0
    
    # Total exposure hours
    total_exposure_seconds = (await db.execute(select(func.sum(Image.exposure_time_seconds)))).scalar() or 0
    total_exposure_hours = total_exposure_seconds / 3600
    
    # Plate solved stats (filtered to masters and subs as requested)
    relevant_subtypes = [ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER]
    
    total_relevant_images = (await db.execute(
        select(func.count(Image.id)).where(Image.subtype.in_(relevant_subtypes))
    )).scalar() or 0
    
    total_plate_solved = (await db.execute(
        select(func.count(Image.id)).where(
            Image.is_plate_solved == True,
            Image.subtype.in_(relevant_subtypes)
        )
    )).scalar() or 0
    
    plate_solved_percentage = round((total_plate_solved / total_relevant_images * 100) if total_relevant_images > 0 else 0, 1)
    
    # Unique objects
    unique_objects_imaged = (await db.execute(select(func.count(func.distinct(ImageCatalogMatch.catalog_designation))))).scalar() or 0
    
    # Total file size
    total_size_bytes = (await db.execute(select(func.sum(Image.file_size_bytes)))).scalar() or 0
    total_file_size_gb = total_size_bytes / (1024 ** 3)
    
    # Messier coverage
    messier_coverage = (await db.execute(
        select(func.count(func.distinct(ImageCatalogMatch.catalog_designation))).where(
            ImageCatalogMatch.catalog_type == "MESSIER"
        )
    )).scalar() or 0
    
    # NGC coverage
    ngc_coverage = (await db.execute(
        select(func.count(func.distinct(ImageCatalogMatch.catalog_designation))).where(
            ImageCatalogMatch.catalog_type == "NGC"
        )
    )).scalar() or 0

    return {
        "total_images": total_images,
        "total_exposure_hours": round(total_exposure_hours, 1),
        "plate_solved_percentage": plate_solved_percentage,
        "total_plate_solved": total_plate_solved,
        "unique_objects_imaged": unique_objects_imaged,
        "total_file_size_gb": round(total_file_size_gb, 2),
        "messier_coverage": messier_coverage,
        "ngc_coverage": ngc_coverage,
        "storage_used_bytes": total_size_bytes
    }


@router.get("/by-month")
@cache_response(ttl_seconds=600)
async def get_stats_by_month(db: AsyncSession = Depends(get_db)):
    """Get monthly image counts for charts."""
    try:
        from sqlalchemy import text
        stmt = text("""
            SELECT 
                to_char(capture_date, 'YYYY-MM') as month,
                count(*) as count,
                COALESCE(sum(exposure_time_seconds) / 3600, 0) as exposure_hours
            FROM images 
            WHERE capture_date IS NOT NULL
            GROUP BY to_char(capture_date, 'YYYY-MM')
            ORDER BY month DESC
            LIMIT 12
        """)
        result = await db.execute(stmt)
        rows = result.fetchall()
        
        return [
            {
                "month": row[0] if row[0] else "Unknown",
                "count": row[1],
                "exposure_hours": round(float(row[2] or 0), 1)
            }
            for row in rows[::-1]
        ]
    except Exception as e:
        print(f"Error in get_stats_by_month: {e}")
        return []


@router.get("/by-subtype")
@cache_response(ttl_seconds=600)
async def get_stats_by_subtype(db: AsyncSession = Depends(get_db)):
    """Get statistics grouped by image subtype."""
    stmt = select(
        Image.subtype,
        func.count(Image.id).label('count'),
        func.sum(Image.exposure_time_seconds).label('seconds')
    ).group_by(Image.subtype)
    
    result = await db.execute(stmt)
    rows = result.all()
    
    return [
        {
            "subtype": row.subtype.value if row.subtype else "Unknown",
            "count": row.count,
            "total_exposure_hours": round((row.seconds or 0) / 3600, 1)
        }
        for row in rows
    ]


@router.get("/by-format")
@cache_response(ttl_seconds=600)
async def get_stats_by_format(db: AsyncSession = Depends(get_db)):
    """Get statistics grouped by file format."""
    total_count = (await db.execute(select(func.count(Image.id)))).scalar() or 1
    
    stmt = select(
        Image.file_format,
        func.count(Image.id).label('count')
    ).group_by(Image.file_format)
    
    result = await db.execute(stmt)
    rows = result.all()
    
    return [
        {
            "format": row.file_format.value if row.file_format else "Unknown",
            "count": row.count,
            "percentage": round((row.count / total_count) * 100, 1)
        }
        for row in rows
    ]


@router.get("/top-objects")
@cache_response(ttl_seconds=600)
async def get_top_objects(db: AsyncSession = Depends(get_db)):
    """Get top imaged objects with counts."""
    stmt = select(
        ImageCatalogMatch.catalog_designation,
        ImageCatalogMatch.catalog_type,
        func.count(ImageCatalogMatch.image_id).label('image_count'),
        func.sum(Image.exposure_time_seconds).label('total_exposure_seconds')
    ).join(
        Image, Image.id == ImageCatalogMatch.image_id
    ).group_by(
        ImageCatalogMatch.catalog_designation,
        ImageCatalogMatch.catalog_type
    ).order_by(
        func.count(ImageCatalogMatch.image_id).desc()
    ).limit(10)
    
    result = await db.execute(stmt)
    rows = result.all()
    
    top_objects = []
    for row in rows:
        top_objects.append({
            "designation": row.catalog_designation,
            "name": "",  # Name would require joining catalog tables
            "image_count": row.image_count,
            "total_exposure_hours": round(float(row.total_exposure_seconds or 0) / 3600, 1)
        })
    
    return top_objects
