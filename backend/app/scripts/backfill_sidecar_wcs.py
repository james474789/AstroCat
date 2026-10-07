"""
Backfill Sidecar WCS

Plate-solve sidecar .ini files were parsed incompletely:

- Astrometry.net .ini ([Astrometry] section): the position, scale and radius
  were stored but the `orientation` was dropped, leaving rotation_degrees NULL.
- ASTAP .ini (flat KEY=VALUE, PLTSOLVD=T, CD matrix/CROTA): not understood at
  all, so those images were indexed as not plate-solved.

This re-reads the sidecar for both groups (see app/extractors/ini_parser.py):

1. SIDECAR rows with no rotation get their rotation filled in.
2. Unsolved rows (not solved by the system solver) whose sidecar is a
   successful ASTAP solve get the full WCS (centre, scale, radius, rotation)
   and are marked plate-solved from the sidecar; they are then catalog
   re-matched and target re-resolved (MANUAL targets untouched).

Keyset batches of 500, committing per batch. Idempotent: rows already filled
(or with no usable sidecar) are skipped on a re-run.

Usage:
    python -m app.scripts.backfill_sidecar_wcs
    python -m app.scripts.backfill_sidecar_wcs --dry-run
"""

import os
import sys
from pathlib import Path

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.extractors.ini_parser import SidecarParser
from app.models.image import Image
from app.utils.field_geometry import effective_field_radius

BATCH_SIZE = 500


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _parse(image):
    try:
        return SidecarParser.parse(Path(image.file_path))
    except Exception as e:
        log(f"Image {image.id}: could not read sidecar: {e}")
        return None


def _fill_rotation(session, dry_run, summary):
    last_id = 0
    while True:
        images = session.execute(
            select(Image)
            .where(Image.plate_solve_source == "SIDECAR")
            .where(Image.rotation_degrees.is_(None))
            .where(Image.id > last_id)
            .order_by(Image.id)
            .limit(BATCH_SIZE)
        ).scalars().all()
        if not images:
            break
        for image in images:
            last_id = image.id
            wcs = _parse(image)
            rotation = wcs.get("rotation") if wcs else None
            if rotation is None:
                summary["rotation_skipped"] += 1
                continue
            summary["rotation_filled"] += 1
            if not dry_run:
                image.rotation_degrees = rotation
        if not dry_run:
            session.commit()
        log(f"Rotation: up to id {last_id}: filled {summary['rotation_filled']}, skipped {summary['rotation_skipped']}")


class _DirListings:
    """One directory listing per folder instead of several stat calls per file (slow on network shares)."""

    MAX_DIRS = 2000

    def __init__(self):
        self._cache = {}

    def has_ini(self, file_path: str) -> bool:
        p = Path(file_path)
        names = self._cache.get(p.parent)
        if names is None:
            if len(self._cache) >= self.MAX_DIRS:
                self._cache.clear()
            try:
                names = {n for n in os.listdir(p.parent) if n.lower().endswith(".ini")}
            except OSError:
                names = set()
            self._cache[p.parent] = names
        return p.with_suffix(".ini").name in names


def _solve_astap_rows(session, dry_run, summary):
    from app.services.matching import SyncCatalogMatcher
    from app.services.targets import assign_target_sync

    matcher = SyncCatalogMatcher(session)
    listings = _DirListings()
    last_id = 0
    while True:
        images = session.execute(
            select(Image)
            .where(or_(Image.is_plate_solved.is_(False), Image.is_plate_solved.is_(None)))
            .where(or_(Image.astrometry_status.is_(None), Image.astrometry_status != "SOLVED"))
            .where(Image.id > last_id)
            .order_by(Image.id)
            .limit(BATCH_SIZE)
        ).scalars().all()
        if not images:
            break
        for image in images:
            last_id = image.id
            summary["unsolved_checked"] += 1
            # Only ASTAP .ini solves are new here; skip the rest without touching the file.
            if not listings.has_ini(image.file_path):
                continue
            wcs = _parse(image)
            if not wcs or wcs.get("ra_center") is None:
                continue
            summary["astap_solved"] += 1
            if dry_run:
                continue
            image.is_plate_solved = True
            image.plate_solve_source = "SIDECAR"
            image.ra_center_degrees = wcs["ra_center"]
            image.dec_center_degrees = wcs["dec_center"]
            image.pixel_scale_arcsec = wcs.get("pixel_scale")
            image.rotation_degrees = wcs.get("rotation")
            image.field_radius_degrees = effective_field_radius(
                wcs.get("radius_degrees"), image.width_pixels, image.height_pixels, wcs.get("pixel_scale"))
            image.center_location = func.ST_SetSRID(
                func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 4326)
            session.flush()
            # match_image commits, persisting the WCS along with the new matches.
            matcher.match_image(image.id)
            if assign_target_sync(session, image):
                summary["targets_changed"] += 1
        if not dry_run:
            session.commit()
        log(f"ASTAP: up to id {last_id}: checked {summary['unsolved_checked']}, solved {summary['astap_solved']}")


def backfill_sidecar_wcs(dry_run: bool = False) -> dict:
    log(f"Starting sidecar WCS backfill{' (dry run)' if dry_run else ''}...")
    summary = {"rotation_filled": 0, "rotation_skipped": 0,
               "unsolved_checked": 0, "astap_solved": 0, "targets_changed": 0}
    with SessionLocal() as session:
        _fill_rotation(session, dry_run, summary)
        _solve_astap_rows(session, dry_run, summary)

    if not dry_run and summary["astap_solved"]:
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
    backfill_sidecar_wcs(dry_run="--dry-run" in sys.argv)
