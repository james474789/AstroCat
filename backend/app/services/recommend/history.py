"""
Imaging history per canonical target (R1 spec §4.4).

`build_history(rows, as_of)` folds (pre-aggregated) light-sub rows into one
TargetHistory per target key. `as_of` keeps only nights strictly before it,
which is what makes the replay test honest.

Filter classes map normalize_filter buckets onto the Moon-rule classes:
L/R/G/B -> BB, Ha -> HA, SII -> SII, OIII/Hb -> OIII, Duo -> HA + OIII (50/50),
and no filter / unknown on a colour camera -> OSC (else BB).

Inferred goal: an explicit target_goals row wins (SET); else the median total
hours of targets with a master and >= 2 nights of the same kind (>= 3 samples),
else across all kinds (>= 3 samples) (INFERRED); else 10 h (DEFAULT).
"""

import bisect
import statistics
from collections import Counter, defaultdict, namedtuple
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.utils.filter_names import normalize_filter

CLASS_BB = "BB"
CLASS_HA = "HA"
CLASS_OIII = "OIII"
CLASS_SII = "SII"
CLASS_OSC = "OSC"
CLASSES = (CLASS_HA, CLASS_SII, CLASS_OIII, CLASS_BB, CLASS_OSC)
NARROWBAND = frozenset({CLASS_HA, CLASS_SII, CLASS_OIII})
BROADBAND = frozenset({CLASS_BB, CLASS_OSC})

DEFAULT_GOAL_HOURS = 10.0
GOAL_MIN_SAMPLES = 3

GOAL_SET = "SET"
GOAL_INFERRED = "INFERRED"
GOAL_DEFAULT = "DEFAULT"

# One pre-aggregated history row: light subs of one target on one night with
# one raw filter name / rig / camera colour / site.
HistoryRow = namedtuple("HistoryRow", ["key", "night", "filter_name", "seconds", "rig_id", "is_color",
                                       "site_id", "best_scale"])
HistoryRow.__new__.__defaults__ = (None, None, None, None)  # rig_id, is_color, site_id, best_scale

GoalRow = namedtuple("GoalRow", ["key", "filter_group", "goal_seconds", "created_at"])


def filter_classes(bucket: str, is_color: Optional[bool]) -> List[Tuple[str, float]]:
    """[(class, share)] for a normalize_filter bucket."""
    if bucket == "Ha":
        return [(CLASS_HA, 1.0)]
    if bucket == "SII":
        return [(CLASS_SII, 1.0)]
    if bucket in ("OIII", "Hb"):
        return [(CLASS_OIII, 1.0)]
    if bucket == "Duo":
        return [(CLASS_HA, 0.5), (CLASS_OIII, 0.5)]
    if bucket in ("L", "R", "G", "B"):
        return [(CLASS_BB, 1.0)]
    return [(CLASS_OSC if is_color else CLASS_BB, 1.0)]


def band_classes(band: str, is_color: Optional[bool]) -> Set[str]:
    """Rig filter band (Filter.band) -> Moon-rule classes."""
    return {c for c, _ in filter_classes(band, is_color)}


@dataclass
class TargetHistory:
    seconds_by_class: Counter = field(default_factory=Counter)
    seconds_by_filter: Counter = field(default_factory=Counter)
    nights: Set[date] = field(default_factory=set)
    first_night: Optional[date] = None
    last_night: Optional[date] = None
    rig_ids: Set[int] = field(default_factory=set)
    has_master: bool = False
    best_scale: Optional[float] = None

    @property
    def total_seconds(self) -> float:
        return float(sum(self.seconds_by_filter.values()))

    @property
    def total_hours(self) -> float:
        return self.total_seconds / 3600.0

    def hours_in(self, classes: Iterable[str]) -> float:
        return sum(self.seconds_by_class.get(c, 0.0) for c in classes) / 3600.0


def sort_rows(rows: Iterable[HistoryRow]) -> List[HistoryRow]:
    """Rows sorted by night (so build_history can slice as-of prefixes with bisect)."""
    return sorted((r for r in rows if r.night is not None), key=lambda r: r.night)


def build_history(rows: Sequence[HistoryRow], as_of: Optional[date] = None,
                  masters: Optional[Mapping[str, date]] = None, presorted: bool = False) -> Dict[str, TargetHistory]:
    """
    {key: TargetHistory}. `as_of` keeps nights strictly before it. `masters`
    maps key -> first night a master exists (counted when < as_of).
    `presorted=True` means rows are sorted by night (sort_rows) and lets the
    as-of cut use a binary search.
    """
    if as_of is not None and presorted:
        nights = _NightIndex.of(rows)
        rows = rows[:bisect.bisect_left(nights, as_of)]
    out: Dict[str, TargetHistory] = {}
    for r in rows:
        if r.night is None or not r.key or not r.seconds or r.seconds <= 0:
            continue
        if as_of is not None and r.night >= as_of:
            continue
        h = out.get(r.key)
        if h is None:
            h = out[r.key] = TargetHistory()
        bucket = normalize_filter(r.filter_name)
        secs = float(r.seconds)
        h.seconds_by_filter[bucket] += secs
        for cls, share in filter_classes(bucket, r.is_color):
            h.seconds_by_class[cls] += secs * share
        h.nights.add(r.night)
        if h.first_night is None or r.night < h.first_night:
            h.first_night = r.night
        if h.last_night is None or r.night > h.last_night:
            h.last_night = r.night
        if r.rig_id is not None:
            h.rig_ids.add(r.rig_id)
        if r.best_scale:
            h.best_scale = r.best_scale if h.best_scale is None else min(h.best_scale, r.best_scale)
    for key, first in (masters or {}).items():
        h = out.get(key)
        if h is not None and first is not None and (as_of is None or first < as_of):
            h.has_master = True
    return out


class _NightIndex:
    """Memoised list of row nights for bisecting presorted rows."""
    _cache: Dict[int, tuple] = {}

    @classmethod
    def of(cls, rows: Sequence[HistoryRow]) -> List[date]:
        hit = cls._cache.get(id(rows))
        if hit is not None and hit[0] is rows and hit[1] == len(rows):
            return hit[2]
        nights = [r.night for r in rows]
        if len(cls._cache) > 8:
            cls._cache.clear()
        cls._cache[id(rows)] = (rows, len(rows), nights)
        return nights


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

class GoalModel:
    """Goal hours per target: explicit rows, else per-kind median, else a default."""

    def __init__(self, explicit: Dict[str, float], median_by_kind: Dict[str, float], median_all: Optional[float]):
        self.explicit = explicit
        self.median_by_kind = median_by_kind
        self.median_all = median_all

    def goal_for(self, key: str, kind: str) -> Tuple[float, str]:
        if key in self.explicit:
            return self.explicit[key], GOAL_SET
        if kind in self.median_by_kind:
            return self.median_by_kind[kind], GOAL_INFERRED
        if self.median_all is not None:
            return self.median_all, GOAL_INFERRED
        return DEFAULT_GOAL_HOURS, GOAL_DEFAULT


def explicit_goals(goal_rows: Iterable[GoalRow], as_of: Optional[date] = None) -> Dict[str, float]:
    """{key: hours}: the ANY row if present, else the sum of per-filter rows. Rows created on/after as_of are ignored."""
    by_key: Dict[str, Dict[str, float]] = defaultdict(dict)
    for g in goal_rows:
        if as_of is not None and g.created_at is not None:
            created = g.created_at.date() if isinstance(g.created_at, datetime) else g.created_at
            if created >= as_of:
                continue
        by_key[g.key][g.filter_group] = float(g.goal_seconds or 0.0)
    out = {}
    for key, groups in by_key.items():
        secs = groups["ANY"] if "ANY" in groups else sum(groups.values())
        if secs > 0:
            out[key] = secs / 3600.0
    return out


def infer_goals(history: Mapping[str, TargetHistory], kind_of: Mapping[str, str],
                goal_rows: Iterable[GoalRow] = (), as_of: Optional[date] = None) -> GoalModel:
    samples: Dict[str, List[float]] = defaultdict(list)
    for key, h in history.items():
        if h.has_master and len(h.nights) >= 2:
            samples[kind_of.get(key, "OTHER")].append(h.total_hours)
    by_kind = {k: float(statistics.median(v)) for k, v in samples.items() if len(v) >= GOAL_MIN_SAMPLES}
    everything = [x for v in samples.values() for x in v]
    median_all = float(statistics.median(everything)) if len(everything) >= GOAL_MIN_SAMPLES else None
    return GoalModel(explicit_goals(goal_rows, as_of), by_kind, median_all)


# ---------------------------------------------------------------------------
# Key folding and narrowband hints (R1 tuning)
# ---------------------------------------------------------------------------

def remap_rows(rows: Sequence[HistoryRow], key_map: Mapping[str, str]) -> List[HistoryRow]:
    """Rows with stray keys replaced by their pool key (order preserved). History mapping only."""
    if not key_map:
        return list(rows)
    return [r._replace(key=key_map[r.key]) if r.key in key_map else r for r in rows]


def narrowband_keys(rows: Iterable[HistoryRow], min_share: float = 0.5) -> Set[str]:
    """Keys with at least `min_share` of their seconds in narrowband classes."""
    nb: Dict[str, float] = defaultdict(float)
    total: Dict[str, float] = defaultdict(float)
    for r in rows:
        if not r.key or not r.seconds or r.seconds <= 0:
            continue
        secs = float(r.seconds)
        total[r.key] += secs
        for cls, share in filter_classes(normalize_filter(r.filter_name), r.is_color):
            if cls in NARROWBAND:
                nb[r.key] += secs * share
    return {k for k, t in total.items() if t > 0 and nb[k] / t >= min_share}
