"""
Backfill images.binning (P0 binning-aware rig fix).

images.binning was never populated by the extractor/indexer; the value only
ever existed in raw_header.XBINNING/YBINNING. Fills it as "NxM" (handles the
header forms 2, "2", 2.0, "1.0"; YBINNING defaults to XBINNING) for every row
that has an XBINNING header and no binning yet, then re-runs equipment
assignment: rig matching and rigs.measured_scale_arcsec both fall back to
images.binning when dimensions and XPIXSZ don't settle the frame's binning
relative to a rig's camera (see relative_binning in app/utils/rig_optics.py).

Keyset batches of 1000, committing per batch. Idempotent: rows that already
carry a binning value are left untouched, and re-running finds nothing to
update on the images side.

Usage:
    python -m app.scripts.backfill_binning
"""

import sys
from typing import Any, Optional

from sqlalchemy import select, update

from app.models.image import Image

BATCH_SIZE = 1000


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _int(value: Any) -> Optional[int]:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def binning_string(raw_header: Any) -> Optional[str]:
    """"NxM" from a raw_header's XBINNING/YBINNING, or None."""
    if not isinstance(raw_header, dict):
        return None
    x = _int(raw_header.get("XBINNING"))
    if not x:
        return None
    y = _int(raw_header.get("YBINNING")) or x
    return f"{x}x{y}"


def backfill_binning(session=None) -> dict:
    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    processed = updated = 0
    last_id = 0
    try:
        while True:
            rows = session.execute(
                select(Image.id, Image.raw_header)
                .where(Image.id > last_id)
                .where(Image.binning.is_(None))
                .where(Image.raw_header.has_key("XBINNING"))
                .order_by(Image.id)
                .limit(BATCH_SIZE)
            ).all()
            if not rows:
                break

            batch = []
            for img_id, raw_header in rows:
                last_id = img_id
                value = binning_string(raw_header)
                if value:
                    batch.append({"id": img_id, "binning": value})

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
    log(f"Binning backfill: {summary}")

    # Rig matching and measured_scale_arcsec both use images.binning as a
    # fallback, so re-assign now that it's populated.
    from app.tasks.equipment import run_assignment
    assignment = run_assignment("all")
    summary["rig_changed"] = assignment.get("rig_changed", 0)
    summary["rigs_measured"] = assignment.get("measured")

    return summary


if __name__ == "__main__":
    print(backfill_binning())
