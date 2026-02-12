import asyncio
import sys
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.services.matching import CatalogMatcher

async def rematch_image(image_id: int):
    """Trigger catalog matching for a specific image."""
    async with AsyncSessionLocal() as session:
        # Fetch image
        stmt = select(Image).where(Image.id == image_id)
        result = await session.execute(stmt)
        image = result.scalar_one_or_none()
        
        if not image:
            print(f"Error: Image {image_id} not found.")
            return

        if not image.is_plate_solved:
            print(f"Error: Image {image_id} is not plate solved. Cannot match catalogs.")
            return

        print(f"Matching catalogs for image {image_id} ({image.file_name})...")
        print(f"Current dimensions: {image.width_pixels} x {image.height_pixels}")
        
        matcher = CatalogMatcher(session)
        count = await matcher.match_image(image_id)
        
        print(f"Success! Found and saved {count} astronomical objects.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.rematch_catalogs <image_id>")
        sys.exit(1)
        
    try:
        img_id = int(sys.argv[1])
        asyncio.run(rematch_image(img_id))
    except ValueError:
        print("Error: Image ID must be an integer.")
        sys.exit(1)
