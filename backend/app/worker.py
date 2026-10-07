"""
AstroCat Celery Worker
Background task processing for image indexing and thumbnail generation.
"""

from celery.signals import setup_logging, worker_ready
from celery import Celery
from celery.schedules import crontab
from app.config import settings
from app.logging_config import setup_logging as configure_logging

@setup_logging.connect
def on_setup_logging(**kwargs):
    configure_logging(log_dir=settings.log_dir)

# Create Celery app
celery_app = Celery(
    "AstroCat",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.indexer",
        "app.tasks.thumbnails",
        "app.tasks.astrometry",
        "app.tasks.bulk",
        "app.tasks.sync_ratings",
        "app.tasks.maintenance",
        "app.tasks.equipment",
        "app.tasks.recommend",
        "app.tasks.quality",
        "app.tasks.seeing",
        "app.tasks.fullres",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task settings
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3000,  # Soft limit at 50 minutes
    task_acks_late=True,  # Only acknowledge task after successful completion
    task_reject_on_worker_lost=True,  # Re-queue task if worker is killed
    
    # Result settings
    result_expires=86400,  # Results expire after 24 hours
    
    # Worker settings
    worker_prefetch_multiplier=1,  # Process one task at a time
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks
    
    # Fix for Celery 6.0 deprecation warning
    broker_connection_retry_on_startup=True,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "cleanup-stuck-astrometry": {
        "task": "app.tasks.astrometry.cleanup_stuck_astrometry",
        "schedule": 300.0,  # Run every 5 minutes
    },
    "update-mount-stats": {
        "task": "app.tasks.indexer.update_mount_stats",
        "schedule": 60.0,  # Run every 60 seconds
    },
    "reconcile-thumbnail-stats": {  # full cache walk (minutes on a big cache): daily, off-peak, never more often
        "task": "app.tasks.indexer.update_thumbnail_stats",
        "schedule": crontab(hour=3, minute=30),
    },
    "precompute-tonight-recommendations": {  # R1: warm tonight's recommendations
        "task": "app.tasks.recommend.precompute_tonight",
        "schedule": crontab(hour=12, minute=0),
    },
    "star-metrics-sweeper": {  # Q1: backfill/retry star quality measurements
        "task": "app.tasks.quality.sweep",
        "schedule": 60.0,  # checks the queue often so it stays topped off; cheap no-op when queue is long
    },
    "refresh-seeing-forecasts": {  # S1: planetary seeing forecast, every 3 h at minute 10
        "task": "app.tasks.seeing.refresh_forecasts",
        "schedule": crontab(minute=10, hour="*/3"),
    },
    "evict-fullres-cache": {  # V1: keep the full-resolution pyramid cache under its cap
        "task": "app.tasks.fullres.evict",
        "schedule": 30 * 60.0,
    },
}


# Optional: Configure task routes for different queues
celery_app.conf.task_routes = {
    # The cache walk is slow; keep it off the indexer queue so it never delays indexing.
    "app.tasks.indexer.update_thumbnail_stats": {"queue": "thumbnails"},
    "app.tasks.indexer.*": {"queue": "indexer"},
    "app.tasks.thumbnails.*": {"queue": "thumbnails"},
    "app.tasks.bulk.*": {"queue": "indexer"},
    "app.tasks.maintenance.*": {"queue": "indexer"},
    "app.tasks.equipment.*": {"queue": "celery"},  # R0: the default queue
    "app.tasks.recommend.*": {"queue": "celery"},  # R1: the default queue
    "app.tasks.seeing.*": {"queue": "celery"},  # S1: the default queue
    # Q1: measurements get their own queue so a library backfill never delays
    # indexing; the sweeper itself stays on the default queue.
    "app.tasks.quality.sweep": {"queue": "celery"},
    "app.tasks.quality.*": {"queue": "quality"},
    # V1: pyramid builds can need several GB of RAM, so they run on a dedicated worker
    # (supervisord's celery_fullres) that the main worker's -Q list does not include.
    "app.tasks.fullres.*": {"queue": "fullres"},
}



@worker_ready.connect
def on_worker_ready(sender=None, **kwargs):
    # The dedicated full-resolution worker (V1) must not re-trigger this, or it would
    # clear the lock of a repair the main worker is already running.
    if str(getattr(sender, "hostname", "")).startswith("fullres@"):
        return
    # Apply any pending one-off data repairs in the background, so startup
    # isn't blocked and each repair runs once per install (see
    # app/services/data_migrations.py).
    try:
        from app.api.settings import restore_settings_cache
        restore_settings_cache()
    except Exception:
        pass
    try:
        # A lock left by a run that died with the previous worker would
        # otherwise block this start's run until it expires.
        from app.services.data_migrations import LOCK_KEY, redis_client
        redis_client().delete(LOCK_KEY)
    except Exception:
        pass
    celery_app.send_task("app.tasks.maintenance.run_data_migrations")


if __name__ == "__main__":
    celery_app.start()
