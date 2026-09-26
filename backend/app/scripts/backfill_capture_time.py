"""
Backfill capture-time provenance (P0 §3.2).

Computes images.capture_date_utc / capture_time_source from the stored
raw_header only (no file IO), in keyset batches of 1000 with a commit per
batch. The indexer's file-mtime fallback is recognised as
capture_date == file_last_modified with no header date (-> FILE_MTIME).

Resumable: by default only rows with capture_time_source IS NULL are
touched, and every processed row gets a source, so a second run is a no-op.
Pass --all to recompute every row.

Usage:
    python -m app.scripts.backfill_capture_time [--all]
"""

import sys
from collections import Counter

from sqlalchemy import select, update

from app.models.image import Image
from app.utils.capture_time import derive_capture_time_for_row

BATCH_SIZE = 1000


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_capture_time(process_all: bool = False, batch_size: int = BATCH_SIZE, session=None) -> dict:
    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    sources = Counter()
    processed = 0
    updated = 0
    last_id = 0
    try:
        while True:
            stmt = select(
                Image.id, Image.raw_header, Image.capture_date, Image.file_last_modified,
                Image.capture_date_utc, Image.capture_time_source,
            ).where(Image.id > last_id)
            if not process_all:
                stmt = stmt.where(Image.capture_time_source.is_(None))
            rows = session.execute(stmt.order_by(Image.id).limit(batch_size)).all()
            if not rows:
                break

            batch = []
            for img_id, raw_header, capture_date, file_mtime, old_utc, old_source in rows:
                last_id = img_id
                utc, source = derive_capture_time_for_row(raw_header, capture_date, file_mtime)
                sources[source] += 1
                if utc != old_utc or source != old_source:
                    batch.append({"id": img_id, "capture_date_utc": utc, "capture_time_source": source})

            if batch:
                session.execute(update(Image), batch)
            session.commit()
            processed += len(rows)
            updated += len(batch)
            log(f"Processed {processed} rows ({updated} updated)...")

            if len(rows) < batch_size:
                break
    finally:
        if own_session:
            session.close()

    summary = {"processed": processed, "updated": updated, "sources": dict(sources)}
    log(f"Capture-time backfill complete: {summary}")
    return summary


if __name__ == "__main__":
    print(backfill_capture_time(process_all="--all" in sys.argv))
