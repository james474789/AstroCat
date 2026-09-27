"""
Repair Pixel Scale

Re-derives plate scale (and, where the header WCS was misread, center and
field radius) for images indexed before the extractor fixes in
app/utils/plate_scale.py:

- HEADER rows: re-run the extractor's WCS logic over the stored raw_header.
  Fixes RESOLUTN=72 (print DPI) stored as 72"/px, ZWO ASIAIR headers whose
  WCS belongs to a downsampled IMAGEW x IMAGEH grid (4x scale, center taken
  ~6 deg off the field), CDELT read in preference to a CD matrix, XISF
  headers without NAXIS cards (no WCS center/radius), and 0 stored for
  "unknown" (now NULL). Rows whose center or radius moved are catalog
  re-matched and target re-resolved (MANUAL targets untouched).
- SOLVER rows: astrometry.net's pixscale is per pixel of the downsampled
  upload; re-derive it from the solved field radius and the original size.
- Then re-run equipment assignment over everything: rig matching uses the
  scale, and rigs.measured_scale_arcsec is re-derived from the fixed rows.

Keyset batches of 500, committing per batch. Idempotent: a second run finds
nothing to change.

Usage:
    python -m app.scripts.repair_pixel_scale
    python -m app.scripts.repair_pixel_scale --dry-run
"""

import math
import sys
from pathlib import Path

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.extractors.fits_extractor import FITSExtractor
from app.models.image import Image
from app.utils.field_geometry import effective_field_radius
from app.utils.plate_scale import solved_pixel_scale, with_image_size

BATCH_SIZE = 500
# Smaller differences are formula noise (e.g. CD determinant vs first
# column), not wrong data; rewriting them would only churn rows and matches.
SCALE_REL_EPS = 0.01
RADIUS_REL_EPS = 0.01
POSITION_EPS_DEG = 0.01


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _extractor(file_path: str) -> FITSExtractor:
    # _extract_wcs only needs file_path for log messages; skip the base
    # constructor's existence check (library files may be offline).
    ext = FITSExtractor.__new__(FITSExtractor)
    ext.file_path = Path(file_path)
    return ext


def _differs(old, new, rel_eps) -> bool:
    if old is None or new is None:
        return (old is None) != (new is None)
    return abs(old - new) > rel_eps * max(abs(old), abs(new), 1e-12)


def _moved(old_ra, old_dec, new_ra, new_dec) -> bool:
    if None in (old_ra, old_dec, new_ra, new_dec):
        return (old_ra is None) != (new_ra is None) or (old_dec is None) != (new_dec is None)
    d_ra = (new_ra - old_ra + 180.0) % 360.0 - 180.0
    return math.hypot(d_ra * math.cos(math.radians(new_dec)), new_dec - old_dec) > POSITION_EPS_DEG


def rederive_header_wcs(image) -> dict:
    """{column: new value} for the WCS columns that materially differ from what raw_header now yields."""
    header = with_image_size(image.raw_header, image.width_pixels, image.height_pixels)
    wcs = _extractor(image.file_path)._extract_wcs(header)
    if not wcs:
        return {}
    scale = wcs.get("pixel_scale")
    radius = effective_field_radius(wcs.get("radius_degrees"), image.width_pixels,
                                    image.height_pixels, scale)
    changes = {}
    if _differs(image.pixel_scale_arcsec or None, scale, SCALE_REL_EPS) or image.pixel_scale_arcsec == 0:
        changes["pixel_scale_arcsec"] = scale
    if _moved(image.ra_center_degrees, image.dec_center_degrees, wcs.get("ra_center"), wcs.get("dec_center")) \
            or _differs(image.field_radius_degrees, radius, RADIUS_REL_EPS):
        changes.update(ra_center_degrees=wcs.get("ra_center"), dec_center_degrees=wcs.get("dec_center"),
                       field_radius_degrees=radius, rotation_degrees=wcs.get("rotation"))
    return changes


def _repair_header_rows(session, dry_run, summary):
    from app.services.matching import SyncCatalogMatcher
    from app.services.targets import assign_target_sync

    matcher = SyncCatalogMatcher(session)
    last_id = 0
    while True:
        images = session.execute(
            select(Image)
            .where(Image.plate_solve_source == "HEADER")
            .where(Image.raw_header.isnot(None))
            .where(or_(Image.astrometry_status.is_(None), Image.astrometry_status != "SOLVED"))
            .where(Image.id > last_id)
            .order_by(Image.id)
            .limit(BATCH_SIZE)
        ).scalars().all()
        if not images:
            break

        for image in images:
            last_id = image.id
            summary["header_checked"] += 1
            try:
                changes = rederive_header_wcs(image)
            except Exception as e:
                summary["header_errors"] += 1
                log(f"Image {image.id}: could not re-derive WCS: {e}")
                continue
            if not changes:
                continue
            if "pixel_scale_arcsec" in changes:
                summary["scale_changed"] += 1
                if changes["pixel_scale_arcsec"] is None:
                    summary["scale_cleared"] += 1
            moved = "field_radius_degrees" in changes
            if moved:
                summary["position_changed"] += 1
            if len(summary["examples"]) < 10:
                summary["examples"].append({"id": image.id, "old_scale": image.pixel_scale_arcsec,
                                            "new_scale": changes.get("pixel_scale_arcsec", image.pixel_scale_arcsec)})
            if dry_run:
                continue

            for col, value in changes.items():
                setattr(image, col, value)
            if moved:
                if image.ra_center_degrees is not None and image.dec_center_degrees is not None:
                    image.center_location = func.ST_SetSRID(
                        func.ST_MakePoint(float(image.ra_center_degrees), float(image.dec_center_degrees)), 4326)
                session.flush()
                # match_image commits, persisting the new WCS along with the new matches.
                matcher.match_image(image.id)
                if assign_target_sync(session, image):
                    summary["targets_changed"] += 1

        if not dry_run:
            session.commit()
        log(f"Header rows up to id {last_id}: scale changed {summary['scale_changed']}, "
            f"position changed {summary['position_changed']}")


def _repair_solver_rows(session, dry_run, summary):
    images = session.execute(
        select(Image)
        .where(Image.plate_solve_source == "SOLVER")
        .where(Image.field_radius_degrees > 0)
        .where(Image.width_pixels > 0)
        .where(Image.height_pixels > 0)
    ).scalars().all()
    for image in images:
        new = solved_pixel_scale({"radius": image.field_radius_degrees},
                                 image.width_pixels, image.height_pixels)
        if new and _differs(image.pixel_scale_arcsec, new, SCALE_REL_EPS):
            summary["solver_changed"] += 1
            if len(summary["examples"]) < 15:
                summary["examples"].append({"id": image.id, "old_scale": image.pixel_scale_arcsec, "new_scale": new})
            if not dry_run:
                image.pixel_scale_arcsec = new
    if not dry_run:
        session.commit()


def repair_pixel_scale(dry_run: bool = False) -> dict:
    log(f"Starting pixel scale repair{' (dry run)' if dry_run else ''}...")
    summary = {"header_checked": 0, "header_errors": 0, "scale_changed": 0, "scale_cleared": 0,
               "position_changed": 0, "targets_changed": 0, "solver_changed": 0, "examples": []}

    with SessionLocal() as session:
        _repair_header_rows(session, dry_run, summary)
        _repair_solver_rows(session, dry_run, summary)

    if not dry_run and (summary["scale_changed"] or summary["solver_changed"]):
        # Rig matching uses the scale, and measured rig scales are medians of it.
        from app.tasks.equipment import run_assignment
        assignment = run_assignment("all")
        summary["rig_changed"] = assignment.get("rig_changed", 0)
        summary["rigs_measured"] = assignment.get("measured")

    log(f"Done: {summary}")
    return summary


if __name__ == "__main__":
    repair_pixel_scale(dry_run="--dry-run" in sys.argv)
