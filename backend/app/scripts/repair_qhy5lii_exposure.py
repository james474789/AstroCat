"""
Repair exposure_time_seconds for the QHY5LII-C capture software that writes
milliseconds into the non-standard EXPOSURE keyword instead of seconds.

That software never writes the standard EXPTIME keyword, and is uniquely
identifiable by its "Presion Time" (sic) header card - a value seen nowhere
else in the dataset. Every row carrying it has EXPOSURE off by 1000x (e.g. a
60s sub stored as 60000), which inflated per-target integration totals
(e.g. NGC6888 showing ~480h instead of ~15h once 27 QHY5LII-C subs summed to
~466h instead of ~0.47h).

Recomputed from raw_header.EXPOSURE each run (not by dividing the current
column value), so this is naturally idempotent - re-running always lands on
the same corrected value instead of compounding.

Usage:
    python -m app.scripts.repair_qhy5lii_exposure
"""

import sys
from typing import Any, Optional

from sqlalchemy import select, update

from app.models.image import Image

BATCH_SIZE = 1000

# Header fingerprint of the affected capture software (see module docstring).
_FINGERPRINT_KEY = "Presion Time"


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _corrected_seconds(raw_header: Any) -> Optional[float]:
    if not isinstance(raw_header, dict):
        return None
    if _FINGERPRINT_KEY not in raw_header or "EXPTIME" in raw_header:
        return None
    value = raw_header.get("EXPOSURE")
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def repair_qhy5lii_exposure(session=None) -> dict:
    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    processed = updated = 0
    last_id = 0
    try:
        while True:
            rows = session.execute(
                select(Image.id, Image.raw_header, Image.exposure_time_seconds)
                .where(Image.id > last_id)
                .where(Image.raw_header.has_key(_FINGERPRINT_KEY))
                .order_by(Image.id)
                .limit(BATCH_SIZE)
            ).all()
            if not rows:
                break

            batch = []
            for img_id, raw_header, current_seconds in rows:
                last_id = img_id
                corrected = _corrected_seconds(raw_header)
                if corrected is not None and corrected != current_seconds:
                    batch.append({"id": img_id, "exposure_time_seconds": corrected})

            if batch:
                session.execute(update(Image), batch)
            session.commit()
            processed += len(rows)
            updated += len(batch)
            log(f"Processed {processed} rows ({updated} updated)...")

            if len(rows) < BATCH_SIZE:
                break
    finally:
        if own_session:
            session.close()

    summary = {"processed": processed, "updated": updated}
    log(f"QHY5LII-C exposure repair: {summary}")

    if updated:
        from app.tasks.equipment import _invalidate_targets_cache
        _invalidate_targets_cache()

    return summary


if __name__ == "__main__":
    print(repair_qhy5lii_exposure())
