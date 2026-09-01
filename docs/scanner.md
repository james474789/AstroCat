# Indexer Scan Process

This document describes how AstroCat's indexer scan works end-to-end: how scans are triggered, how directories are diffed against the database, and how each discovered file is processed in the background.

**Source files:**

- `backend/app/api/indexer.py` — REST endpoints that trigger scans and report status
- `backend/app/tasks/indexer.py` — Celery tasks (`reindex_all`, `scan_directory`, `process_image`)
- `backend/app/tasks/bulk.py` — per-mount bulk operations
- `backend/app/services/matching.py` — `SyncCatalogMatcher` (catalog matching)
- `backend/app/worker.py` — Celery app configuration, queues, and beat schedule

## Overview

The scan is a **directory-diff fan-out**:

1. A synchronous walk per mount point diffs disk contents against the `images` table.
2. Files deleted from disk are tombstoned (bulk-deleted from the DB).
3. Each **new** file is enqueued as its own Celery task and processed in parallel across workers.
4. Each task extracts metadata, generates a thumbnail, upserts the record (with PostGIS geometry), and spatially matches catalogs.
5. Live state is tracked in Redis; statistics are rolled up periodically via Celery Beat.

## 1. Entry Points (`backend/app/api/indexer.py`)

The FastAPI layer does not scan anything itself — it queues Celery tasks and returns a `task_id` immediately:

| Endpoint | Action |
|---|---|
| `POST /api/indexer/scan` | Queues `reindex_all` — full scan of every configured mount point |
| `POST /api/indexer/folder/scan` | Queues `scan_directory(path)` for a single folder |
| `POST /api/indexer/batch/matches` | Bulk recalculation of catalog matches for a mount |
| `POST /api/indexer/batch/rescan` | Bulk re-run of astrometry (plate solving) for a mount, with optional `force` |
| `POST /api/indexer/batch/thumbnails` | Bulk thumbnail regeneration for a mount |
| `POST /api/indexer/batch/metadata` | Bulk metadata re-extraction for a mount |
| `GET /api/indexer/status` | Reads live scan state from Redis + mount stats from the DB |

**Path validation:** folder-scoped endpoints reject paths that do not start with `/data/` or that contain `..` (path traversal protection). All `/api/indexer/*` routes require authentication.

## 2. Full Scan — the `reindex_all` task

```
reindex_all (Celery task)
 ├─ Redis: set indexer:is_running = 1          <- UI "scan in progress" flag
 ├─ for each path in settings.image_paths_list  <- from IMAGE_PATHS env, e.g. /data/images,/data/mount2
 │    └─ _scan_directory(path)                 <- runs SYNCHRONOUSLY inside the worker
 └─ finally:
      Redis: last_scan_at, last_scan_duration, files_scanned/added/removed
      set is_running = 0
      -> update_mount_stats()                  <- per-mount counts/sizes into SystemStats table
```

Design note (deliberate, commented in code): each mount is walked **synchronously within one task** rather than spawning sub-scan tasks, so progress totals are simple to track and aggregate. The real parallelism happens at the per-file level (Section 4).

## 3. Directory Walk — `_scan_directory`

The core diffing logic, optimized to avoid per-file DB queries:

1. **Pre-fetch known files** — a single query pulls all rows where `file_path LIKE '{dir}%'` into an in-memory dict: `{file_path: pixinsight_annotation_path}`.
2. **`os.walk`** the directory, keeping only allowed extensions:
   `.fits .fit .xisf .jpg .jpeg .png .cr2 .cr3 .arw .nef .dng .tif .tiff`
3. **Three-way diff**:
   - **Existing file** → skipped for re-processing. The only check is a *backfill*: if a `*_Annotated` PixInsight sidecar now exists on disk but is not recorded in the DB, just that column is updated.
   - **New file** → `process_image.delay(path)` — **fan-out: one Celery task per file**, distributed across all workers.
   - **Files with `_Annotated` stems** are never indexed as independent images; they are annotation overlays attached to their parent image.
4. **Tombstoning** — `existing_DB_paths − disk_files` = files deleted from disk → bulk `DELETE` in batches of 500.
5. Returns `{files_found, files_queued, files_removed}`.

## 4. Per-File Processing — the `process_image` task

Runs on the Celery workers (8 workers via `CELERY_WORKERS` in docker-compose, routed to the `indexer` queue):

1. **Extract metadata** — the extractor factory (`backend/app/extractors/factory.py`) picks:
   - `FITSExtractor` (astropy) for `.fits` / `.fit`
   - `XISFExtractor` for `.xisf`
   - `ExifExtractor` (ExifRead + rawpy + Pillow) for everything else
   
   Strings are sanitized of `\x00` characters so they survive PostgreSQL JSONB storage.
   FITS header reads are fault tolerant: unparsable cards (e.g. `CCD-TEMP = -19.80C`
   written by some all-sky cameras) are skipped and logged via `_safe_get()` instead
   of aborting extraction. Non-finite floats (NaN/Inf) and datetimes in headers are
   normalized so `raw_header` always survives JSONB storage.
2. **Detect PixInsight annotation sidecar** — `{stem}_Annotated{ext}` next to the file, recorded as `pixinsight_annotation_path`.
3. **Generate thumbnail** — WebP written to the thumbnail cache, with **STF (screen-transfer-function) stretch applied for subframes** so linear FITS/RAW data is actually visible. Failures are logged and non-fatal (`thumbnail_path = None`).
4. **Upsert into `images`**:
   If metadata extraction fails entirely, `process_image` still inserts a minimal
   record (path, name, format, size, file dates) with `extraction_error` populated,
   so malformed files are indexed exactly once instead of being re-detected as
   "new" on every scan. Transient database errors are retried with backoff.
   - *New record*: full insert, including PostGIS geography — `ST_SetSRID(ST_MakePoint(ra, dec), 4326)` stored as `center_location`.
   - *Existing record*: metadata refresh, **but WCS fields are protected when `astrometry_status == "SOLVED"`** — header/sidecar re-extraction cannot clobber a plate-solve result obtained from Astrometry.net.
   - `capture_date` falls back to the file's modification time when metadata lacks it.
5. **Catalog matching** (only if plate-solved) — `SyncCatalogMatcher.match_image()`:
   - Deletes previous `AUTOMATIC` matches (manual matches are preserved).
   - Runs four PostGIS `ST_DWithin` queries against the **Messier**, **NGC**, **Caldwell**, and **Named Stars** catalogs (search radius = the image's field radius or 1°, converted degrees→meters via ×111,320).
   - **WCS pixel-bounds validation**: candidates found in the circular radius search are rejected unless they actually fall inside the rectangular image frame — this avoids false positives near the field edge.
   - Survivors are saved to `image_catalog_matches` with `angular_separation_degrees` and `confidence_score = 1.0 − dist/5`.
6. Commit.

## 5. Status & State Tracking

- **Redis keys** are the live status store:
  - `indexer:is_running` (`1` while a scan/bulk job is active)
  - `indexer:last_scan_at`, `indexer:last_scan_duration`
  - `indexer:files_scanned`, `indexer:files_added`, `indexer:files_updated`, `indexer:files_removed`
  - `indexer:process_failures`, `indexer:failed_files` (last 100) - per-scan processing
    failure tracking, reset at the start of each full scan and exposed via
    `GET /api/indexer/status`
  
  `GET /api/indexer/status` reads these and caches mount-point stats for 10 seconds.
- **Mount statistics** live in the `SystemStats` table (`category = "mount:{path}"`), populated by `update_mount_stats` via a per-mount `COUNT/SUM` aggregation — so the Admin page never has to aggregate the whole `images` table on request.
- **Bulk tasks** (`tasks/bulk.py`) set the same `indexer:is_running` lock, so any bulk operation also flips the UI into "running" state.
- **Celery Beat schedule** (in `backend/app/worker.py`):
  - `update_mount_stats` — every 60 seconds
  - `cleanup_stuck_astrometry` — every 5 minutes

## 6. Reliability Settings (`backend/app/worker.py`)

| Setting | Value | Purpose |
|---|---|---|
| `task_acks_late` | `True` | A task is only acknowledged after it completes |
| `task_reject_on_worker_lost` | `True` | A killed worker re-queues its file instead of losing it |
| `worker_prefetch_multiplier` | `1` | Fair distribution — one task at a time per worker process |
| `worker_max_tasks_per_child` | `100` | Worker process recycling (astropy/rawpy memory hygiene) |
| `task_time_limit` / `task_soft_time_limit` | 3600 s / 3000 s | Hard ceiling per task |
| `reindex_all` / `scan_directory` limits | 8 h / 6 h | Per-task override - NAS directory walks can exceed the global 50 min soft limit |
| Queue routing | `indexer.*` and `bulk.*` → `indexer` queue; `thumbnails.*` → `thumbnails` queue | Isolates workloads |

## Known Quirks (non-functional)

- `process_image` contains a duplicated PostGIS `center_location` update block — harmless redundancy.
- Celery tasks run with `task_acks_late=True`: application exceptions are acked as
  failures and NOT retried (only worker loss re-queues a task), which is why the
  extraction path is fault tolerant by design.
- `reindex_all` always writes `files_updated = 0` to Redis; updates are not currently counted separately from additions.

