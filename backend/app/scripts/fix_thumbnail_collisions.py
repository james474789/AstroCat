
import logging
import sys
import os

# Add parent directory to path so we can import app modules
sys.path.append(os.getcwd())

from sqlalchemy import text
from app.database import SessionLocal
from app.tasks.thumbnails import generate_thumbnail

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_thumbnail_collisions():
    """
    Finds all images that share a thumbnail path with another image
    and re-queues them for processing to generate unique thumbnails.
    """
    logger.info("Starting thumbnail collision detection...")
    
    session = SessionLocal()
    try:
        # Find all thumbnail paths that are used by more than 1 image
        # And get the IDs for those images
        query = text("""
            SELECT id 
            FROM images 
            WHERE thumbnail_path IN (
                SELECT thumbnail_path 
                FROM images 
                WHERE thumbnail_path IS NOT NULL 
                GROUP BY thumbnail_path 
                HAVING COUNT(*) > 1
            );
        """)
        
        result = session.execute(query)
        image_ids = [row[0] for row in result.fetchall()]
        
        count = len(image_ids)
        logger.info(f"Found {count} images with colliding thumbnails.")
        
        if count == 0:
            logger.info("No collisions found. Exiting.")
            return

        logger.info("Queueing images for thumbnail regeneration (lightweight task)...")
        
        for i, img_id in enumerate(image_ids):
            generate_thumbnail.delay(img_id)
            if i % 100 == 0:
                logger.info(f"Queued {i}/{count}...")
                
        logger.info(f"Successfully queued {count} images for reprocessing.")
        
    except Exception as e:
        logger.error(f"Error executing fix script: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    fix_thumbnail_collisions()
