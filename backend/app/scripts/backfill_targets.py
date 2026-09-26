"""
Backfill Targets (F2)

Resolves target_key/target_source for LIGHT sub-frames that don't have one
yet, in keyset batches of 1000, committing per batch. Resumable: by default
only touches rows where target_source IS NULL; pass --all to re-resolve
every non-MANUAL row (e.g. after an alias index update or catalog reseed).

Usage:
    python -m app.scripts.backfill_targets
    python -m app.scripts.backfill_targets --all

Run after F1's frame-type backfill so frame_type is populated first; safe
to re-run any time since it only touches rows that still need it (unless
--all is passed) and never overwrites a MANUAL target.
"""

import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.image import Image, FrameType
from app.services.targets import assign_target_sync

BATCH_SIZE = 1000


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_targets(process_all: bool = False) -> dict:
    log("Starting target backfill...")

    total_processed = 0
    total_changed = 0
    last_id = 0

    with SessionLocal() as session:
        while True:
            stmt = (
                select(Image)
                .where(Image.frame_type == FrameType.LIGHT)
                .where(Image.id > last_id)
            )
            if not process_all:
                stmt = stmt.where(Image.target_source.is_(None))
            else:
                stmt = stmt.where(Image.target_source != "MANUAL")

            stmt = stmt.order_by(Image.id).limit(BATCH_SIZE)

            images = session.execute(stmt).scalars().all()
            if not images:
                break

            batch_changed = 0
            for image in images:
                if assign_target_sync(session, image):
                    batch_changed += 1
                last_id = image.id

            session.commit()

            total_processed += len(images)
            total_changed += batch_changed
            log(f"Processed {total_processed} images so far ({batch_changed} changed in this batch)...")

    # Invalidate the cached targets list so the UI reflects the backfill immediately.
    try:
        import redis
        from app.config import settings

        r = redis.from_url(settings.redis_url)
        for key in r.scan_iter("cache:targets:*"):
            r.delete(key)
    except Exception as e:
        log(f"Warning: failed to clear targets cache: {e}")

    log(f"Backfill complete. Processed {total_processed} images, changed {total_changed}.")
    return {"processed": total_processed, "changed": total_changed}


if __name__ == "__main__":
    process_all = "--all" in sys.argv
    backfill_targets(process_all=process_all)
