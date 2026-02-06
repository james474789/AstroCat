"""
AstroCat Celery Worker
Background task processing for image indexing and thumbnail generation.
"""

from celery.signals import setup_logging
from celery import Celery
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
}


# Optional: Configure task routes for different queues
celery_app.conf.task_routes = {
    "app.tasks.indexer.*": {"queue": "indexer"},
    "app.tasks.thumbnails.*": {"queue": "thumbnails"},
    "app.tasks.bulk.*": {"queue": "indexer"},
}


if __name__ == "__main__":
    celery_app.start()
