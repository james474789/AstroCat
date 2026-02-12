
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

# Add backend directory to path so we can import app modules
# Add backend directory to path so we can import app modules
# In Docker, the app is at /app, and this script is at /app/scripts/
sys.path.append("/app")

from app.database import SessionLocal
from app.models.image import Image
from sqlalchemy import select, func

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Force synchronous driver for the script if needed
# The app.database module tries to do this, but we ensure it here
from app.config import settings
if "+asyncpg" in settings.database_url:
    # We need to make sure we aren't accidentally using async engine for sync operations
    # But app.database.SessionLocal already does the replacement.
    # The error might be due to missing driver or library in the script context.
    pass

def patch_missing_dates():
    """
    Scan all images in the database.
    If file_last_modified is missing, read it from disk and update the DB.
    """
    # Create session
    session = SessionLocal()
    try:
        # Get count first for logging
        count_stmt = select(func.count(Image.id)).where(Image.file_last_modified == None)
        total_missing = session.execute(count_stmt).scalar()
        logger.info(f"Found {total_missing} images with missing date metadata.")
        
        if total_missing == 0:
            logger.info("No images need patching.")
            return

        # Process in batches to avoid memory issues
        BATCH_SIZE = 10
        processed_count = 0
        updated_count = 0
        missing_file_count = 0
        
        while True: # Keep picking up NULLs
            stmt = select(Image).where(Image.file_last_modified == None).limit(BATCH_SIZE)
            images = session.execute(stmt).scalars().all()
            
            if not images:
                break
                
            for image in images:
                processed_count += 1
                file_path = Path(image.file_path)
                
                logger.info(f"[{processed_count}/{total_missing}] Checking: {file_path}")
                
                if not file_path.exists():
                    logger.warning(f"[{processed_count}/{total_missing}] File not found: {file_path}")
                    # To stop it from looping on this missing file, we should mark it.
                    # As a temporary hack, we'll set it to a very old date or a special value if possible.
                    # But the schema might not allow a "dummy" date easily without confusing the app.
                    # Instead, we'll just log and continue, but this script will loop forever if we use `while True`.
                    # Let's stick to a fixed list for this run.
                    missing_file_count += 1
                    continue
                    
                try:
                    stats = file_path.stat()
                    modified_at = datetime.fromtimestamp(stats.st_mtime)
                    created_at = datetime.fromtimestamp(stats.st_ctime)
                    
                    image.file_last_modified = modified_at
                    image.file_created = created_at
                    updated_count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {e}")
            
            session.commit()
            logger.info(f"Batch complete. Total Progress: {processed_count}/{total_missing} processed, {updated_count} updated.")
            
            # If we aren't making progress (all files are missing), break to avoid infinite loop
            if processed_count >= total_missing:
                break
        logger.info(f"Patch complete.")
        logger.info(f"Total updated: {updated_count}")
        logger.info(f"Files not found: {missing_file_count}")
        
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    patch_missing_dates()
