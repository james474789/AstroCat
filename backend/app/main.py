"""
AstroCat FastAPI Application
Main entry point for the backend API server.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import init_db, close_db
from app.version import __version__

# API Routers (will be implemented in Phase 4)
# from app.api import images, search, catalogs, stats, indexer


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    Runs on startup and cleanup on shutdown.
    """
    # Startup
    from app.logging_config import setup_logging
    setup_logging(log_dir=settings.log_dir)
    
    print("🚀 Starting AstroCat Backend...")
    
    # Initialize database (creates tables if they don't exist)
    # In production, use Alembic migrations instead
    if settings.debug:
        await init_db()
        print("✅ Database initialized (debug mode)")
    
    print(f"📁 Watching image paths: {settings.image_paths_list}")
    print("✅ AstroCat Backend ready!")
    
    yield
    
    # Shutdown
    print("🛑 Shutting down AstroCat Backend...")
    await close_db()
    print("✅ Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="AstroCat",
    description="""
    ## Astronomical Image Database API
    
    AstroCat is a system for cataloging, indexing, and searching astronomical images.
    
    ### Features
    - **Image Indexing**: Automatically extract metadata from FITS, CR2, and JPEG files
    - **Plate Solving**: Support for WCS coordinates and sidecar files
    - **Catalog Matching**: Match images to Messier and NGC objects
    - **Advanced Search**: Search by coordinates, object names, exposure time, and more
    - **Thumbnail Generation**: On-demand thumbnail creation for quick previews
    """,
    version=__version__,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Configure rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)



# Configure CSRF protection middleware
class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.csrf_enabled:
            # Only enforce on unsafe methods for API routes
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api"):
                # Exempt login and initial admin setup (no CSRF token yet)
                if request.url.path not in {"/api/auth/login", "/api/auth/admin-sign-up"}:
                    cookie_token = request.cookies.get(settings.csrf_cookie_name)
                    header_token = request.headers.get(settings.csrf_header_name)

                    if not cookie_token or not header_token or cookie_token != header_token:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "CSRF validation failed"}
                        )

        return await call_next(request)


app.add_middleware(CSRFMiddleware)


# =============================================================================
# Health & Version Check Endpoints
# =============================================================================

@app.get("/", tags=["Health"])
async def root():
    """Root endpoint - API information."""
    return {
        "name": "AstroCat API",
        "version": __version__,
        "docs": "/api/docs",
        "status": "running"
    }


@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "service": "AstroCat-backend",
        "version": __version__
    }


@app.get("/api/version", tags=["Health"])
async def stack_version():
    """Comprehensive stack and component version inspection."""
    import sys
    import platform
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    version_info = {
        "app_version": __version__,
        "backend": {
            "version": __version__,
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
        },
        "database": {
            "status": "disconnected",
            "postgres_version": None,
            "postgis_version": None,
            "schema_revision": None,
        },
        "redis": {
            "status": "disconnected",
            "redis_version": None,
        }
    }

    # DB Checks
    try:
        async with AsyncSessionLocal() as session:
            pg_res = await session.execute(text("SELECT version()"))
            version_info["database"]["postgres_version"] = pg_res.scalar()

            gis_res = await session.execute(text("SELECT PostGIS_Version()"))
            version_info["database"]["postgis_version"] = gis_res.scalar()

            try:
                rev_res = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                version_info["database"]["schema_revision"] = rev_res.scalar()
            except Exception:
                version_info["database"]["schema_revision"] = "unversioned/initial"

            version_info["database"]["status"] = "connected"
    except Exception as e:
        version_info["database"]["error"] = str(e)

    # Redis Checks
    try:
        import redis.asyncio as redis_async
        r = redis_async.from_url(settings.redis_url)
        info = await r.info("server")
        version_info["redis"]["redis_version"] = info.get("redis_version")
        version_info["redis"]["status"] = "connected"
        await r.close()
    except Exception as e:
        version_info["redis"]["error"] = str(e)

    return version_info



@app.get("/api/health/db", tags=["Health"])
async def database_health():
    """Database connectivity check."""
    from sqlalchemy import text
    from app.database import AsyncSessionLocal
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            result.fetchone()
            
            # Check PostGIS extension
            postgis = await session.execute(text("SELECT PostGIS_Version()"))
            postgis_version = postgis.fetchone()[0]
            
        return {
            "status": "healthy",
            "database": "connected",
            "postgis_version": postgis_version
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e)
        }


@app.get("/api/health/redis", tags=["Health"])
async def redis_health():
    """Redis connectivity check."""
    import redis.asyncio as redis_async
    
    try:
        r = redis_async.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        return {
            "status": "healthy",
            "redis": "connected"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "redis": "disconnected",
            "error": str(e)
        }


from app.api import auth, images, catalogs, indexer, search, stats, fits_stats, admin, filesystem, users, settings as settings_api
from app.api.dependencies import get_current_user


# =============================================
# API Routers
# =============================================

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(users.router, prefix="/api/users", tags=["Users"], dependencies=[Depends(get_current_user)])
app.include_router(images.router, prefix="/api/images", tags=["Images"], dependencies=[Depends(get_current_user)])
app.include_router(catalogs.router, prefix="/api/catalogs", tags=["Catalogs"], dependencies=[Depends(get_current_user)])
app.include_router(indexer.router, prefix="/api/indexer", tags=["Indexer"], dependencies=[Depends(get_current_user)])
app.include_router(search.router, prefix="/api/search", tags=["Search"], dependencies=[Depends(get_current_user)])
app.include_router(stats.router, prefix="/api/stats", tags=["Statistics"], dependencies=[Depends(get_current_user)])
app.include_router(fits_stats.router, prefix="/api/stats/fits", tags=["fits-stats"], dependencies=[Depends(get_current_user)])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"], dependencies=[Depends(get_current_user)])
app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"], dependencies=[Depends(get_current_user)])
app.include_router(filesystem.router, prefix="/api/filesystem", tags=["Filesystem"], dependencies=[Depends(get_current_user)])



# =============================================================================
# Temporary placeholder endpoints (Removed as routers are active)
# =============================================================================


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )
