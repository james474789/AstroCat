"""
Repair images whose Nova solve returned an empty calibration

Before the calibration check in app/tasks/astrometry.py, a Nova job that
reported "success" but returned no calibration still ran the solved branch:
every position column was set to NULL while is_plate_solved stayed true and
astrometry_status became SOLVED. This finds those rows (plate-solved, no RA/Dec)
and, for each:

- File carries a WCS (FITS/XISF header or PixInsight solution): restore centre,
  scale, rotation and radius from the file, mark it HEADER-solved (status NONE),
  and re-match catalogs and targets.
- No WCS in the file: mark it unsolved and queue a fresh astrometry.net solve.

Idempotent: repaired rows no longer match the selection. Rows being re-solved
are unsolved, so a re-run does not queue them twice.

Usage:
    python -m app.scripts.repair_empty_nova_solves
    python -m app.scripts.repair_empty_nova_solves --dry-run
"""

import sys

from sqlalchemy import func, select

from app.database import SessionLocal
from app.extractors.factory import get_extractor
from app.models.image import Image
from app.utils.field_geometry import effective_field_radius

WCS_FORMATS = ("FITS", "FIT", "XISF")


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def repair_empty_nova_solves(dry_run: bool = False) -> dict:
    from app.services.matching import SyncCatalogMatcher
    from app.services.targets import assign_target_sync
    from app.tasks.astrometry import astrometry_task

    summary = {"checked": 0, "restored": 0, "queued": 0, "unreadable": 0, "targets_changed": 0}
    to_queue = []
    with SessionLocal() as session:
        matcher = SyncCatalogMatcher(session)
        images = session.execute(
            select(Image)
            .where(Image.is_plate_solved.is_(True))
            .where(Image.ra_center_degrees.is_(None))
            .order_by(Image.id)
        ).scalars().all()
        for image in images:
            summary["checked"] += 1
            wcs, header = {}, None
            if str(getattr(image.file_format, "value", image.file_format)).upper() in WCS_FORMATS:
                try:
                    metadata = get_extractor(image.file_path).extract()
                except Exception as e:
                    log(f"Image {image.id}: could not read {image.file_path}: {e}")
                    summary["unreadable"] += 1
                    metadata = {}
                if metadata.get("is_plate_solved") and metadata.get("plate_solve_source") == "HEADER":
                    wcs = metadata.get("wcs") or {}
                    header = metadata.get("raw_header")

            if wcs.get("ra_center") is not None and wcs.get("dec_center") is not None:
                summary["restored"] += 1
                if dry_run:
                    continue
                image.plate_solve_source = "HEADER"
                image.plate_solve_provider = None
                image.astrometry_status = "NONE"
                image.ra_center_degrees = wcs["ra_center"]
                image.dec_center_degrees = wcs["dec_center"]
                image.pixel_scale_arcsec = wcs.get("pixel_scale")
                image.rotation_degrees = wcs.get("rotation")
                image.field_radius_degrees = effective_field_radius(
                    wcs.get("radius_degrees"), image.width_pixels, image.height_pixels, wcs.get("pixel_scale"))
                image.center_location = func.ST_SetSRID(
                    func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 4326)
                if header:
                    image.raw_header = header
                session.flush()
                # match_image commits, persisting the WCS along with the new matches.
                matcher.match_image(image.id)
                if assign_target_sync(session, image):
                    summary["targets_changed"] += 1
            else:
                summary["queued"] += 1
                if dry_run:
                    continue
                image.is_plate_solved = False
                image.plate_solve_source = None
                image.plate_solve_provider = None
                image.astrometry_status = "NONE"
                to_queue.append(image.id)
            if not dry_run:
                session.commit()

    for image_id in to_queue:
        astrometry_task.delay(image_id)

    log(f"Done. {summary}")
    return summary


if __name__ == "__main__":
    repair_empty_nova_solves(dry_run="--dry-run" in sys.argv)
