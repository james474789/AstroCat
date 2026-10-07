"""
Thumbnail cache statistics (the "thumbnails" row of system_stats).

The Admin page reads count/size from the database, so the row has to be kept
current without ever walking the cache directory on a hot path: the cache can
hold ~100k files and a full walk takes minutes. Instead every thumbnail write
applies a small delta (one stat before and after the write), and a rare full
walk (update_thumbnail_stats task) seeds the row and corrects drift.
"""

import logging
import os
from typing import Optional, Tuple

from sqlalchemy import text

logger = logging.getLogger(__name__)

CATEGORY = "thumbnails"


def file_size(path: str) -> Optional[int]:
    """Size in bytes, or None if the file doesn't exist."""
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def delta_for_write(old_size: Optional[int], new_size: Optional[int]) -> Tuple[int, int]:
    """(count delta, bytes delta) for a file that went from old_size to new_size (None = absent)."""
    d_count = (1 if new_size is not None else 0) - (1 if old_size is not None else 0)
    return d_count, (new_size or 0) - (old_size or 0)


def apply_delta(session, d_count: int, d_bytes: int) -> None:
    """Atomically add a delta to the thumbnails row, creating it if missing. Does not commit."""
    if d_count == 0 and d_bytes == 0:
        return
    session.execute(
        text(
            """
            INSERT INTO system_stats (category, count, size_bytes)
            VALUES (:category, GREATEST(:dc, 0), GREATEST(:db, 0))
            ON CONFLICT (category) DO UPDATE SET
                count = GREATEST(system_stats.count + :dc, 0),
                size_bytes = GREATEST(system_stats.size_bytes + :db, 0),
                updated_at = now()
            """
        ),
        {"category": CATEGORY, "dc": d_count, "db": d_bytes},
    )


def record_write(thumb_path: Optional[str], old_size: Optional[int]) -> None:
    """Account for a thumbnail that was (re)written. Never raises: stats must not fail generation."""
    if not thumb_path:
        return
    try:
        d_count, d_bytes = delta_for_write(old_size, file_size(thumb_path))
        if d_count == 0 and d_bytes == 0:
            return
        from app.database import SessionLocal
        with SessionLocal() as session:
            apply_delta(session, d_count, d_bytes)
            session.commit()
    except Exception as e:
        logger.warning(f"Could not update thumbnail stats: {e}")


def generate_tracked(source_path: str, output_dir: str, **kwargs) -> Optional[str]:
    """ThumbnailGenerator.generate plus the stats delta for whatever it wrote."""
    from app.services.thumbnails import ThumbnailGenerator

    old_size = file_size(ThumbnailGenerator.thumb_path_for(source_path, output_dir))
    thumb_path = ThumbnailGenerator.generate(source_path, output_dir, **kwargs)
    record_write(thumb_path, old_size)
    return thumb_path


