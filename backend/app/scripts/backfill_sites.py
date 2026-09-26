"""
Backfill per-image site coordinates (P0 §3.3).

Fills images.site_latitude / site_longitude / site_name from the stored
raw_header only (no file IO): FITS/XISF SITELAT/SITELONG (decimal or
sexagesimal, e.g. "56d0m0.000s N"), OBSGEO-B/L, then EXIF GPS; name from
SITENAME/OBSERVAT. Out-of-range or exactly (0, 0) coordinates are treated
as missing. Keyset batches of 1000, commit per batch.

Resumable: by default only rows that have no site yet and whose raw_header
carries at least one site key are scanned. Pass --all to recompute every
row that has a raw_header.

Usage:
    python -m app.scripts.backfill_sites [--all]
"""

import sys

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import array

from app.models.image import Image
from app.utils.header_values import derive_site

BATCH_SIZE = 1000

# raw_header keys that can yield a site (used to skip rows cheaply in SQL).
SITE_HEADER_KEYS = [
    "SITELAT", "SITELONG", "OBSGEO-B", "OBSGEO-L", "LAT-OBS", "LONG-OBS",
    "SITENAME", "OBSERVAT", "EXIF:GPS GPSLatitude", "PIL:GPSInfo",
]


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_image_sites(process_all: bool = False, batch_size: int = BATCH_SIZE, session=None) -> dict:
    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    processed = 0
    updated = 0
    with_coords = 0
    last_id = 0
    try:
        while True:
            stmt = select(
                Image.id, Image.raw_header, Image.site_latitude, Image.site_longitude, Image.site_name,
            ).where(Image.id > last_id).where(Image.raw_header.isnot(None))
            stmt = stmt.where(Image.raw_header.has_any(array(SITE_HEADER_KEYS)))
            if not process_all:
                stmt = stmt.where(Image.site_latitude.is_(None)).where(Image.site_name.is_(None))
            rows = session.execute(stmt.order_by(Image.id).limit(batch_size)).all()
            if not rows:
                break

            batch = []
            for img_id, raw_header, old_lat, old_lon, old_name in rows:
                last_id = img_id
                lat, lon, name = derive_site({}, raw_header)
                if lat is not None:
                    with_coords += 1
                if (lat, lon, name) != (old_lat, old_lon, old_name):
                    batch.append({"id": img_id, "site_latitude": lat, "site_longitude": lon, "site_name": name})

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

    summary = {"processed": processed, "updated": updated, "with_coordinates": with_coords}
    log(f"Site backfill complete: {summary}")
    return summary


if __name__ == "__main__":
    print(backfill_image_sites(process_all="--all" in sys.argv))
