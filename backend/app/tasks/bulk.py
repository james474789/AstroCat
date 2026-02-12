"""
Bulk Tasks
Background tasks for bulk operations on mount points (matching, rescanning).
"""

import logging
import time
from sqlalchemy import select, func
from app.worker import celery_app
from app.database import SessionLocal
from app.models.image import Image
from app.config import settings
import redis
import json
import hashlib

logger = logging.getLogger(__name__)

def get_mount_hash(path: str) -> str:
    """Generate a consistent hash for a mount path."""
    return hashlib.md5(path.encode()).hexdigest()

@celery_app.task(bind=True, name="app.tasks.bulk.bulk_match_task")
def bulk_match_task(self, mount_path: str):
    """
    Bulk recalculate catalog matches for all solved images in a mount point.
    Uses sync session for stability in Celery worker.
    """
    r = redis.from_url(settings.redis_url)
    mount_hash = get_mount_hash(mount_path)
    key = f"bulk:match:{mount_hash}"
    logger.info(f"Starting bulk match for mount path: {mount_path}")
    
    # Init progress
    r.hset(key, mapping={
        "status": "running",
        "processed": 0,
        "total": 0,
        "errors": 0,
        "skipped": 0,
        "updated_at": int(time.time())
    })
    r.expire(key, 3600) # 1 hour TTL
    
    try:
        r.set("indexer:is_running", "1")
        with SessionLocal() as session:
            # Get all images in path to track skipped ones
            images = session.query(Image.id, Image.is_plate_solved).filter(
                Image.file_path.like(f"{mount_path}%")
            ).all()
            
            total = len(images)
            logger.info(f"Bulk match scanned {total} total images for {mount_path}")
            r.hset(key, "total", total)
            
            from app.services.matching import SyncCatalogMatcher
            matcher = SyncCatalogMatcher(session)
            
            processed = 0
            errors = 0
            skipped = 0
            
            for img_id, is_solved in images:
                if not is_solved:
                    skipped += 1
                else:
                    try:
                        matcher.match_image(img_id)
                    except Exception as e:
                        logger.error(f"Error matching image {img_id}: {e}")
                        errors += 1
                
                processed += 1
                if processed % 20 == 0:
                    r.hset(key, mapping={
                        "processed": processed,
                        "errors": errors,
                        "skipped": skipped,
                        "updated_at": int(time.time())
                    })
            
            # Final update
            logger.info(f"Bulk match completed for {mount_path}: {processed} processed, {errors} errors, {skipped} skipped")
            r.hset(key, mapping={
                "status": "completed",
                "processed": processed,
                "errors": errors,
                "skipped": skipped,
                "updated_at": int(time.time())
            })
            
    except Exception as e:
        logger.error(f"Bulk match error: {e}", exc_info=True)
        r.hset(key, "status", "failed")
        r.hset(key, "error", str(e))
    finally:
        r.set("indexer:is_running", "0")


@celery_app.task(bind=True, name="app.tasks.bulk.bulk_astrometry_task")
def bulk_astrometry_task(self, mount_path: str, force: bool = False):
    """
    Bulk submit images for astrometry.net plate solving using a sync session
    for stability in Celery workers.
    """
    from app.tasks.astrometry import astrometry_task
    
    logger.info(f"[BULK RESCAN] Task started for mount_path={mount_path}, force={force}, task_id={self.request.id}")

    # Redis setup
    try:
        r = redis.from_url(settings.redis_url)
        logger.info(f"[BULK RESCAN] Connected to Redis for mount={mount_path}")
    except Exception as e:
        logger.error(f"[BULK RESCAN] Failed to connect to Redis: {e}", exc_info=True)
        return

    mount_hash = get_mount_hash(mount_path)
    key = f"bulk:rescan:{mount_hash}"

    # Init progress
    logger.info(f"[BULK RESCAN] Initializing progress tracking at key={key}")
    r.hset(key, mapping={
        "status": "running",
        "processed": 0,
        "total": 0,
        "queued": 0,
        "updated_at": int(time.time())
    })
    r.expire(key, 3600)

    try:
        r.set("indexer:is_running", "1")

        with SessionLocal() as session:
            # Get all images in path (sync)
            images = session.query(Image).filter(Image.file_path.like(f"{mount_path}%")).all()

            total = len(images)
            logger.info(f"[BULK RESCAN] Found {total} images for mount={mount_path}")
            r.hset(key, "total", total)

            if total == 0:
                logger.warning(f"[BULK RESCAN] No images found for mount={mount_path}. Check if path exists and has images in database.")

            queued = 0
            processed = 0
            skipped = 0

            # Separate into queues for prioritization
            priority_queue = [] # Imported (Header WCS) -> Upgrade to Solved
            standard_queue = [] # Unsolved or Forced
            skipped_count = 0

            for img in images:
                # 1. Planetary Exclusion
                if img.subtype == 'PLANETARY':
                    skipped_count += 1
                    continue

                # 2. Priority Queue: Imported but not System Solved
                # We want to auto-upgrade these even without Force, using their hints.
                if img.is_plate_solved and img.astrometry_status != 'SOLVED':
                    # Skip if already busy unless forced
                    if img.astrometry_status in ['SUBMITTED', 'PROCESSING'] and not force:
                        skipped_count += 1
                    else:
                        priority_queue.append(img)
                    continue

                # 3. Standard Queue: Unsolved or Force Re-solve
                should_rescan = False
                if force:
                    should_rescan = True
                else:
                    # Process if unsolved and idle
                    if not img.is_plate_solved and img.astrometry_status not in ['SUBMITTED', 'PROCESSING', 'SOLVED']:
                        should_rescan = True
                    # Always retry FAILED
                    elif img.astrometry_status == 'FAILED':
                        should_rescan = True
                
                if should_rescan:
                    standard_queue.append(img)
                else:
                    skipped_count += 1

            # Combine: Priority First
            final_queue = priority_queue + standard_queue
            
            logger.info(f"[BULK RESCAN] Queuing {len(priority_queue)} priority (Imported) and {len(standard_queue)} standard images. Skipped: {skipped_count}")

            # Update Redis with initial skip count
            processed = skipped_count
            skipped = skipped_count
            queued = 0
            
            r.hset(key, mapping={
                "processed": processed,
                "queued": queued,
                "skipped": skipped,
                "updated_at": int(time.time())
            })

            # Process Queue
            for img in final_queue:
                astrometry_task.delay(img.id)
                queued += 1
                processed += 1
                
                if processed % 10 == 0:
                    r.hset(key, mapping={
                        "processed": processed,
                        "queued": queued,
                        "skipped": skipped,
                        "updated_at": int(time.time())
                    })

            # Final update
            logger.info(f"[BULK RESCAN] Completed for mount={mount_path}: {processed} processed, {queued} queued, {skipped} skipped")
            r.hset(key, mapping={
                "status": "completed",
                "processed": processed,
                "queued": queued,
                "skipped": skipped,
                "updated_at": int(time.time())
            })
            logger.info(f"[BULK RESCAN] Task finished successfully for mount={mount_path}")

    except Exception as e:
        logger.error(f"Bulk rescan error: {e}", exc_info=True)
        r.hset(key, "status", "failed")
        r.hset(key, "error", str(e))
    finally:
        r.set("indexer:is_running", "0")
@celery_app.task(bind=True, name="app.tasks.bulk.bulk_thumbnail_task")
def bulk_thumbnail_task(self, mount_path: str):
    """
    Bulk regenerate thumbnails for all images in a mount point/folder.
    """
    from app.tasks.thumbnails import generate_thumbnail
    r = redis.from_url(settings.redis_url)
    mount_hash = get_mount_hash(mount_path)
    key = f"bulk:thumbnails:{mount_hash}"
    logger.info(f"Starting bulk thumbnail regeneration for path: {mount_path}")
    
    r.hset(key, mapping={
        "status": "running",
        "processed": 0,
        "total": 0,
        "updated_at": int(time.time())
    })
    r.expire(key, 3600)
    
    try:
        r.set("indexer:is_running", "1")
        with SessionLocal() as session:
            images = session.query(Image.id).filter(
                Image.file_path.like(f"{mount_path}%")
            ).all()
            
            total = len(images)
            r.hset(key, "total", total)
            
            processed = 0
            for (img_id,) in images:
                generate_thumbnail.delay(img_id, force=True)
                processed += 1
                if processed % 10 == 0:
                    r.hset(key, mapping={
                        "processed": processed,
                        "updated_at": int(time.time())
                    })
            
            r.hset(key, mapping={
                "status": "completed",
                "processed": processed,
                "updated_at": int(time.time())
            })
            
    except Exception as e:
        logger.error(f"Bulk thumbnail error: {e}", exc_info=True)
        r.hset(key, "status", "failed")
        r.hset(key, "error", str(e))
    finally:
        r.set("indexer:is_running", "0")


@celery_app.task(bind=True, name="app.tasks.bulk.bulk_metadata_task")
def bulk_metadata_task(self, mount_path: str):
    """
    Bulk re-extract metadata for all images in a mount point/folder.
    Reuses process_image task which now protects SOLVED WCS data.
    """
    from app.tasks.indexer import process_image
    r = redis.from_url(settings.redis_url)
    mount_hash = get_mount_hash(mount_path)
    key = f"bulk:metadata:{mount_hash}"
    logger.info(f"Starting bulk metadata re-extraction for path: {mount_path}")
    
    r.hset(key, mapping={
        "status": "running",
        "processed": 0,
        "total": 0,
        "updated_at": int(time.time())
    })
    r.expire(key, 3600)
    
    try:
        r.set("indexer:is_running", "1")
        with SessionLocal() as session:
            images = session.query(Image.file_path).filter(
                Image.file_path.like(f"{mount_path}%")
            ).all()
            
            total = len(images)
            r.hset(key, "total", total)
            
            processed = 0
            for (file_path,) in images:
                process_image.delay(file_path)
                processed += 1
                if processed % 10 == 0:
                    r.hset(key, mapping={
                        "processed": processed,
                        "updated_at": int(time.time())
                    })
            
            r.hset(key, mapping={
                "status": "completed",
                "processed": processed,
                "updated_at": int(time.time())
            })
            
    except Exception as e:
        logger.error(f"Bulk metadata error: {e}", exc_info=True)
        r.hset(key, "status", "failed")
        r.hset(key, "error", str(e))
    finally:
        r.set("indexer:is_running", "0")
