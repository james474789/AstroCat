import asyncio
import os
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.extractors.factory import get_extractor

async def backfill_metadata():
    """Find images with missing critical metadata and re-extract them."""
    print("Starting metadata backfill (dimensions, exposure, camera, etc.)...")
    
    async with AsyncSessionLocal() as session:
        # Query images with missing dimensions OR missing exposure data
        # Focus on those where we likely have files but failed previous extraction
        stmt = select(Image).where(
            (Image.width_pixels == None) | 
            (Image.height_pixels == None) |
            (Image.exposure_time_seconds == None) |
            (Image.camera_name == None)
        )
        result = await session.execute(stmt)
        images = result.scalars().all()
        
        total = len(images)
        print(f"Found {total} images needing metadata backfill.")
        
        count = 0
        updated = 0
        
        for img in images:
            count += 1
            if count % 100 == 0:
                print(f"Processing... {count}/{total}")
            
            if not os.path.exists(img.file_path):
                # Skip if file not found locally
                continue
                
            try:
                extractor = get_extractor(img.file_path)
                data = extractor.extract()
                
                has_updates = False
                
                # Update dimensions if missing
                if not img.width_pixels and data.get("width_pixels"):
                    img.width_pixels = data["width_pixels"]
                    has_updates = True
                if not img.height_pixels and data.get("height_pixels"):
                    img.height_pixels = data["height_pixels"]
                    has_updates = True
                
                # Update exposure data if missing
                if not img.exposure_time_seconds and data.get("exposure_time_seconds"):
                    img.exposure_time_seconds = data["exposure_time_seconds"]
                    has_updates = True
                if not img.capture_date and data.get("capture_date"):
                    img.capture_date = data["capture_date"]
                    has_updates = True
                if not img.iso_speed and data.get("iso_speed"):
                    img.iso_speed = data["iso_speed"]
                    has_updates = True
                if not img.gain and data.get("gain"):
                    img.gain = data["gain"]
                    has_updates = True
                
                # Update equipment if missing
                if not img.camera_name and data.get("camera_name"):
                    img.camera_name = data["camera_name"]
                    has_updates = True
                if not img.lens_model and data.get("lens_model"):
                    img.lens_model = data["lens_model"]
                    has_updates = True
                
                # Update photography metadata
                if img.aperture is None and data.get("aperture"):
                    img.aperture = data["aperture"]
                    has_updates = True
                if img.focal_length is None and data.get("focal_length"):
                    img.focal_length = data["focal_length"]
                    has_updates = True
                
                # Raw header check
                if not img.raw_header and data.get("raw_header"):
                    img.raw_header = data["raw_header"]
                    has_updates = True
                
                if has_updates:
                    updated += 1
                    if updated % 20 == 0:
                        await session.commit()
                        
            except Exception:
                # Silently catch extraction errors
                pass
        
        await session.commit()
        print(f"Backfill complete. Updated {updated} images.")

if __name__ == "__main__":
    asyncio.run(backfill_metadata())
