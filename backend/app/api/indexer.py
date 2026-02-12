import logging
from fastapi import APIRouter, BackgroundTasks
from app.tasks.indexer import reindex_all
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/scan")
async def trigger_scan(background_tasks: BackgroundTasks):
    """Trigger full re-index of all configured paths."""
    task = reindex_all.delay()
    return {"message": "Scan started", "task_id": task.id}



@router.post("/batch/matches")
async def trigger_bulk_matches(payload: dict, background_tasks: BackgroundTasks):
    """
    Trigger bulk recalculation of matches for a mount point.
    payload: {"path": "/data/mount1"}
    """
    path = payload.get("path")
    if not path:
        return {"error": "Path required"}
    
    # Input Validation: Ensure path is within /data/
    if not path.startswith("/data/"):
         logger.warning(f"Invalid path for bulk match: {path}")
         return {"error": "Invalid path. Must start with /data/"}

    # Prevent traversal
    if ".." in path:
         logger.warning(f"Path traversal attempt in bulk match: {path}")
         return {"error": "Invalid path"}

    logger.info(f"Triggering bulk match for mount path: {path}")
    from app.tasks.bulk import bulk_match_task
    task = bulk_match_task.delay(path)
    
    return {"message": "Bulk matching started", "task_id": task.id}


@router.post("/batch/rescan")
async def trigger_bulk_rescan(payload: dict, background_tasks: BackgroundTasks):
    """
    Trigger bulk rescan for a mount point.
    payload: {"path": "/data/mount1", "force": boolean}
    """
    path = payload.get("path")
    force = payload.get("force", False)
    
    if not path:
        logger.warning("Bulk rescan triggered without path")
        return {"error": "Path required"}
    
    if not isinstance(path, str) or not path.startswith("/data/"):
        logger.warning(f"Invalid path format for bulk rescan: {path}")
        return {"error": "Invalid path format. Must start with /data/"}
        
    logger.info(f"Triggering bulk rescan for mount path: {path} (force={force})")
    
    try:
        from app.tasks.bulk import bulk_astrometry_task
        task = bulk_astrometry_task.delay(path, force)
        logger.info(f"Bulk rescan task queued successfully: task_id={task.id}, path={path}, force={force}")
        return {"message": "Bulk rescan started", "task_id": task.id, "path": path}
    except Exception as e:
        logger.error(f"Failed to queue bulk rescan task: {e}", exc_info=True)
        return {"error": f"Failed to start bulk rescan: {str(e)}"}


@router.post("/batch/thumbnails")
async def trigger_bulk_thumbnails(payload: dict, background_tasks: BackgroundTasks):
    """
    Trigger bulk thumbnail regeneration for a folder/mount point.
    payload: {"path": "/data/mount1"}
    """
    path = payload.get("path")
    if not path:
        return {"error": "Path required"}
    
    if not path.startswith("/data/"):
        return {"error": "Invalid path. Must start with /data/"}

    logger.info(f"Triggering bulk thumbnails for path: {path}")
    from app.tasks.bulk import bulk_thumbnail_task
    task = bulk_thumbnail_task.delay(path)
    
    return {"message": "Bulk thumbnail generation started", "task_id": task.id}


@router.post("/batch/metadata")
async def trigger_bulk_metadata(payload: dict, background_tasks: BackgroundTasks):
    """
    Trigger bulk metadata re-extraction for a folder/mount point.
    payload: {"path": "/data/mount1"}
    """
    path = payload.get("path")
    if not path:
        return {"error": "Path required"}
    
    if not path.startswith("/data/"):
        return {"error": "Invalid path. Must start with /data/"}

    logger.info(f"Triggering bulk metadata for path: {path}")
    from app.tasks.bulk import bulk_metadata_task
    task = bulk_metadata_task.delay(path)
    
    return {"message": "Bulk metadata extraction started", "task_id": task.id}



@router.get("/status")
async def get_indexer_status():
    """Get current status of indexer with real data from database."""
    import os
    from datetime import datetime
    from sqlalchemy import func, text
    from sqlalchemy.future import select
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    import redis
    import time
    import hashlib
    
    # Simple global cache for this function (method-local static simulation)
    if not hasattr(get_indexer_status, "cache"):
        get_indexer_status.cache = {
            "data": [],
            "last_updated": 0
        }
    
    # Check Redis for scan state
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        is_running = r.get("indexer:is_running") == "1"
        last_scan_at = r.get("indexer:last_scan_at")
        last_scan_duration = r.get("indexer:last_scan_duration")
        files_scanned = r.get("indexer:files_scanned")
        files_added = r.get("indexer:files_added")
        files_updated = r.get("indexer:files_updated")
        files_removed = r.get("indexer:files_removed")
    except:
        is_running = False
        last_scan_at = None
        last_scan_duration = None
        files_scanned = None
        files_added = None
        files_updated = None
        files_removed = None
    
    # Get mount point stats from database-persisted SystemStats (fast and consistent)
    mount_points = []
    total_indexed = 0
    
    if (time.time() - get_indexer_status.cache["last_updated"]) < 10 and get_indexer_status.cache["data"]:
        mount_points = get_indexer_status.cache["data"]["mount_points"]
        total_indexed = get_indexer_status.cache["data"]["total_indexed"]
    else:
        try:
            from app.models.system_stats import SystemStats
            async with AsyncSessionLocal() as session:
                # Get mount stats from SystemStats table (updated by background task)
                # This is much faster than querying the entire images table
                for path in settings.image_paths_list:
                    exists = os.path.exists(path)
                    category = f"mount:{path}"
                    
                    stmt = select(SystemStats).where(SystemStats.category == category)
                    result = await session.execute(stmt)
                    mount_stat = result.scalar_one_or_none()
                    
                    if mount_stat:
                        mount_points.append({
                            "path": path,
                            "status": "connected" if exists else "disconnected",
                            "file_count": mount_stat.count,
                            "size_gb": round(mount_stat.size_bytes / (1024 ** 3), 2)
                        })
                    else:
                        # No stats yet (first run or before background task completes)
                        mount_points.append({
                            "path": path,
                            "status": "connected" if exists else "disconnected",
                            "file_count": 0,
                            "size_gb": 0
                        })
                
                # Get total indexed count
                total_result = await session.execute(text("SELECT COUNT(*) FROM images"))
                total_indexed = total_result.scalar() or 0
                
                # Update cache (increased from 3s to 10s since stats are updated periodically)
                get_indexer_status.cache["data"] = {
                    "mount_points": mount_points,
                    "total_indexed": total_indexed
                }
                get_indexer_status.cache["last_updated"] = time.time()
        except Exception as e:
            print(f"Error getting indexer stats from DB: {e}")
            # Serve stale if available
            if get_indexer_status.cache["data"]:
                mount_points = get_indexer_status.cache["data"]["mount_points"]
                total_indexed = get_indexer_status.cache["data"]["total_indexed"]

    # Enhance mount points with bulk status from Redis
    if mount_points:
        try:
           r = redis.from_url(settings.redis_url, decode_responses=True)
           for mp in mount_points:
               path = mp["path"]
               mount_hash = hashlib.md5(path.encode()).hexdigest()
               
               # Check match status
               match_key = f"bulk:match:{mount_hash}"
               match_status = r.hgetall(match_key)
               if match_status:
                   mp["bulk_match"] = match_status
               
               # Check rescan status
               rescan_key = f"bulk:rescan:{mount_hash}"
               rescan_status = r.hgetall(rescan_key)
               if rescan_status:
                    mp["bulk_rescan"] = rescan_status
                    
        except Exception as e:
            print(f"Error fetching bulk stats: {e}")

    return {
        "is_running": is_running,
        "last_scan_at": last_scan_at if last_scan_at else None,
        "last_scan_duration_seconds": int(last_scan_duration) if last_scan_duration else 0,
        "files_scanned": int(files_scanned) if files_scanned else 0,
        "files_added": int(files_added) if files_added else 0,
        "files_updated": int(files_updated) if files_updated else 0,
        "files_removed": int(files_removed) if files_removed else 0,
        "indexed_count": total_indexed,
        "mount_points": mount_points
    }



@router.get("/thumbnails/stats")
async def get_thumbnail_stats():
    """Get thumbnail cache statistics from the database (fast)."""
    from sqlalchemy.future import select
    from app.database import AsyncSessionLocal
    from app.models.system_stats import SystemStats
    
    async with AsyncSessionLocal() as session:
        stmt = select(SystemStats).where(SystemStats.category == "thumbnails")
        result = await session.execute(stmt)
        stats = result.scalar_one_or_none()
        
        if not stats:
            # If no stats in DB, return zeros
            return {
                "count": 0,
                "size_bytes": 0,
                "size_mb": 0
            }
        
        return {
            "count": stats.count,
            "size_bytes": stats.size_bytes,
            "size_mb": stats.size_mb
        }


@router.post("/thumbnails/clear")
async def clear_thumbnail_cache():
    """Clear all thumbnails from disk and database."""
    import os
    import shutil
    from sqlalchemy import update
    from app.database import AsyncSessionLocal
    from app.models.image import Image
    
    thumb_cache_dir = settings.thumbnail_cache_path
    deleted_count = 0
    
    # 1. Clear files
    if os.path.exists(thumb_cache_dir):
        for f in os.listdir(thumb_cache_dir):
            fp = os.path.join(thumb_cache_dir, f)
            try:
                if os.path.isfile(fp):
                    os.unlink(fp)
                    deleted_count += 1
            except Exception as e:
                print(f"Error deleting {fp}: {e}")
                
    # 2. Clear database paths
    async with AsyncSessionLocal() as session:
        stmt = update(Image).values(thumbnail_path=None)
        await session.execute(stmt)
        await session.commit()
    
    # 3. Perform a rescan to update the database stats
    from app.tasks.indexer import update_thumbnail_stats
    update_thumbnail_stats()
        
    return {"message": "Cache cleared and stats updated", "files_deleted": deleted_count}


@router.post("/thumbnails/regenerate")
async def regenerate_thumbnails_endpoint():
    """Trigger regeneration of all thumbnails and recalculate stats."""
    from app.tasks.indexer import regenerate_thumbnails
    
    task = regenerate_thumbnails.delay()
    
    return {"message": "Regeneration and stats refresh started", "task_id": task.id}
