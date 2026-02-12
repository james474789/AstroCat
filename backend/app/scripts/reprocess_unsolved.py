"""
Script to re-queue all unsolved images for processing.
"""
import asyncio
from sqlalchemy.future import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.tasks.indexer import process_image

async def reprocess_unsolved():
    print("🔍 Finding unsolved images...")
    async with AsyncSessionLocal() as session:
        stmt = select(Image).where(Image.is_plate_solved == False)
        result = await session.execute(stmt)
        unsolved_images = result.scalars().all()
        
        print(f"🚀 Found {len(unsolved_images)} unsolved images. Re-queueing...")
        
        for image in unsolved_images:
            print(f"  - Queuing: {image.file_path}")
            process_image.delay(image.file_path)
            
    print("✅ All unsolved images have been re-queued.")

if __name__ == "__main__":
    asyncio.run(reprocess_unsolved())
