import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from sqlalchemy.future import select
from sqlalchemy import func

from app.worker import celery_app
from app.database import AsyncSessionLocal
from app.models.image import Image
from app.services.astrometry_service import AstrometryService
from app.config import settings
import redis
import json

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, name="app.tasks.astrometry.astrometry_task", autoretry_for=(Exception,), retry_backoff=True, max_retries=None)
def astrometry_task(self, image_id: int):
    """
    Legacy background task to upload image to Astrometry.net and poll.
    Kept for backward compatibility or alternative use.
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(_rescan_logic(self, image_id))

@celery_app.task(bind=True, name="app.tasks.astrometry.monitor_astrometry_task", autoretry_for=(Exception,), retry_backoff=True, max_retries=None)
def monitor_astrometry_task(self, submission_id: str, image_id: int):
    """
    Background task to poll Astrometry.net for completion of an EXISTING submission.
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(_monitor_logic(submission_id, image_id))

async def _rescan_logic(task, image_id: int):
    # This logic duplicates the new flow but all in one background task
    # Determine Provider and Config
    # Check Redis for system setting
    provider = "nova"
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        sys_settings = r.get("system_settings")
        if sys_settings:
            provider = json.loads(sys_settings).get("astrometry_provider", "nova")
    except Exception as e:
        logger.error(f"Failed to read settings from Redis: {e}")

    api_key = settings.astrometry_api_key
    base_url = "http://nova.astrometry.net/api" # Default
    
    if provider == "local":
        api_key = settings.local_astrometry_api_key
        base_url = settings.local_astrometry_url
        if not api_key or not base_url:
             logger.error("Local astrometry config missing, falling back to Nova")
             provider = "nova"
             api_key = settings.astrometry_api_key
    
    if not api_key:
        logger.error("ASTROMETRY_API_KEY not set")
        return {"status": "error", "message": "API key missing"}

    # Check for submission limit (throttling) with a Redis lock to prevent race conditions
    max_submissions = 8
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        sys_settings = r.get("system_settings")
        if sys_settings:
            max_submissions = json.loads(sys_settings).get("astrometry_max_submissions", 8)
    except Exception as e:
        logger.error(f"Failed to read max_submissions from Redis: {e}")

    # Use a lock to ensure only one worker checks and reserves at a time
    lock = r.lock("astrometry:submission_lock", timeout=10)
    if lock.acquire(blocking=True, blocking_timeout=10):
        try:
            async with AsyncSessionLocal() as session:
                # Count images in non-terminal states
                active_count_stmt = select(func.count(Image.id)).where(
                    Image.astrometry_status.in_(["SUBMITTED", "PROCESSING"])
                )
                active_count_res = await session.execute(active_count_stmt)
                active_count = active_count_res.scalar() or 0

                if active_count >= max_submissions:
                    logger.info(f"[ASTROMETRY] Throttling: {active_count} active submissions >= limit {max_submissions}. Postponing image {image_id}")
                    # Release lock before retrying
                    lock.release()
                    task.retry(countdown=20)
                    return {"status": "throttled", "message": "Postponed due to limit"}

                stmt = select(Image).where(Image.id == image_id)
                result = await session.execute(stmt)
                image = result.scalar_one_or_none()
                if not image:
                    lock.release()
                    return {"status": "error", "message": "Image not found"}
                
                # If already submitted by another worker who got the lock before us, just return
                if image.astrometry_status in ["SUBMITTED", "PROCESSING", "SOLVED"]:
                    lock.release()
                    return {"status": "already_started"}

                file_path = image.file_path
                previous_status = image.astrometry_status
                image.astrometry_status = "SUBMITTED"
                image.plate_solve_provider = provider.upper() # NOVA or LOCAL
                await session.commit()
        finally:
            # Ensure lock is released if it hasn't been already (retry raises)
            try:
                lock.release()
            except:
                pass
    else:
        # Could not acquire lock within timeout
        logger.warning(f"[ASTROMETRY] Could not acquire submission lock for image {image_id}, retrying...")
        task.retry(countdown=5)
        return {"status": "throttled", "message": "Lock timeout"}
    
    
    # Prepare hints if available
    hints = {}
    
    if previous_status == 'FAILED':
        logger.info(f"[ASTROMETRY] Previous status was FAILED. Forcing blind solve (no hints).")
    else:
        if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
            hints["center_ra"] = image.ra_center_degrees
            hints["center_dec"] = image.dec_center_degrees
            # Default radius if not specified (5 degrees is generous but safe)
            hints["radius"] = image.field_radius_degrees or 5.0
            logger.info(f"[ASTROMETRY] Using position hints for image {image_id}: {hints}")
        
        if image.pixel_scale_arcsec is not None:
            hints["scale_units"] = "arcsecperpix"
            hints["scale_lower"] = image.pixel_scale_arcsec * 0.9
            hints["scale_upper"] = image.pixel_scale_arcsec * 1.1
            logger.info(f"[ASTROMETRY] Using scale hints for image {image_id}: {image.pixel_scale_arcsec} arcsec/pix")
    
    try:
        logger.info(f"[ASTROMETRY] Uploading image {image_id} to {provider} ({base_url})...")
        sub_data = await AstrometryService.upload_file(file_path, api_key, base_url, hints=hints)
        submission_id = sub_data.get("subid")
        
        if not submission_id:
            raise ValueError(f"No submission ID returned: {sub_data}")

        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            image.astrometry_submission_id = str(submission_id)
            image.astrometry_status = "PROCESSING"
            await session.commit()
            
        # Continue to polling
        return await _monitor_logic(submission_id, image_id)
            
    except Exception as e:
        logger.error(f"[ASTROMETRY] Upload failed: {e}")
        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            image.astrometry_status = "FAILED"
            await session.commit()
        raise e

async def _monitor_logic(submission_id: str | int, image_id: int):
    # Poll for Completion
    # Poll for Completion
    job_id = None
    max_attempts = 45 # 45 * 15s = 675s = 11.25 mins
    
    import asyncio as aio
    
    # Ensure submission_id is string/int handling? 
    # Usually it's an int from API but stored as string sometimes.
    
    # Check provider from image to know which URL to poll
    base_url = "http://nova.astrometry.net/api"
    async with AsyncSessionLocal() as session:
         stmt = select(Image).where(Image.id == image_id)
         img_check = (await session.execute(stmt)).scalar_one_or_none()
         if img_check and img_check.plate_solve_provider == "LOCAL":
             if settings.local_astrometry_url:
                 base_url = settings.local_astrometry_url

    for i in range(max_attempts):
        logger.info(f"[ASTROMETRY] Polling submission {submission_id} (Attempt {i+1})...")
        try:
            sub_status = await AstrometryService.get_submission_status(submission_id, base_url)
        except Exception as e:
            logger.warning(f"Poll check failed: {e}")
            await aio.sleep(15)
            continue
        
        jobs = sub_status.get("jobs", [])
        
        if jobs and jobs[0] is not None:
             job_id = jobs[0]
             
             async with AsyncSessionLocal() as session:
                stmt = select(Image).where(Image.id == image_id)
                image = (await session.execute(stmt)).scalar_one()
                if image.astrometry_job_id != str(job_id):
                    image.astrometry_job_id = str(job_id)
                    # We might want to set status to PROCESSING if not already
                    if image.astrometry_status == "SUBMITTED":
                         image.astrometry_status = "PROCESSING"
                    await session.commit()
             
             # Check JOB status
             job_status = await AstrometryService.get_job_status(job_id, base_url)
             status_str = job_status.get("status", "")
             
             if status_str == "success":
                 logger.info(f"[ASTROMETRY] Job {job_id} success! Retrieving results...")
                 break
             elif status_str == "failure":
                 logger.error(f"[ASTROMETRY] Job {job_id} failed.")
                 async with AsyncSessionLocal() as session:
                    stmt = select(Image).where(Image.id == image_id)
                    image = (await session.execute(stmt)).scalar_one()
                    image.astrometry_status = "FAILED"
                    await session.commit()
                 return {"status": "failed", "job_id": job_id}
        
        await aio.sleep(15)

    if not job_id:
        # Timed out or no job created
        logger.error(f"[ASTROMETRY] Timed out waiting for job creation for submission {submission_id}")
        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            image.astrometry_status = "FAILED"
            await session.commit()
        return {"status": "timeout"}

    # Get Results (WCS)
    try:
        cal = await AstrometryService.get_calibration(job_id, base_url)
        
        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            
            image.is_plate_solved = True
            image.ra_center_degrees = cal.get("ra")
            image.dec_center_degrees = cal.get("dec")
            image.field_radius_degrees = cal.get("radius")
            image.pixel_scale_arcsec = cal.get("pixscale")
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
        logger.error(f"[ASTROMETRY] Failed to save results: {e}")
        async with AsyncSessionLocal() as session:
            stmt = select(Image).where(Image.id == image_id)
            image = (await session.execute(stmt)).scalar_one()
            image.astrometry_status = "FAILED"
            await session.commit()
        raise e

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
            # Find images stuck in non-terminal states for > 5 mins
            # We use updated_at to judge "stuckness"
            five_mins_ago = datetime.utcnow() - timedelta(minutes=5)
            
            stmt = select(Image).where(
                Image.astrometry_status.in_(["SUBMITTED", "PROCESSING"]),
                Image.updated_at < five_mins_ago
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
