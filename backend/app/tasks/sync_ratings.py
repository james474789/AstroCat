"""
Sync Ratings Task
Background task to sync manual rating updates to XMP sidecar files.
"""

import logging
import redis
from datetime import datetime
from sqlalchemy import select

from app.worker import celery_app
from app.database import SessionLocal
from app.models.image import Image
from app.services.xmp import write_xmp_rating
from app.config import settings

logger = logging.getLogger(__name__)

LOCK_NAME = "sync_ratings_lock"
LOCK_TIMEOUT = 300  # 5 minutes

@celery_app.task(bind=True, name="app.tasks.sync_ratings.sync_ratings_to_filesystem")
def sync_ratings_to_filesystem(self):
    """
    Background task to sync updated ratings to XMP sidecar files.
    Uses a Redis lock to ensure only one worker runs this at a time.
    Loops until no more pending updates match, ensuring concurrent triggers are handled.
    """
    r = redis.from_url(settings.redis_url)
    
    # Non-blocking lock: ensure single execution thread
    lock_acquired = r.set(LOCK_NAME, "1", nx=True, ex=LOCK_TIMEOUT)
    if not lock_acquired:
        logger.info("Sync ratings task already running (lock held). Skipping.")
        return {"status": "skipped", "reason": "locked"}

    stats = {"processed": 0, "errors": 0}
    
    try:
        # Loop until no pending work remains
        while True:
            processed_in_batch = 0
            
            with SessionLocal() as session:
                # Fetch pending updates in batches
                stmt = select(Image).where(Image.rating_manually_edited == True).limit(50)
                result = session.execute(stmt)
                images = result.scalars().all()
                
                if not images:
                    break  # No more work found
                
                for image in images:
                    try:
                        if image.rating is not None:
                            # Write XMP
                            write_xmp_rating(image.file_path, image.rating)
                        
                        # Update DB success state
                        image.rating_manually_edited = False
                        image.rating_flushed_at = datetime.utcnow()
                        processed_in_batch += 1
                        stats["processed"] += 1
                        
                    except Exception as e:
                        logger.error(f"Error syncing rating for image {image.id} ({image.file_path}): {e}")
                        stats["errors"] += 1
                        # Start of error handling: 
                        # Consider whether to retry immediately or leave flag=True
                        # For now, leaving flag=True implies it will be picked up next time.
                        
                session.commit()
            
            # Refresh lock expiration if we are still working
            r.expire(LOCK_NAME, LOCK_TIMEOUT)
            
            # If we fetched fewer than limit, we likely drained the queue.
            if processed_in_batch < 50:
                 break
                 
    except Exception as e:
        logger.error(f"Error in sync_ratings_to_filesystem: {e}", exc_info=True)
        raise
    finally:
        # Release lock
        r.delete(LOCK_NAME)
        
    logger.info(f"Sync ratings completed. Processed: {stats['processed']}, Errors: {stats['errors']}")
    return {"status": "completed", "stats": stats}
