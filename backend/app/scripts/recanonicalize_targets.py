"""
Re-canonicalise existing target keys (P0 §3.1).

Before P0 the MATCH path stored the raw normalised catalog designation, so
plate-solved frames of M81 were keyed NGC3031, the Bubble was C11 as well as
NGC7635, and Sharpless regions never merged with their NGC/IC nebula. This
re-keys existing rows through the current alias index:

- MATCH/HEADER keys whose alias resolves to a different canonical key are
  moved to it (one bulk UPDATE per old key);
- HEADER_RAW `OBJ:<text>` keys whose text now resolves become HEADER rows
  under the canonical key;
- target_goals follow their key; where both old and new key have a goal for
  the same filter_group the larger goal is kept;
- the Redis `cache:targets:*` entries are dropped.

MANUAL rows are never touched. Idempotent: a second run finds nothing to
remap and changes nothing.

`mark_unresolved_lights_none()` is the NONE-sentinel pass that data
migration 0004 runs afterwards.

Usage:
    python -m app.scripts.recanonicalize_targets
"""

import sys
from typing import Dict, Iterable, Optional, Tuple

from sqlalchemy import text

from app.services.targets import AliasIndex, NONE_SOURCE

OBJ_PREFIX = "OBJ:"
_MAX_RESOLVE_HOPS = 5


def log(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _canonical(key: str, alias_index: AliasIndex) -> Optional[str]:
    """Follow resolve() to a fixed point so a remap is never itself remappable."""
    current = alias_index.resolve(key)
    if not current:
        return None
    for _ in range(_MAX_RESOLVE_HOPS):
        nxt = alias_index.resolve(current)
        if not nxt or nxt == current:
            break
        current = nxt
    return current


def compute_key_remap(keys: Iterable[str], alias_index: AliasIndex) -> Dict[str, Tuple[str, bool]]:
    """
    Pure: {old_key: (new_key, from_obj)} for every key that should move.
    from_obj is True for an OBJ: (HEADER_RAW) key that now resolves.
    """
    remap = {}
    for key in keys:
        if not key:
            continue
        if key.startswith(OBJ_PREFIX):
            new = _canonical(key[len(OBJ_PREFIX):], alias_index)
            if new and new != key:
                remap[key] = (new, True)
        else:
            new = _canonical(key, alias_index)
            if new and new != key:
                remap[key] = (new, False)
    return remap


def merge_goal_rows(old_goals: Dict[str, float], new_goals: Dict[str, float]) -> Dict[str, Tuple[str, float]]:
    """
    Pure: decide what happens to each of the old key's goals.
    Returns {filter_group: (action, goal_seconds)} where action is
      "move"   - no goal on the new key for this group; re-key the old row;
      "raise"  - both exist, old is larger; set the new row to it, drop old;
      "drop"   - both exist, new is >= old; drop the old row.
    """
    plan = {}
    for group, old_value in old_goals.items():
        if group not in new_goals:
            plan[group] = ("move", old_value)
        elif (old_value or 0) > (new_goals[group] or 0):
            plan[group] = ("raise", old_value)
        else:
            plan[group] = ("drop", new_goals[group])
    return plan


_SELECT_KEYS_SQL = text(
    "SELECT DISTINCT target_key FROM images "
    "WHERE frame_type = 'LIGHT' AND target_key IS NOT NULL "
    "AND (target_source IS NULL OR target_source <> 'MANUAL')"
)

_UPDATE_IMAGES_SQL = text(
    "UPDATE images SET target_key = :new, "
    "target_source = CASE WHEN target_source = 'HEADER_RAW' THEN 'HEADER' ELSE target_source END "
    "WHERE target_key = :old AND frame_type = 'LIGHT' "
    "AND (target_source IS NULL OR target_source <> 'MANUAL')"
)

_SELECT_GOALS_SQL = text(
    "SELECT filter_group, goal_seconds FROM target_goals WHERE target_key = :key"
)


def _goals(session, key: str) -> Dict[str, float]:
    return {row[0]: row[1] for row in session.execute(_SELECT_GOALS_SQL, {"key": key}).all()}


def _merge_goals(session, old: str, new: str) -> int:
    old_goals = _goals(session, old)
    if not old_goals:
        return 0
    new_goals = _goals(session, new)
    merged = 0
    for group, (action, value) in merge_goal_rows(old_goals, new_goals).items():
        params = {"old": old, "new": new, "group": group, "value": value}
        if action == "move":
            session.execute(text(
                "UPDATE target_goals SET target_key = :new, updated_at = now() "
                "WHERE target_key = :old AND filter_group = :group"), params)
        else:
            merged += 1
            if action == "raise":
                session.execute(text(
                    "UPDATE target_goals SET goal_seconds = :value, updated_at = now() "
                    "WHERE target_key = :new AND filter_group = :group"), params)
            session.execute(text(
                "DELETE FROM target_goals WHERE target_key = :old AND filter_group = :group"), params)
    return merged


def _clear_targets_cache():
    try:
        import redis
        from app.config import settings

        r = redis.from_url(settings.redis_url)
        for key in r.scan_iter("cache:targets:*"):
            r.delete(key)
    except Exception as e:
        log(f"Warning: failed to clear targets cache: {e}")


def _reset_alias_index_cache():
    """Make the process-cached alias index pick up the new cross-IDs."""
    from app.services import targets as targets_service

    targets_service._alias_index_cache["index"] = None
    targets_service._alias_index_cache["loaded_at"] = 0.0


def recanonicalize_targets(session=None) -> dict:
    """
    Re-key non-MANUAL light rows to canonical target keys. Returns
    {remapped_keys, rows_updated, goals_merged, remaps, sh2_cross_ids}.
    `remaps` is {old: new} and `sh2_cross_ids` lists every Sh2<->NGC/IC pair
    the alias index applied, so the Admin summary can be reviewed.
    """
    from app.services.targets import _build_alias_index_sync

    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()

    try:
        alias_index = _build_alias_index_sync(session)
        keys = session.execute(_SELECT_KEYS_SQL).scalars().all()
        remap = compute_key_remap(keys, alias_index)

        rows_updated = 0
        goals_merged = 0
        for old, (new, _from_obj) in sorted(remap.items()):
            result = session.execute(_UPDATE_IMAGES_SQL, {"old": old, "new": new})
            count = result.rowcount or 0
            rows_updated += count
            goals_merged += _merge_goals(session, old, new)
            session.commit()
            log(f"{old} -> {new}: {count} rows")
    finally:
        if own_session:
            session.close()

    _reset_alias_index_cache()
    _clear_targets_cache()

    summary = {
        "remapped_keys": len(remap),
        "rows_updated": rows_updated,
        "goals_merged": goals_merged,
        "remaps": {old: new for old, (new, _f) in sorted(remap.items())},
        "sh2_cross_ids": list(getattr(alias_index, "sh2_cross_ids", []) or []),
    }
    log(f"Recanonicalise complete: {summary['remapped_keys']} keys, {rows_updated} rows, "
        f"{goals_merged} goals merged, {len(summary['sh2_cross_ids'])} Sh2 cross-IDs.")
    return summary


_MARK_NONE_SQL = text(
    "UPDATE images SET target_source = :none "
    "WHERE frame_type = 'LIGHT' AND target_key IS NULL AND target_source IS NULL"
)


def mark_unresolved_lights_none(session=None) -> int:
    """
    NONE-sentinel pass: lights with no key and no source (already through
    the resolver) get target_source='NONE'. Returns rows updated.
    """
    own_session = session is None
    if own_session:
        from app.database import SessionLocal
        session = SessionLocal()
    try:
        result = session.execute(_MARK_NONE_SQL, {"none": NONE_SOURCE})
        session.commit()
        return result.rowcount or 0
    finally:
        if own_session:
            session.close()


def reresolve_target_keys(keys: Iterable[str]) -> dict:
    """
    Re-run the resolver on non-MANUAL lights currently keyed to any of `keys`.
    Used when a cross-ID turns out to be wrong: recanonicalize_targets merges
    keys in place and can't split them again, but MATCH/HEADER rows can be
    re-derived from their catalog matches and header text.
    """
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models.image import Image, FrameType
    from app.services.targets import assign_target_sync

    keys = sorted(set(keys))
    _reset_alias_index_cache()
    processed = changed = 0
    moved = {}
    with SessionLocal() as session:
        rows = session.execute(
            select(Image)
            .where(Image.frame_type == FrameType.LIGHT)
            .where(Image.target_key.in_(keys))
            .where((Image.target_source.is_(None)) | (Image.target_source != "MANUAL"))
        ).scalars().all()
        for image in rows:
            before = image.target_key
            if assign_target_sync(session, image):
                changed += 1
                k = f"{before}->{image.target_key}"
                moved[k] = moved.get(k, 0) + 1
            processed += 1
        session.commit()
    _clear_targets_cache()
    log(f"Re-resolved {processed} rows for {keys}: {changed} changed {moved}")
    return {"keys": keys, "processed": processed, "changed": changed, "moved": moved}


if __name__ == "__main__":
    print(recanonicalize_targets())
