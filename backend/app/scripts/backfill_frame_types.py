"""
Backfill frame_type / frame_type_source for existing rows (F1).

Classifies rows *without re-reading files* -- it uses the `raw_header` and
`file_path` values already stored in the database, so it runs at roughly
100k rows/minute.

By default it only touches rows that have never been classified
(`frame_type_source IS NULL`), so it is safe and cheap to run on every
startup. Pass --all to reclassify every row except ones a user set manually
(`frame_type_source == 'MANUAL'`).

Usage:
    python -m app.scripts.backfill_frame_types [--dry-run] [--all] [--batch-size N]
"""

import argparse
import logging
import sys
from collections import Counter

from sqlalchemy import select, update, delete

from app.database import SessionLocal
from app.models.image import Image, FrameType
from app.models.matches import ImageCatalogMatch
from app.services.frame_type import classify_frame_type

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


def _clear_stats_cache():
    """Bump the Redis stats cache so dashboards reflect the new classification."""
    try:
        import redis
        from app.config import settings

        r = redis.from_url(settings.redis_url)
        keys = list(r.scan_iter("cache:stats:*"))
        if keys:
            r.delete(*keys)
            logger.info(f"Cleared {len(keys)} cache:stats:* keys")
    except Exception as e:
        logger.warning(f"Could not clear stats cache (non-fatal): {e}")


def backfill_frame_types(dry_run: bool = False, reclassify_all: bool = False, batch_size: int = BATCH_SIZE):
    """
    Keyset-paginated, resumable backfill of frame_type/frame_type_source.

    Returns a summary dict: {"total": n, "transitions": Counter, "dry_run": bool}
    """
    transitions = Counter()  # (old_frame_type, new_frame_type, source) -> count
    total_processed = 0
    total_updated = 0
    last_id = 0

    with SessionLocal() as session:
        while True:
            stmt = select(Image.id, Image.file_path, Image.raw_header,
                          Image.frame_type, Image.frame_type_source).where(Image.id > last_id)

            if not reclassify_all:
                stmt = stmt.where(Image.frame_type_source.is_(None))
            else:
                stmt = stmt.where(Image.frame_type_source != "MANUAL")

            stmt = stmt.order_by(Image.id).limit(batch_size)

            rows = session.execute(stmt).all()
            if not rows:
                break

            batch_updates = []
            became_non_light_ids = []

            for row in rows:
                img_id, file_path, raw_header, old_frame_type, old_source = row
                last_id = img_id
                total_processed += 1

                result = classify_frame_type(raw_header, file_path or "")
                transitions[(old_frame_type.value if old_frame_type else None, result.frame_type.value, result.source)] += 1

                if old_frame_type != result.frame_type or old_source != result.source:
                    total_updated += 1
                    if not dry_run:
                        batch_updates.append({"id": img_id, "frame_type": result.frame_type, "frame_type_source": result.source})
                        if result.frame_type != FrameType.LIGHT:
                            became_non_light_ids.append(img_id)

            if not dry_run and batch_updates:
                session.execute(update(Image), batch_updates)
                if became_non_light_ids:
                    session.execute(
                        delete(ImageCatalogMatch).where(
                            ImageCatalogMatch.image_id.in_(became_non_light_ids),
                            ImageCatalogMatch.match_source != "MANUAL"
                        )
                    )
                session.commit()

            logger.info(f"Processed {total_processed} rows (last_id={last_id}, updated={total_updated})...")

            if len(rows) < batch_size:
                break

    summary = {"total": total_processed, "updated": total_updated, "transitions": transitions, "dry_run": dry_run}

    if not dry_run and total_updated:
        _clear_stats_cache()

    return summary


def _print_summary(summary: dict):
    print()
    print(f"{'[DRY RUN] ' if summary['dry_run'] else ''}Frame type backfill summary")
    print(f"  Rows scanned: {summary['total']}")
    print(f"  Rows changed: {summary['updated']}")
    print()
    print(f"  {'old':<10} {'new':<10} {'source':<10} count")
    for (old, new, source), count in sorted(summary["transitions"].items(), key=lambda kv: -kv[1]):
        print(f"  {str(old):<10} {new:<10} {source:<10} {count}")


def main():
    parser = argparse.ArgumentParser(description="Backfill Image.frame_type / frame_type_source from stored raw_header/file_path.")
    parser.add_argument("--dry-run", action="store_true", help="Print a summary without writing changes.")
    parser.add_argument("--all", action="store_true", dest="reclassify_all",
                         help="Reclassify every row (except MANUAL), not just unclassified ones.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    summary = backfill_frame_types(dry_run=args.dry_run, reclassify_all=args.reclassify_all, batch_size=args.batch_size)
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
