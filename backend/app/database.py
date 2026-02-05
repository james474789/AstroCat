"""
AstroCat Database Configuration
Async SQLAlchemy engine and session management with PostGIS support.
"""

from sqlalchemy import text, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    pool_size=20,          # Increased pool size
    max_overflow=40,       # Increased overflow
    pool_pre_ping=True,    # Check connection health before use
    pool_recycle=3600,     # Recycle connections every hour
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Create sync engine and session factory for Celery tasks
# Derive sync URL from async URL by switching driver to psycopg2
_sync_db_url = settings.database_url.replace("+asyncpg", "+psycopg2")
sync_engine = create_engine(
    _sync_db_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,    # Robustness for sync operations too
    pool_recycle=3600,
)
SessionLocal = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base class for all models
Base = declarative_base()


async def get_db() -> AsyncSession:
    """
    Dependency that provides a database session.
    Use with FastAPI's Depends().
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database tables.
    For development/testing only - use Alembic migrations in production.
    """
    async with engine.begin() as conn:
        # Enable PostGIS extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connections."""
    await engine.dispose()
