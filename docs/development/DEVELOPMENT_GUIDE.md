# AstroCat Development Guide

This guide provides instructions for setting up a local development environment and contributing to the AstroCat project.

## Prerequisites

- **Python 3.12+**
- **Node.js 20+** (with npm)
- **Docker** and **Docker Compose**
- **Git**

## Local Environment Setup

### 1. Database (PostgreSQL + PostGIS)
The easiest way to run the database is via Docker:
```bash
docker-compose up -d db redis
```

### 2. Backend (FastAPI)
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # Configure your settings
uvicorn app.main:app --reload --port 8089
```

### 3. Frontend (React)
```bash
cd frontend
npm install
npm run dev
```

## Common Workflows

### Database Migrations
We use Alembic for schema changes.
- **Create a migration**: `alembic revision --autogenerate -m "description"`
- **Apply migrations**: `alembic upgrade head`

### Seeding Catalogs
Catalogs are seeded automatically on first run. To manually reseed:
```bash
docker exec AstroCat-backend python -m app.data.seed
docker exec AstroCat-backend python -m app.scripts.seed_named_stars
```

### Triggering a Re-scan
You can trigger a directory scan via the API Docs (`/api/docs`) or the Settings page in the UI. This will populate the `images` table and start the background tasks for metadata extraction.

### Utility & Maintenance Scripts
For information on bulk updates, database cleanup, and catalog seeding, see [UTILITY_SCRIPTS.md](UTILITY_SCRIPTS.md).

## Troubleshooting

- **PostGIS Errors**: Ensure you are using the `postgis/postgis` Docker image, not a standard PostgreSQL image.
- **FITS Header Warnings**: These are common and often ignored; the extractor is designed to handle many non-standard formats.
- **Celery Worker**: If background tasks aren't running (e.g. processing or rescan stays stuck):
  1. Ensure the worker is started: `celery -A app.worker worker --loglevel=info` (or via docker-compose).
  2. Check `ASTROMETRY_API_KEY` is set in `.env` if plate solving fails.
