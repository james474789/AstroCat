"""
Target Resolution Service (F2)

Resolves each LIGHT sub-frame to a single canonical "target" (a catalog
object such as M31, or a free-text OBJ:<name> key), and provides sync/async
wrappers so the resolver can be called from both the Celery indexer (sync
SessionLocal) and the async astrometry task / API routes.

See docs/design/F2-target-integration.md §3.3 for the full spec.

Design notes:
- `normalize_designation`, `AliasIndex`, and `resolve_target` are pure
  functions/classes with no DB access, so they are unit-testable in
  isolation (tests/test_targets.py).
- `assign_target_sync` / `assign_target_async` are the only pieces that
  touch a session; they load an (process-cached, lazily refreshed)
  AliasIndex and the image's catalog matches, then delegate to
  `resolve_target`.
"""

import re
import time
import logging
from collections import namedtuple
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Designation / text normalization
# ---------------------------------------------------------------------------

_PREFIX_EXPANSIONS = [
    (re.compile(r"^MESSIER\b[\s_-]*"), "M"),
    (re.compile(r"^CALDWELL\b[\s_-]*"), "C"),
]

_SEPARATORS = re.compile(r"[\s_-]+")
_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


def normalize_designation(s: Optional[str]) -> str:
    """
    Normalize a catalog designation (or other short identifier) into a
    canonical, space-free, upper-case form with leading zeros stripped from
    the trailing numeric run.

    Examples:
        "M 31"          -> "M31"
        "Messier 31"    -> "M31"
        "NGC 0224"      -> "NGC224"
        "ngc224"        -> "NGC224"
        "IC 434"        -> "IC434"
        "Caldwell 14"   -> "C14"
        "C 14"          -> "C14"
        "Sh2-155"       -> "SH2155"
    """
    if not s:
        return ""

    text = s.strip().upper()
    if not text:
        return ""

    for pattern, repl in _PREFIX_EXPANSIONS:
        text = pattern.sub(repl, text)

    text = _SEPARATORS.sub("", text)

    match = _TRAILING_NUMBER.match(text)
    if match:
        prefix, digits = match.group(1), match.group(2)
        digits = digits.lstrip("0") or "0"
        text = prefix + digits

    return text


# Mosaic/panel suffix stripping, per spec regex.
_PANEL_SUFFIX_RE = re.compile(
    r"(?i)[\s_\-]*(panel|pane|p|tile|mosaic|mos)[\s_\-]*\d+$|\s*\[\d+\]$"
)


def strip_panel_suffix(text: Optional[str]) -> str:
    """Strip trailing mosaic/panel markers, e.g. 'M31 Panel 2' -> 'M31'."""
    if not text:
        return ""
    return _PANEL_SUFFIX_RE.sub("", text).strip()


_HEADER_SPLIT_SEPARATORS = (" - ", "(")


def _header_candidates(text: str) -> List[str]:
    """
    Build the list of text variants to try against the alias index for a
    header object-name lookup: the panel-stripped text, the raw text, and
    each with the portion before ' - ' / '(' taken (e.g. "M42 (Orion)" -> "M42").
    """
    if not text:
        return []

    stripped_panel = strip_panel_suffix(text)
    candidates = [stripped_panel, text]

    for base in (stripped_panel, text):
        for sep in _HEADER_SPLIT_SEPARATORS:
            if sep in base:
                candidates.append(base.split(sep, 1)[0].strip())

    # De-duplicate while preserving order.
    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


# ---------------------------------------------------------------------------
# Alias index (pure, in-memory)
# ---------------------------------------------------------------------------

class AliasIndex:
    """
    In-memory dict mapping normalized alias text -> canonical target_key.
    Pure data structure; building it from the DB is a separate step so this
    class stays trivially unit-testable.
    """

    def __init__(self):
        self._map = {}

    def add_alias(self, alias: Optional[str], canonical: str) -> None:
        if not alias or not canonical:
            return
        key = normalize_designation(alias)
        if not key:
            return
        # First registration wins so higher-priority catalogs (added first)
        # aren't overwritten by a later, lower-priority alias collision.
        self._map.setdefault(key, canonical)

    def add_aliases(self, aliases: Iterable[Optional[str]], canonical: str) -> None:
        for alias in aliases:
            self.add_alias(alias, canonical)

    def resolve(self, text: Optional[str]) -> Optional[str]:
        """Resolve free text (header object name or similar) to a canonical target_key, or None."""
        for candidate in _header_candidates(text):
            key = normalize_designation(candidate)
            if key and key in self._map:
                return self._map[key]
        return None

    def __len__(self):
        return len(self._map)


def _split_common_names(common_name: Optional[str]) -> List[str]:
    """OpenNGC-style common names can be comma-separated lists."""
    if not common_name:
        return []
    return [n.strip() for n in common_name.split(",") if n.strip()]


def build_alias_index(
    messier_rows: Sequence = (),
    ngc_rows: Sequence = (),
    caldwell_rows: Sequence = (),
    star_rows: Sequence = (),
) -> AliasIndex:
    """
    Build an AliasIndex from plain catalog rows (SQLAlchemy ORM objects, or
    any object/namedtuple exposing the same attributes work for tests).

    Processing order matters: Messier first, then NGC/IC (so canonical
    Messier keys exist for objects with both), then Caldwell (so
    source_designation can resolve through the NGC/Messier aliases already
    registered), then named stars last (header-resolution only).
    """
    index = AliasIndex()

    # Messier
    for row in messier_rows:
        canonical = f"M{row.messier_number}"
        index.add_alias(row.designation, canonical)
        index.add_alias(getattr(row, "ngc_designation", None), canonical)
        for name in _split_common_names(getattr(row, "common_name", None)):
            index.add_alias(name, canonical)

    # NGC / IC
    for row in ngc_rows:
        messier_designation = getattr(row, "messier_designation", None)
        if messier_designation:
            canonical = normalize_designation(messier_designation)
        else:
            canonical = normalize_designation(row.designation)
        index.add_alias(row.designation, canonical)
        index.add_alias(getattr(row, "ic_designation", None), canonical)
        for name in _split_common_names(getattr(row, "common_name", None)):
            index.add_alias(name, canonical)

    # Caldwell
    for row in caldwell_rows:
        source_designation = getattr(row, "source_designation", None)
        resolved = index.resolve(source_designation) if source_designation else None
        canonical = resolved or normalize_designation(row.designation)
        index.add_alias(row.designation, canonical)
        aliases_str = getattr(row, "aliases", None)
        if aliases_str:
            for alias in aliases_str.split(","):
                index.add_alias(alias.strip(), canonical)
        for name in _split_common_names(getattr(row, "common_name", None)):
            index.add_alias(name, canonical)

    # Named stars (header resolution only; never a MATCH candidate)
    for row in star_rows:
        canonical = normalize_designation(row.designation)
        index.add_alias(row.designation, canonical)
        for name in _split_common_names(getattr(row, "common_name", None)):
            index.add_alias(name, canonical)

    return index


# ---------------------------------------------------------------------------
# resolve_target — pure resolution logic
# ---------------------------------------------------------------------------

# Simple tuple describing one catalog match, as used by resolve_target.
# (catalog_type, designation, separation_deg, is_in_field, magnitude)
MatchInfo = namedtuple(
    "MatchInfo", ["catalog_type", "designation", "separation_deg", "is_in_field", "magnitude"]
)

_CATALOG_PRIORITY = {"MESSIER": 0, "NGC": 1, "IC": 1, "CALDWELL": 2}

_LIGHT_VALUES = {"LIGHT"}


def _as_str(value) -> str:
    """Accept either an enum member (with .value) or a plain string."""
    return getattr(value, "value", value)


def resolve_target(
    *,
    frame_type,
    object_name: Optional[str],
    matches: Sequence,
    field_radius: Optional[float],
    current_source: Optional[str],
    alias_index: AliasIndex,
    current_key: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve the (target_key, target_source) for an image.

    `matches` is a sequence of 5-tuples (catalog_type, designation,
    separation_deg, is_in_field, magnitude); MatchInfo namedtuples work too.

    `current_key`/`current_source` describe the image's existing values. A
    MANUAL source is a user override and is always preserved unchanged -
    this is why `current_key` is accepted even though it doesn't otherwise
    participate in resolution (the design doc's pseudocode omits it, but a
    pure "preserve MANUAL" behavior needs the current key to preserve).
    """
    if current_source == "MANUAL":
        return (current_key, "MANUAL")

    # Exclusion: only LIGHT sub-frames get a target.
    if _as_str(frame_type) not in _LIGHT_VALUES:
        return (None, None)

    # 2. HEADER
    if object_name:
        resolved = alias_index.resolve(object_name)
        if resolved:
            return (resolved, "HEADER")

    # 3. MATCH — central object among plate-solved catalog matches.
    if matches and field_radius:
        threshold = 0.35 * field_radius
        candidates = []
        for m in matches:
            catalog_type, designation, separation_deg, is_in_field, magnitude = m
            if not is_in_field:
                continue
            if _as_str(catalog_type) == "NAMED_STAR":
                continue
            if separation_deg is None or separation_deg > threshold:
                continue
            candidates.append((catalog_type, designation, separation_deg, magnitude))

        if candidates:
            def sort_key(item):
                catalog_type, _designation, separation_deg, magnitude = item
                priority = _CATALOG_PRIORITY.get(_as_str(catalog_type), 9)
                mag = magnitude if magnitude is not None else float("inf")
                return (separation_deg, priority, mag)

            best = sorted(candidates, key=sort_key)[0]
            return (normalize_designation(best[1]), "MATCH")

    # 4. HEADER_RAW — unresolved header text becomes an OBJ: key.
    if object_name:
        stripped = strip_panel_suffix(object_name)
        norm = normalize_designation(stripped)
        if norm:
            return (f"OBJ:{norm}", "HEADER_RAW")

    # 5. Unassigned
    return (None, None)


# ---------------------------------------------------------------------------
# Process-local cached alias index + sync/async wrappers
# ---------------------------------------------------------------------------

_ALIAS_INDEX_TTL_SECONDS = 3600
_alias_index_cache = {"index": None, "loaded_at": 0.0}


def _build_alias_index_sync(session) -> AliasIndex:
    from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog, NamedStarCatalog
    from sqlalchemy import select

    messier_rows = session.execute(select(MessierCatalog)).scalars().all()
    ngc_rows = session.execute(select(NGCCatalog)).scalars().all()
    caldwell_rows = session.execute(select(CaldwellCatalog)).scalars().all()
    star_rows = session.execute(select(NamedStarCatalog)).scalars().all()
    return build_alias_index(messier_rows, ngc_rows, caldwell_rows, star_rows)


async def _build_alias_index_async(db) -> AliasIndex:
    from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog, NamedStarCatalog
    from sqlalchemy import select

    messier_rows = (await db.execute(select(MessierCatalog))).scalars().all()
    ngc_rows = (await db.execute(select(NGCCatalog))).scalars().all()
    caldwell_rows = (await db.execute(select(CaldwellCatalog))).scalars().all()
    star_rows = (await db.execute(select(NamedStarCatalog))).scalars().all()
    return build_alias_index(messier_rows, ngc_rows, caldwell_rows, star_rows)


def _cache_is_stale() -> bool:
    return (
        _alias_index_cache["index"] is None
        or (time.time() - _alias_index_cache["loaded_at"]) > _ALIAS_INDEX_TTL_SECONDS
    )


def get_alias_index_sync(session) -> AliasIndex:
    if _cache_is_stale():
        _alias_index_cache["index"] = _build_alias_index_sync(session)
        _alias_index_cache["loaded_at"] = time.time()
    return _alias_index_cache["index"]


async def get_alias_index_async(db) -> AliasIndex:
    if _cache_is_stale():
        _alias_index_cache["index"] = await _build_alias_index_async(db)
        _alias_index_cache["loaded_at"] = time.time()
    return _alias_index_cache["index"]


def _load_match_infos_sync(session, image_id: int) -> List[MatchInfo]:
    from app.models.matches import ImageCatalogMatch
    from sqlalchemy import select

    rows = session.execute(
        select(ImageCatalogMatch).where(ImageCatalogMatch.image_id == image_id)
    ).scalars().all()
    return _matches_with_magnitudes_sync(session, rows)


async def _load_match_infos_async(db, image_id: int) -> List[MatchInfo]:
    from app.models.matches import ImageCatalogMatch
    from sqlalchemy import select

    rows = (await db.execute(
        select(ImageCatalogMatch).where(ImageCatalogMatch.image_id == image_id)
    )).scalars().all()
    return await _matches_with_magnitudes_async(db, rows)


def _matches_with_magnitudes_sync(session, rows) -> List[MatchInfo]:
    from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog
    from app.models.matches import CatalogType
    from sqlalchemy import select

    mag_map = {}
    by_type = {"MESSIER": [], "NGC": [], "IC": [], "CALDWELL": []}
    for r in rows:
        t = _as_str(r.catalog_type)
        if t in by_type:
            by_type[t].append(r.catalog_designation)

    if by_type["MESSIER"]:
        for obj in session.execute(
            select(MessierCatalog).where(MessierCatalog.designation.in_(by_type["MESSIER"]))
        ).scalars():
            mag_map[("MESSIER", obj.designation)] = obj.apparent_magnitude

    ngc_ic_desigs = by_type["NGC"] + by_type["IC"]
    if ngc_ic_desigs:
        for obj in session.execute(
            select(NGCCatalog).where(NGCCatalog.designation.in_(ngc_ic_desigs))
        ).scalars():
            mag_map[("NGC", obj.designation)] = obj.apparent_magnitude
            mag_map[("IC", obj.designation)] = obj.apparent_magnitude

    if by_type["CALDWELL"]:
        for obj in session.execute(
            select(CaldwellCatalog).where(CaldwellCatalog.designation.in_(by_type["CALDWELL"]))
        ).scalars():
            mag_map[("CALDWELL", obj.designation)] = obj.apparent_magnitude

    return [
        MatchInfo(
            catalog_type=_as_str(r.catalog_type),
            designation=r.catalog_designation,
            separation_deg=r.angular_separation_degrees,
            is_in_field=bool(r.is_in_field),
            magnitude=mag_map.get((_as_str(r.catalog_type), r.catalog_designation)),
        )
        for r in rows
    ]


async def _matches_with_magnitudes_async(db, rows) -> List[MatchInfo]:
    from app.models.catalog import MessierCatalog, NGCCatalog, CaldwellCatalog
    from sqlalchemy import select

    mag_map = {}
    by_type = {"MESSIER": [], "NGC": [], "IC": [], "CALDWELL": []}
    for r in rows:
        t = _as_str(r.catalog_type)
        if t in by_type:
            by_type[t].append(r.catalog_designation)

    if by_type["MESSIER"]:
        result = await db.execute(
            select(MessierCatalog).where(MessierCatalog.designation.in_(by_type["MESSIER"]))
        )
        for obj in result.scalars():
            mag_map[("MESSIER", obj.designation)] = obj.apparent_magnitude

    ngc_ic_desigs = by_type["NGC"] + by_type["IC"]
    if ngc_ic_desigs:
        result = await db.execute(
            select(NGCCatalog).where(NGCCatalog.designation.in_(ngc_ic_desigs))
        )
        for obj in result.scalars():
            mag_map[("NGC", obj.designation)] = obj.apparent_magnitude
            mag_map[("IC", obj.designation)] = obj.apparent_magnitude

    if by_type["CALDWELL"]:
        result = await db.execute(
            select(CaldwellCatalog).where(CaldwellCatalog.designation.in_(by_type["CALDWELL"]))
        )
        for obj in result.scalars():
            mag_map[("CALDWELL", obj.designation)] = obj.apparent_magnitude

    return [
        MatchInfo(
            catalog_type=_as_str(r.catalog_type),
            designation=r.catalog_designation,
            separation_deg=r.angular_separation_degrees,
            is_in_field=bool(r.is_in_field),
            magnitude=mag_map.get((_as_str(r.catalog_type), r.catalog_designation)),
        )
        for r in rows
    ]


def assign_target_sync(session, image) -> bool:
    """
    Resolve and set image.target_key / image.target_source in place.
    Returns True if the value changed. Does not commit - caller controls
    the transaction boundary. Skips (no-op) when the current source is
    MANUAL, per the F2 spec (user overrides are never touched by automation).
    """
    if image.target_source == "MANUAL":
        return False

    alias_index = get_alias_index_sync(session)
    matches = _load_match_infos_sync(session, image.id) if image.is_plate_solved else []

    key, source = resolve_target(
        frame_type=image.frame_type,
        object_name=image.object_name,
        matches=matches,
        field_radius=image.field_radius_degrees,
        current_source=image.target_source,
        current_key=image.target_key,
        alias_index=alias_index,
    )

    changed = (image.target_key != key) or (image.target_source != source)
    image.target_key = key
    image.target_source = source
    return changed


async def assign_target_async(db, image) -> bool:
    """Async counterpart of assign_target_sync (for tasks/astrometry.py and API routes)."""
    if image.target_source == "MANUAL":
        return False

    alias_index = await get_alias_index_async(db)
    matches = await _load_match_infos_async(db, image.id) if image.is_plate_solved else []

    key, source = resolve_target(
        frame_type=image.frame_type,
        object_name=image.object_name,
        matches=matches,
        field_radius=image.field_radius_degrees,
        current_source=image.target_source,
        current_key=image.target_key,
        alias_index=alias_index,
    )

    changed = (image.target_key != key) or (image.target_source != source)
    image.target_key = key
    image.target_source = source
    return changed


def resolve_manual_target(text: Optional[str], alias_index: AliasIndex) -> Optional[str]:
    """
    Resolve free text typed by a user (PUT /api/images/{id} or bulk assign)
    into a target_key. Empty string clears the target. Resolves through the
    alias index first (so "m 31" -> "M31"), otherwise falls back to an
    OBJ:<normalized> key so any free-text target can be tracked.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    resolved = alias_index.resolve(text)
    if resolved:
        return resolved

    norm = normalize_designation(strip_panel_suffix(text) or text)
    if not norm:
        return None
    return f"OBJ:{norm}"
