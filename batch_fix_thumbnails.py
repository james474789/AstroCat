
import sys
import os
import logging
from sqlalchemy import or_

sys.path.append('/app')

from app.database import SessionLocal
from app.models.image import Image, ImageSubtype
from app.tasks.thumbnails import generate_thumbnail

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fix_missing_thumbnails():
    db = SessionLocal()
    try:
        # Find images that have no thumbnail path or have a placeholder but the file doesn't exist
        # Also look for images that were previously logged as failures if possible, 
        # but checking for null thumbnail_path is the most direct way to find "not showing" ones.
        images_to_fix = db.query(Image).filter(
            or_(
                Image.thumbnail_path == None,
                Image.thumbnail_path == ""
            )
        ).all()

        total = len(images_to_fix)
        logger.info(f"Found {total} images without thumbnails.")

        success_count = 0
        fail_count = 0

        for i, image in enumerate(images_to_fix):
            logger.info(f"[{i+1}/{total}] Processing Image ID: {image.id} - {image.file_name}")
            
            try:
                # We use the existing task logic which now has the tifffile fallback
                result = generate_thumbnail(image.id)
                if result.get("status") == "completed":
                    success_count += 1
                    logger.info(f"  Successfully generated: {result.get('thumbnail_path')}")
                else:
                    fail_count += 1
                    logger.error(f"  Failed: {result.get('message')}")
            except Exception as e:
                fail_count += 1
                logger.error(f"  Error processing image {image.id}: {e}")

            # Commit periodically
            if (i + 1) % 10 == 0:
                db.commit()

        db.commit()
        logger.info(f"Batch processing complete.")
        logger.info(f"Success: {success_count}")
        logger.info(f"Failed: {fail_count}")

    finally:
        db.close()

if __name__ == "__main__":
    fix_missing_thumbnails()
