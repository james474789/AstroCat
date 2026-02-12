# System Processors

This document describes the various background processors (Celery tasks) that power the AstroCat indexing and analysis pipeline. These processors are responsible for scanning files, plate solving, catalog matching, and maintaining system state.

The "Processors" status box in the Admin Dashboard reflects the activity of these tasks in the job queue.

## Indexer Processors
Located in: `backend/app/tasks/indexer.py`

These tasks manage the file system synchronization and initial ingestion of images.

### 1. `scan_directory`
*   **Task Name:** `app.tasks.indexer.scan_directory`
*   **Purpose:** Synchronizes the database with a specific directory on disk.
*   **Operation:**
    1.  Recursively walks the specified directory.
    2.  Identifies supported image files (`.fits`, `.jpg`, `.cr2`, etc.).
    3.  **New/Incomplete Files:** If a file is not in the DB, or is missing a thumbnail/plate solution, it queues a `process_image` task.
    4.  **Deleted Files:** Identifies files that exist in the DB but are no longer on disk and removes them.
    5.  Updates the database and returns a summary of changes.

### 2. `process_image`
*   **Task Name:** `app.tasks.indexer.process_image`
*   **Purpose:** Fully processes a single image file.
*   **Operation:**
    1.  **Metadata Extraction:** Reads FITS headers and other file metadata (Camera, Telescope, Exposure, Date, Object Name).
    2.  **Thumbnail Generation:** Creates a web-friendly thumbnail and caches it.
    3.  **Database Upsert:** Creates or updates the `Image` record with all extracted metadata.
    4.  **Geospatial Data:** Updates PostGIS geometry if WCS coordinates are present.
    5.  **Catalog Matching:** If the image is plate-solved, it immediately triggers the Catalog Matcher to find celestial objects in the field of view.

### 3. `reindex_all`
*   **Task Name:** `app.tasks.indexer.reindex_all`
*   **Purpose:** Triggers a full system re-scan of all configured paths.
*   **Operation:**
    1.  Sets a global "Running" flag in Redis.
    2.  Iterates through all configured image paths (settings).
    3.  Synchronously calls `scan_directory` for each path.
    4.  Updates global statistics in Redis (Total scanned, added, removed) for the Admin Dashboard.

### 4. `regenerate_thumbnails` / `regenerate_single_thumbnail`
*   **Task Names:** `app.tasks.indexer.regenerate_thumbnails`, `app.tasks.indexer.regenerate_single_thumbnail`
*   **Purpose:** Utility tasks to refresh thumbnails.
*   **Operation:**
    *   `regenerate_thumbnails`: Iterates all images in the DB and queues a single task for each.
    *   `regenerate_single_thumbnail`: Generates a thumbnail for a specific file and updates the DB path.

---

## Astrometry Processors
Located in: `backend/app/tasks/astrometry.py`

These tasks handle the interaction with plate-solving services (Astrometry.net or Local).

### 5. `rescan_image_task`
*   **Task Name:** `app.tasks.astrometry.rescan_image_task`
*   **Purpose:** Submits an image for plate solving.
*   **Operation:**
    1.  Checks system settings to determine the provider (Nova online or Local instance).
    2.  Updates image status to `SUBMITTED`.
    3.  Uploads the file to the Astrometry API.
    4.  Upon successful upload, transitions to polling (calls `monitor_submission_task` logic).

### 6. `monitor_submission_task`
*   **Task Name:** `app.tasks.astrometry.monitor_submission_task`
*   **Purpose:** Polls an external service for the status of a plate-solving job.
*   **Operation:**
    1.  Periodically polls the submission status endpoint.
    2.  **Success:**
        *   Retrieves WCS calibration data (RA, Dec, Pixel Scale, Rotation).
        *   Updates the `Image` record.
        *   Triggers `CatalogMatcher` to find objects for the newly solved image.
        *   Mark status as `SOLVED`.
    3.  **Failure:** Marks status as `FAILED`.
    4.  **Timeout:** If the job takes too long, marks as `FAILED`.

---

## Bulk Operation Processors
Located in: `backend/app/tasks/bulk.py`

These tasks handle batch operations triggered by the user, usually via the UI.

### 7. `bulk_match_task`
*   **Task Name:** `app.tasks.bulk.bulk_match_task`
*   **Purpose:** Recalculates catalog matches for a whole directory tree.
*   **Operation:**
    1.  Finds all *plate-solved* images in the specified directory tree.
    2.  Runs the Catalog Matcher for each image.
    3.  Reports real-time progress to Redis (processed count, errors) for UI progress bars.

### 8. `bulk_rescan_task`
*   **Task Name:** `app.tasks.bulk.bulk_rescan_task`
*   **Purpose:** Re-submits a batch of images for plate solving.
*   **Operation:**
    1.  Finds all images in the directory tree.
    2.  Filters based on `force` flag or if the image is unsolved/failed.
    3.  Queues individual `rescan_image_task` jobs for each eligible image.
    4.  Reports real-time progress to Redis.

---

## Maintenance & Periodic Tasks
Located in: `backend/app/worker.py` (Schedule) and task-specific files.

These tasks run automatically on a fixed schedule via **Celery Beat** to maintain system health and consistency.

### 9. `cleanup_stuck_astrometry`
*   **Task Name:** `app.tasks.astrometry.cleanup_stuck_astrometry`
*   **Purpose:** Rescues images that have crashed or timed out during the plate-solving process.
*   **Schedule:** Runs every 5 minutes.
*   **Operation:**
    1.  Scans the database for images stuck in `SUBMITTED` or `PROCESSING` states.
    2.  Check the "stale" threshold: If the image has not been updated in **5 minutes**, it is considered stuck.
    3.  Marks the image as `FAILED` so it can be manually or automatically retried later.
    4.  Logs a warning for each rescued image.

