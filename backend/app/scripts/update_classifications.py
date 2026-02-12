"""
Update image classifications based on mount source.
If mount source is [/data/mount2], classification should be 'INTEGRATION_MASTER'.
"""
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal
from app.models.image import ImageSubtype

async def update_classifications():
    async with AsyncSessionLocal() as session:
        print(f"🔍 Updating images in /data/mount2 to {ImageSubtype.INTEGRATION_MASTER}...")
        
        # SQL update statement
        query = text("""
            UPDATE images 
            SET subtype = :new_subtype
            WHERE file_path LIKE '/data/mount2/%'
            RETURNING id;
        """)
        
        result = await session.execute(query, {"new_subtype": ImageSubtype.INTEGRATION_MASTER})
        updated_ids = result.all()
        
        await session.commit()
        print(f"✅ Updated {len(updated_ids)} images.")
        print("🎉 Update complete!")

if __name__ == "__main__":
    asyncio.run(update_classifications())
