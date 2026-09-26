"""
Backfill Field Radius

Plate-solved images whose field_radius_degrees is NULL or 0 (typically
sidecar .ini solves that omit the radius) get a radius derived from pixel
scale x image half-diagonal. Each fixed image is then catalog re-matched
(its matches were computed with the 1.0 deg fallback radius) and its target
re-resolved (MANUAL targets are left untouched).

Keyset batches of 500, committing per batch. Safe to re-run: rows that
already have a positive radius, or can't derive one, are skipped.

Usage:
    python -m app.scripts.backfill_field_radius
    python -m app.scripts.backfill_field_radius --dry-run
"""

import sys

from sqlalchemy import select, or_

from app.database import SessionLocal
from app.models.image import Image
from app.services.matching import SyncCatalogMatcher
from app.services.targets import assign_target_sync
from app.utils.field_geometry import field_radius_from_scale

BATCH_SIZE = 500


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_field_radius(dry_run: bool = False):
    log(f"Starting field radius backfill{' (dry run)' if dry_run else ''}...")

    fixed = 0
    skipped = 0
    targets_changed = 0
    last_id = 0

    with SessionLocal() as session:
        matcher = SyncCatalogMatcher(session)
        while True:
            images = session.execute(
                select(Image)
                .where(Image.is_plate_solved.is_(True))
                .where(or_(Image.field_radius_degrees.is_(None), Image.field_radius_degrees <= 0))
                .where(Image.id > last_id)
                .order_by(Image.id)
                .limit(BATCH_SIZE)
            ).scalars().all()
            if not images:
                break

            for image in images:
                last_id = image.id
                radius = field_radius_from_scale(
                    image.width_pixels, image.height_pixels, image.pixel_scale_arcsec
                )
                if radius is None:
                    skipped += 1
                    continue
                fixed += 1
                if dry_run:
                    continue

                image.field_radius_degrees = radius
                session.flush()
                # match_image commits, persisting the radius along with the new matches.
                matcher.match_image(image.id)
                if assign_target_sync(session, image):
                    targets_changed += 1

            if not dry_run:
                session.commit()
            log(f"Up to id {last_id}: fixed {fixed}, skipped {skipped}, targets changed {targets_changed}")

    if not dry_run:
        try:
            import redis
            from app.config import settings

            r = redis.from_url(settings.redis_url)
            for key in r.scan_iter("cache:targets:*"):
                r.delete(key)
        except Exception as e:
            log(f"Warning: failed to clear targets cache: {e}")

    log(f"Done. Fixed {fixed} radii, skipped {skipped} (no pixel scale/dimensions), targets changed {targets_changed}.")


if __name__ == "__main__":
    backfill_field_radius(dry_run="--dry-run" in sys.argv)
