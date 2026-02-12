
import asyncio
import os
from sqlalchemy.future import select
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.config import settings

async def main():
    print("Checking for missing files...")
    allowed_extensions = {
        '.fits', '.fit', '.xisf', '.jpg', '.jpeg', '.png', '.cr2', '.cr3', '.arw', '.nef', '.dng', '.tif', '.tiff'
    }
    
    # Get all file paths from DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Image.file_path))
        db_paths = set(result.scalars().all())
    
    print(f"Found {len(db_paths)} images in database.")
    
    missing = []
    total_found = 0
    
    # Check configured paths
    for path in settings.image_paths_list:
        print(f"Scanning {path}...")
        if not os.path.exists(path):
            print(f"Path not found: {path}")
            continue
            
        for root, _, files in os.walk(path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in allowed_extensions:
                    full_path = os.path.join(root, file)
                    total_found += 1
                    if full_path not in db_paths:
                        missing.append(full_path)

    print(f"Total image files found on disk: {total_found}")
    print(f"Missing images: {len(missing)}")
    
    for m in missing:
        print(f"MISSING: {m}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
