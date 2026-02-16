"""
Indexer Tasks
Background tasks for scanning directories and processing images.
"""

import os
import hashlib
from typing import List
from pathlib import Path
import logging
import time
import redis
from datetime import datetime

from app.worker import celery_app
from app.database import SessionLocal
from app.models.image import Image
from app.extractors.factory import get_extractor, determine_format
from app.services.matching import SyncCatalogMatcher
from app.config import settings
from sqlalchemy import select, func, delete, update
from app.models.system_stats import SystemStats

logger = logging.getLogger(__name__)


def _scan_directory(directory_path: str):
    """Core scan logic shared by Celery tasks and synchronous calls."""
    logger.info(f"Scanning directory: {directory_path}")
    files_found = 0
    files_queued = 0
    files_removed = 0

    allowed_extensions = {
        '.fits', '.fit', '.xisf', '.jpg', '.jpeg', '.png', '.cr2', '.cr3', '.arw', '.nef', '.dng', '.tif', '.tiff'
    }

    disk_files = set()

    try:
        with SessionLocal() as session:
            # 1. Get all existing file paths in this directory from DB for bulk lookup
            stmt = select(Image.file_path, Image.pixinsight_annotation_path).where(Image.file_path.like(f"{directory_path}%"))
            result = session.execute(stmt)
            # Map file_path -> pixinsight_annotation_path
            existing_paths = {row[0]: row[1] for row in result.all()}

            # 2. Walk directory to find current files
            for root, _, files in os.walk(directory_path):
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in allowed_extensions:
                        str_path = str(file_path)
                        disk_files.add(str_path)
                        files_found += 1

                        # Check if already indexed using our pre-fetched set
                        if str_path in existing_paths:
                            # Existing file. Check if we need to backfill the annotation.
                            # Skip if this IS an annotation file (safety check)
                            if file_path.stem.endswith("_Annotated"):
                                continue

                            current_annotation = existing_paths[str_path]
                            if not current_annotation:
                                # Check if annotation exists on disk
                                annotation_fname = file_path.stem + "_Annotated" + file_path.suffix
                                annotation_file = file_path.parent / annotation_fname
                                if annotation_file.exists():
                                    # Update DB
                                    logger.info(f"Backfilling annotation for existing image: {str_path}")
                                    stmt = update(Image).where(Image.file_path == str_path).values(pixinsight_annotation_path=str(annotation_file))
                                    session.execute(stmt)
                                    session.commit()
                        else:
                             # New file logic remains...
                            # SKIP if this is an annotation file (suffix "_Annotated")
                            # We will handle it when processing the main file, or if main file exists we should update it.
                            # Actually, we should check if it ends with _Annotated.{ext}
                            # Robust check: stem ends with _Annotated
                            if file_path.stem.endswith("_Annotated"):
                                logger.debug(f"Skipping annotation file from main index: {str_path}")
                                continue

                            # New file, queue for processing
                            logger.info(f"Queuing new file: {str_path}")
                            process_image.delay(str_path)
                            files_queued += 1

            # 3. Find files in DB that are no longer on disk (Efficiency: Bulk Delete)
            missing_paths = set(existing_paths.keys()) - disk_files
            if missing_paths:
                files_removed = len(missing_paths)
                logger.info(f"Removing {files_removed} deleted files from index in {directory_path}")

                # Bulk delete in batches for safety/performance
                missing_list = list(missing_paths)
                batch_size = 500
                for i in range(0, len(missing_list), batch_size):
                    batch = missing_list[i:i + batch_size]
                    stmt = delete(Image).where(Image.file_path.in_(batch))
                    session.execute(stmt)

                session.commit()

    except Exception as e:
        logger.error(f"Error scanning directory {directory_path}: {e}", exc_info=True)
        raise

    return {
        "status": "completed",
        "directory": directory_path,
        "files_found": files_found,
        "files_queued": files_queued,
        "files_removed": files_removed
    }


def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA-256 hash of file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


@celery_app.task(bind=True, name="app.tasks.indexer.scan_directory")
def scan_directory(self, directory_path: str):
    """
    Scan a directory for new image files (Synchronous).
    """
    return _scan_directory(directory_path)


def sanitize_metadata(data):
    """Recursively remove null characters from strings in metadata (keys and values)."""
    if isinstance(data, dict):
        return {sanitize_metadata(k): sanitize_metadata(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_metadata(item) for item in data]
    elif isinstance(data, str):
        return data.replace('\x00', '').replace('\\u0000', '')
    else:
        return data


@celery_app.task(bind=True, name="app.tasks.indexer.process_image")
def process_image(self, file_path: str):
    """
    Process a single image file - extract metadata and match catalogs (Synchronous).
    """
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": "File not found"}
        
    # 1. Extract Metadata
    extractor = get_extractor(file_path)
    metadata = extractor.extract()
    
    # Sanitize metadata to remove null characters (PostgreSQL JSONB constraint)
    metadata = sanitize_metadata(metadata)
    
    file_stats = extractor.get_file_stats()
    
    logger.info(f"PROCESSING: {file_path}")
    
    logger.info(f"PROCESSING: {file_path}")

    # Check for PixInsight Annotation File
    # Convention: {filename}_Annotated.{ext}
    # We look for a file in the same directory with same extension but _Annotated suffix
    pixinsight_annotation_path = None
    try:
        # e.g. path/to/image.xisf -> path/to/image_Annotated.xisf
        annotation_fname = path.stem + "_Annotated" + path.suffix
        annotation_file = path.parent / annotation_fname
        
        if annotation_file.exists():
            pixinsight_annotation_path = str(annotation_file)
            logger.info(f"Found PixInsight annotation: {pixinsight_annotation_path}")
    except Exception as e:
        logger.error(f"Error checking for annotation file: {e}")
    
    # 1.5 Generate Thumbnail
    from app.services.thumbnails import ThumbnailGenerator
    
    # Determine if stf stretch is needed (Default to True for new imports as they are likely subframes)
    # Ideally extractors should return this.
    is_subframe = True
    if metadata.get("subtype"):
        # If extractor determined it (e.g. from header), use it
        # We need the enum value or string match
        from app.models.image import ImageSubtype
        is_subframe = (metadata["subtype"] == ImageSubtype.SUB_FRAME)
    
    try:
        max_size = (settings.thumbnail_max_size, settings.thumbnail_max_size)
        thumbnail_path = ThumbnailGenerator.generate(
            file_path, 
            settings.thumbnail_cache_path, 
            max_size=max_size,
            is_subframe=is_subframe, 
            apply_stf=is_subframe
        )
    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {file_path}: {e}")
        thumbnail_path = None
    
    # 2. Save to Database
    with SessionLocal() as session:
        # Re-check existence
        stmt = select(Image).where(Image.file_path == str(file_path))
        result = session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        wcs = metadata.get("wcs", {})
        
        if existing:
            image = existing
            # Update metadata for existing records
            image.file_size_bytes = file_stats["file_size_bytes"]
            if file_stats.get("modified_at"):
                image.file_last_modified = datetime.fromtimestamp(file_stats.get("modified_at"))
            if file_stats.get("created_at"):
                image.file_created = datetime.fromtimestamp(file_stats.get("created_at"))
            image.width_pixels = metadata.get("width_pixels")
            image.height_pixels = metadata.get("height_pixels")
            
            image.exposure_time_seconds = metadata.get("exposure_time_seconds")
            # If capture date is missing from metadata, fallback to file modification time
            image.capture_date = metadata.get("capture_date") or (datetime.fromtimestamp(file_stats.get("modified_at")) if file_stats.get("modified_at") else None)
            image.gain = metadata.get("gain")
            image.iso_speed = metadata.get("iso_speed")
            image.temperature_celsius = metadata.get("temperature_celsius")
            
            image.camera_name = metadata.get("camera_name")
            image.telescope_name = metadata.get("telescope_name")
            image.filter_name = metadata.get("filter_name")
            
            image.object_name = metadata.get("object_name")
            image.observer_name = metadata.get("observer")
            
            # Store additional photography metadata
            image.rating = metadata.get("rating")
            image.aperture = metadata.get("aperture")
            image.focal_length = metadata.get("focal_length")
            image.focal_length_35mm = metadata.get("focal_length_35mm")
            image.white_balance = metadata.get("white_balance")
            image.metering_mode = metadata.get("metering_mode")
            image.flash_fired = metadata.get("flash_fired")
            image.lens_model = metadata.get("lens_model")
            
            # Protect WCS data if already solved by Astrometry.net
            if image.astrometry_status != "SOLVED":
                image.is_plate_solved = metadata.get("is_plate_solved", False)
                image.plate_solve_source = metadata.get("plate_solve_source")
                image.ra_center_degrees = wcs.get("ra_center")
                image.dec_center_degrees = wcs.get("dec_center")
                image.field_radius_degrees = wcs.get("radius_degrees")
                image.pixel_scale_arcsec = wcs.get("pixel_scale")
                image.rotation_degrees = wcs.get("rotation")
            else:
                logger.debug(f"Skipping WCS update for image {image.id} as it is already SOLVED by system.")
            image.raw_header = metadata.get("raw_header")
            
            # Update PostGIS geometry
            if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
                image.center_location = func.ST_SetSRID(
                    func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 
                    4326
                )

            # Update PostGIS geometry
            if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
                image.center_location = func.ST_SetSRID(
                    func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 
                    4326
                )
            
            # Update PixInsight Annotation Path
            if pixinsight_annotation_path:
                image.pixinsight_annotation_path = pixinsight_annotation_path

            # Update thumbnail if we generated one
            if thumbnail_path:
                image.thumbnail_path = thumbnail_path
        else:
            image = Image(
                file_path=str(file_path),
                file_name=path.name,
                file_format=determine_format(file_path),
                file_size_bytes=file_stats["file_size_bytes"],
                file_last_modified=datetime.fromtimestamp(file_stats.get("modified_at")) if file_stats.get("modified_at") else None,
                file_created=datetime.fromtimestamp(file_stats.get("created_at")) if file_stats.get("created_at") else None,
                
                # Store thumbnail path
                thumbnail_path=thumbnail_path,
                
                width_pixels=metadata.get("width_pixels"),
                height_pixels=metadata.get("height_pixels"),
                
                exposure_time_seconds=metadata.get("exposure_time_seconds"),
                # If capture date is missing from metadata, fallback to file modification time
                capture_date=metadata.get("capture_date") or (datetime.fromtimestamp(file_stats.get("modified_at")) if file_stats.get("modified_at") else None),
                gain=metadata.get("gain"),
                iso_speed=metadata.get("iso_speed"),
                temperature_celsius=metadata.get("temperature_celsius"),
                
                camera_name=metadata.get("camera_name"),
                telescope_name=metadata.get("telescope_name"),
                filter_name=metadata.get("filter_name"),
                
                object_name=metadata.get("object_name"),
                observer_name=metadata.get("observer"),
                
                # Store additional photography metadata
                rating=metadata.get("rating"),
                aperture=metadata.get("aperture"),
                focal_length=metadata.get("focal_length"),
                focal_length_35mm=metadata.get("focal_length_35mm"),
                white_balance=metadata.get("white_balance"),
                metering_mode=metadata.get("metering_mode"),
                flash_fired=metadata.get("flash_fired"),
                lens_model=metadata.get("lens_model"),
                
                is_plate_solved=metadata.get("is_plate_solved", False),
                plate_solve_source=metadata.get("plate_solve_source"),
                ra_center_degrees=wcs.get("ra_center"),
                dec_center_degrees=wcs.get("dec_center"),
                field_radius_degrees=wcs.get("radius_degrees"),
                pixel_scale_arcsec=wcs.get("pixel_scale"),
                rotation_degrees=wcs.get("rotation"),
                
                # Store full WCS/HEADER info
                raw_header=metadata.get("raw_header"),
                
                # PostGIS geometry
                center_location=func.ST_SetSRID(
                    func.ST_MakePoint(float(wcs.get("ra_center")), float(wcs.get("dec_center"))), 
                    4326
                ) if wcs.get("ra_center") is not None and wcs.get("dec_center") is not None else None,
                
                # PixInsight Annotation
                pixinsight_annotation_path=pixinsight_annotation_path
            )
            session.add(image)
            session.flush() # Get ID
            
        # 3. Match Catalogs (if plate solved)
        matches_count = 0
        if image.is_plate_solved:
            matcher = SyncCatalogMatcher(session)
            matches_count = matcher.match_image(image.id)
            logger.info(f"MATCHED {matches_count} objects for {file_path}")
        
        session.commit()
    
    return {"status": "completed", "file": file_path, "matches": matches_count}


@celery_app.task(bind=True, name="app.tasks.indexer.reindex_all")
def reindex_all(self):
    """
    Re-scan all configured image paths with state tracking (Synchronous).
    """
    # Connect to Redis for state tracking
    r = redis.from_url(settings.redis_url)
    
    # Mark scan as running
    r.set("indexer:is_running", "1")
    start_time = time.time()
    
    total_files_scanned = 0
    total_files_added = 0
    total_files_removed = 0
    
    results = []
    
    try:
        for path in settings.image_paths_list:
            if os.path.exists(path):
                # Run synchronously inside the worker to track progress without spawning extra tasks
                result = _scan_directory(path)
                results.append({"path": path, **result})
                total_files_scanned += result.get("files_found", 0)
                total_files_added += result.get("files_queued", 0)
                total_files_removed += result.get("files_removed", 0)
    except Exception as e:
        logger.error(f"Error during reindex_all: {e}", exc_info=True)
    finally:
        # Calculate duration
        duration = int(time.time() - start_time)
        
        # Update Redis with scan results
        r.set("indexer:is_running", "0")
        r.set("indexer:last_scan_at", datetime.utcnow().isoformat() + "Z")
        r.set("indexer:last_scan_duration", str(duration))
        r.set("indexer:files_scanned", str(total_files_scanned))
        r.set("indexer:files_added", str(total_files_added))
        r.set("indexer:files_updated", "0")
        r.set("indexer:files_removed", str(total_files_removed))
        
        # Trigger mount stats update after scan completion
        logger.info("Triggering mount stats update after scan completion")
        update_mount_stats()
            
            
    return {"status": "completed", "paths": results, "duration": duration}


def update_thumbnail_stats():
    """Update thumbnail statistics in the database by walking the cache directory."""
    logger.info("Updating thumbnail statistics in database...")
    thumb_cache_dir = settings.thumbnail_cache_path
    count = 0
    size_bytes = 0
    
    if os.path.exists(thumb_cache_dir):
        try:
            for f in os.listdir(thumb_cache_dir):
                fp = os.path.join(thumb_cache_dir, f)
                if os.path.isfile(fp):
                    count += 1
                    size_bytes += os.path.getsize(fp)
        except Exception as e:
            logger.error(f"Error walking thumbnail cache: {e}")

    try:
        with SessionLocal() as session:
            # Try to get existing stats
            stmt = select(SystemStats).where(SystemStats.category == "thumbnails")
            result = session.execute(stmt)
            stats = result.scalar_one_or_none()
            
            if not stats:
                stats = SystemStats(category="thumbnails")
                session.add(stats)
            
            stats.count = count
            stats.size_bytes = size_bytes
            session.commit()
            logger.info(f"Thumbnail stats updated: {count} files, {size_bytes} bytes")
    except Exception as e:
        logger.error(f"Error updating thumbnail stats in DB: {e}")


@celery_app.task(name="app.tasks.indexer.update_mount_stats")
def update_mount_stats():
    """Update mount point statistics in the database by querying the images table."""
    logger.info("Updating mount point statistics in database...")
    
    try:
        with SessionLocal() as session:
            # Get stats for each mount point using aggregation query
            # Extract mount path as first two levels: /data/mount3
            from sqlalchemy import text
            result = session.execute(text("""
                SELECT 
                    SUBSTRING(file_path FROM '^/[^/]+/[^/]+') as mount,
                    COUNT(*) as file_count,
                    COALESCE(SUM(file_size_bytes), 0) as total_size
                FROM images
                WHERE file_path IS NOT NULL
                GROUP BY mount
            """))
            
            mount_stats = {}
            for row in result:
                mount = row[0]
                mount_stats[mount] = {
                    "file_count": int(row[1]),
                    "size_bytes": float(row[2])
                }
            
            # Update or create stats for each mount point
            for mount_path in settings.image_paths_list:
                stats_data = mount_stats.get(mount_path, {"file_count": 0, "size_bytes": 0})
                category = f"mount:{mount_path}"
                
                stmt = select(SystemStats).where(SystemStats.category == category)
                result = session.execute(stmt)
                stats = result.scalar_one_or_none()
                
                if not stats:
                    stats = SystemStats(category=category)
                    session.add(stats)
                
                stats.count = stats_data["file_count"]
                stats.size_bytes = int(stats_data["size_bytes"])
            
            session.commit()
            logger.info(f"Mount stats updated for {len(settings.image_paths_list)} mount points")
    except Exception as e:
        logger.error(f"Error updating mount stats in DB: {e}")


@celery_app.task(bind=True, name="app.tasks.indexer.regenerate_thumbnails")
def regenerate_thumbnails(self):
    """
    Background task to regenerate all thumbnails and refresh stats.
    """
    from app.tasks.thumbnails import generate_thumbnail
    logger.info("Starting global thumbnail regeneration...")
    
    try:
        with SessionLocal() as session:
            # 1. Get all image IDs
            stmt = select(Image.id)
            result = session.execute(stmt)
            image_ids = [row[0] for row in result.all()]
            
            logger.info(f"Queuing thumbnail generation for {len(image_ids)} images")
            
            # 2. Queue each image for thumbnail generation
            # Note: For very large libraries, this might be a lot of tasks.
            # In Phase 3 we might want more efficient batching.
            for img_id in image_ids:
                generate_thumbnail.delay(img_id, force=True)
                
        # 3. Update stats at the end of the queuing process
        # Note: This will show old/cleared stats until workers finish their jobs.
        # But this fulfills the "perform a rescan at the end of their process" requirement.
        update_thumbnail_stats()
        
        return {"status": "completed", "images_queued": len(image_ids)}
    except Exception as e:
        logger.error(f"Error during regenerate_thumbnails task: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
