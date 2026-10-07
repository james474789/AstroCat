"""
Null Fake Rotation

The FITS extractor used to start rotation at 0.0 and keep it when the header
gave no rotation at all (no CD matrix, CROTA2 or rotation keyword), e.g. a
header with only OBJCTRA/OBJCTDEC pointing. Such rows stored a made-up 0 that
the field-overlap footprints then drew as a north-up rectangle.

Header-sourced rows with rotation exactly 0 are re-derived from the stored
raw_header with the current extractor: where it now yields no rotation the
column is set to NULL; a genuine 0 (CD matrix / CROTA2 / keyword) is left
alone. Only rotation_degrees is touched.

Keyset batches of 500, committing per batch. Idempotent.

Usage:
    python -m app.scripts.null_fake_rotation
    python -m app.scripts.null_fake_rotation --dry-run
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


def null_fake_rotation(dry_run: bool = False) -> dict:
    summary = {"checked": 0, "nulled": 0, "kept": 0, "errors": 0}
    last_id = 0
    with SessionLocal() as session:
        while True:
            images = session.execute(
                select(Image)
                .where(Image.plate_solve_source == "HEADER")
                .where(Image.rotation_degrees == 0)
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
                if wcs and wcs.get("rotation") is None:
                    summary["nulled"] += 1
                    if not dry_run:
                        image.rotation_degrees = None
                else:
                    summary["kept"] += 1
            if not dry_run:
                session.commit()
            log(f"Up to id {last_id}: {summary}")
    log(f"Done. {summary}")
    return summary


if __name__ == "__main__":
    null_fake_rotation(dry_run="--dry-run" in sys.argv)
