"""
Maintenance Tasks
Background runner for one-off data migrations (app/services/data_migrations.py).
"""

import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.maintenance.run_data_migrations",
                 soft_time_limit=21600, time_limit=28800)
def run_data_migrations(only_id: str = None):
    """
    Queued automatically on worker start (pending entries only) and from
    Admin > Maintenance (only_id re-runs one entry regardless of status).
    """
    from app.services.data_migrations import run_data_migrations as _run

    try:
        return _run(only_id=only_id)
    except Exception as e:
        logger.error(f"Data migration run failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
