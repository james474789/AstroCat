import asyncio
import sys
import traceback
from app.database import AsyncSessionLocal
from app.services.matching import CatalogMatcher

async def debug_match(image_id):
    print(f"Debugging match for image {image_id}...")
    async with AsyncSessionLocal() as session:
        matcher = CatalogMatcher(session)
        try:
            count = await matcher.match_image(image_id)
            print(f"Success! Found {count} matches.")
        except Exception as e:
            print(f"FAILED: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rematch_debug.py <image_id>")
    else:
        asyncio.run(debug_match(int(sys.argv[1])))
