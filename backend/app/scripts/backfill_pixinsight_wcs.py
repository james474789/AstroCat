"""
Backfill PixInsight WCS

XISF files solved in PixInsight carry the solution in `PCL:AstrometricSolution`
properties, which the XISF extractor used to ignore: those images were indexed
with the mount's OBJCTRA/OBJCTDEC pointing, an optics-derived scale and no
rotation (or as unsolved). The extractor now reads the solution (see
app/utils/pixinsight_wcs.py).

This re-reads each XISF row that was not solved by the system solver and has
not already been read this way. Rows whose file holds a PixInsight solution get
centre, scale, rotation and field radius from it, are marked plate-solved from
the file header, and are catalog re-matched and target re-resolved (MANUAL
targets untouched). Rows without a solution are left alone.

Keyset batches of 100, committing per batch. Idempotent: converted rows carry
the PIWCS marker (conversion version) in raw_header and are skipped on a re-run
unless converted by an older version (v1 mirrored rows and used the linear
model only).

Usage:
    python -m app.scripts.backfill_pixinsight_wcs
    python -m app.scripts.backfill_pixinsight_wcs --dry-run
"""

import sys
from pathlib import Path

from sqlalchemy import func, or_, select, text

from app.database import SessionLocal
from app.extractors.xisf_extractor import XISFExtractor
from app.models.image import Image
from app.utils.field_geometry import effective_field_radius
from app.utils.pixinsight_wcs import MARKER_KEY, MARKER_VERSION

BATCH_SIZE = 100


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _extract(image):
    # Skip the base constructor's existence check; offline files just fail to open.
    ext = XISFExtractor.__new__(XISFExtractor)
    ext.file_path = Path(image.file_path)
    return ext.extract()


def backfill_pixinsight_wcs(dry_run: bool = False) -> dict:
    from app.services.matching import SyncCatalogMatcher
    from app.services.targets import assign_target_sync

    log(f"Starting PixInsight WCS backfill{' (dry run)' if dry_run else ''}...")
    summary = {"checked": 0, "converted": 0, "no_solution": 0, "unreadable": 0, "targets_changed": 0}
    last_id = 0
    with SessionLocal() as session:
        matcher = SyncCatalogMatcher(session)
        while True:
            images = session.execute(
                select(Image)
                .where(func.lower(Image.file_path).like("%.xisf"))
                .where(or_(Image.astrometry_status.is_(None), Image.astrometry_status != "SOLVED"))
                .where(or_(Image.raw_header.is_(None),
                           text(f"coalesce(images.raw_header->>'{MARKER_KEY}', '') <> '{MARKER_VERSION}'")))
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
                    metadata = _extract(image)
                except Exception as e:
                    log(f"Image {image.id}: could not read {image.file_path}: {e}")
                    summary["unreadable"] += 1
                    continue
                header = metadata.get("raw_header") or {}
                wcs = metadata.get("wcs") or {}
                if not header.get(MARKER_KEY) or wcs.get("ra_center") is None:
                    summary["no_solution"] += 1
                    continue
                summary["converted"] += 1
                if dry_run:
                    continue
                image.is_plate_solved = True
                image.plate_solve_source = "HEADER"
                image.ra_center_degrees = wcs["ra_center"]
                image.dec_center_degrees = wcs["dec_center"]
                image.pixel_scale_arcsec = wcs.get("pixel_scale")
                image.rotation_degrees = wcs.get("rotation")
                image.field_radius_degrees = effective_field_radius(
                    wcs.get("radius_degrees"), image.width_pixels, image.height_pixels, wcs.get("pixel_scale"))
                image.center_location = func.ST_SetSRID(
                    func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 4326)
                image.raw_header = header
                session.flush()
                # match_image commits, persisting the WCS along with the new matches.
                matcher.match_image(image.id)
                if assign_target_sync(session, image):
                    summary["targets_changed"] += 1
            if not dry_run:
                session.commit()
            log(f"Up to id {last_id}: {summary}")

    if not dry_run and summary["converted"]:
        try:
            import redis
            from app.config import settings

            r = redis.from_url(settings.redis_url)
            for key in r.scan_iter("cache:targets:*"):
                r.delete(key)
        except Exception as e:
            log(f"Warning: failed to clear targets cache: {e}")

    log(f"Done. {summary}")
    return summary


if __name__ == "__main__":
    backfill_pixinsight_wcs(dry_run="--dry-run" in sys.argv)
