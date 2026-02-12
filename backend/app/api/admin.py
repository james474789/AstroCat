"""
Admin API
Endpoints for system observability and administration.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from app.config import settings
from app.worker import celery_app
import redis
import os
import shutil
from app.database import AsyncSessionLocal
from sqlalchemy import text, select

import time

router = APIRouter()

# Simple in-memory cache
class AdminStatsCache:
    def __init__(self):
        self.data = {
            "database": None,
            "disk": None,
            "workers": None
        }
        self.last_updated = {
            "database": 0,
            "disk": 0,
            "workers": 0
        }
        
    def get(self, key, default=None):
        return self.data.get(key, default)
        
    def set(self, key, value):
        self.data[key] = value
        self.last_updated[key] = time.time()
        
    def is_stale(self, key, ttl_seconds):
        return (time.time() - self.last_updated.get(key, 0)) > ttl_seconds

stats_cache = AdminStatsCache()

@router.get("/workers")
async def get_worker_stats():
    """
    Get detailed information about Celery workers and currently active tasks.
    This is a slow endpoint due to Celery's inspect() broadcast.
    Uses 10-second cache to reduce load on worker discovery.
    """
    if not stats_cache.is_stale("workers", 10) and stats_cache.get("workers"):
        return JSONResponse(content=jsonable_encoder(stats_cache.get("workers")))

    worker_data = {
        "count": 0,
        "concurrency": 0,
        "details": [],
        "queue_active": 0,
        "queue_reserved": 0,
        "queue_scheduled": 0
    }

    try:
        i = celery_app.control.inspect()
        
        # Active - Celery 5.3.6 doesn't support timeout parameter
        active = i.active() or {}
        worker_data["queue_active"] = sum(len(tasks) for tasks in active.values())
        
        # Reserved
        reserved = i.reserved() or {}
        worker_data["queue_reserved"] = sum(len(tasks) for tasks in reserved.values())
        
        # Scheduled
        scheduled = i.scheduled() or {}
        worker_data["queue_scheduled"] = sum(len(tasks) for tasks in scheduled.values())

        # Workers / Concurrency
        worker_stats = i.stats() or {}
        total_concurrency = 0
        for worker, details in worker_stats.items():
            pool = details.get('pool', {})
            total_concurrency += pool.get('max-concurrency', 0)
        worker_data["concurrency"] = total_concurrency

        if active:
            worker_data["count"] = len(active)
            for worker_name, tasks in active.items():
                worker_info = {
                    "name": worker_name,
                    "task_count": len(tasks),
                    "current_tasks": [t.get("name") for t in tasks]
                }
                worker_data["details"].append(worker_info)
        
        stats_cache.set("workers", worker_data)
        return JSONResponse(content=jsonable_encoder(worker_data))

    except Exception as e:
        print(f"Error inspecting Celery: {e}")
        # Return stale cache if available rather than erroring
        if stats_cache.get("workers"):
            return JSONResponse(content=jsonable_encoder(stats_cache.get("workers")))
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/queue")
async def get_queue_details():
    """
    Get detailed information about active, reserved, scheduled, and pending tasks.
    """
    details = {
        "active": [],
        "reserved": [],
        "scheduled": [],
        "pending": []
    }

    try:
        i = celery_app.control.inspect()
        
        # 1. Active Tasks (currently running)
        active = i.active() or {}
        for worker, tasks in active.items():
            for task in tasks:
                details["active"].append({
                    "id": task.get("id"),
                    "name": task.get("name"),
                    "args": task.get("args"),
                    "kwargs": task.get("kwargs"),
                    "worker": worker,
                    "time_start": task.get("time_start")
                })

        # 2. Reserved Tasks (claimed but not started)
        reserved = i.reserved() or {}
        for worker, tasks in reserved.items():
            for task in tasks:
                details["reserved"].append({
                    "id": task.get("id"),
                    "name": task.get("name"),
                    "args": task.get("args"),
                    "worker": worker
                })

        # 3. Scheduled Tasks (ETA)
        scheduled = i.scheduled() or {}
        for worker, tasks in scheduled.items():
            for task in tasks:
                details["scheduled"].append({
                    "id": task.get("id"),
                    "name": task.get("request", {}).get("name"),
                    "args": task.get("request", {}).get("args"),
                    "worker": worker,
                    "eta": task.get("eta")
                })

        # 4. Pending Tasks (in Redis lists)
        # These are harder to get detailed info for without manual Redis parsing
        # but let's try to peek at the 'celery' queue
        try:
            r = redis.from_url(settings.redis_url, decode_responses=True)
            for q_name in ["celery", "indexer", "thumbnails"]:
                # Peek at the first 50 items in each list
                items = r.lrange(q_name, 0, 49)
                for item in items:
                    import json
                    import base64
                    try:
                        # Celery messages in Redis are base64 encoded JSON
                        msg = json.loads(item)
                        body = json.loads(base64.b64decode(msg['body']).decode('utf-8'))
                        # body is usually a list [args, kwargs, embed]
                        # task name is in headers or properties
                        headers = msg.get('headers', {})
                        task_name = headers.get('task')
                        
                        details["pending"].append({
                            "id": headers.get('id'),
                            "name": task_name,
                            "queue": q_name,
                            "args": body[0] if body else None
                        })
                    except Exception as pe:
                        # Fallback for unexpected formats
                        details["pending"].append({
                            "name": f"Unknown Task ({q_name})",
                            "queue": q_name
                        })
        except Exception as re:
            print(f"Error peeking Redis queues: {re}")

        return JSONResponse(content=jsonable_encoder(details))

    except Exception as e:
        print(f"Error fetching queue details: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/stats")
async def get_system_stats():
    """
    Get fast aggregated system statistics (Redis, DB, Disk).
    Worker details are served via /workers independently.
    """
    stats = {
        "queue": {
            "pending": 0
        },
        "database": {
            "status": "unknown",
            "record_count": 0,
            "size_str": "0 MB",
            "astrometry_counts": {}
        },
        "redis": {
            "status": "unknown",
            "memory_used_mb": 0
        },
        "disk": {
            "thumbnail_cache_gb": 0,
            "mounts": []
        },
        "error": None
    }

    try:
        # 1. Redis
        try:
            r = redis.from_url(settings.redis_url)
            r.ping()
            stats["redis"]["status"] = "connected"
            info = r.info("memory")
            stats["redis"]["memory_used_mb"] = round(info.get("used_memory", 0) / (1024 * 1024), 2)
            pending_count = r.llen("celery") + r.llen("indexer") + r.llen("thumbnails")
            stats["queue"]["pending"] = pending_count
        except Exception as e:
            stats["redis"]["status"] = f"error: {str(e)}"

        # 3. Database
        if not stats_cache.is_stale("database", 5) and stats_cache.get("database"):
            stats["database"] = stats_cache.get("database")
        else:
            try:
                async with AsyncSessionLocal() as session:
                    await session.execute(text("SELECT 1"))
                    stats["database"]["status"] = "connected"
                    
                    # Get both record count and astrometry counts in ONE query
                    # Categorize based on astrometry_status and is_plate_solved
                    result = await session.execute(text("""
                        SELECT 
                            COUNT(*) as total_count,
                            COALESCE(SUM(CASE WHEN astrometry_status = 'SOLVED' THEN 1 ELSE 0 END), 0) as solved,
                            COALESCE(SUM(CASE WHEN (astrometry_status = 'NONE' OR astrometry_status IS NULL) AND is_plate_solved THEN 1 ELSE 0 END), 0) as imported,
                            COALESCE(SUM(CASE WHEN (astrometry_status = 'NONE' OR astrometry_status IS NULL) AND NOT is_plate_solved THEN 1 ELSE 0 END), 0) as unsolved,
                            COALESCE(SUM(CASE WHEN astrometry_status = 'FAILED' THEN 1 ELSE 0 END), 0) as failed,
                            COALESCE(SUM(CASE WHEN astrometry_status = 'SUBMITTED' THEN 1 ELSE 0 END), 0) as submitted,
                            COALESCE(SUM(CASE WHEN astrometry_status = 'PROCESSING' THEN 1 ELSE 0 END), 0) as processing
                        FROM images
                    """))
                    row = result.first()
                    
                    stats["database"]["record_count"] = int(row[0]) if row else 0
                    
                    # Build astrometry counts from single query
                    if row:
                        counts = {}
                        if row[1] > 0: counts['SOLVED'] = int(row[1])
                        if row[2] > 0: counts['IMPORTED'] = int(row[2])
                        if row[3] > 0: counts['UNSOLVED'] = int(row[3])
                        if row[4] > 0: counts['FAILED'] = int(row[4])
                        if row[5] > 0: counts['SUBMITTED'] = int(row[5])
                        if row[6] > 0: counts['PROCESSING'] = int(row[6])
                        stats["database"]["astrometry_counts"] = counts
                    
                    # Get database size
                    size_result = await session.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
                    stats["database"]["size_str"] = size_result.scalar()
                    
                    stats_cache.set("database", stats["database"])
            except Exception as e:
                stats["database"]["status"] = f"error: {str(e)}"
                if stats_cache.get("database"):
                    stats["database"] = stats_cache.get("database")

        # 4. Disk
        if not stats_cache.is_stale("disk", 5) and stats_cache.get("disk"):
            stats["disk"] = stats_cache.get("disk")
        else:
            try:
                from app.models.system_stats import SystemStats
                async with AsyncSessionLocal() as session:
                    # Get thumbnail stats from DB (this is now fast)
                    stmt = select(SystemStats).where(SystemStats.category == "thumbnails")
                    result = await session.execute(stmt)
                    thumb_stats = result.scalar_one_or_none()
                    
                    if thumb_stats:
                        stats["disk"]["thumbnail_cache_gb"] = round(thumb_stats.size_bytes / (1024**3), 2)
                    
                    # Get mount point stats from DB (fast and persistent)
                    mounts = []
                    for mount_path in settings.image_paths_list:
                        category = f"mount:{mount_path}"
                        stmt = select(SystemStats).where(SystemStats.category == category)
                        result = await session.execute(stmt)
                        mount_stat = result.scalar_one_or_none()
                        
                        if mount_stat:
                            mounts.append({
                                "path": mount_path,
                                "file_count": mount_stat.count,
                                "size_gb": round(mount_stat.size_bytes / (1024**3), 2)
                            })
                        else:
                            # No stats yet, show zeros
                            mounts.append({
                                "path": mount_path,
                                "file_count": 0,
                                "size_gb": 0
                            })
                    
                    stats["disk"]["mounts"] = mounts
                    
                    # Update cache
                    stats_cache.set("disk", stats["disk"])
            except Exception as e:
                print(f"Error getting disk stats from DB: {e}")
                if stats_cache.get("disk"):
                    stats["disk"] = stats_cache.get("disk")

        
        # Explicit serialization to catch encoding errors
        return JSONResponse(content=jsonable_encoder(stats))

    except Exception as e:
        print(f"CRITICAL ERROR in get_system_stats: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
