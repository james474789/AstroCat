# Redis Queue Persistence

This document explains the behavior of the Redis task queue when running `docker compose up -d` or restarting the system.

## Summary of Impact

When running `docker compose up -d`, the impact on Celery tasks depends on their current state:

| Task State | Impact | Explanation |
| :--- | :--- | :--- |
| **Pending** | **Safe** | Tasks remain in the `redis_data` volume and will be processed when workers resume. |
| **Processing** | **Safe** | Tasks are now robust; if a worker is killed, the task is re-queued and retried. |
| **Completed** | **Safe** | Results remain in Redis (DB 1) for 24 hours. |

## Technical Details

### 1. Persistence of Pending Tasks
The system configuration ensures that tasks waiting in the queue survive a container restart:
- **Volume Mapping:** In `docker-compose.yml`, the `redis` service uses a named volume `redis_data` mapped to `/data`.
- **Redis Default Behavior:** Redis (version 7-alpine) uses RDB snapshotting. When the container receives a shutdown signal (SIGTERM), it attempts to save the current state to disk.
- **Reloading:** Upon restart, Redis reloads the dataset from the persistent volume.

### 2. Robustness of Active Tasks
Tasks currently being processed by `celery_worker` are now protected:
- **Late Acknowledgment:** The configuration `task_acks_late=True` ensures that a task is only removed from the Redis queue *after* it has successfully completed.
- **Worker Loss Handling:** The configuration `task_reject_on_worker_lost=True` ensures that if a worker is killed or crashes while processing, the task is automatically returned to the queue to be picked up by another worker.

## Recommendations

### Improving Resilience
To allow active tasks to survive a restart, the following configuration change to `backend/app/worker.py` is required:

```python
# In backend/app/worker.py
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # ... other settings
)
```

> [!IMPORTANT]
> Enabling `task_acks_late` ensures that tasks are only removed from the queue *after* they complete successfully. If the worker is killed during processing, the task remains in Redis and will be picked up again when a worker becomes available.
