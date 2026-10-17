# Astrometry.net Rescan Process Documentation

This document describes the end-to-end technical workflow for the "Rescan" feature, which utilizes Astrometry.net to plate-solve images and update their celestial coordinates and metadata.

## Overview

The process is designed as an asynchronous background task to handle potentially long processing times (ranging from 1 to 10 minutes) on the Astrometry.net servers without blocking the user interface.

## Architecture

```mermaid
sequenceDiagram
    participant UI as Frontend (React)
    participant API as Backend (FastAPI)
    participant Worker as Background Worker (Celery)
    participant DB as Database (Postgres)
    participant Nova as Astrometry.net (External)

    UI->>API: POST /api/images/{id}/rescan
    API->>Nova: Login & Upload (Sync)
    Nova-->>API: Return submission_id
    API->>Worker: Dispatch monitor_submission_task
    API->>DB: Set submission_id & status = 'SUBMITTED'
    API-->>UI: 202 + submission_id
    
    loop Polling (Frontend)
        UI->>API: GET Image Details
        API-->>UI: Status: SUBMITTED/PROCESSING
    end
    
    loop Polling (Worker)
        Worker->>Nova: Check submission status
        Nova-->>Worker: Processing / Job ID
        Worker->>DB: Update Job ID & Status
        Worker->>Nova: Check job status
        Nova-->>Worker: Success / Failure
    end

    Worker->>Nova: Retrieve WCS Calibration
    Worker->>DB: Update RA/Dec, orientation, matches
    Worker->>DB: Set status = 'SOLVED'
    UI->>DB: Poll/Refresh -> SOLVED
```

## Step-by-Step Workflow

### 1. Trigger (Frontend)
The user clicks the **"🔭 Start Rescan"** button on the `ImageDetail` page. This sends a POST request to the backend.

### 2. Synchronous Upload (API)
The FastAPI endpoint executes the upload directly to ensure immediate feedback:
- **Environment Check**: Verifies `ASTROMETRY_API_KEY`.
- **Upload**: Calls the `AstrometryService` to upload the file to `nova.astrometry.net`.
- **Response**: Upon successful upload, it receives a `submission_id` and immediately returns it to the frontend with a `SUBMITTED` status.
- **Task Dispatch**: Simultaneously, it queues a **Celery Task** (`monitor_astrometry_task`) to handle the long-running polling.

### 3. Polling (Frontend)
The frontend receives the `submission_id` and enters a polling mode, refreshing the image details every 5 seconds to show progress (Status changes from `SUBMITTED` -> `PROCESSING` -> `SOLVED`).

### 4. Background Monitoring (Worker)
The `monitor_astrometry_task` runs in the background. Each run checks the submission once and re-schedules itself 15 seconds later, so no worker slot is held while the solver works:
- **Submission Check**: Polls the API to see if the submission has been assigned a `job_id`.
- **Job Check**: Once a job starts, it polls for the final status (`success` or `failure`).
- **Timeout**: After 20 minutes without a result the image is marked `FAILED`. `cleanup_stuck_astrometry` only fails images stuck in `SUBMITTED`/`PROCESSING` for 30 minutes, so it never races the monitor.

## Bulk Rescans

Bulk rescans (and "solve the overlay footprints") don't queue one Celery task per image. They add image IDs to the Redis sorted set `astrometry:pending` (imported-WCS upgrades first; images already pending keep their place). The `feed_astrometry_queue` task runs every 30 seconds from beat and after every finished solve. It dispatches `astrometry_task` only into free slots under `astrometry_max_submissions`. The indexer status endpoint reports the backlog as `astrometry_pending`; `redis-cli del astrometry:pending` cancels it.

## Hints

`build_solve_hints` derives a position hint (current RA/Dec) and a scale hint (field width in degrees, ±10%, since the upload is downsampled). An image whose previous attempt `FAILED` gets no hints. A local solver receives all hints; Nova receives only the scale hint.

### 5. Results Retrieval & DB Update
Once the job succeeds:
- **Calibration**: Downloads the WCS (World Coordinate System) data (RA, Dec, Pixel Scale, Rotation).
- **Database**: Updates the `Image` record with the new coordinates and sets `is_plate_solved = True`.
- **Geometry**: Updates the PostGIS `center_location` for spatial search.
- **Catalog Matching**: Automatically triggers a re-run of the local catalog matcher to identify objects (Messier/NGC) based on the new coordinates.

## Error Handling

- **Automatic Retries**: `astrometry_task` retries an upload up to 5 times (backoff from 1 to 15 minutes) for transient errors only: network/timeouts, solver 5xx/429, and NAS read hiccups (`is_retryable_error`). The image keeps its previous status between attempts. If retries run out, it is left as it was, so the next bulk rescan picks it up.
- **Status Updates**: Any other failure (missing or unreadable file, API login fail, 4xx, image won't solve) sets the status to `FAILED` immediately, with no retry.
- **Cleanup**: Temporary JPG files are deleted regardless of success or failure.

## Configuration

The system supports two plate-solving providers, configured in the `.env` file:

### Remote (Astrometry.net)
- `ASTROMETRY_API_KEY`: Your API key from [nova.astrometry.net](http://nova.astrometry.net/api_help).

### Local (StellarSolver-API)
- `LOCAL_ASTROMETRY_URL`: URL of the local solver (e.g. `http://localhost:8000/api`).
- `LOCAL_ASTROMETRY_API_KEY`: API key for the local solver.

The system checks settings to determine which provider to use. If a local solver is configured, it will be used instead of the remote service.
