"""
Backfill camera, exposure, ISO, lens and capture date for Canon CR3 files that
were indexed before the CR3 EXIF reader existed (QA: exifread cannot read CR3).

Only fills fields that are still empty, so it is idempotent and never
overwrites values from a later extraction. Files that are missing or unreadable
are skipped. Reads each CR3's header only (no full decode).

Usage:
    python -m app.scripts.backfill_cr3_exif
"""

import sys

from sqlalchemy import select, update

from app.models.image import Image

BATCH_SIZE = 500
_FIELDS = ("camera_name", "exposure_time_seconds", "iso_speed", "capture_date",
           "lens_model", "aperture", "focal_length")


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_cr3_exif(session=None) -> dict:
    from app.extractors.cr3 import read_cr3_exif
    from app.utils.capture_time import derive_capture_time_for_row

    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    processed = updated = unreadable = 0
    last_id = 0
    try:
        while True:
            rows = session.execute(
                select(Image)
                .where(Image.id > last_id)
                .where(Image.file_path.ilike("%.cr3"))
                .where(
                    Image.camera_name.is_(None)
                    | Image.exposure_time_seconds.is_(None)
                    | Image.iso_speed.is_(None)
                    | Image.capture_date.is_(None)
                    | Image.lens_model.is_(None)
                    | (Image.capture_time_source == "FILE_MTIME")
                )
                .order_by(Image.id)
                .limit(BATCH_SIZE)
            ).scalars().all()
            if not rows:
                break

            batch = []
            for img in rows:
                last_id = img.id
                data = read_cr3_exif(img.file_path)
                if not data:
                    unreadable += 1
                    continue
                # A capture_date from the indexer's file-mtime fallback is a placeholder,
                # so the real EXIF date replaces it.
                mtime_placeholder = img.capture_time_source == "FILE_MTIME"
                values = {k: data[k] for k in _FIELDS
                          if k in data and (getattr(img, k) is None
                                            or (k == "capture_date" and mtime_placeholder))}
                if not values:
                    continue
                if "capture_date" in values:
                    utc, source = derive_capture_time_for_row(
                        img.raw_header, values["capture_date"], img.file_last_modified)
                    values["capture_date_utc"] = utc
                    values["capture_time_source"] = source
                values["id"] = img.id
                batch.append(values)

            # Rows with a stable id may be re-selected if still incomplete after a
            # partial fill; last_id keyset paging prevents looping.
            if batch:
                session.execute(update(Image), batch)
            session.commit()
            processed += len(rows)
            updated += len(batch)
            log(f"Processed {processed} CR3 rows ({updated} updated)...")
    finally:
        if own_session:
            session.close()

    summary = {"processed": processed, "updated": updated, "unreadable": unreadable}
    log(f"CR3 EXIF backfill: {summary}")
    return summary


if __name__ == "__main__":
    print(backfill_cr3_exif())
