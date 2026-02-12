# AstroCat Utility & Maintenance Scripts

Scripts are located in `backend/app/scripts/` and run inside the backend container:

```bash
docker exec AstroCat-backend python -m app.scripts.<script_name>
```

---

## Classification & Maintenance

| Script | Description |
|--------|-------------|
| `update_classifications` | Sets images in `/data/mount2/` to `INTEGRATION_MASTER` subtype. |
| `update_planetary` | Sets images in `/data/mount3/Planetary` to `PLANETARY` subtype. |
| `normalize_existing` | Normalizes object designations (e.g. "M 42" → "M42") across catalogs and matches. |
| `backfill_dimensions` | Populates missing `width_pixels`/`height_pixels` for images. |
| `fix_thumbnail_collisions` | Detects and resolves filename collisions in the thumbnail cache. |

## Catalog Management

| Script | Description |
|--------|-------------|
| `seed_named_stars` | Seeds the `named_star_catalog` table from `NamedStars.csv`. Truncates and re-inserts. |
| `rematch_all` | Re-runs catalog matching for all plate-solved images. |
| `rematch_catalogs` | Re-runs catalog matching with updated catalog data. |
| `rematch_debug` | Debug version of catalog rematching for troubleshooting individual images. |

## Troubleshooting & Diagnostics

| Script | Description |
|--------|-------------|
| `check_missing` | Compares files on disk against the database and reports unindexed files. |
| `check_db` | Health check: verifies tables exist and provides row counts. |
| `verify_count` | Quick summary of image counts, solved vs. unsolved, and format breakdown. |
| `debug_extractor` | Tests metadata extraction on a single file for debugging. |
| `monitor_progress` | Monitors the progress of a running bulk operation in real time. |

## Administration

| Script | Description |
|--------|-------------|
| `create_admin` | Creates an admin user account. |
| `initialize_db` | Runs database initialization (migrations + catalog seeding). |
| `reprocess_unsolved` | Re-queues unsolved images for astrometry processing. |
