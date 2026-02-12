"""
Update image classifications for Planetary images.
If file path contains '/data/mount3/Planetary', classification should be 'PLANETARY'.
"""
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
from app.models.image import ImageSubtype

async def update_planetary():
    async with AsyncSessionLocal() as session:
        print(f"🔍 Updating images with path containing '/data/mount3/Planetary' to {ImageSubtype.PLANETARY}...")
        
        # SQL update statement
        query = text("""
            UPDATE images 
            SET subtype = :new_subtype
            WHERE file_path LIKE '%/data/mount3/Planetary%'
            RETURNING id;
        """)
        
        result = await session.execute(query, {"new_subtype": ImageSubtype.PLANETARY})
        updated_ids = result.all()
        
        await session.commit()
        print(f"✅ Updated {len(updated_ids)} images.")
        print("🎉 Update complete!")

if __name__ == "__main__":
    asyncio.run(update_planetary())
