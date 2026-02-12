import asyncio
import sys
from sqlalchemy import text
from app.database import AsyncSessionLocal

def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

async def verify():
    async with AsyncSessionLocal() as session:
        try:
            r = await session.execute(text("SELECT count(*) FROM named_star_catalog"))
            log(f"COUNT:{r.scalar()}")
        except Exception as e:
            log(f"ERROR:{e}")

if __name__ == "__main__":
    asyncio.run(verify())
