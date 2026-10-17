import asyncio
import logging
import math
import os
import time
from datetime import datetime, timedelta
from sqlalchemy.future import select
from sqlalchemy import func

from app.worker import celery_app
from app.database import AsyncSessionLocal, SessionLocal
from app.models.image import Image
from app.services.astrometry_service import AstrometryService, NOVA_API_URL
from app.tasks.indexer import _is_transient_error
from app.utils.plate_scale import solved_pixel_scale
from app.config import settings
import httpx
import redis
import json

logger = logging.getLogger(__name__)

# Images waiting for a submission slot: a Redis sorted set, lowest score first.
# Bulk runs add to it instead of fanning out one Celery task per image, which
# left thousands of tasks parked on the default queue ahead of the beat jobs.
PENDING_KEY = "astrometry:pending"
# Images handed to astrometry_task that have not claimed a slot yet (score =
# dispatch time), so the feeder doesn't over-dispatch while the worker is busy.
DISPATCHED_KEY = "astrometry:dispatched"
DISPATCH_TTL = 600
PRIORITY_OFFSET = 1e10  # imported-WCS upgrades go ahead of plain unsolved images

POLL_INTERVAL = 15
# How long to wait for the solver after upload. STUCK_AFTER must stay longer, or
# cleanup_stuck_astrometry fails submissions that are still being polled.
MONITOR_TIMEOUT = 20 * 60
STUCK_AFTER = timedelta(minutes=30)

MAX_UPLOAD_RETRIES = 5
ACTIVE_STATUSES = ("SUBMITTED", "PROCESSING")


def calibration_has_solution(cal) -> bool:
    """True when an astrometry.net calibration carries a usable centre."""
    if not isinstance(cal, dict):
        return False
    try:
        ra, dec = float(cal["ra"]), float(cal["dec"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(ra) and math.isfinite(dec)


def is_retryable_error(exc: Exception) -> bool:
    """
    Network blips, solver 5xx/429 and NAS read hiccups. Anything else (missing
    or unreadable file, rejected login, 4xx) fails the same way on every retry.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    if isinstance(exc, httpx.TransportError):
        return True
    return _is_transient_error(exc)


def build_solve_hints(image, previous_status) -> dict:
    """
    Solver hints from the image's current position and plate scale. A retry
    after FAILED gets none, since bad hints are a common reason for failing.
    """
    hints = {}
    if previous_status == "FAILED":
        logger.info(f"[ASTROMETRY] Previous status was FAILED. Forcing blind solve (no hints).")
        return hints

    if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
        hints["center_ra"] = image.ra_center_degrees
        hints["center_dec"] = image.dec_center_degrees
        # Default radius if not specified (5 degrees is generous but safe)
        hints["radius"] = image.field_radius_degrees or 5.0
        logger.info(f"[ASTROMETRY] Using position hints for image {image.id}: {hints}")

    if image.pixel_scale_arcsec and image.width_pixels:
        # The upload is a downsampled JPEG, so an arcsec/px hint would be
        # off by the resize factor; the field width in degrees is not.
        width_deg = image.pixel_scale_arcsec * image.width_pixels / 3600.0
        hints["scale_units"] = "degwidth"
        hints["scale_lower"] = width_deg * 0.9
        hints["scale_upper"] = width_deg * 1.1
        logger.info(f"[ASTROMETRY] Using scale hints for image {image.id}: {width_deg:.3f} deg wide")
    return hints


def _redis():
    return redis.from_url(settings.redis_url, decode_responses=True)


def _system_settings() -> dict:
    try:
        raw = _redis().get("system_settings")
        return json.loads(raw) if raw else {}
    except Exception as e:
        logger.error(f"Failed to read settings from Redis: {e}")
        return {}


def _max_submissions() -> int:
    return int(_system_settings().get("astrometry_max_submissions", 8))


def _solver_config():
    """(provider, api_key, base_url) for the configured solver."""
    provider = _system_settings().get("astrometry_provider", "nova")
    api_key = settings.astrometry_api_key
    base_url = NOVA_API_URL

    if provider == "local":
        api_key = settings.local_astrometry_api_key
        base_url = settings.local_astrometry_url
        if not api_key or not base_url:
            logger.error("Local astrometry config missing, falling back to Nova")
            provider = "nova"
            api_key = settings.astrometry_api_key
            base_url = NOVA_API_URL
    return provider, api_key, base_url


def enqueue_astrometry(image_ids, priority: bool = False, front: bool = False) -> int:
    """
    Add images to the pending set. Images already pending keep their place, so
    re-running a bulk rescan doesn't duplicate work. Returns how many were new.
    """
    ids = list(image_ids)
    if not ids:
        return 0
    base = time.time()
    if priority:
        base -= PRIORITY_OFFSET
    if front:
        base -= 2 * PRIORITY_OFFSET
    r = _redis()
    added = 0
    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        mapping = {str(image_id): base + (start + n) * 1e-3 for n, image_id in enumerate(chunk)}
        added += r.zadd(PENDING_KEY, mapping, nx=True)
    return added


def pending_astrometry_count() -> int:
    try:
        return _redis().zcard(PENDING_KEY)
    except Exception:
        return 0


@celery_app.task(name="app.tasks.astrometry.feed_astrometry_queue")
def feed_astrometry_queue():
    """
    Dispatch pending images into free submission slots. Runs from beat and
    after every finished solve.
    """
    r = _redis()
    lock = r.lock("astrometry:feed_lock", timeout=30)
    if not lock.acquire(blocking=False):
        return 0
    try:
        if not r.zcard(PENDING_KEY):
            return 0
        r.zremrangebyscore(DISPATCHED_KEY, "-inf", time.time() - DISPATCH_TTL)
        with SessionLocal() as session:
            active = session.query(func.count(Image.id)).filter(
                Image.astrometry_status.in_(ACTIVE_STATUSES)
            ).scalar() or 0
        free = _max_submissions() - active - r.zcard(DISPATCHED_KEY)
        if free <= 0:
            return 0

        popped = r.zpopmin(PENDING_KEY, free)
        now = time.time()
        for member, _score in popped:
            r.zadd(DISPATCHED_KEY, {member: now})
            astrometry_task.delay(int(member))
        if popped:
            logger.info(f"[ASTROMETRY] Dispatched {len(popped)} pending image(s); {r.zcard(PENDING_KEY)} still pending")
        return len(popped)
    finally:
        try:
            lock.release()
        except Exception:
            pass


@celery_app.task(bind=True, name="app.tasks.astrometry.astrometry_task", max_retries=MAX_UPLOAD_RETRIES)
def astrometry_task(self, image_id: int):
    """
    Upload one image to the solver, then hand off to monitor_astrometry_task so
    no worker slot is held while the solve runs.
    """
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_submit(self, image_id))
    finally:
        try:
            _redis().zrem(DISPATCHED_KEY, str(image_id))
        except Exception:
            pass


@celery_app.task(bind=True, name="app.tasks.astrometry.monitor_astrometry_task")
def monitor_astrometry_task(self, submission_id: str, image_id: int, started_at: float | None = None):
    """
    Check an EXISTING submission once, and re-schedule itself until the solve
    finishes or MONITOR_TIMEOUT passes.
    """
    started_at = started_at or time.time()
    loop = asyncio.get_event_loop()
    try:
        result = loop.run_until_complete(_check_submission(submission_id, image_id))
    except Exception as e:
        # Only retryable errors get here (see _apply_solution); poll again.
        logger.warning(f"[ASTROMETRY] Check of submission {submission_id} failed: {e}")
        result = None

    if result is None and time.time() - started_at > MONITOR_TIMEOUT:
        logger.error(f"[ASTROMETRY] Timed out waiting for submission {submission_id} (image {image_id})")
        loop.run_until_complete(_set_status(image_id, "FAILED"))
        result = {"status": "timeout"}

    if result is None:
        monitor_astrometry_task.apply_async((submission_id, image_id, started_at), countdown=POLL_INTERVAL)
        return {"status": "pending"}

    feed_astrometry_queue.delay()
    return result


async def _set_status(image_id: int, status: str, **fields):
    async with AsyncSessionLocal() as session:
        stmt = select(Image).where(Image.id == image_id)
        image = (await session.execute(stmt)).scalar_one_or_none()
        if image is None:
            return
        image.astrometry_status = status
        for name, value in fields.items():
            setattr(image, name, value)
        await session.commit()


async def _submit(task, image_id: int):
    provider, api_key, base_url = _solver_config()
    if not api_key:
        logger.error("ASTROMETRY_API_KEY not set")
        return {"status": "error", "message": "API key missing"}

    # Re-check the submission limit under a Redis lock so two workers can't
    # both take the last slot. Over the limit, the image goes back to the
    # front of the pending set for the feeder to retry.
    r = _redis()
    lock = r.lock("astrometry:submission_lock", timeout=10)
    if not lock.acquire(blocking=True, blocking_timeout=10):
        logger.warning(f"[ASTROMETRY] Could not acquire submission lock for image {image_id}, re-queuing")
        enqueue_astrometry([image_id], front=True)
        return {"status": "throttled", "message": "Lock timeout"}
    try:
        async with AsyncSessionLocal() as session:
            active_count_stmt = select(func.count(Image.id)).where(
                Image.astrometry_status.in_(ACTIVE_STATUSES)
            )
            active_count = (await session.execute(active_count_stmt)).scalar() or 0
            max_submissions = _max_submissions()
            if active_count >= max_submissions:
                logger.info(f"[ASTROMETRY] Throttling: {active_count} active submissions >= limit {max_submissions}. Re-queuing image {image_id}")
                enqueue_astrometry([image_id], front=True)
                return {"status": "throttled", "message": "Postponed due to limit"}

            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one_or_none()
            if not image:
                return {"status": "error", "message": "Image not found"}

            # Already submitted by another worker that got the lock first
            if image.astrometry_status in ["SUBMITTED", "PROCESSING", "SOLVED"]:
                return {"status": "already_started"}

            file_path = image.file_path
            previous_status = image.astrometry_status
            hints = build_solve_hints(image, previous_status)
            image.astrometry_status = "SUBMITTED"
            image.plate_solve_provider = provider.upper()  # NOVA or LOCAL
            await session.commit()
    finally:
        try:
            lock.release()
        except Exception:
            pass

    try:
        logger.info(f"[ASTROMETRY] Uploading image {image_id} to {provider} ({base_url})...")
        sub_data = await AstrometryService.upload_file(file_path, api_key, base_url, hints=hints)
        submission_id = sub_data.get("subid")
        if not submission_id:
            raise ValueError(f"No submission ID returned: {sub_data}")
    except Exception as e:
        if is_retryable_error(e) and task.request.retries < task.max_retries:
            logger.warning(f"[ASTROMETRY] Upload failed for image {image_id} ({type(e).__name__}: {e}); will retry")
            # Put the status back so the retry is treated like the first attempt.
            await _set_status(image_id, previous_status)
            raise task.retry(exc=e, countdown=min(60 * 2 ** task.request.retries, 900))
        logger.error(f"[ASTROMETRY] Upload failed for image {image_id}: {type(e).__name__}: {e}")
        await _set_status(image_id, "FAILED")
        return {"status": "failed", "message": f"{type(e).__name__}: {e}"}

    await _set_status(image_id, "PROCESSING", astrometry_submission_id=str(submission_id))
    monitor_astrometry_task.apply_async((submission_id, image_id, time.time()), countdown=POLL_INTERVAL)
    return {"status": "submitted", "submission_id": submission_id}


async def _monitor_base_url(image_id: int) -> str:
    async with AsyncSessionLocal() as session:
        stmt = select(Image).where(Image.id == image_id)
        img_check = (await session.execute(stmt)).scalar_one_or_none()
    if img_check and img_check.plate_solve_provider == "LOCAL" and settings.local_astrometry_url:
        return settings.local_astrometry_url
    return NOVA_API_URL


async def _check_submission(submission_id: str | int, image_id: int):
    """One poll of the solver. None while the solve is still running, else the final result."""
    base_url = await _monitor_base_url(image_id)
    logger.info(f"[ASTROMETRY] Polling submission {submission_id}...")
    try:
        sub_status = await AstrometryService.get_submission_status(submission_id, base_url)
        jobs = sub_status.get("jobs", [])
        if not jobs or jobs[0] is None:
            return None
        job_id = jobs[0]

        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            if image.astrometry_job_id != str(job_id):
                image.astrometry_job_id = str(job_id)
                if image.astrometry_status == "SUBMITTED":
                    image.astrometry_status = "PROCESSING"
                await session.commit()

        job_status = await AstrometryService.get_job_status(job_id, base_url)
        status_str = job_status.get("status", "")
    except Exception as e:
        logger.warning(f"Poll check failed: {e}")
        return None

    if status_str == "success":
        logger.info(f"[ASTROMETRY] Job {job_id} success! Retrieving results...")
        return await _apply_solution(submission_id, image_id, job_id, base_url)
    if status_str == "failure":
        logger.error(f"[ASTROMETRY] Job {job_id} failed.")
        await _set_status(image_id, "FAILED")
        return {"status": "failed", "job_id": job_id}
    return None


async def _apply_solution(submission_id: str | int, image_id: int, job_id, base_url: str):
    try:
        cal = await AstrometryService.get_calibration(job_id, base_url)

        # A "success" job can still return an empty calibration; writing it
        # would null out an existing (e.g. PixInsight) solution while marking
        # the image SOLVED.
        if not calibration_has_solution(cal):
            logger.error(f"[ASTROMETRY] Job {job_id} returned no usable calibration ({cal!r}); not applying.")
            await _set_status(image_id, "FAILED")
            return {"status": "failed", "job_id": job_id, "reason": "empty_calibration"}

        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()

            image.is_plate_solved = True
            image.ra_center_degrees = cal.get("ra")
            image.dec_center_degrees = cal.get("dec")
            image.field_radius_degrees = cal.get("radius")
            # cal["pixscale"] is per pixel of the downsampled upload; convert to
            # the original image's pixels.
            image.pixel_scale_arcsec = solved_pixel_scale(cal, image.width_pixels, image.height_pixels)
            image.rotation_degrees = cal.get("orientation")

            if image.raw_header is None:
                image.raw_header = {}
            new_header = dict(image.raw_header)
            new_header["astrometry_parity"] = cal.get("parity")
            image.raw_header = new_header

            if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
                image.center_location = func.ST_SetSRID(
                    func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)),
                    4326
                )

            # Construct user-facing URL if possible
            if base_url and "nova" in base_url:
                 image.astrometry_url = f"http://nova.astrometry.net/status/{submission_id}"
            else:
                 # Local URL might not have a nice frontend? Use base_url for now or leave blank/generic
                 image.astrometry_url = f"{base_url}/status/{submission_id}"

            image.astrometry_status = "SOLVED"
            image.plate_solve_source = "SOLVER"

            await session.commit()

            # Trigger matching
            from app.services.matching import CatalogMatcher
            matcher = CatalogMatcher(session)
            await matcher.match_image(image.id)
            await session.commit()

            # Assign Target (F2), after catalog matching has run for this solve.
            from app.services.targets import assign_target_async
            await assign_target_async(session, image)
            await session.commit()

            # --- Download Annotated Image ---
            try:
                annotated_path = os.path.join(settings.thumbnail_cache_path, f"annotated_{image_id}.jpg")
                logger.info(f"[ASTROMETRY] Downloading annotated image to {annotated_path}")
                await AstrometryService.download_annotated_image(job_id, annotated_path, base_url)
            except Exception as e:
                logger.error(f"[ASTROMETRY] Failed to download annotated image: {e}")
                # Don't fail the whole task, just log it

            # --- Fetch WCS File (SIP Distortion) ---
            try:
                logger.info(f"[ASTROMETRY] Fetching WCS file for job {job_id}...")
                wcs_header = await AstrometryService.get_wcs_file(job_id, base_url)

                # Save to DB
                async with AsyncSessionLocal() as session:
                    stmt = select(Image).where(Image.id == image_id)
                    image = (await session.execute(stmt)).scalar_one()
                    image.wcs_header = wcs_header
                    await session.commit()

                logger.info(f"[ASTROMETRY] WCS header saved for image {image_id}")
            except Exception as e:
                logger.error(f"[ASTROMETRY] Failed to fetch/save WCS file: {e}")

            return {"status": "solved", "job_id": job_id}

    except Exception as e:
        if is_retryable_error(e):
            # The job is done on the solver side; the next poll applies it again.
            raise
        logger.error(f"[ASTROMETRY] Failed to save results: {e}")
        await _set_status(image_id, "FAILED")
        return {"status": "failed", "job_id": job_id, "reason": str(e)}

def _clear_stale_indexer_running_flag(stale_after_seconds: int = 1800):
    """
    Clear a stuck indexer:is_running flag.

    reindex_all writes a heartbeat (indexer:last_heartbeat, TTL 1h) after each
    mount and removes it when it finishes. If the flag is still '1' but the
    heartbeat is missing or older than stale_after_seconds, the scan task was
    killed without its finally block running (container restart, hard time
    limit, lost worker) - clear the flag so the Admin UI stops showing
    'Scanning Files...' forever. Bulk operation hashes that report fresh
    progress also keep the flag alive.
    """
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        if r.get("indexer:is_running") != "1":
            return

        heartbeat = r.get("indexer:last_heartbeat")
        if heartbeat:
            try:
                hb_dt = datetime.fromisoformat(heartbeat)
                if (datetime.utcnow() - hb_dt).total_seconds() < stale_after_seconds:
                    return  # scan is actively alive
            except ValueError:
                pass

        # Don't clear while a bulk operation reports recent progress.
        for key in r.scan_iter("bulk:*"):
            updated = r.hget(key, "updated_at")
            if updated:
                try:
                    if int(time.time()) - int(updated) < 600:
                        return
                except (ValueError, TypeError):
                    continue

        logger.warning("Clearing stale indexer:is_running flag (no recent heartbeat or bulk activity)")
        r.set("indexer:is_running", "0")
    except Exception as e:
        logger.warning(f"Failed to clear stale indexer:is_running flag: {e}")


@celery_app.task(name="app.tasks.astrometry.cleanup_stuck_astrometry")
def cleanup_stuck_astrometry():
    """
    Periodic task to mark images as FAILED if they have been in SUBMITTED or PROCESSING
    status for more than 30 minutes.
    """
    import asyncio
    from datetime import datetime, timedelta
    
    async def _cleanup():
        logger.info("Running stuck astrometry cleanup...")
        try:
            _clear_stale_indexer_running_flag()
        except Exception as e:
            logger.warning(f"Failed to check/clear stale indexer flag: {e}")
        async with AsyncSessionLocal() as session:
            # Find images stuck in non-terminal states for longer than the
            # monitor would ever poll them. We use updated_at to judge "stuckness"
            cutoff = datetime.utcnow() - STUCK_AFTER
            
            stmt = select(Image).where(
                Image.astrometry_status.in_(["SUBMITTED", "PROCESSING"]),
                Image.updated_at < cutoff
            )
            
            result = await session.execute(stmt)
            stuck_images = result.scalars().all()
            
            if not stuck_images:
                return 0
                
            count = 0
            for img in stuck_images:
                logger.warning(f"Marking image {img.id} as FAILED due to timeout (stuck in {img.astrometry_status} since {img.updated_at})")
                img.astrometry_status = "FAILED"
                count += 1
            
            await session.commit()
            return count

    loop = asyncio.get_event_loop()
    return loop.run_until_complete(_cleanup())
