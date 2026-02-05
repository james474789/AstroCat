# AstroCat Utility & Maintenance Scripts

This document describes the various utility and maintenance scripts available in the `backend/app/scripts/` directory. These scripts are typically run within the backend container.

## Running Scripts

To run any of the scripts listed below, use the following pattern:

```bash
docker exec AstroCat-backend python -m app.scripts.<script_name>
```

---

## Classification & Maintenance

### update_classifications
Updates the `subtype` of images based on their file path. 
- **Rule**: If an image is located in `/data/mount2/`, it is classified as `INTEGRATION_MASTER`.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.update_classifications`

### normalize_existing
Normalizes object designations in the database (e.g., removing spaces from "M 42" to "M42") across catalogs and matches.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.normalize_existing`

---

## Catalog Management

### seed_named_stars
Seeds the `named_star_catalog` table from the included `NamedStars.csv` file. It truncates the existing table before re-inserting and updating PostGIS geography columns.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.seed_named_stars`

### rematch_all
Re-runs catalog matching for all images that have been successfuly plate-solved. Useful after updating catalogs or matching logic.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.rematch_all`

---

## Troubleshooting & Diagnostics

### check_missing
Scans the configured `IMAGE_PATHS` on disk and compares them against the database. Reports any files found on disk that haven't been indexed.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.check_missing`

### check_db
Performs a basic health check of the database, verifies table existence, and provides row counts for main catalogs.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.check_db`

### verify_count
Provides a quick summary count of images, solved vs. unsolved, and formats in the database.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.verify_count`

---

## Additional Maintenance Scripts

### update_planetary
Updates images in `/data/mount3/Planetary` to have subtype `PLANETARY`.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.update_planetary`

### populate_headers
Re-populates raw_header JSONB fields for images missing header data.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.populate_headers`

### reprocess_unsolved
Re-queues unsolved images for astrometry processing.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.reprocess_unsolved`

### rematch_debug
Debug version of catalog rematching for troubleshooting.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.rematch_debug`

### debug_extractor
Tests metadata extraction on a single file for debugging.
- **Usage**: `docker exec AstroCat-backend python -m app.scripts.debug_extractor`

