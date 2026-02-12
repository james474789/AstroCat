# Redis Queue Persistence

## Impact of Restarting Containers

When running `docker compose up -d` or restarting the system, Celery tasks are protected:

| Task State | Impact | Explanation |
| :--- | :--- | :--- |
| **Pending** | **Safe** | Tasks remain in the `redis_data` volume and are processed when workers resume. |
| **Processing** | **Safe** | Late acknowledgment ensures in-flight tasks are re-queued on worker crash. |
| **Completed** | **Safe** | Results remain in Redis (DB 1) for 24 hours. |

## How It Works

### Persistence of Pending Tasks
- **Volume Mapping:** The `redis` service uses a named volume `redis_data` mapped to `/data`.
- **Redis Behavior:** Redis 7-alpine uses RDB snapshotting. On shutdown (SIGTERM), it saves state to disk and reloads on restart.

### Robustness of Active Tasks
Two Celery configuration settings (in `backend/app/worker.py`) protect in-flight tasks:

```python
celery_app.conf.update(
    task_acks_late=True,              # Remove from queue only after success
    task_reject_on_worker_lost=True,  # Re-queue if worker dies mid-task
    worker_prefetch_multiplier=1,
)
```

- **`task_acks_late=True`**: A task is only removed from Redis *after* it completes successfully.
- **`task_reject_on_worker_lost=True`**: If a worker crashes, the task is returned to the queue automatically.
