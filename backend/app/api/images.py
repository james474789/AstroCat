"""
Images API
Endpoints for listing, retrieving, and managing images.
"""

from typing import Optional, List, Union
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, desc, asc, func, text, nulls_last
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
import os
import math

from app.database import get_db
from app.models.image import Image, ImageFormat, ImageSubtype
from app.schemas.image import ImageDetail, ImageList, UpdateImageRequest
from app.schemas.image import ImageDetail, ImageList
from app.schemas.common import PaginatedResponse
from app.services.thumbnails import ThumbnailGenerator
from app.utils.path_security import validate_path_safety, sanitize_filename

import io
from fastapi.responses import StreamingResponse
from PIL import Image as PILImage

router = APIRouter()


def _build_image_query(
    subtype: Optional[ImageSubtype] = None,
    format: Optional[ImageFormat] = None,
    is_plate_solved: Optional[Union[bool, str]] = None,
    rating: Optional[int] = None,
    search: Optional[str] = None,
    object_name: Optional[str] = None,
    exposure_min: Optional[float] = None,
    exposure_max: Optional[float] = None,
    max_exposure_exclusive: bool = False,
    rotation_min: Optional[float] = None,
    rotation_max: Optional[float] = None,
    pixel_scale_min: Optional[float] = None,
    pixel_scale_max: Optional[float] = None,
    pixel_scale_max_exclusive: bool = False,
    filter: Optional[str] = None,
    camera: Optional[str] = None,
    ra: Optional[float] = None,
    dec: Optional[float] = None,
    radius: Optional[float] = None,
    path: Optional[str] = None,
    start_date: Optional[Union[datetime, date]] = None,
    end_date: Optional[Union[datetime, date]] = None,
    header_key: Optional[str] = None,
    header_value: Optional[str] = None,
    telescope: Optional[str] = None,
    gain_min: Optional[float] = None,
    gain_max: Optional[float] = None,
):
    """
    Helper to build the SQLAlchemy select statement for images based on filters.
    """
    stmt = select(Image).options(selectinload(Image.catalog_matches))
    
    # Filters
    if subtype:
        stmt = stmt.where(Image.subtype == subtype)
    if format:
        # Handle format variations (e.g. FITS/FIT, JPG/JPEG)
        aliases = {
            ImageFormat.FITS: [ImageFormat.FITS, ImageFormat.FIT],
            ImageFormat.FIT: [ImageFormat.FITS, ImageFormat.FIT],
            ImageFormat.TIFF: [ImageFormat.TIFF, ImageFormat.TIF],
            ImageFormat.TIF: [ImageFormat.TIFF, ImageFormat.TIF],
            ImageFormat.JPG: [ImageFormat.JPG, ImageFormat.JPEG],
            ImageFormat.JPEG: [ImageFormat.JPG, ImageFormat.JPEG],
        }
        
        target_formats = aliases.get(format, [format])
        stmt = stmt.where(Image.file_format.in_(target_formats))
    if is_plate_solved is not None:
        if is_plate_solved == 'solved':
            stmt = stmt.where(Image.astrometry_status == 'SOLVED')
        elif is_plate_solved == 'imported':
            stmt = stmt.where(Image.is_plate_solved == True).where(Image.astrometry_status != 'SOLVED')
        elif is_plate_solved == 'unsolved':
            stmt = stmt.where(Image.is_plate_solved == False)
        elif isinstance(is_plate_solved, bool):
            stmt = stmt.where(Image.is_plate_solved == is_plate_solved)
        elif is_plate_solved in ['true', '1']:
            stmt = stmt.where(Image.is_plate_solved == True)
        elif is_plate_solved in ['false', '0']:
            stmt = stmt.where(Image.is_plate_solved == False)
    
    if rating is not None:
        stmt = stmt.where(Image.rating >= rating)
    
    # Quick search - searches both file names and object names
    if search:
        from sqlalchemy import or_
        stmt = stmt.where(
            or_(
                Image.file_name.ilike(f"%{search}%"),
                Image.object_name.ilike(f"%{search}%")
            )
        )
    
    if object_name:
        from app.models.matches import ImageCatalogMatch
        from app.models.catalog import MessierCatalog, NGCCatalog, NamedStarCatalog
        
        # Search both header object name and matched catalog designations
        # Normalize by removing spaces for exact matching
        normalized_query = object_name.replace(" ", "")
        
        # 1. Search Matches (Messier, NGC, Named Stars)
        # We want images that have a match where the designation OR common name matches query
        # Use EXACT matching (case-insensitive) to prevent 'M1' from matching 'M13', 'M10', etc.
        
        # Subquery for Named Stars matching the query (exact match on designation, partial on common name)
        named_star_matches = select(NamedStarCatalog.designation).where(
            (func.replace(NamedStarCatalog.designation, ' ', '').ilike(normalized_query)) |
            (NamedStarCatalog.common_name.ilike(f"%{object_name}%"))
        )
        
        stmt = stmt.outerjoin(ImageCatalogMatch).where(
            (func.replace(Image.object_name, ' ', '').ilike(normalized_query)) | 
            (func.replace(ImageCatalogMatch.catalog_designation, ' ', '').ilike(normalized_query)) |
            (ImageCatalogMatch.catalog_designation.in_(named_star_matches))
        ).distinct()
    if exposure_min is not None:
        stmt = stmt.where(Image.exposure_time_seconds >= exposure_min)
    if exposure_max is not None:
        if max_exposure_exclusive:
            stmt = stmt.where(Image.exposure_time_seconds < exposure_max)
        else:
            stmt = stmt.where(Image.exposure_time_seconds <= exposure_max)
    if camera:
        stmt = stmt.where(Image.camera_name.ilike(f"%{camera}%"))

    if telescope:
        stmt = stmt.where(Image.telescope_name.ilike(f"%{telescope}%"))

    if gain_min is not None:
        stmt = stmt.where(Image.gain >= gain_min)
    if gain_max is not None:
        stmt = stmt.where(Image.gain <= gain_max)

    if filter:
        stmt = stmt.where(Image.filter_name.ilike(f"%{filter}%"))

    if rotation_min is not None:
        stmt = stmt.where(Image.rotation_degrees >= rotation_min)
    if rotation_max is not None:
         stmt = stmt.where(Image.rotation_degrees < rotation_max)
    
    if pixel_scale_min is not None:
        stmt = stmt.where(Image.pixel_scale_arcsec >= pixel_scale_min)
    if pixel_scale_max is not None:
        if pixel_scale_max_exclusive:
            stmt = stmt.where(Image.pixel_scale_arcsec < pixel_scale_max)
        else:
            stmt = stmt.where(Image.pixel_scale_arcsec <= pixel_scale_max)
        
    if start_date:
        stmt = stmt.where(Image.capture_date >= start_date)
    if end_date:
        stmt = stmt.where(Image.capture_date <= end_date)
        
    if path:
        # Filter by path prefix
        stmt = stmt.where(Image.file_path.startswith(path))
        
    # Spatial Search
    if ra is not None and dec is not None:
        # Default radius 1.0 degree if not specified
        search_radius = radius if radius is not None else 1.0
        # Approx meters per degree
        radius_meters = search_radius * 111320
        
        stmt = stmt.where(
            text("""
            ST_DWithin(
                center_location, 
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography, 
                :radius_meters
            )
            """)
        ).params(ra=ra, dec=dec, radius_meters=radius_meters)

    # FITS Header Search (JSONB)
    if header_key:
        # Cast to text to allow insensitive search on values
        # This assumes raw_header is indexed or small enough for scan
        if header_value:
            # Search for specific key having specific value
            stmt = stmt.where(
                Image.raw_header[header_key].astext.ilike(f"%{header_value}%")
            )
        else:
            # Search for key existence
            stmt = stmt.where(Image.raw_header.has_key(header_key))
            
    return stmt


@router.get("/", response_model=PaginatedResponse[ImageList])
async def list_images(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    subtype: Optional[ImageSubtype] = None,
    format: Optional[ImageFormat] = None,
    is_plate_solved: Optional[str] = Query(None, description="Filter by plate solve status: 'solved', 'imported', 'unsolved', or boolean"),
    rating: Optional[int] = Query(None, ge=0, le=5, description="Minimum rating (0-5 stars)"),
    search: Optional[str] = Query(None, description="Search file names and object names"),
    object_name: Optional[str] = None,
    exposure_min: Optional[float] = None,
    exposure_max: Optional[float] = None,
    max_exposure_exclusive: bool = False,
    rotation_min: Optional[float] = None,
    rotation_max: Optional[float] = None,
    pixel_scale_min: Optional[float] = None,
    pixel_scale_max: Optional[float] = None,
    pixel_scale_max_exclusive: bool = False,
    filter: Optional[str] = None,
    camera: Optional[str] = None,
    ra: Optional[float] = Query(None, description="RA in degrees"),
    dec: Optional[float] = Query(None, description="Dec in degrees"),
    radius: Optional[float] = Query(None, description="Radius in degrees"),
    path: Optional[str] = Query(None, description="Filter by file path prefix"),
    start_date: Optional[Union[datetime, date]] = Query(None, description="Start of date range"),
    end_date: Optional[Union[datetime, date]] = Query(None, description="End of date range"),
    header_key: Optional[str] = None,
    header_value: Optional[str] = None,
    sort_by: str = Query("capture_date", description="Field to sort by"),
    sort_order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    telescope: Optional[str] = None,
    gain_min: Optional[float] = None,
    gain_max: Optional[float] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    List images with filtering and pagination.
    """
    # Build query using helper
    stmt = _build_image_query(
        subtype=subtype,
        format=format,
        is_plate_solved=is_plate_solved,
        rating=rating,
        search=search,
        object_name=object_name,
        exposure_min=exposure_min,
        exposure_max=exposure_max,
        max_exposure_exclusive=max_exposure_exclusive,
        rotation_min=rotation_min,
        rotation_max=rotation_max,
        pixel_scale_min=pixel_scale_min,
        pixel_scale_max=pixel_scale_max,
        pixel_scale_max_exclusive=pixel_scale_max_exclusive,
        filter=filter,
        camera=camera,
        ra=ra,
        dec=dec,
        radius=radius,
        path=path,
        start_date=start_date,
        end_date=end_date,
        header_key=header_key,
        header_value=header_value,
        telescope=telescope,
        gain_min=gain_min,
        gain_max=gain_max
    )
    
    # Count total
    # We use a subquery to correctly handle the distinct() and joins if present
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await db.scalar(count_stmt)
    total = total or 0
    
    # Dynamic Sorting
    sort_col = getattr(Image, sort_by, Image.capture_date)
    
    if sort_order.lower() == 'asc':
        stmt = stmt.order_by(nulls_last(sort_col.asc()))
    else:
        stmt = stmt.order_by(nulls_last(sort_col.desc()))
    
    # Pagination
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    
    result = await db.execute(stmt)
    images = result.scalars().all()
    
    return {
        "items": images,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if page_size > 0 else 1
    }



@router.get("/export_csv")
async def export_images_csv(
    subtype: Optional[ImageSubtype] = None,
    format: Optional[ImageFormat] = None,
    is_plate_solved: Optional[str] = Query(None, description="Filter by plate solve status: 'solved', 'imported', 'unsolved', or boolean"),
    rating: Optional[int] = Query(None, ge=0, le=5, description="Minimum rating (0-5 stars)"),
    search: Optional[str] = Query(None, description="Search file names and object names"),
    object_name: Optional[str] = None,
    exposure_min: Optional[float] = None,
    exposure_max: Optional[float] = None,
    max_exposure_exclusive: bool = False,
    rotation_min: Optional[float] = None,
    rotation_max: Optional[float] = None,
    pixel_scale_min: Optional[float] = None,
    pixel_scale_max: Optional[float] = None,
    pixel_scale_max_exclusive: bool = False,
    filter: Optional[str] = None,
    camera: Optional[str] = None,
    ra: Optional[float] = Query(None, description="RA in degrees"),
    dec: Optional[float] = Query(None, description="Dec in degrees"),
    radius: Optional[float] = Query(None, description="Radius in degrees"),
    path: Optional[str] = Query(None, description="Filter by file path prefix"),
    start_date: Optional[Union[datetime, date]] = Query(None, description="Start of date range"),
    end_date: Optional[Union[datetime, date]] = Query(None, description="End of date range"),
    header_key: Optional[str] = None,
    header_value: Optional[str] = None,
    sort_by: str = Query("capture_date", description="Field to sort by"),
    sort_order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    telescope: Optional[str] = None,
    gain_min: Optional[float] = None,
    gain_max: Optional[float] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Export matching images with comprehensive metadata to CSV.
    """
    import csv
    import io
    
    # 1. Build Query (same as list_images)
    stmt = _build_image_query(
        subtype=subtype,
        format=format,
        is_plate_solved=is_plate_solved,
        rating=rating,
        search=search,
        object_name=object_name,
        exposure_min=exposure_min,
        exposure_max=exposure_max,
        max_exposure_exclusive=max_exposure_exclusive,
        rotation_min=rotation_min,
        rotation_max=rotation_max,
        pixel_scale_min=pixel_scale_min,
        pixel_scale_max=pixel_scale_max,
        pixel_scale_max_exclusive=pixel_scale_max_exclusive,
        filter=filter,
        camera=camera,
        ra=ra,
        dec=dec,
        radius=radius,
        path=path,
        start_date=start_date,
        end_date=end_date,
        header_key=header_key,
        header_value=header_value,
        telescope=telescope,
        gain_min=gain_min,
        gain_max=gain_max
    )
    
    # Apply Sorting
    sort_col = getattr(Image, sort_by, Image.capture_date)
    if sort_order.lower() == 'asc':
        stmt = stmt.order_by(nulls_last(sort_col.asc()))
    else:
        stmt = stmt.order_by(nulls_last(sort_col.desc()))
        
    # Execute (No pagination - get all)
    result = await db.execute(stmt)
    images = result.scalars().all()
    
    # 2. Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header Columns
    headers = [
        "ID", "File Name", "File Path", "Format", "Size (Bytes)", "Hash",
        "Width", "Height",
        "Subtype", "Plate Solved", "Solve Source", "Solve Provider",
        "RA", "Dec", "Field Radius", "Pixel Scale", "Rotation",
        "Exposure (s)", "Capture Date",
        "Camera", "Telescope", "Filter", "Gain", "Binning",
        "Rating", "Manual Rating", "Aperture", "Focal Length", "35mm Equiv",
        "White Balance", "Metering", "Flash", "Lens",
        "Observer", "Object (Header)", "Site", "Site Lat", "Site Lon",
        "Astrometry Status", "Submission ID", "Job ID",
        "Indexed At", "Updated At",
        "Detected Objects"
    ]
    
    writer.writerow(headers)
    
    # Data
    for img in images:
        # Collect detected objects
        detected_objects = []
        if img.catalog_matches:
            # Sort by designation for consistency
            sorted_matches = sorted([m.catalog_designation for m in img.catalog_matches])
            # Deduplicate just in case
            seen = set()
            detected_objects = [x for x in sorted_matches if not (x in seen or seen.add(x))]
            
        detected_objects_str = ", ".join(detected_objects)
        
        row = [
            img.id,
            img.file_name,
            img.file_path,
            img.file_format.value if img.file_format else "",
            img.file_size_bytes,
            img.file_hash or "",
            img.width_pixels,
            img.height_pixels,
            img.subtype.value if img.subtype else "",
            img.is_plate_solved,
            img.plate_solve_source or "",
            img.plate_solve_provider or "",
            round(img.ra_center_degrees, 6) if img.ra_center_degrees is not None else "",
            round(img.dec_center_degrees, 6) if img.dec_center_degrees is not None else "",
            round(img.field_radius_degrees, 4) if img.field_radius_degrees is not None else "",
            round(img.pixel_scale_arcsec, 4) if img.pixel_scale_arcsec is not None else "",
            round(img.rotation_degrees, 3) if img.rotation_degrees is not None else "",
            img.exposure_time_seconds,
            img.capture_date.isoformat() if img.capture_date else "",
            img.camera_name or "",
            img.telescope_name or "",
            img.filter_name or "",
            img.gain,
            img.binning or "",
            img.rating,
            img.rating_manually_edited,
            img.aperture,
            img.focal_length,
            img.focal_length_35mm,
            img.white_balance or "",
            img.metering_mode or "",
            img.flash_fired,
            img.lens_model or "",
            img.observer_name or "",
            img.object_name or "",
            img.site_name or "",
            img.site_latitude,
            img.site_longitude,
            img.astrometry_status,
            img.astrometry_submission_id or "",
            img.astrometry_job_id or "",
            img.indexed_at.isoformat() if img.indexed_at else "",
            img.updated_at.isoformat() if img.updated_at else "",
            detected_objects_str
        ]
        
        writer.writerow(row)
        
    output.seek(0)
    
    filename = f"astrocat_export_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/{image_id}", response_model=ImageDetail)
async def get_image(image_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single image by ID."""
    stmt = select(Image).options(selectinload(Image.catalog_matches)).where(Image.id == image_id)
    result = await db.execute(stmt)
    image = result.scalar_one_or_none()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Check for annotated image existence
    from app.config import settings
    annotated_path = os.path.join(settings.thumbnail_cache_path, f"annotated_{image_id}.jpg")
    image.has_annotated_image = os.path.exists(annotated_path)

    # If image is plate solved, calculate pixel coordinates for matches
    if image.is_plate_solved and image.catalog_matches:
        try:
            from astropy.wcs import WCS
            from astropy.io import fits
            import json

            wcs = None
            
            # 0. Try to use stored WCS Header (Full SIP Solution from Astrometry.net) - HIGHEST PRIORITY
            if hasattr(image, 'wcs_header') and image.wcs_header:
                try:
                    # Convert JSONB dict to FITS Header object
                    header = fits.Header()
                    for k, v in image.raw_header.items() if (image.raw_header and isinstance(image.raw_header, dict)) else {}:
                        if isinstance(v, (int, float, str, bool)):
                            header[k] = v
                    # Overlay specifically the WCS parts from wcs_header
                    for k, v in image.wcs_header.items():
                        if isinstance(v, (int, float, str, bool)):
                            header[k] = v
                            
                    wcs = WCS(header)
                except Exception as e:
                    print(f"Failed to create WCS from stored wcs_header: {e}")

            # 0.5 Try to use raw_header if it has WCS (Handles FITS files solved externally)
            if (wcs is None) and image.raw_header:
                try:
                    # Check for SIP coefficients or standard WCS in the original header
                    if "CRVAL1" in image.raw_header and ("CD1_1" in image.raw_header or "CDELT1" in image.raw_header):
                         header = fits.Header()
                         for k, v in image.raw_header.items():
                             if isinstance(v, (int, float, str, bool)):
                                 header[k] = v
                         
                         wcs_test = WCS(header)
                         if wcs_test.is_celestial:
                              wcs = wcs_test
                except:
                    pass

            # 1. Try to construct WCS from DB columns (Preferred for solved images)
            if (wcs is None) and all(v is not None for v in [image.ra_center_degrees, image.dec_center_degrees, image.pixel_scale_arcsec, image.width_pixels, image.height_pixels]):
                try:
                    wcs = WCS(naxis=2)
                    wcs.wcs.crpix = [image.width_pixels / 2, image.height_pixels / 2]
                    wcs.wcs.crval = [image.ra_center_degrees, image.dec_center_degrees]
                    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
                    
                    # Convert arcsec/pixel to deg/pixel
                    scale = image.pixel_scale_arcsec / 3600.0
                    
                    # Handle rotation if present
                    # User specified "deg E of N - clockwise"?
                    # Testing reveals that positive rotation values in DB need to be applied strictly as-is
                    # to achieve the correct CCW rotation in the Web-Standard frame.
                    # e.g. 270 deg (North Right) -> +270 applied -> North Right.
                    rot_raw = image.rotation_degrees or 0.0
                    rot = rot_raw
                    
                    import math
                    rad = math.radians(rot)
                    cos_a = math.cos(rad)
                    sin_a = math.sin(rad)
                    
                    # Get Parity from header (defaults to 1 for Normal)
                    parity = 1
                    if image.raw_header and isinstance(image.raw_header, dict):
                         parity = image.raw_header.get('astrometry_parity', 1)
                    
                    # Scale logic (Web Standard - Top Left Origin):
                    # Parity 1 (Normal): East is Left. 
                    # Standard FITS (CD1_1 < 0) implies East Left (Right is West).
                    # Deriv: xi increases East (RA+).
                    # Move Right (x+) -> West (RA-). -> xi decreases.
                    # xi = s_x * x. 
                    # Neg = s_x * Pos. -> s_x must be Negative.
                    
                    s_x = -scale * parity
                    s_y = -scale
                    
                    wcs.wcs.cd = [
                        [s_x * cos_a, -s_y * sin_a],
                        [s_x * sin_a, s_y * cos_a]
                    ]

                except Exception as e:
                    print(f"Failed to create WCS from DB: {e}")
                    wcs = None

            # 2. Fallback: Construct WCS from raw header if DB failed
            if (wcs is None or not wcs.is_celestial) and image.raw_header:
                # Convert JSONB dict to FITS Header object
                # We need to ensure values are proper types (float/int/str)
                # This is a best-effort conversion
                try:
                    # Create a minimal header for WCS
                    header = fits.Header()
                    for k, v in image.raw_header.items():
                        # Skip history/comment for speed/safety being dicts/lists sometimes
                        if k.upper() in ['HISTORY', 'COMMENT']:
                            continue
                            
                        # Handle potential JSON types
                        if isinstance(v, (int, float, str, bool)):
                            header[k] = v
                    
                    wcs = WCS(header)
                except Exception as e:
                    print(f"Failed to create WCS from header: {e}")
            
            if wcs and wcs.is_celestial:
                # We need to look up the coordinates for each match
                # Since matches only store designation, we need to query the catalog tables
                # Optimization: Identify all unique designations and fetch coordinates in batch
                # But for single image view, N is small (~10-50), so straight queries or joins might be improved later
                # For now, let's just make sure we have the coordinates.
                # EDIT: Wait, the match object DOES NOT have coordinates on it.
                # We need to fetch the coordinates.
                
                # Fetch coordinates for matches
                # We can't easily do this without a join in the initial query or separate queries.
                # Let's do separate queries for now to be safe and simple.
                from app.models.catalog import MessierCatalog, NGCCatalog, NamedStarCatalog
                from app.models.matches import CatalogType
                
                # Collect designations
                messier_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.MESSIER]
                ngc_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.NGC]
                star_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.NAMED_STAR]
                
                coords_map = {} # (type, desig) -> (ra, dec)
                
                if messier_desigs:
                    m_objs = await db.execute(select(MessierCatalog).where(MessierCatalog.designation.in_(messier_desigs)))
                    for obj in m_objs.scalars():
                        coords_map[(CatalogType.MESSIER, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)
                        
                if ngc_desigs:
                    n_objs = await db.execute(select(NGCCatalog).where(NGCCatalog.designation.in_(ngc_desigs)))
                    for obj in n_objs.scalars():
                        coords_map[(CatalogType.NGC, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)

                if star_desigs:
                    # Also try normalized versions in case of old "no-space" matches
                    norm_map = {d.replace(" ", "").upper(): d for d in star_desigs}
                    
                    s_objs = await db.execute(select(NamedStarCatalog).where(
                        (NamedStarCatalog.designation.in_(star_desigs)) |
                        (func.replace(NamedStarCatalog.designation, ' ', '').in_(list(norm_map.keys())))
                    ))
                    for obj in s_objs.scalars():
                        # Map back to the EXACT designation used in the match record
                        # Direct match
                        if obj.designation in star_desigs:
                            coords_map[(CatalogType.NAMED_STAR, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)
                        
                        # Normalized match for older records
                        norm_o = obj.designation.replace(" ", "").upper()
                        if norm_o in norm_map:
                            coords_map[(CatalogType.NAMED_STAR, norm_map[norm_o])] = (obj.ra_degrees, obj.dec_degrees)
                
                # Calculate pixels
                for match in image.catalog_matches:
                    key = (match.catalog_type, match.catalog_designation)
                    if key in coords_map:
                        ra, dec = coords_map[key]
                        x, y = wcs.world_to_pixel_values(ra, dec)
                        
                        # Invert Y is NO LONGER NECESSARY as WCS is Top-Left (Web Standard)
                        # We use s_y = -scale to generate Top-Down coordinates directly.

                        
                        # Check bounds (roughly)
                        # allow some margin for objects just outside field
                        margin = 100
                        if -margin <= x <= image.width_pixels + margin and -margin <= y <= image.height_pixels + margin:
                            match.pixel_x = float(x)
                            match.pixel_y = float(y)
                        
                        # Populate Sky Coords
                        match.ra_degrees = float(ra)
                        match.dec_degrees = float(dec)

        except Exception as e:
            print(f"Error calculating WCS overlays: {e}")
            # Continue without overlays rather than failing request

    return image


@router.put("/{image_id}", response_model=ImageDetail)
async def update_image(
    image_id: int,
    update_data: UpdateImageRequest,
    db: AsyncSession = Depends(get_db)
):
    """Update image metadata (e.g. subtype, rating)."""
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Check for annotated image existence
    from app.config import settings
    annotated_path = os.path.join(settings.thumbnail_cache_path, f"annotated_{image_id}.jpg")
    image.has_annotated_image = os.path.exists(annotated_path)
    
    # Update subtype if provided
    subtype_changed = False
    if update_data.subtype is not None:
        # If subtype changed, trigger thumbnail regeneration
        if image.subtype != update_data.subtype:
            subtype_changed = True
        image.subtype = update_data.subtype
    
    # Update rating if provided
    if update_data.rating is not None:
        image.rating = update_data.rating
    
    # Update rating_manually_edited flag if provided
    if update_data.rating_manually_edited is not None:
        image.rating_manually_edited = update_data.rating_manually_edited
    
    # Update plate_solve_source if provided
    if update_data.plate_solve_source is not None:
        image.plate_solve_source = update_data.plate_solve_source
        
    await db.commit()
    await db.refresh(image)

    # Trigger thumbnail regeneration if subtype changed (after commit so worker sees new value)
    if subtype_changed:
        try:
            from app.tasks.thumbnails import generate_thumbnail
            generate_thumbnail.delay(image.id, force=True)
        except Exception as e:
            print(f"Failed to trigger thumbnail regeneration: {e}")
    
    # Trigger background sync to filesystem
    try:
        from app.tasks.sync_ratings import sync_ratings_to_filesystem
        sync_ratings_to_filesystem.delay()
    except Exception as e:
        print(f"Failed to trigger rating sync: {e}")
    
    # Reload with relationships for proper serialization (same as get_image)
    stmt = select(Image).options(selectinload(Image.catalog_matches)).where(Image.id == image_id)
    result = await db.execute(stmt)
    image = result.scalar_one_or_none()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # If image is plate solved, calculate pixel coordinates for matches
    if image.is_plate_solved and image.catalog_matches:
        try:
            from astropy.wcs import WCS
            from astropy.io import fits
            import json

            wcs = None
            
            # 1. Try to construct WCS from DB columns (Preferred for solved images)
            if all(v is not None for v in [image.ra_center_degrees, image.dec_center_degrees, image.pixel_scale_arcsec, image.width_pixels, image.height_pixels]):
                try:
                    wcs = WCS(naxis=2)
                    wcs.wcs.crpix = [image.width_pixels / 2.0, image.height_pixels / 2.0]
                    wcs.wcs.crval = [image.ra_center_degrees, image.dec_center_degrees]
                    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
                    
                    scale = image.pixel_scale_arcsec / 3600.0
                    rot_raw = image.rotation_degrees or 0.0
                    rot = rot_raw
                    
                    import math
                    rad = math.radians(rot)
                    cos_a = math.cos(rad)
                    sin_a = math.sin(rad)
                    
                    parity = 1
                    if image.raw_header and isinstance(image.raw_header, dict):
                         parity = image.raw_header.get('astrometry_parity', 1)
                    
                    s_x = -scale * parity
                    s_y = -scale
                    
                    wcs.wcs.cd = [
                        [s_x * cos_a, -s_y * sin_a],
                        [s_x * sin_a, s_y * cos_a]
                    ]

                except Exception as e:
                    print(f"Failed to create WCS from DB: {e}")
                    wcs = None

            if (wcs is None or not wcs.is_celestial) and image.raw_header:
                try:
                    header = fits.Header()
                    for k, v in image.raw_header.items():
                        if k.upper() in ['HISTORY', 'COMMENT']:
                            continue
                        if isinstance(v, (int, float, str, bool)):
                            header[k] = v
                    
                    wcs = WCS(header)
                except Exception as e:
                    print(f"Failed to create WCS from header: {e}")
            
            if wcs and wcs.is_celestial:
                from app.models.catalog import MessierCatalog, NGCCatalog, NamedStarCatalog
                from app.models.matches import CatalogType
                
                messier_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.MESSIER]
                ngc_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.NGC]
                star_desigs = [m.catalog_designation for m in image.catalog_matches if m.catalog_type == CatalogType.NAMED_STAR]
                
                coords_map = {}
                
                if messier_desigs:
                    m_objs = await db.execute(select(MessierCatalog).where(MessierCatalog.designation.in_(messier_desigs)))
                    for obj in m_objs.scalars():
                        coords_map[(CatalogType.MESSIER, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)
                        
                if ngc_desigs:
                    n_objs = await db.execute(select(NGCCatalog).where(NGCCatalog.designation.in_(ngc_desigs)))
                    for obj in n_objs.scalars():
                        coords_map[(CatalogType.NGC, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)

                if star_desigs:
                    norm_map = {d.replace(" ", "").upper(): d for d in star_desigs}
                    
                    s_objs = await db.execute(select(NamedStarCatalog).where(
                        (NamedStarCatalog.designation.in_(star_desigs)) |
                        (func.replace(NamedStarCatalog.designation, ' ', '').in_(list(norm_map.keys())))
                    ))
                    for obj in s_objs.scalars():
                        if obj.designation in star_desigs:
                            coords_map[(CatalogType.NAMED_STAR, obj.designation)] = (obj.ra_degrees, obj.dec_degrees)
                        
                        norm_o = obj.designation.replace(" ", "").upper()
                        if norm_o in norm_map:
                            coords_map[(CatalogType.NAMED_STAR, norm_map[norm_o])] = (obj.ra_degrees, obj.dec_degrees)
                
                for match in image.catalog_matches:
                    key = (match.catalog_type, match.catalog_designation)
                    if key in coords_map:
                        ra, dec = coords_map[key]
                        x, y = wcs.world_to_pixel_values(ra, dec)
                        
                        margin = 100
                        if -margin <= x <= image.width_pixels + margin and -margin <= y <= image.height_pixels + margin:
                            match.pixel_x = float(x)
                            match.pixel_y = float(y)
                        
                        match.ra_degrees = float(ra)
                        match.dec_degrees = float(dec)

        except Exception as e:
            print(f"Error calculating WCS overlays: {e}")

    return image


@router.get("/{image_id}/thumbnail")
async def get_thumbnail(
    image_id: int, 
    stretched: bool = Query(False, description="Apply STF stretch for better visibility"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the thumbnail file for an image. 
    If stretched=True, generates a temporary preview with STF applied (streams response).
    """
    from app.config import settings
    
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # 1. Stretched Preview (On-Demand, Streaming)
    if stretched:
        if not os.path.exists(image.file_path):
             raise HTTPException(status_code=404, detail="Source file missing, cannot stretch")
             
        try:
            # Check for subframe status to enable linear extraction for proper STF
            is_subframe = (image.subtype == ImageSubtype.SUB_FRAME)
            
            # Load with STF enabled
            img = ThumbnailGenerator.load_source_image(image.file_path, is_subframe=is_subframe, apply_stf=True)
            
            if not img:
                 raise HTTPException(status_code=500, detail="Failed to generate preview")
                 
            # Resize for web view (similar to thumbnail size but maybe slightly larger dynamic?)
            # Standard thumbnail size is usually fine (800x800 max)
            max_size = (1024, 1024) 
            img.thumbnail(max_size, PILImage.Resampling.LANCZOS)
            
            # Stream response
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            
            return StreamingResponse(
                buf, 
                media_type="image/jpeg"
            )
            
        except Exception as e:
            print(f"Error generating stretched preview: {e}")
            raise HTTPException(status_code=500, detail="Error generating preview")

    # 2. Standard Cached Thumbnail (Linear/Default)
    if not image.thumbnail_path or not os.path.exists(image.thumbnail_path):
        # Optional: Auto-generate if missing? 
        # For now, stick to existing behavior (404 if missing, rely on background task)
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    
    # Validate thumbnail path is within allowed cache directory
    allowed_paths = [settings.thumbnail_cache_path]
    if not validate_path_safety(image.thumbnail_path, allowed_paths):
        raise HTTPException(status_code=403, detail="Access denied: Invalid thumbnail path")
        
    return FileResponse(image.thumbnail_path)


@router.post("/{image_id}/thumbnail/regenerate")
async def regenerate_thumbnail_endpoint(image_id: int, db: AsyncSession = Depends(get_db)):
    """Force regenerate the thumbnail for an image."""
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    from app.tasks.thumbnails import generate_thumbnail
    # We trigger the background task with force=True
    # (Although the task doesn't explicitly check it yet, ThumbnailGenerator.generate will overwrite the file)
    generate_thumbnail.delay(image_id, force=True)
    
    return {"status": "queued", "message": "Thumbnail regeneration task queued"}


@router.get("/{image_id}/annotated")
async def get_annotated_image(image_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get the annotated image (from Astrometry.net) if available.
    """
    from app.config import settings
    
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    annotated_path = os.path.join(settings.thumbnail_cache_path, f"annotated_{image_id}.jpg")
    
    if not os.path.exists(annotated_path):
        raise HTTPException(status_code=404, detail="Annotated image not found")
    
    # Validate annotated path is within allowed cache directory
    allowed_paths = [settings.thumbnail_cache_path]
    if not validate_path_safety(annotated_path, allowed_paths):
        raise HTTPException(status_code=403, detail="Access denied: Invalid annotated path")
        
    return FileResponse(annotated_path)


@router.get("/{image_id}/fits")
async def get_fits_header(image_id: int, db: AsyncSession = Depends(get_db)):
    """Get the full FITS header for an image."""
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    if not image.raw_header:
        raise HTTPException(status_code=404, detail="No FITS header data available")
        
    return image.raw_header


@router.get("/{image_id}/download")
async def download_image(
    image_id: int, 
    format: str = Query("jpg", description="Download format: 'original' or 'jpg'"),
    db: AsyncSession = Depends(get_db)
):
    """
    Download image file.
    If format='jpg', converts FITS/RAW to JPEG (preview quality).
    If format='original', returns the original file.
    """
    from app.config import settings
    
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    if not os.path.exists(image.file_path):
        raise HTTPException(status_code=404, detail="Source file missing")
    
    # Validate image path is within allowed directories
    allowed_paths = settings.image_paths_list
    if not validate_path_safety(image.file_path, allowed_paths):
        raise HTTPException(status_code=403, detail="Access denied: Invalid file path")

    if format == 'original':
        # Sanitize the filename for safe download
        safe_filename = sanitize_filename(os.path.basename(image.file_path))
        return FileResponse(
            image.file_path, 
            filename=safe_filename,
            media_type='application/octet-stream'
        )
    
    # Generate JPG on the fly
    try:
        # We use the service to load/process the image
        img = ThumbnailGenerator.load_source_image(image.file_path)
        if not img:
            raise HTTPException(status_code=500, detail="Failed to process image")
            
        # Save to buffer
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        
        filename = os.path.splitext(image.file_name)[0] + ".jpg"
        
        return StreamingResponse(
            buf, 
            media_type="image/jpeg",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
            
    except Exception as e:
        print(f"Download error: {e}")
        raise HTTPException(status_code=500, detail="Error generating download")



@router.post("/{image_id}/rescan", status_code=202)
async def rescan_image(image_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger Astrometry.net rescan."""
    # 1. Fetch Image
    stmt = select(Image).where(Image.id == image_id)
    result = await db.execute(stmt)
    image = result.scalar_one_or_none()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check if already processing?
    # if image.astrometry_status in ['SUBMITTED', 'PROCESSING']:
    #      return {"status": "processing", "message": "Already processing", "start_rescan": False}
    
    # Import services
    from app.services.astrometry_service import AstrometryService
    from app.config import settings
    # Import tasks
    from app.tasks.astrometry import monitor_astrometry_task
    
    import redis
    import json
    import logging

    logger = logging.getLogger(__name__)
    
    # Check Redis for system setting
    provider = "nova"
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        sys_settings = r.get("system_settings")
        if sys_settings:
            provider = json.loads(sys_settings).get("astrometry_provider", "nova")
    except Exception as e:
        print(f"Failed to read settings from Redis: {e}")

    api_key = settings.astrometry_api_key
    base_url = "http://nova.astrometry.net/api" # Default
    
    if provider == "local":
        api_key = settings.local_astrometry_api_key
        base_url = settings.local_astrometry_url
        if not api_key or not base_url:
             # Fallback
             provider = "nova"
             api_key = settings.astrometry_api_key
    
    if not api_key:
         raise HTTPException(status_code=500, detail="Astrometry API key not configured")

    # 2. Synchronous Upload
    try:
        # Check previous status before updating
        previous_status = image.astrometry_status
        
        # Update status first to indicate activity
        image.astrometry_status = "SUBMITTED"
        image.plate_solve_provider = provider.upper()
        await db.commit()
        
        # Prepare hints if available
        hints = {}
        
        # Force blind solve if previous attempt failed
        if previous_status == 'FAILED':
            logger.info(f"[ASTROMETRY] Previous status was FAILED. Forcing blind solve (no hints).")
        else:
            if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
                hints["center_ra"] = image.ra_center_degrees
                hints["center_dec"] = image.dec_center_degrees
                # Default radius if not specified (5 degrees is generous but safe)
                hints["radius"] = image.field_radius_degrees or 5.0
                logger.info(f"[ASTROMETRY] Using position hints for image {image_id}: {hints}")
            
            if image.pixel_scale_arcsec is not None:
                hints["scale_units"] = "arcsecperpix"
                hints["scale_lower"] = image.pixel_scale_arcsec * 0.9
                hints["scale_upper"] = image.pixel_scale_arcsec * 1.1
                logger.info(f"[ASTROMETRY] Using scale hints for image {image_id}: {image.pixel_scale_arcsec} arcsec/pix")
        
        logger.info(f"[ASTROMETRY] Rescan initiated for Image {image_id} via {provider} (URL: {base_url})")
        
        # Upload
        sub_data = await AstrometryService.upload_file(image.file_path, api_key, base_url, hints=hints)
        submission_id = sub_data.get("subid")
        
        if not submission_id:
            raise ValueError(f"No submission ID returned: {sub_data}")
            
        # Update DB
        image.astrometry_submission_id = str(submission_id)
        # Status remains SUBMITTED until monitor picks it up or we set it here
        await db.commit()
        
        logger.info(f"[ASTROMETRY] Submission {submission_id} created for Image {image.id}. Queuing monitor task.")
        
        # 3. Queue Monitor Task
        monitor_astrometry_task.delay(submission_id, image.id)
        
        return {
            "status": "submitted", 
            "message": f"Image uploaded to {provider}", 
            "submission_id": submission_id
        }
        
    except Exception as e:
        logger.error(f"[ASTROMETRY] Rescan failed for Image {image_id}: {e}")
        image.astrometry_status = "FAILED"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to upload: {str(e)}")


@router.post("/{image_id}/fetch_annotation")
async def fetch_annotation(image_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger download of annotated image from Astrometry.net."""
    image = await db.get(Image, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if not image.astrometry_job_id:
        raise HTTPException(status_code=400, detail="Image has no astrometry job ID")
    
    from app.services.astrometry_service import AstrometryService
    from app.config import settings
    
    # Base URL determination
    base_url = "http://nova.astrometry.net/api"
    if image.plate_solve_provider == "LOCAL":
        base_url = settings.local_astrometry_url or base_url
    
    annotated_path = os.path.join(settings.thumbnail_cache_path, f"annotated_{image_id}.jpg")
    
    try:
        await AstrometryService.download_annotated_image(image.astrometry_job_id, annotated_path, base_url)
        return {"status": "success", "message": "Annotated image downloaded"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to download: {str(e)}")
