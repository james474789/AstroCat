"""
Data Migrations

One-off data repairs that must reach every install (including people who
just pull the published image), without anyone running a script by hand and
without slowing container startup.

How it works:
- REGISTRY lists repairs in the order they must run. Each has a stable id.
- On Celery worker start (app/worker.py), run_data_migrations is queued once.
  It runs, in the background, every registry entry that has no "applied" row
  in the data_migrations table, recording the outcome. After the first boot on
  a given version there is nothing pending, so startup cost stays flat.
- A failed repair is recorded as "failed" and retried on the next start;
  later entries are not run past a failure, since they may depend on it.
- Admin > Maintenance lists every entry with its status and can re-run one.

Adding a repair: write an idempotent function taking no arguments and
returning a JSON-serializable summary, then append it to REGISTRY with a new
id (next number + short slug). Never renumber or remove existing ids.
"""

import json
import logging
import time
from collections import namedtuple
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

DataMigrationSpec = namedtuple("DataMigrationSpec", ["id", "description", "run"])

LOCK_KEY = "data_migrations:lock"
LOCK_TTL_SECONDS = 8 * 3600


def _backfill_frame_types():
    from app.scripts.backfill_frame_types import backfill_frame_types
    return backfill_frame_types(dry_run=False, reclassify_all=False)


def _repair_field_radius():
    from app.scripts.backfill_field_radius import backfill_field_radius
    return backfill_field_radius(dry_run=False)


def _backfill_targets():
    from app.scripts.backfill_targets import backfill_targets
    return backfill_targets(process_all=False)


REGISTRY: List[DataMigrationSpec] = [
    DataMigrationSpec(
        "0001_backfill_frame_types",
        "Classify frame types (Light/Dark/Flat/Bias) for images indexed before frame types existed.",
        _backfill_frame_types,
    ),
    DataMigrationSpec(
        "0002_repair_field_radius",
        "Derive missing field-of-view radii for plate-solved images (sidecar .ini solves), then re-match catalogs and targets.",
        _repair_field_radius,
    ),
    DataMigrationSpec(
        "0003_backfill_targets",
        "Resolve targets for Light frames indexed before targets existed.",
        _backfill_targets,
    ),
]

_BY_ID = {spec.id: spec for spec in REGISTRY}


def get_spec(migration_id: str) -> Optional[DataMigrationSpec]:
    return _BY_ID.get(migration_id)


def pending_specs(registry: Iterable[DataMigrationSpec], applied_ids: Iterable[str]) -> List[DataMigrationSpec]:
    """Registry entries not yet applied, in registry order."""
    applied = set(applied_ids)
    return [spec for spec in registry if spec.id not in applied]


def redis_client():
    import redis
    from app.config import settings
    return redis.from_url(settings.redis_url)


def is_running() -> bool:
    try:
        return bool(redis_client().exists(LOCK_KEY))
    except Exception:
        return False


def _record(session, spec_id: str, status: str, duration: float, result: str) -> None:
    from app.models.data_migration import DataMigration

    row = session.get(DataMigration, spec_id)
    if row is None:
        row = DataMigration(id=spec_id)
        session.add(row)
    row.status = status
    row.duration_seconds = duration
    row.result = result
    session.commit()


def _run_one(session, spec: DataMigrationSpec) -> bool:
    logger.info(f"Data migration {spec.id}: starting")
    started = time.time()
    try:
        summary = spec.run()
        duration = time.time() - started
        _record(session, spec.id, "applied", duration, json.dumps(summary, default=str))
        logger.info(f"Data migration {spec.id}: applied in {duration:.1f}s ({summary})")
        return True
    except Exception as e:
        session.rollback()
        duration = time.time() - started
        logger.error(f"Data migration {spec.id}: failed after {duration:.1f}s: {e}", exc_info=True)
        _record(session, spec.id, "failed", duration, f"{type(e).__name__}: {e}")
        return False


def run_data_migrations(only_id: Optional[str] = None) -> dict:
    """
    Run pending data migrations (or, with only_id, re-run that one entry
    regardless of its recorded status). Guarded by a Redis lock so a worker
    restart mid-run, or an Admin click during a run, can't start a second
    concurrent pass.
    """
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models.data_migration import DataMigration

    if only_id is not None and only_id not in _BY_ID:
        return {"status": "error", "message": f"Unknown data migration '{only_id}'"}

    r = redis_client()
    if not r.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SECONDS):
        logger.info("Data migrations already running elsewhere; skipping.")
        return {"status": "skipped", "reason": "already running"}

    ran, failed = [], None
    try:
        with SessionLocal() as session:
            if only_id is not None:
                specs = [_BY_ID[only_id]]
            else:
                applied = session.execute(
                    select(DataMigration.id).where(DataMigration.status == "applied")
                ).scalars().all()
                specs = pending_specs(REGISTRY, applied)

            if not specs:
                logger.info("Data migrations: nothing pending.")

            for spec in specs:
                if _run_one(session, spec):
                    ran.append(spec.id)
                else:
                    failed = spec.id
                    break
    finally:
        r.delete(LOCK_KEY)

    return {"status": "failed" if failed else "completed", "ran": ran, "failed": failed}


async def list_data_migrations(db) -> dict:
    """Registry entries merged with their recorded status, for the Admin UI."""
    from sqlalchemy import select
    from app.models.data_migration import DataMigration

    rows = {row.id: row for row in (await db.execute(select(DataMigration))).scalars().all()}
    items = []
    for spec in REGISTRY:
        row = rows.get(spec.id)
        items.append({
            "id": spec.id,
            "description": spec.description,
            "status": row.status if row else "pending",
            "applied_at": row.applied_at.isoformat() if row and row.applied_at else None,
            "duration_seconds": row.duration_seconds if row else None,
            "result": row.result if row else None,
        })
    return {"running": is_running(), "items": items}
