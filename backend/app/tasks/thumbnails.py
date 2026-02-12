"""
Thumbnail Tasks
Background tasks for thumbnail generation.
"""

from app.worker import celery_app


@celery_app.task(bind=True, name="app.tasks.thumbnails.generate_thumbnail")
def generate_thumbnail(self, image_id: int, force: bool = False):
    """
    Generate a thumbnail for an image.
    
    Args:
        image_id: Database ID of the image
        force: If True, regenerate even if thumbnail exists
        
    Returns:
        dict with thumbnail path and status
    """
    # TODO: Implement in Phase 3
    from app.database import SessionLocal
    from app.models.image import Image
    from app.services.thumbnails import ThumbnailGenerator
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    thumb_path = None
    
    try:
        with SessionLocal() as session:
            image = session.query(Image).filter(Image.id == image_id).first()
            if not image:
                return {"status": "error", "message": "Image not found"}
                
            if not os.path.exists(image.file_path):
                 return {"status": "error", "message": "Source file not found"}

            thumb_cache_dir = os.environ.get("THUMBNAIL_CACHE_PATH", "/data/thumbnails")
            
            # Generate (this now includes the hash fix and force parameter)
            # Check for subframe status to enable STF stretching
            from app.models.image import ImageSubtype
            is_subframe = (image.subtype == ImageSubtype.SUB_FRAME)
            
            # Apply STF stretch for subframes by default
            thumb_path = ThumbnailGenerator.generate(
                image.file_path, 
                thumb_cache_dir, 
                is_subframe=is_subframe, 
                apply_stf=is_subframe,
                overwrite=force
            )
            
            if thumb_path:
                image.thumbnail_path = thumb_path
                from datetime import datetime
                image.thumbnail_generated_at = datetime.utcnow()
                session.commit()
                return {
                    "status": "completed",
                    "image_id": image_id,
                    "thumbnail_path": thumb_path
                }
            else:
                 return {"status": "error", "message": "Failed to generate thumbnail"}
                 
    except Exception as e:
        logger.error(f"Error regenerating thumbnail for image {image_id}: {e}")
        return {"status": "error", "message": str(e)}


@celery_app.task(bind=True, name="app.tasks.thumbnails.generate_batch")
def generate_batch(self, image_ids: list):
    """
    Generate thumbnails for multiple images.
    
    Args:
        image_ids: List of database IDs
        
    Returns:
        dict with batch generation status
    """
    # TODO: Implement in Phase 3
    return {
        "status": "completed",
        "count": len(image_ids),
        "message": "Batch thumbnail task placeholder - implement in Phase 3"
    }
