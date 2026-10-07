"""
Restore Header Rotation

Migration 0012 (null_fake_rotation) NULLed header-solved rows whose rotation
the extractor then could not find, but it missed two sources of real rotation:
CD/PC/CROTA2 keywords on headers whose astropy WCS fails to build (e.g.
TAN-SIP without SIP coefficients), and explicit rotator keywords whose value
is 0. The extractor now reads both.

Header-sourced rows with NULL rotation are re-derived from raw_header; any that
now yield a rotation get it back. Only rotation_degrees is touched.
Batches of 500, committing per batch. Idempotent.

Usage:
    python -m app.scripts.restore_header_rotation [--dry-run]
"""

import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.image import Image
from app.scripts.repair_pixel_scale import _extractor
from app.utils.plate_scale import with_image_size

BATCH_SIZE = 500


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def restore_header_rotation(dry_run: bool = False) -> dict:
    summary = {"checked": 0, "restored": 0, "still_unknown": 0, "errors": 0}
    last_id = 0
    with SessionLocal() as session:
        while True:
            images = session.execute(
                select(Image)
                .where(Image.plate_solve_source == "HEADER")
                .where(Image.rotation_degrees.is_(None))
                .where(Image.raw_header.isnot(None))
                .where(Image.id > last_id)
                .order_by(Image.id)
                .limit(BATCH_SIZE)
            ).scalars().all()
            if not images:
                break
            for image in images:
                last_id = image.id
                summary["checked"] += 1
                try:
                    header = with_image_size(image.raw_header, image.width_pixels, image.height_pixels)
                    wcs = _extractor(image.file_path)._extract_wcs(header)
                except Exception as e:
                    summary["errors"] += 1
                    log(f"Image {image.id}: could not re-derive WCS: {e}")
                    continue
                rotation = wcs.get("rotation") if wcs else None
                if rotation is None:
                    summary["still_unknown"] += 1
                    continue
                summary["restored"] += 1
                if not dry_run:
                    image.rotation_degrees = rotation
            if not dry_run:
                session.commit()
            log(f"Up to id {last_id}: {summary}")
    log(f"Done. {summary}")
    return summary


if __name__ == "__main__":
    restore_header_rotation(dry_run="--dry-run" in sys.argv)
