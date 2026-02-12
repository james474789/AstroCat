
import asyncio
import os
import time
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.services.xmp import write_xmp_rating

async def verify_xmp_sync(image_id: int):
    print(f"Verifying XMP sync for image {image_id}")
    
    async with AsyncSessionLocal() as session:
        # 1. Get Image Path
        stmt = select(Image).where(Image.id == image_id)
        result = await session.execute(stmt)
        image = result.scalar_one_or_none()
        
        if not image:
            print(f"Image {image_id} not found!")
            return
            
        print(f"Image Path: {image.file_path}")
        xmp_path = os.path.splitext(image.file_path)[0] + ".xmp"
        if image.file_path.lower().endswith('.xmp'):
            xmp_path = image.file_path
            
        # Cleanup previously generated XMP if exists (for clean test)
        # Be careful if user wants to preserve? 
        # "if none exists create it... if exists update it"
        # We can just check the update.
        
        # 2. Simulate API Update (We can just update DB directly or hit API)
        # Updating DB directly simulates the API's DB action.
        # But we need to trigger the task.
        # Since we are in a script, we can't easily trigger the Celery task unless we import it.
        # But the Celery task runs in a separate worker process.
        # We can't rely on `sync_ratings_to_filesystem.delay()` actually running *immediately* or *at all* if no worker is running.
        # Wait, the user has a `celery_worker` container running.
        # So calling `.delay()` WILL send it to the queue.
        
        print("Updating rating to 5 and setting manual flag...")
        image.rating = 5
        image.rating_manually_edited = True
        image.rating_flushed_at = None
        
        await session.commit()
        await session.refresh(image)

        # Trigger task
        print("Triggering background task...")
        from app.tasks.sync_ratings import sync_ratings_to_filesystem
        sync_ratings_to_filesystem.delay()
        
        # 3. Wait and Verify
        print("Waiting for sync (max 30s)...")
        for i in range(30):
            await session.refresh(image)
            if image.rating_manually_edited == False:
                print(f"Success! Flag cleared after {i} seconds.")
                print(f"Flushed At: {image.rating_flushed_at}")
                
                # Check XMP file
                if os.path.exists(xmp_path):
                    with open(xmp_path, 'r') as f:
                        content = f.read()
                    print(f"XMP Content Preview: {content[:200]}...")
                    if "<xmp:Rating>5</xmp:Rating>" in content:
                        print("XMP content verification PASSED: Rating is 5.")
                    else:
                        print("XMP content verification FAILED: Rating 5 not found.")
                else:
                    print(f"XMP file not found at {xmp_path}")
                return
            
            await asyncio.sleep(1)
            
        print("Timeout waiting for sync.")
        print(f"Current State: Manual={image.rating_manually_edited}")

if __name__ == "__main__":
    asyncio.run(verify_xmp_sync(44515))
