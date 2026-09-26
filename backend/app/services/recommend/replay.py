"""
Replay test (R1 spec §5, tuning round §14). Pure: no DB access;
`scripts/replay_recommendations.py` loads the data and writes the report.

For each historical imaging night N (>= 30 min of resolved light subs):
- history = build_history(rows, as_of=N): strictly earlier nights only;
  target_goals created on/after N are ignored;
- site = the majority site of N's subs, else the default site;
- rig = the majority rig of N's subs, else the rigs used within N +/- 365 days,
  else every active rig;
- actual = the target keys with >= 30 min on N;
- run recommend(...) and keep `ranked` (every lane counts: R1 has no novelty
  or revisit lanes).

Each actual (key, night) pair is classified by where it was taken:
HOME (majority site_latitude within 1.5 deg of a configured site), REMOTE
(elsewhere, e.g. a remote telescope) or UNKNOWN_SITE (no latitude). Headline
metrics use HOME + UNKNOWN_SITE; REMOTE is reported separately, because
remote nights are scored against the home sky. Pairs are also split by the
majority target_source: HEADER (HEADER, HEADER_RAW, MANUAL) vs MATCH.

Metrics: hit@k (k = 1, 3, 5, 10), MRR and feasible_recall (the share of actual
in-pool pairs that pass hard feasibility; every miss is listed with its reason
and the numbers behind it). Baselines over the same feasible set: recency,
altitude (usable hours) and random (seeded, averaged over 20 runs).
"""

import bisect
import itertools
import random
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from app.services.recommend import (
    RIG_MODE_ALL, RIG_MODE_SINGLE, EngineInputs, Params, PreparedNight, Result, pair_details, recommend,
)
from app.services.recommend.history import HistoryRow, build_history, infer_goals
from app.services.recommend.scoring import CLASS_BB, CLASS_OSC, MOON_RULES, Weights

MIN_NIGHT_SECONDS = 1800.0
MIN_TARGET_SECONDS = 1800.0
KS = (1, 3, 5, 10)
RIG_WINDOW_DAYS = 365
RANDOM_RUNS = 20
MAJORITY_SHARE = 0.5
METRIC_KEYS = tuple(f"hit@{k}" for k in KS) + ("mrr", "feasible_recall")
NOT_IN_POOL = "NOT_IN_POOL"

HOME = "HOME"
REMOTE = "REMOTE"
UNKNOWN_SITE = "UNKNOWN_SITE"
HOME_LAT_TOLERANCE_DEG = 1.5
SOURCE_HEADER = "HEADER"
SOURCE_MATCH = "MATCH"
SOURCE_OTHER = "OTHER"
HEADER_LIKE = frozenset({"HEADER", "HEADER_RAW", "MANUAL"})

# Old coarse grid (kept for callers that pass explicit factors).
GRID_FACTORS = (0.5, 1.0, 2.0)
GRID_KEYS = ("observability", "project", "momentum", "urgency")
# Tuning grid (values, not factors); 3*3*2*2*2*2*1*2 = 288 combinations.
TUNING_SPACE: Dict[str, Tuple[float, ...]] = {
    "momentum": (0.3, 0.45, 0.6),
    "momentum_tau_days": (10.0, 20.0, 45.0),
    "recency_rank": (0.0, 0.2),
    "project": (0.0, 0.1),
    "observability": (0.125, 0.25),
    "framing": (0.1, 0.2),
    "urgency": (0.075,),
    "prior": (0.05, 0.1),
}
MOON_BB_D_VALUES = (60.0, 90.0, 120.0)

# (key, night, rounded site latitude or None, target_source, seconds)
PairRow = namedtuple("PairRow", ["key", "night", "lat", "source", "seconds"])


def headline(o: "NightOutcome", key: str) -> bool:
    return o.pair_class.get(key, UNKNOWN_SITE) in (HOME, UNKNOWN_SITE)


def remote_only(o: "NightOutcome", key: str) -> bool:
    return o.pair_class.get(key, UNKNOWN_SITE) == REMOTE


# ---------------------------------------------------------------------------
# Nights
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayNight:
    night: date
    site_id: Optional[int]
    rig_id: Optional[int]
    actual: frozenset
    seconds: float


def _majority(seconds_by: Mapping[Any, float], total: float) -> Optional[Any]:
    known = {k: v for k, v in seconds_by.items() if k is not None}
    if not known or total <= 0:
        return None
    best = max(sorted(known, key=str), key=lambda k: known[k])
    return best if known[best] > MAJORITY_SHARE * total else None


def replay_nights(rows: Iterable[HistoryRow], since: Optional[date] = None, until: Optional[date] = None,
                  min_night_seconds: float = MIN_NIGHT_SECONDS,
                  min_target_seconds: float = MIN_TARGET_SECONDS) -> List[ReplayNight]:
    total: Dict[date, float] = defaultdict(float)
    by_key: Dict[date, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_site: Dict[date, Dict[Any, float]] = defaultdict(lambda: defaultdict(float))
    by_rig: Dict[date, Dict[Any, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r.night is None or not r.seconds or r.seconds <= 0:
            continue
        if (since and r.night < since) or (until and r.night > until):
            continue
        s = float(r.seconds)
        total[r.night] += s
        by_key[r.night][r.key] += s
        by_site[r.night][r.site_id] += s
        by_rig[r.night][r.rig_id] += s
    out = []
    for night in sorted(total):
        if total[night] < min_night_seconds:
            continue
        actual = frozenset(k for k, v in by_key[night].items() if v >= min_target_seconds)
        if not actual:
            continue
        out.append(ReplayNight(night=night, site_id=_majority(by_site[night], total[night]),
                               rig_id=_majority(by_rig[night], total[night]), actual=actual,
                               seconds=total[night]))
    return out


def rig_nights_index(rows: Iterable[HistoryRow]) -> Dict[int, List[date]]:
    idx: Dict[int, Set[date]] = defaultdict(set)
    for r in rows:
        if r.rig_id is not None and r.night is not None:
            idx[r.rig_id].add(r.night)
    return {k: sorted(v) for k, v in idx.items()}


def rigs_near(index: Mapping[int, List[date]], night: date, days: int = RIG_WINDOW_DAYS) -> List[int]:
    lo, hi = night - timedelta(days=days), night + timedelta(days=days)
    out = []
    for rig_id, nights in index.items():
        i = bisect.bisect_left(nights, lo)
        if i < len(nights) and nights[i] <= hi:
            out.append(rig_id)
    return sorted(out)


# ---------------------------------------------------------------------------
# Pair classification
# ---------------------------------------------------------------------------

def source_class(source: Optional[str]) -> str:
    if source in HEADER_LIKE:
        return SOURCE_HEADER
    if source == "MATCH":
        return SOURCE_MATCH
    return SOURCE_OTHER


def classify_pairs(pair_rows: Iterable[PairRow], site_lats: Sequence[float],
                   key_map: Optional[Mapping[str, str]] = None,
                   tolerance_deg: float = HOME_LAT_TOLERANCE_DEG) -> Dict[Tuple[str, date], Tuple[str, str]]:
    """
    {(key, night): (HOME | REMOTE | UNKNOWN_SITE, HEADER | MATCH | OTHER)} from
    the majority (by seconds) latitude bucket and target_source of each pair.
    Keys go through key_map (the same folding as the history).
    """
    key_map = key_map or {}
    lat_s: Dict[Tuple[str, date], Dict[Any, float]] = defaultdict(lambda: defaultdict(float))
    src_s: Dict[Tuple[str, date], Dict[Any, float]] = defaultdict(lambda: defaultdict(float))
    for r in pair_rows:
        if r.night is None or not r.seconds:
            continue
        k = (key_map.get(r.key, r.key), r.night)
        lat = None if r.lat is None else round(float(r.lat), 1)
        lat_s[k][lat] += float(r.seconds)
        src_s[k][r.source] += float(r.seconds)
    out = {}
    for k, buckets in lat_s.items():
        lat = max(sorted(buckets, key=lambda x: (x is None, x if x is not None else 0)), key=lambda x: buckets[x])
        if lat is None:
            site = UNKNOWN_SITE
        elif any(abs(lat - s) <= tolerance_deg for s in site_lats):
            site = HOME
        else:
            site = REMOTE
        srcs = src_s[k]
        src = max(sorted(srcs, key=str), key=lambda x: srcs[x])
        out[k] = (site, source_class(src))
    return out


# ---------------------------------------------------------------------------
# Inputs "as of" a night
# ---------------------------------------------------------------------------

@dataclass
class ReplayData:
    """Everything the replay needs, loaded once (no DB access here)."""
    pool: Any                                   # CandidatePool
    rows: List[HistoryRow]                      # sorted by night (history.sort_rows), keys folded
    masters: Dict[str, date]
    goal_rows: list
    sites: Dict[int, Any]                       # id -> SiteSpec
    default_site_id: int
    horizons: Dict[int, Any]                    # site id -> HorizonSpec
    rig_specs: Dict[int, Any]                   # rig id -> RigSpec (rigs with scale + FOV)
    active_rig_ids: List[int]
    rig_index: Dict[int, List[date]] = field(default_factory=dict)
    pair_info: Dict[Tuple[str, date], Tuple[str, str]] = field(default_factory=dict)
    fold_stats: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.rig_index:
            self.rig_index = rig_nights_index(self.rows)
        self.kind_of = {c.key: c.kind for c in self.pool.candidates}

    def rigs_for(self, nr: ReplayNight) -> Tuple[list, str, bool]:
        """(rig specs, rig_mode, rig_known)."""
        if nr.rig_id is not None and nr.rig_id in self.rig_specs:
            return [self.rig_specs[nr.rig_id]], RIG_MODE_SINGLE, True
        near = [self.rig_specs[i] for i in rigs_near(self.rig_index, nr.night) if i in self.rig_specs]
        if near:
            return near, RIG_MODE_ALL, False
        return [self.rig_specs[i] for i in self.active_rig_ids if i in self.rig_specs], RIG_MODE_ALL, False

    def inputs_for(self, nr: ReplayNight) -> Tuple[EngineInputs, str, bool]:
        site_id = nr.site_id if nr.site_id in self.sites else self.default_site_id
        history = build_history(self.rows, as_of=nr.night, masters=self.masters, presorted=True)
        goals = infer_goals(history, self.kind_of, self.goal_rows, as_of=nr.night)
        rigs, mode, known = self.rigs_for(nr)
        inputs = EngineInputs(candidates=self.pool, history=history, site=self.sites[site_id],
                              horizon=self.horizons[site_id], rigs=rigs, goals=goals, night=nr.night)
        return inputs, mode, known


# ---------------------------------------------------------------------------
# Per-night outcome
# ---------------------------------------------------------------------------

@dataclass
class NightOutcome:
    night: date
    tier: str
    moon_illum: float
    rig_known: bool
    actual: frozenset
    in_pool: frozenset
    ranked: List[str]                    # engine order (feasible keys)
    last_night: Dict[str, Optional[date]]
    usable_h: Dict[str, float]
    misses: List[Dict[str, Any]]
    pair_class: Dict[str, str] = field(default_factory=dict)    # key -> HOME | REMOTE | UNKNOWN_SITE
    pair_source: Dict[str, str] = field(default_factory=dict)   # key -> HEADER | MATCH | OTHER

    def baseline(self, name: str, rng: Optional[random.Random] = None) -> List[str]:
        if name == "recency":
            return sorted(self.ranked, key=lambda k: (-(self.last_night[k].toordinal() if self.last_night.get(k)
                                                         else -10 ** 9), k))
        if name == "altitude":
            return sorted(self.ranked, key=lambda k: (-self.usable_h.get(k, 0.0), k))
        if name == "random":
            keys = sorted(self.ranked)
            (rng or random.Random(0)).shuffle(keys)
            return keys
        return list(self.ranked)


def _miss_details(result: Result, i: int) -> Optional[Dict[str, Any]]:
    """Numbers for the rig that got furthest through the hard filters."""
    evals = result.evals
    if not evals or result.prepared is None:
        return None
    codes = [int(e.excluded[i]) for e in evals]
    ev = evals[int(np.argmax(codes))]
    return pair_details(result.prepared, ev, i, result.params)


def outcome_from_result(nr: ReplayNight, inputs: EngineInputs, result: Result, rig_known: bool,
                        pair_info: Optional[Mapping[Tuple[str, date], Tuple[str, str]]] = None,
                        with_details: bool = True) -> NightOutcome:
    pool = inputs.pool
    pair_info = pair_info or {}
    ranked = [p.key for p in result.ranked]
    feasible = set(ranked)
    in_pool = frozenset(k for k in nr.actual if k in pool.index)
    pair_class = {k: pair_info.get((k, nr.night), (UNKNOWN_SITE, SOURCE_OTHER))[0] for k in nr.actual}
    pair_source = {k: pair_info.get((k, nr.night), (UNKNOWN_SITE, SOURCE_OTHER))[1] for k in nr.actual}
    misses = []
    for key in sorted(nr.actual):
        if key in feasible:
            continue
        miss = {"night": nr.night.isoformat(), "target_key": key, "site_class": pair_class[key],
                "source": pair_source[key]}
        if key not in pool.index:
            miss["reason"] = NOT_IN_POOL
        else:
            miss["reason"] = result.excluded.get(key, "UNKNOWN")
            if with_details:
                miss["details"] = _miss_details(result, pool.index[key])
        misses.append(miss)
    return NightOutcome(
        night=nr.night, tier=result.context.tier, moon_illum=result.context.moon_illum, rig_known=rig_known,
        actual=nr.actual, in_pool=in_pool, ranked=ranked,
        last_night={p.key: p.last_imaged for p in result.ranked},
        usable_h={p.key: p.usable_hours for p in result.ranked},
        misses=misses, pair_class=pair_class, pair_source=pair_source,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

Keep = Optional[Callable[[NightOutcome, str], bool]]


def rank_metrics(ranked: Sequence[str], actual: Iterable[str]) -> Dict[str, float]:
    actual = set(actual)
    best = next((i for i, k in enumerate(ranked) if k in actual), None)
    out = {f"hit@{k}": 1.0 if best is not None and best < k else 0.0 for k in KS}
    out["mrr"] = 1.0 / (best + 1) if best is not None else 0.0
    return out


def aggregate(outcomes: Sequence[NightOutcome], order: Callable[[NightOutcome], List[str]],
              keep: Keep = None) -> Dict[str, float]:
    """Mean metrics over nights; with `keep`, only the actual pairs it accepts (nights without any are skipped)."""
    sums = defaultdict(float)
    pairs = hits = n = 0
    for o in outcomes:
        actual = o.actual if keep is None else frozenset(k for k in o.actual if keep(o, k))
        if not actual:
            continue
        n += 1
        for k, v in rank_metrics(order(o), actual).items():
            sums[k] += v
        in_pool = o.in_pool & actual
        pairs += len(in_pool)
        hits += len(in_pool & set(o.ranked))
    if not n:
        return {**{k: 0.0 for k in METRIC_KEYS}, "nights": 0}
    out = {k: round(sums[k] / n, 4) for k in METRIC_KEYS if k != "feasible_recall"}
    out["feasible_recall"] = round(hits / pairs, 4) if pairs else 0.0
    out["nights"] = n
    return out


def random_metrics(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS,
                   keep: Keep = None) -> Dict[str, float]:
    acc = defaultdict(float)
    nights = 0
    for run in range(runs):
        rng = random.Random(seed * 1000 + run)
        m = aggregate(outcomes, lambda o, rng=rng: o.baseline("random", rng), keep)
        nights = m["nights"]
        for k in METRIC_KEYS:
            acc[k] += m[k]
    out = {k: round(acc[k] / runs, 4) for k in METRIC_KEYS}
    out["nights"] = nights
    return out


def all_metrics(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS,
                keep: Keep = None) -> Dict[str, Dict[str, float]]:
    return {
        "engine": aggregate(outcomes, lambda o: o.ranked, keep),
        "recency": aggregate(outcomes, lambda o: o.baseline("recency"), keep),
        "altitude": aggregate(outcomes, lambda o: o.baseline("altitude"), keep),
        "random": random_metrics(outcomes, seed, runs, keep),
    }


def moon_bucket(illum: float) -> str:
    if illum < 0.25:
        return "<0.25"
    if illum <= 0.75:
        return "0.25-0.75"
    return ">0.75"


def breakdown(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS,
              keep: Keep = headline) -> Dict[str, Any]:
    """Night-level dimensions on the headline set, plus pair-level site class and source."""
    dims = {
        "tier": lambda o: o.tier,
        "moon": lambda o: moon_bucket(o.moon_illum),
        "year": lambda o: str(o.night.year),
        "rig_known": lambda o: "known" if o.rig_known else "unknown",
    }
    out: Dict[str, Any] = {}
    for dim, fn in dims.items():
        groups: Dict[str, List[NightOutcome]] = defaultdict(list)
        for o in outcomes:
            groups[fn(o)].append(o)
        out[dim] = {g: all_metrics(v, seed, runs, keep) for g, v in sorted(groups.items())}
    out["site_class"] = {
        c: all_metrics(outcomes, seed, runs, lambda o, k, c=c: o.pair_class.get(k, UNKNOWN_SITE) == c)
        for c in (HOME, UNKNOWN_SITE, REMOTE)
    }
    out["source"] = {
        s: all_metrics(outcomes, seed, runs,
                       lambda o, k, s=s: (keep is None or keep(o, k)) and o.pair_source.get(k) == s)
        for s in (SOURCE_HEADER, SOURCE_MATCH, SOURCE_OTHER)
    }
    return out


def miss_summary(outcomes: Sequence[NightOutcome], keep: Keep = None) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for o in outcomes:
        for m in o.misses:
            if keep is None or keep(o, m["target_key"]):
                counts[m["reason"]] += 1
    return dict(sorted(counts.items()))


def pair_counts(outcomes: Sequence[NightOutcome]) -> Dict[str, Dict[str, int]]:
    site: Dict[str, int] = defaultdict(int)
    source: Dict[str, int] = defaultdict(int)
    for o in outcomes:
        for k in o.actual:
            site[o.pair_class.get(k, UNKNOWN_SITE)] += 1
            source[o.pair_source.get(k, SOURCE_OTHER)] += 1
    return {"site_class": dict(sorted(site.items())), "source": dict(sorted(source.items()))}


def params_dict(params: Params) -> Dict[str, Any]:
    return {
        "weights": params.weights.as_dict(),
        "momentum_tau_days": params.momentum_tau_days,
        "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in params.moon_rules.items()},
        "moon_hard_fraction": params.moon_hard_fraction,
        "bright_broadband_factor": params.bright_broadband_factor,
        "min_usable_h": params.min_usable_h,
        "min_night_seconds": MIN_NIGHT_SECONDS,
        "min_target_seconds": MIN_TARGET_SECONDS,
        "home_lat_tolerance_deg": HOME_LAT_TOLERANCE_DEG,
    }


def build_report(outcomes: Sequence[NightOutcome], params: Params, generated_at: Optional[datetime] = None,
                 seed: int = 0, runs: int = RANDOM_RUNS, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The report contract; metrics/baselines/breakdown are on the headline set (HOME + UNKNOWN_SITE)."""
    m = all_metrics(outcomes, seed, runs, headline)
    remote = all_metrics(outcomes, seed, runs, remote_only)
    pairs = sum(len(o.actual) for o in outcomes)
    in_pool = sum(len(o.in_pool) for o in outcomes)
    report = {
        "generated_at": (generated_at or datetime.utcnow()).replace(microsecond=0).isoformat() + "Z",
        "nights": m["engine"]["nights"],
        "params": params_dict(params),
        "metrics": {k: m["engine"][k] for k in METRIC_KEYS},
        "baselines": {b: {k: m[b][k] for k in METRIC_KEYS} for b in ("recency", "altitude", "random")},
        "breakdown": breakdown(outcomes, seed, runs),
        "misses": [x for o in outcomes for x in o.misses],
        # Additive fields.
        "headline_set": [HOME, UNKNOWN_SITE],
        "remote": {"nights": remote["engine"]["nights"],
                   "metrics": {k: remote["engine"][k] for k in METRIC_KEYS},
                   "baselines": {b: {k: remote[b][k] for k in METRIC_KEYS} for b in ("recency", "altitude", "random")},
                   "miss_reasons": miss_summary(outcomes, remote_only)},
        "pair_counts": pair_counts(outcomes),
        "nights_total": len(outcomes),
        "actual_pairs": pairs,
        "pool_coverage": round(in_pool / pairs, 4) if pairs else 0.0,
        "miss_reasons": miss_summary(outcomes, headline),
        "beats_baselines": {
            b: {"hit@5": m["engine"]["hit@5"] > m[b]["hit@5"], "mrr": m["engine"]["mrr"] > m[b]["mrr"]}
            for b in ("recency", "altitude", "random")
        },
        "seed": seed,
    }
    if extra:
        report.update(extra)
    return report


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

InputsFn = Callable[[ReplayNight], Tuple[EngineInputs, str, bool]]


def _slim(prep: PreparedNight) -> PreparedNight:
    """Drop the N x T arrays so many nights can be kept for re-scoring."""
    prep.sky = None
    prep.feats.limit = None
    return prep


def run_replay(nights: Sequence[ReplayNight], inputs_fn: InputsFn, params: Optional[Params] = None,
               progress: Optional[Callable[[int, int], None]] = None,
               keep: Optional[Dict[date, Tuple[EngineInputs, str, bool, PreparedNight]]] = None,
               pair_info: Optional[Mapping[Tuple[str, date], Tuple[str, str]]] = None,
               with_details: bool = True) -> List[NightOutcome]:
    """
    Run the engine for each night and collect outcomes. With `keep` (a dict),
    the per-night inputs and slimmed prepared state are stored for re-scoring
    (grid search).
    """
    params = replace(params or Params(), light=True)
    outcomes = []
    for n, nr in enumerate(nights, start=1):
        cached = keep.get(nr.night) if keep is not None else None
        if cached is not None:
            inputs, mode, known, prep = cached
        else:
            inputs, mode, known = inputs_fn(nr)
            prep = None
        result = recommend(inputs, replace(params, rig_mode=mode), prepared=prep)
        outcomes.append(outcome_from_result(nr, inputs, result, known, pair_info, with_details))
        if keep is not None and cached is None:
            keep[nr.night] = (inputs, mode, known, _slim(result.prepared))
        if progress:
            progress(n, len(nights))
    return outcomes


def weight_grid(base: Optional[Weights] = None, factors: Sequence[float] = GRID_FACTORS,
                keys: Sequence[str] = GRID_KEYS) -> List[Weights]:
    base = base or Weights()
    out = []
    for combo in itertools.product(factors, repeat=len(keys)):
        out.append(replace(base, **{k: round(getattr(base, k) * f, 4) for k, f in zip(keys, combo)}))
    return out


def tuning_grid(space: Optional[Mapping[str, Sequence[float]]] = None, base: Optional[Weights] = None,
                max_combos: Optional[int] = None, seed: int = 0) -> List[Tuple[Weights, float]]:
    """
    [(Weights, momentum_tau_days)] over the tuning space (values, not factors).
    Weights not in the space keep their base value. With max_combos, a seeded
    sample (the base-like combination is always kept first).
    """
    space = dict(space or TUNING_SPACE)
    base = base or Weights()
    taus = space.pop("momentum_tau_days", (45.0,))
    keys = sorted(space)
    combos = []
    for tau in taus:
        for values in itertools.product(*(space[k] for k in keys)):
            combos.append((replace(base, **dict(zip(keys, values))), float(tau)))
    if max_combos is not None and len(combos) > max_combos:
        rng = random.Random(seed)
        head, rest = combos[:1], combos[1:]
        combos = head + rng.sample(rest, max_combos - 1)
    return combos


def moon_bb_grid(base: Optional[Mapping[str, Tuple[float, float]]] = None,
                 d_values: Sequence[float] = MOON_BB_D_VALUES) -> List[Dict[str, Tuple[float, float]]]:
    """Moon rules with the broadband/OSC D varied (W and the narrowband rules kept)."""
    base = dict(base or MOON_RULES)
    out = []
    for d in d_values:
        rules = dict(base)
        for cls in (CLASS_BB, CLASS_OSC):
            rules[cls] = (float(d), base[cls][1])
        out.append(rules)
    return out


def moon_rule_grid(base: Optional[Mapping[str, Tuple[float, float]]] = None,
                   factors: Sequence[float] = (0.75, 1.0, 1.25)) -> List[Dict[str, Tuple[float, float]]]:
    base = dict(base or MOON_RULES)
    return [{k: (round(d * f, 2), w) for k, (d, w) in base.items()} for f in factors]


def _grid_row(outcomes, weights: Weights, tau: float, rules) -> Dict[str, Any]:
    return {
        "weights": weights.as_dict(),
        "momentum_tau_days": tau,
        "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in rules.items()},
        "metrics": aggregate(outcomes, lambda o: o.ranked, headline),
        "recency": aggregate(outcomes, lambda o: o.baseline("recency"), headline),
    }


def _rank_rows(rows):
    return sorted(rows, key=lambda r: (-r["metrics"]["hit@5"], -r["metrics"]["mrr"]))


def grid_search(nights: Sequence[ReplayNight], inputs_fn: InputsFn, base: Optional[Params] = None,
                combos: Optional[Sequence[Tuple[Weights, float]]] = None,
                moon_rules: Optional[Sequence[Mapping[str, Tuple[float, float]]]] = None,
                top: int = 5, progress: Optional[Callable[[str], None]] = None,
                pair_info: Optional[Mapping[Tuple[str, date], Tuple[str, str]]] = None,
                weights: Optional[Sequence[Weights]] = None,
                warm_keep: Optional[Dict[date, Any]] = None) -> Dict[str, Any]:
    """
    Staged grid on the headline set, hit@5 then MRR. Nothing is applied.
    Stage 1 (only with several Moon rules): each rule set at the base weights;
    the best one is kept. Stage 2: every (weights, tau) combination with it.
    Each row carries the recency baseline on the same pairs.
    `weights` (without tau) is accepted for the old coarse grid. `warm_keep` is
    the `keep` dict of a run_replay with base.moon_rules (reused, not recomputed).
    """
    base = base or Params()
    if combos is None:
        combos = [(w, base.momentum_tau_days) for w in weights] if weights else tuning_grid(base=base.weights)
    rules_list = list(moon_rules or [dict(base.moon_rules)])
    inputs_cache: Dict[date, Tuple[EngineInputs, str, bool]] = {}

    def cached_inputs(nr):
        if nr.night not in inputs_cache:
            inputs_cache[nr.night] = inputs_fn(nr)
        return inputs_cache[nr.night]

    def run(rules, weights, tau, keep):
        p = replace(base, weights=weights, momentum_tau_days=tau, moon_rules=dict(rules))
        return run_replay(nights, cached_inputs, p, keep=keep, pair_info=pair_info, with_details=False)

    moon_stage = []
    keeps: Dict[int, Dict[date, Any]] = {}
    base_key = sorted(base.moon_rules.items())

    def fresh_keep(rules):
        return warm_keep if (warm_keep is not None and sorted(rules.items()) == base_key) else {}

    if len(rules_list) > 1:
        for i, rules in enumerate(rules_list):
            keeps[i] = fresh_keep(rules)
            row = _grid_row(run(rules, base.weights, base.momentum_tau_days, keeps[i]), base.weights,
                            base.momentum_tau_days, rules)
            row["_i"] = i
            moon_stage.append(row)
            if progress:
                progress(f"moon {i + 1}/{len(rules_list)}: BB D={rules[CLASS_BB][0]} "
                         f"hit@5={row['metrics']['hit@5']} mrr={row['metrics']['mrr']}")
        best_i = _rank_rows(moon_stage)[0]["_i"]
        keeps = {best_i: keeps[best_i]}  # free the other rule sets' prepared nights
        for row in moon_stage:
            row.pop("_i", None)
    else:
        best_i = 0
    rules = rules_list[best_i]
    keep = keeps[best_i] if best_i in keeps else fresh_keep(rules)
    rows = []
    for n, (w, tau) in enumerate(combos, start=1):
        rows.append(_grid_row(run(rules, w, tau, keep), w, tau, rules))
        if progress and (n % 10 == 0 or n == len(combos)):
            best = _rank_rows(rows)[0]["metrics"]
            progress(f"grid {n}/{len(combos)}: best so far hit@5={best['hit@5']} mrr={best['mrr']}")
    return {"moon_stage": _rank_rows(moon_stage), "evaluated": len(rows), "top": _rank_rows(rows)[:top]}


def format_table(report: Dict[str, Any]) -> str:
    """A plain-text summary table for the console."""
    head = f"{'':10s}" + "".join(f"{k:>10s}" for k in METRIC_KEYS)
    lines = [f"Replay over {report['nights']} nights, headline = HOME + UNKNOWN_SITE "
             f"(pool coverage {report.get('pool_coverage', 0):.2%})", head]
    rows = [("engine", report["metrics"])] + list(report["baselines"].items())
    for name, m in rows:
        lines.append(f"{name:10s}" + "".join(f"{m[k]:>10.3f}" for k in METRIC_KEYS))
    if report.get("miss_reasons"):
        lines.append("headline misses by reason: " + ", ".join(f"{k} {v}" for k, v in report["miss_reasons"].items()))
    remote = report.get("remote")
    if remote and remote.get("nights"):
        lines.append(f"REMOTE ({remote['nights']} nights): " + ", ".join(
            f"{k} {remote['metrics'][k]:.3f}" for k in METRIC_KEYS))
    if report.get("pair_counts"):
        lines.append(f"pairs: {report['pair_counts']}")
    return "\n".join(lines)


def format_grid(grid: Dict[str, Any]) -> str:
    lines = []
    for row in grid.get("moon_stage", []):
        lines.append(f"  moon BB D={row['moon_rules']['BB']['D']}: hit@5={row['metrics']['hit@5']:.3f} "
                     f"mrr={row['metrics']['mrr']:.3f} (recency hit@5={row['recency']['hit@5']:.3f} "
                     f"mrr={row['recency']['mrr']:.3f})")
    lines.append(f"Top settings of {grid['evaluated']} by hit@5 (MRR tie-break), headline set:")
    for i, row in enumerate(grid["top"], start=1):
        lines.append(f"  {i}. hit@5={row['metrics']['hit@5']:.3f} mrr={row['metrics']['mrr']:.3f} "
                     f"| recency hit@5={row['recency']['hit@5']:.3f} mrr={row['recency']['mrr']:.3f} "
                     f"| tau={row['momentum_tau_days']:g} weights={row['weights']}")
    return "\n".join(lines)
