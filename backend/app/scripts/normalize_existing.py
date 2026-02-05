"""
Normalize existing object names in the database.
Removes whitespace from designations in catalogs and matches.
"""
import asyncio
from sqlalchemy import text
from app.database import AsyncSessionLocal

async def normalize_existing():
    async with AsyncSessionLocal() as session:
        print("🔍 Normalizing NGC catalog designations...")
        # Update ngc_catalog
        result = await session.execute(text("""
            UPDATE ngc_catalog 
            SET designation = REPLACE(designation, ' ', '')
            WHERE designation LIKE '% %'
            RETURNING id;
        """))
        ngc_count = len(result.all())
        print(f"✅ Updated {ngc_count} NGC objects")

        print("🔍 Normalizing Messier catalog designations...")
        # Update messier_catalog
        result = await session.execute(text("""
            UPDATE messier_catalog 
            SET designation = REPLACE(designation, ' ', '')
            WHERE designation LIKE '% %'
            RETURNING id;
        """))
        messier_count = len(result.all())
        print(f"✅ Updated {messier_count} Messier objects")

        print("🔍 Normalizing Image-Catalog Matches...")
        # Update image_catalog_matches
        # We also need to handle potential duplicates created by normalization
        # For simplicity in this script, we'll just update them. 
        # If duplicates occur, the dashboard might still show them until re-indexed,
        # but the group by will work better.
        result = await session.execute(text("""
            UPDATE image_catalog_matches 
            SET catalog_designation = REPLACE(catalog_designation, ' ', '')
            WHERE catalog_designation LIKE '% %'
            RETURNING id;
        """))
        match_count = len(result.all())
        print(f"✅ Updated {match_count} matches")

        await session.commit()
        print("🎉 Normalization complete!")

if __name__ == "__main__":
    asyncio.run(normalize_existing())
