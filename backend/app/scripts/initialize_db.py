
import asyncio
import logging
import os
import subprocess
import sys
from sqlalchemy import text, inspect
from app.database import engine, Base
from alembic.config import Config
from alembic import command

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

    # 1. Enable PostGIS extension
    async with engine.begin() as conn:
        logger.info("Ensuring PostGIS extension is enabled...")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        
        # 2. Check for existing tables
        def get_tables(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_table_names()
        
        tables = await conn.run_sync(get_tables)
        logger.info(f"Found tables: {tables}")

        if not tables:
            logger.info("No tables detected. Performing fresh install setup...")
            
            # Create all tables from SQLAlchemy models
            # This ensures the schema is complete for a fresh install
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ All tables created successfully.")
            
            # We must stamp the migration history so Alembic knows we are at 'head'
            # since the tables already reflect the latest models.
            logger.info("Stamping database with Alembic 'head'...")
            
            # Run python -m alembic stamp head
            try:
                subprocess.run([sys.executable, "-m", "alembic", "stamp", "head"], check=True)
                logger.info("✅ Alembic stamped to head.")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning(f"⚠️ Failed to stamp Alembic via subprocess: {e}")
                logger.info("Falling back to internal alembic command...")
                alembic_cfg = Config("alembic.ini")
                command.stamp(alembic_cfg, "head")
                logger.info("✅ Alembic stamped (fallback).")

        else:
            logger.info("Existing tables detected. Running any pending migrations...")
            # Run migrations to bring the database up to date
            try:
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
                logger.info("✅ Migrations completed successfully.")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning(f"⚠️ Failed to run migrations via subprocess: {e}")
                logger.info("Falling back to internal alembic command...")
                alembic_cfg = Config("alembic.ini")
                try:
                    command.upgrade(alembic_cfg, "head")
                    logger.info("✅ Migrations completed (fallback).")
                except Exception as ex:
                    logger.error(f"❌ Internal Alembic upgrade failed: {ex}")
                    raise ex

    logger.info("🎉 Database initialization complete!")

if __name__ == "__main__":
    asyncio.run(initialize_db())
