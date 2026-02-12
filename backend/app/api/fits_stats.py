from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Query, Depends
from sqlalchemy import func, case, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.image import Image
from app.schemas.fits_stats import (
    FitsStatsResponse, 
    FitsStatsOverview, 
    DistributionBin, 
    UsageStats, 
    SkyPoint
)

router = APIRouter()

@router.get("/", response_model=FitsStatsResponse)
async def get_fits_stats(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    cameras: Optional[List[str]] = Query(None),
    telescopes: Optional[List[str]] = Query(None),
    objects: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Get statistics derived from FITS image metadata.
    """
    # 1. Base Query Construction
    query = select(Image)
    conditions = []

    if date_from:
        conditions.append(Image.capture_date >= date_from)
    if date_to:
        conditions.append(Image.capture_date <= date_to)
    if cameras:
        # Use ILIKE for partial matching on all provided camera strings (OR if multiple)
        cam_conditions = [Image.camera_name.ilike(f"%{c}%") for c in cameras if c]
        if cam_conditions:
            conditions.append(or_(*cam_conditions))
    if telescopes:
        tel_conditions = [Image.telescope_name.ilike(f"%{t}%") for t in telescopes if t]
        if tel_conditions:
            conditions.append(or_(*tel_conditions))
    if objects:
        obj_conditions = [Image.object_name.ilike(f"%{o}%") for o in objects if o]
        if obj_conditions:
            conditions.append(or_(*obj_conditions))
    
    # 2. Overview Stats (Aggregations)
    # We need to apply filters to these aggregations
    
    # Helper to apply filters to a statement
    def apply_filters(stmt):
        if conditions:
            return stmt.where(and_(*conditions))
        return stmt

    # Overview: Total images, total exposure, avg exposure, total subs
    overview_stmt = select(
        func.count(Image.id),
        func.sum(Image.exposure_time_seconds),
        func.avg(Image.exposure_time_seconds),
        func.sum(case((Image.subtype == "SUB_FRAME", 1), else_=0))
    )
    overview_stmt = apply_filters(overview_stmt)
    overview_result = await db.execute(overview_stmt)
    total_count, total_seconds, avg_seconds, total_subs = overview_result.one()
    
    overview = FitsStatsOverview(
        total_images=total_count or 0,
        total_exposure_hours=(total_seconds or 0) / 3600,
        average_exposure_seconds=avg_seconds or 0.0,
        total_subs=total_subs or 0
    )

    # 3. Usage Stats Helpers
    async def get_usage_stats(column):
        stmt = select(column, func.count(column))\
            .group_by(column)\
            .order_by(func.count(column).desc())
        stmt = apply_filters(stmt)
        # Filter out nulls for cleaner stats
        stmt = stmt.where(column.isnot(None))
        result = await db.execute(stmt)
        return [UsageStats(name=str(row[0]), count=row[1]) for row in result.all()]

    camera_stats = await get_usage_stats(Image.camera_name)
    telescope_stats = await get_usage_stats(Image.telescope_name)
    filter_stats = await get_usage_stats(Image.filter_name)

    # 4. Exposure Distribution (Histogram)
    # Simple binning strategy: < 60s, 60-300s, 300-600s, 600-1200s, > 1200s
    # Using CASE statement for efficiency
    
    case_buckets = case(
        (Image.exposure_time_seconds < 60, '0-60'),
        (Image.exposure_time_seconds < 120, '60-120'),
        (Image.exposure_time_seconds < 300, '120-300'),
        else_='300+'
    ).label("bucket")

    dist_stmt = select(case_buckets, func.count(Image.id))\
        .group_by("bucket")
    dist_stmt = apply_filters(dist_stmt)
    dist_result = await db.execute(dist_stmt)
    
    # Map buckets back to DistributionBin structure (simplified for this specific binning)
    # Note: real bin_start/end might need more dynamic handling if requested, 
    # but fixed buckets are standard for this type of dashboard.
    # To map to DistributionBin(bin_start, bin_end, count), we interpret the labels:
    
    bucket_map = {
        '0-60': (0, 60),
        '60-120': (60, 120),
        '120-300': (120, 300),
        '300+': (300, 99999) # Arbitrary upper bound for display
    }
    
    distribution = []
    rows = dist_result.all()
    for row in rows:
        bucket_label = row[0]
        count = row[1]
        if bucket_label in bucket_map:
            start, end = bucket_map[bucket_label]
            distribution.append(DistributionBin(bin_start=start, bin_end=end, count=count))
            
    # Sort distribution by start time
    distribution.sort(key=lambda x: x.bin_start)

    # 4b. Rotation Distribution (Histogram)
    # Binning strategy: 15 degree increments from 0 to 360
    
    # Create buckets for rotation
    rotation_case = case(
        *[
            (and_(Image.rotation_degrees >= i, Image.rotation_degrees < i + 15), f"{i}-{i+15}")
            for i in range(0, 360, 15)
        ],
        else_='Unknown'
    ).label("rot_bucket")

    rot_stmt = select(rotation_case, func.count(Image.id))\
        .where(Image.rotation_degrees.isnot(None))\
        .group_by("rot_bucket")
    rot_stmt = apply_filters(rot_stmt)
    rot_result = await db.execute(rot_stmt)

    rot_distribution = []
    rot_rows = rot_result.all()
    
    # Initialize all bins with 0
    rot_map = {f"{i}-{i+15}": 0 for i in range(0, 360, 15)}
    
    for row in rot_rows:
        bucket_label = row[0]
        count = row[1]
        if bucket_label in rot_map:
            rot_map[bucket_label] = count
            
    # Convert to DistributionBin list
    for label, count in rot_map.items():
        start, end = map(int, label.split('-'))
        rot_distribution.append(DistributionBin(bin_start=start, bin_end=end, count=count))
            
    rot_distribution.sort(key=lambda x: x.bin_start)

    # 4c. Pixel Scale Distribution
    # Bins: 0-0.5, 0.5-1.0, 1.0-1.5, 1.5-2.0, 2.0-3.0, 3.0-5.0, 5.0+
    scale_case = case(
        (Image.pixel_scale_arcsec < 0.5, '0-0.5'),
        (Image.pixel_scale_arcsec < 1.0, '0.5-1.0'),
        (Image.pixel_scale_arcsec < 1.5, '1.0-1.5'),
        (Image.pixel_scale_arcsec < 2.0, '1.5-2.0'),
        (Image.pixel_scale_arcsec < 3.0, '2.0-3.0'),
        (Image.pixel_scale_arcsec < 5.0, '3.0-5.0'),
        else_='5.0+'
    ).label("scale_bucket")

    scale_stmt = select(scale_case, func.count(Image.id))\
        .where(Image.pixel_scale_arcsec.isnot(None))\
        .group_by("scale_bucket")
    scale_stmt = apply_filters(scale_stmt)
    scale_result = await db.execute(scale_stmt)
    
    scale_map = {
        '0-0.5': (0, 0.5),
        '0.5-1.0': (0.5, 1.0),
        '1.0-1.5': (1.0, 1.5),
        '1.5-2.0': (1.5, 2.0),
        '2.0-3.0': (2.0, 3.0),
        '3.0-5.0': (3.0, 5.0),
        '5.0+': (5.0, 999.0)
    }
    
    scale_distribution = []
    scale_rows = scale_result.all()
    
    # Initialize all bins with 0 if desired, or just sparse
    # Let's do sparse for now to avoid clutter if empty
    
    for row in scale_rows:
        bucket_label = row[0]
        count = row[1]
        if bucket_label in scale_map:
            start, end = scale_map[bucket_label]
            scale_distribution.append(DistributionBin(bin_start=start, bin_end=end, count=count))

    scale_distribution.sort(key=lambda x: x.bin_start)

    # 5. Sky Coverage
    # Return a simplified list of points. For large datasets, this should be sampled or clustered.
    # We will limit to 2000 points to avoid overwhelming the frontend.
    sky_stmt = select(Image.ra_center_degrees, Image.dec_center_degrees)
    sky_stmt = apply_filters(sky_stmt)
    sky_stmt = sky_stmt.where(Image.ra_center_degrees.isnot(None))\
        .where(Image.dec_center_degrees.isnot(None))\
        .order_by(Image.capture_date.desc())\
        .limit(10000)
        
    sky_result = await db.execute(sky_stmt)
    sky_coverage = [
        SkyPoint(ra=row[0], dec=row[1]) 
        for row in sky_result.all()
    ]

    return FitsStatsResponse(
        overview=overview,
        exposure_distribution=distribution,
        rotation_distribution=rot_distribution,
        pixel_scale_distribution=scale_distribution,
        cameras=camera_stats,
        telescopes=telescope_stats,
        filters=filter_stats,
        sky_coverage=sky_coverage
    )
