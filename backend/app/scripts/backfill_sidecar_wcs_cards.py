"""
Backfill Sidecar WCS Cards

Sidecar .ini plate solves carry a full linear WCS ([WCS] section, or the CD
matrix in ASTAP's flat format), but only the centre/scale/rotation summary
was stored in raw_header["SIDECAR"]. The sky overlay (app/services/
sky_overlay.py) needs the WCS itself, so re-read each sidecar and add
raw_header["SIDECAR"]["wcs"] plus the row order it was solved in (see
SidecarParser._add_wcs). Nothing else on the row changes.

Keyset batches of 500, committing per batch. Idempotent: rows that already
have the cards are skipped; rows whose sidecar has no WCS are left as they are.

Usage:
    python -m app.scripts.backfill_sidecar_wcs_cards
    python -m app.scripts.backfill_sidecar_wcs_cards --dry-run
"""

import sys
from pathlib import Path

from sqlalchemy import select, text

from app.database import SessionLocal
from app.extractors.ini_parser import SidecarParser
from app.models.image import Image

BATCH_SIZE = 500


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def backfill_sidecar_wcs_cards(dry_run: bool = False) -> dict:
    log(f"Starting sidecar WCS card backfill{' (dry run)' if dry_run else ''}...")
    summary = {"checked": 0, "filled": 0, "bottom_up": 0, "no_wcs": 0, "unreadable": 0}
    last_id = 0
    with SessionLocal() as session:
        while True:
            images = session.execute(
                select(Image)
                .where(text("images.raw_header ? 'SIDECAR'"))
                .where(text("NOT (images.raw_header->'SIDECAR' ? 'wcs')"))
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
                    parsed = SidecarParser.parse(Path(image.file_path))
                except Exception as e:
                    log(f"Image {image.id}: could not read sidecar: {e}")
                    summary["unreadable"] += 1
                    continue
                if not parsed or not parsed.get("wcs"):
                    summary["no_wcs"] += 1
                    continue
                summary["filled"] += 1
                if parsed["wcs_row_order"] == "BOTTOM_UP":
                    summary["bottom_up"] += 1
                if dry_run:
                    continue
                header = dict(image.raw_header)
                sidecar = dict(header.get("SIDECAR") or {})
                for key in ("wcs", "wcs_source", "wcs_row_order"):
                    sidecar[key] = parsed[key]
                header["SIDECAR"] = sidecar
                image.raw_header = header  # new dict so the JSONB change is detected
            if not dry_run:
                session.commit()
            log(f"Up to id {last_id}: {summary}")
    log(f"Done: {summary}")
    return summary


if __name__ == "__main__":
    backfill_sidecar_wcs_cards(dry_run="--dry-run" in sys.argv)
