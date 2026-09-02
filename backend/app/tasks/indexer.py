"""
Indexer Tasks
Background tasks for scanning directories and processing images.
"""

import os
import errno
import hashlib
import math
from typing import List
from pathlib import Path
import logging
import time
import redis
from datetime import datetime, date

from app.worker import celery_app
from app.database import SessionLocal
from app.models.image import Image
from app.extractors.factory import get_extractor, determine_format
from app.services.matching import SyncCatalogMatcher
from app.config import settings
from sqlalchemy import select, func, delete, update
from sqlalchemy.exc import OperationalError
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
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
    r = redis.from_url(settings.redis_url)

    try:
        with SessionLocal() as session:
            # 1. Get all existing file paths in this directory from DB for bulk lookup
            stmt = select(Image.file_path, Image.pixinsight_annotation_path).where(Image.file_path.like(f"{directory_path}%"))
            result = session.execute(stmt)
            # Map file_path -> pixinsight_annotation_path
            existing_paths = {row[0]: row[1] for row in result.all()}

            # 2. Walk directory to find current files
            for root, _, files in os.walk(directory_path):
                # Refresh heartbeat on each directory level to prevent soft timeout during long NAS walks
                _touch_indexer_heartbeat()
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
    finally:
        # Ensure heartbeat is cleared when scan completes
        try:
            r.delete("indexer:last_heartbeat")
        except Exception:
            pass

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


@celery_app.task(bind=True, name="app.tasks.indexer.scan_directory",
                 soft_time_limit=21600, time_limit=28800)
def scan_directory(self, directory_path: str):
    """
    Scan a directory for new image files (Synchronous).
    """
    return _scan_directory(directory_path)


def sanitize_metadata(data):
    """
    Recursively remove null characters from strings in metadata (keys and values)
    and normalize values PostgreSQL JSONB cannot store:
      - non-finite floats (NaN/Infinity) -> None (json.dumps would emit NaN
        tokens that PostgreSQL rejects)
      - datetime/date objects -> ISO strings (not JSON-serializable otherwise)
    """
    if isinstance(data, dict):
        return {sanitize_metadata(k): sanitize_metadata(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_metadata(item) for item in data]
    elif isinstance(data, str):
        return data.replace('\x00', '').replace('\u0000', '')
    elif isinstance(data, bool):
        return data
    elif isinstance(data, float):
        return data if math.isfinite(data) else None
    elif isinstance(data, (datetime, date)):
        return data.isoformat()
    else:
        return data


_TRANSIENT_ERRNOS = {
    errno.EAGAIN, errno.EINTR, errno.ETIMEDOUT,
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ENETRESET,
    errno.ENFILE, errno.EMFILE, errno.EIO, errno.EBUSY,
}


def _is_transient_error(exc: Exception) -> bool:
    """
    Classify errors worth retrying instead of recording permanently.

    EAGAIN / ETIMEDOUT / ECONNRESET are the classic NAS/CIFS transient I/O
    errors seen during large backfills (a worker briefly cannot read a file
    while the share is saturated). These should be retried with backoff and,
    if the retries are exhausted, left unindexed so the next directory scan
    naturally re-queues them - instead of being marked as permanent failures.
    """
    if isinstance(exc, (BlockingIOError, TimeoutError, ConnectionError, InterruptedError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNOS:
        return True
    return False


def _touch_indexer_heartbeat():
    """
    Refresh the 'scan alive' heartbeat used by cleanup_stuck_astrometry to
    detect a stale indexer:is_running flag (e.g. a scan task hard-killed by a
    container restart or hard time limit before its finally block ran).
    """
    try:
        r = redis.from_url(settings.redis_url)
        r.set("indexer:last_heartbeat", datetime.utcnow().isoformat(), ex=3600)
    except Exception:
        pass


def _record_process_failure(file_path: str, error: str):
    """
    Track per-file processing failures in Redis so poison files are visible in
    the Admin UI (GET /api/indexer/status) instead of looping silently forever.
    Counters are reset at the start of each full reindex_all scan.
    """
    try:
        r = redis.from_url(settings.redis_url)
        r.incr("indexer:process_failures")
        r.lpush("indexer:failed_files", f"{file_path} :: {error}"[:500])
        r.ltrim("indexer:failed_files", 0, 99)
    except Exception:
        # Observability must never break processing
        pass


def _process_image_impl(file_path: str, generate_thumbnail: bool = True):
    """
    Process a single image file - extract metadata and match catalogs (Synchronous).
    Set generate_thumbnail=False to do a metadata-only refresh without regenerating thumbnails.

    Fault tolerant by design: if metadata extraction fails (e.g. malformed FITS
    header cards), a minimal record is still written to the database so the
    scanner diff sees the file as indexed and stops re-queueing it on every scan.
    """
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": "File not found"}

    # 1. Extract Metadata
    extraction_error = None
    try:
        extractor = get_extractor(file_path)
        metadata = extractor.extract()

        # Sanitize metadata to remove null characters (PostgreSQL JSONB constraint)
        metadata = sanitize_metadata(metadata)

        file_stats = extractor.get_file_stats()
    except Exception as e:
        if _is_transient_error(e):
            # Transient storage hiccup (e.g. NAS EAGAIN under backfill load).
            # Do NOT write a minimal record: the Celery wrapper retries with
            # backoff, and if all retries are exhausted the file stays unindexed
            # so the next directory scan naturally re-queues it (self-healing).
            logger.warning(f"Transient error processing {file_path} ({type(e).__name__}: {e}); task will be retried")
            raise
        # Poison file: log it, record it, but do NOT abort - the minimal record
        # written below breaks the re-scan loop for malformed files.
        extraction_error = f"{type(e).__name__}: {e}"[:500]
        logger.warning(f"Extraction failed for {file_path} ({extraction_error}); inserting minimal record")
        _record_process_failure(file_path, extraction_error)
        metadata = {}
        try:
            st = os.stat(file_path)
            file_stats = {
                "file_size_bytes": st.st_size,
                "created_at": st.st_ctime,
                "modified_at": st.st_mtime,
            }
        except OSError:
            file_stats = {"file_size_bytes": 0}

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
    
    thumbnail_path = None
    if generate_thumbnail:
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

            # Record/clear metadata extraction diagnostics
            image.extraction_error = extraction_error

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
                pixinsight_annotation_path=pixinsight_annotation_path,
                
                # Metadata extraction diagnostics (set when extraction failed)
                extraction_error=extraction_error
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


@celery_app.task(bind=True, name="app.tasks.indexer.process_image",
                 autoretry_for=(OperationalError, BlockingIOError, TimeoutError),
                 retry_backoff=True, retry_backoff_max=60, max_retries=3,
                 soft_time_limit=7200, time_limit=10800)
def process_image(self, file_path: str, generate_thumbnail: bool = True):
    """
    Celery entry point for single-file processing.

    - Transient database connection errors are retried with exponential backoff.
    - Any other failure is recorded in Redis (Admin UI visibility) and re-raised
      so Celery marks the task failed. Metadata extraction failures never reach
      this point: they are handled inside _process_image_impl, which inserts a
      minimal record so the scanner stops re-queueing the file on every scan.
    """
    try:
        return _process_image_impl(file_path, generate_thumbnail)
    except SoftTimeLimitExceeded:
        logger.error(f"Metadata extraction soft time limit (2 hours) exceeded for file: {file_path}")
        _record_process_failure(file_path, "Task timeout: Soft time limit (2 hours) exceeded")
        raise
    except TimeLimitExceeded:
        logger.error(f"Metadata extraction hard time limit (3 hours) exceeded for file: {file_path}")
        _record_process_failure(file_path, "Task timeout: Hard time limit (3 hours) exceeded")
        raise
    except Exception as e:
        if not _is_transient_error(e):
            _record_process_failure(file_path, f"{type(e).__name__}: {e}"[:500])
        raise


@celery_app.task(bind=True, name="app.tasks.indexer.reindex_all",
                 soft_time_limit=21600, time_limit=28800)
def reindex_all(self):
    """
    Re-scan all configured image paths with state tracking (Synchronous).
    """
    # Connect to Redis for state tracking
    r = redis.from_url(settings.redis_url)
    
    # Mark scan as running
    r.set("indexer:is_running", "1")
    # Reset per-scan failure tracking (populated by process_image)
    try:
        r.delete("indexer:process_failures", "indexer:failed_files")
    except Exception:
        pass
    start_time = time.time()
    
    total_files_scanned = 0
    total_files_added = 0
    total_files_removed = 0
    
    results = []
    
    try:
        for path in settings.image_paths_list:
            if os.path.exists(path):
                r.set("indexer:current_path", path)
                _touch_indexer_heartbeat()
                # Run synchronously inside the worker to track progress without spawning extra tasks
                result = _scan_directory(path)
                results.append({"path": path, **result})
                total_files_scanned += result.get("files_found", 0)
                total_files_added += result.get("files_queued", 0)
                total_files_removed += result.get("files_removed", 0)

                # Publish live progress after each mount so the Admin UI never
                # shows stale counters (and looks frozen) mid-scan.
                r.set("indexer:files_scanned", str(total_files_scanned))
                r.set("indexer:files_added", str(total_files_added))
                r.set("indexer:files_updated", "0")
                r.set("indexer:files_removed", str(total_files_removed))
                logger.info(f"Scan progress: {path} done; scanned={total_files_scanned} added={total_files_added} removed={total_files_removed}")
    except SoftTimeLimitExceeded:
        logger.warning("Indexer scan soft time limit (6 hours) exceeded, saving state and stopping gracefully")
        # Ensure is_running is cleared so UI isn't blocked
        r.set("indexer:is_running", "0")
        raise  # Let Celery handle timeout properly
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
        
        # Remove live-scan-only markers that are meaningless once the scan ends
        try:
            r.delete("indexer:current_path", "indexer:last_heartbeat")
        except Exception:
            pass

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
