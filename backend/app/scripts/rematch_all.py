import asyncio
import sys
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.services.matching import CatalogMatcher

def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

async def rematch_all():
    log("Starting rematch of all images...")
    async with AsyncSessionLocal() as session:
        # Find all plate solved images
        stmt = select(Image.id).where(Image.is_plate_solved == True)
        result = await session.execute(stmt)
        image_ids = result.scalars().all()
        
        log(f"Found {len(image_ids)} images to rematch.")
        
        matcher = CatalogMatcher(session)
        
        for i, img_id in enumerate(image_ids):
            try:
                new_matches = await matcher.match_image(img_id)
                log(f"[{i+1}/{len(image_ids)}] Image {img_id}: {new_matches} matches found.")
            except Exception as e:
                log(f"Error matching image {img_id}: {e}")
        
    log("Rematch complete.")

if __name__ == "__main__":
    asyncio.run(rematch_all())
