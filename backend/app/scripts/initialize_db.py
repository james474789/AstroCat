
import asyncio
import logging
import os
import subprocess
import sys
from sqlalchemy import text, inspect
from app.database import engine, Base
from alembic.config import Config
from alembic import command

# Import all models to register them with Base.metadata
# This must happen before create_all() is called
from app.models import (
    Image, ImageSubtype, ImageFormat,
    MessierCatalog, NGCCatalog,
    ImageCatalogMatch, CatalogType,
    User, SystemStats
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def initialize_db():
    """
    Initialize the database for production.
    If the database is empty, it creates all tables and stamps Alembic to head.
    If the database exists, it runs migrations to reach head.
    """
    logger.info("🚀 Starting database initialization...")

    # 1. Enable PostGIS extension and create tables if missing
    is_fresh_install = False
    async with engine.begin() as conn:
        logger.info("Ensuring PostGIS extension is enabled...")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        
        # 2. Check for existing application tables
        # Use 'images' table as a marker for a previous install
        def check_if_fresh(sync_conn):
            inspector = inspect(sync_conn)
            return 'images' not in inspector.get_table_names()
        
        is_fresh_install = await conn.run_sync(check_if_fresh)
        logger.info(f"Is fresh install? {is_fresh_install}")

        if is_fresh_install:
            logger.info("Baseline tables ('images') not detected. Creating baseline schema...")
            def create_tables(sync_conn):
                Base.metadata.create_all(sync_conn)
            await conn.run_sync(create_tables)
            logger.info("✅ All tables created successfully.")

    # 3. Perform Alembic operations outside the transaction block
    # so they can see the committed tables.
    PRE_OPTIMIZATION_REVISION = 'eff1c1ee4206'
    if is_fresh_install:
        logger.info(f"Stamping database with baseline revision '{PRE_OPTIMIZATION_REVISION}'...")
        try:
            subprocess.run([sys.executable, "-m", "alembic", "stamp", PRE_OPTIMIZATION_REVISION], check=True)
            logger.info(f"✅ Alembic stamped to {PRE_OPTIMIZATION_REVISION}.")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"⚠️ Failed to stamp via subprocess, trying internal: {e}")
            alembic_cfg = Config("alembic.ini")
            await asyncio.to_thread(command.stamp, alembic_cfg, PRE_OPTIMIZATION_REVISION)

    logger.info("Running migrations to reach latest 'head'...")
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        logger.info("✅ Head revision reached.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"⚠️ Failed to upgrade via subprocess, trying internal: {e}")
        alembic_cfg = Config("alembic.ini")
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    logger.info("🎉 Database initialization complete!")

if __name__ == "__main__":
    asyncio.run(initialize_db())
