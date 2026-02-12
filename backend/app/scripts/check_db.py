import asyncio
from sqlalchemy import text, inspect
from app.database import AsyncSessionLocal, engine

async def check_db():
    print("Checking database...")
    async with AsyncSessionLocal() as session:
        # Check tables
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
                print(f"Tables: {tables}")
                
            if 'named_star_catalog' in tables:
                r = await session.execute(text('SELECT count(*) FROM named_star_catalog'))
                count = r.scalar()
                print(f"Named Stars Count: {count}")
                
                if count > 0:
                    r = await session.execute(text('SELECT * FROM named_star_catalog LIMIT 5'))
                    rows = r.fetchall()
                    print(f"Sample data: {rows}")
            else:
                print("named_star_catalog table NOT FOUND")
                
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_db())
