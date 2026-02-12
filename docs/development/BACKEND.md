# AstroCat Backend Documentation

The AstroCat backend is a high-performance Python application built with FastAPI. It handles metadata extraction, spatial data management, and background task orchestration.

## Core Structure

The backend is located in the `backend/` directory and follows a modular structure:

- `app/api/`: REST API route handlers.
  - `images.py`: Image CRUD, thumbnails, CSV export
  - `search.py`: Coordinate and catalog search
  - `catalogs.py`: Messier/NGC catalog browsing
  - `stats.py`: Statistics and aggregations
  - `fits_stats.py`: FITS-specific analytics
  - `indexer.py`: Scan control and job management
  - `admin.py`: Administration statistics and queue management
  - `filesystem.py`: Directory browser for mount points
  - `settings.py`: Application configuration
- `app/models/`: SQLAlchemy database models.
- `app/schemas/`: Pydantic models for request/response validation.
- `app/services/`: Business logic (matching, thumbnails, indexing).
- `app/extractors/`: Format-specific metadata extraction logic.
- `app/tasks/`: Celery task definitions.
- `app/data/`: Seed data for Messier, NGC, and Named Star catalogs.

## Metadata Extraction Pipeline

AstroCat supports a variety of astronomical and photographic file formats. The extraction logic is unified under a base extractor interface.

### Supported Formats
| Format | Tool | Notes |
|--------|------|-------|
| **FITS** | Astropy | Extracts WCS, exposure, and equipment info. |
| **XISF** | xisf | PixInsight native format. Extracts metadata and image data. |
| **CR2/CR3/NEF/ARW/DNG**| rawpy/Pillow | Extracts EXIF data and handles RAW processing. |
| **JPG/TIFF/PNG** | Pillow | Extracts standard EXIF metadata. |
| **Sidecar (.ini)**| configparser | Parses Astrometry.net output for plate-solved data. |

### Extraction Logic
The `FITSExtractor` is particularly robust, attempting to find WCS information even in non-standard headers by checking multiple keywords (CRVAL, CD matrix, CDELT, etc.). If header WCS is missing, it automatically looks for sidecar `.ini` files in the same directory.

## Background Tasks (Celery)

Background processing is essential for handling 40,000+ images without blocking the API.

- **`process_image`**: Extracts metadata, generates thumbnails, and runs catalog matching.
- **`scan_directory`**: Recursively walks mount points to identify new or updated files.
- **`generate_thumbnail`**: On-demand generation of previews to save initial indexing time and disk space.
- **`monitor_submission_task`**: Polls Astrometry.net for plate-solving completion. Implements **blind solve** logic: if a solved-with-hints attempt fails, it automatically retries without hints.

## Blind Solve
If an initial plate solve attempt (using extracted RA/DEC hints) fails, the backend automatically triggers a "blind" solve. This resubmits the image to Astrometry.net without coordinate hints, allowing it to search the entire sky, which is useful for images with missing or incorrect header data.

## Configuration

Configuration is managed via environment variables (usually stored in a `.env` file). Key settings include:

- `DATABASE_URL`: Connection string for PostgreSQL.
- `REDIS_URL`: Connection string for Celery/Redis.
- `IMAGE_PATHS`: Comma-separated list of host paths to monitor.
- `THUMBNAIL_CACHE_PATH`: Where generated thumbnails are stored (default: `/data/thumbnails`).

## API Documentation

FastAPI automatically generates interactive documentation available at:
- `/api/docs`: Swagger UI
- `/api/redoc`: ReDoc
