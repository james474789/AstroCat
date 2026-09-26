"""
Replay test (R1 spec §5). Pure: no DB access; `scripts/replay_recommendations.py`
loads the data and writes the report.

For each historical imaging night N (>= 30 min of resolved light subs):
- history = build_history(rows, as_of=N): strictly earlier nights only;
  target_goals created on/after N are ignored;
- site = the majority site of N's subs, else the default site;
- rig = the majority rig of N's subs, else the rigs used within N +/- 365 days,
  else every active rig;
- actual = the target keys with >= 30 min on N;
- run recommend(...) and keep `ranked` (every lane counts: R1 has no novelty
  or revisit lanes).

Metrics: hit@k (k = 1, 3, 5, 10), MRR and feasible_recall (the share of actual
in-pool (night, key) pairs that pass hard feasibility; every miss is listed
with its reason), with breakdowns by tier, Moon illumination, year and whether
the rig was known. Baselines over the same feasible set: recency, altitude
(usable hours) and random (seeded, averaged over 20 runs).
"""

import bisect
import itertools
import random
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.recommend import (
    RIG_MODE_ALL, RIG_MODE_SINGLE, EngineInputs, Params, PreparedNight, Result, prepare_night, recommend,
)
from app.services.recommend.history import HistoryRow, build_history, infer_goals
from app.services.recommend.scoring import MOON_RULES, Weights

MIN_NIGHT_SECONDS = 1800.0
MIN_TARGET_SECONDS = 1800.0
KS = (1, 3, 5, 10)
RIG_WINDOW_DAYS = 365
RANDOM_RUNS = 20
MAJORITY_SHARE = 0.5
METRIC_KEYS = tuple(f"hit@{k}" for k in KS) + ("mrr", "feasible_recall")
GRID_FACTORS = (0.5, 1.0, 2.0)
GRID_KEYS = ("observability", "project", "momentum", "urgency")
MOON_GRID_FACTORS = (0.75, 1.0, 1.25)
NOT_IN_POOL = "NOT_IN_POOL"


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
# Inputs "as of" a night
# ---------------------------------------------------------------------------

@dataclass
class ReplayData:
    """Everything the replay needs, loaded once (no DB access here)."""
    pool: Any                                   # CandidatePool
    rows: List[HistoryRow]                      # sorted by night (history.sort_rows)
    masters: Dict[str, date]
    goal_rows: list
    sites: Dict[int, Any]                       # id -> SiteSpec
    default_site_id: int
    horizons: Dict[int, Any]                    # site id -> HorizonSpec
    rig_specs: Dict[int, Any]                   # rig id -> RigSpec (rigs with scale + FOV)
    active_rig_ids: List[int]
    rig_index: Dict[int, List[date]] = field(default_factory=dict)

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


def outcome_from_result(nr: ReplayNight, inputs: EngineInputs, result: Result, rig_known: bool) -> NightOutcome:
    pool = inputs.pool
    ranked = [p.key for p in result.ranked]
    feasible = set(ranked)
    in_pool = frozenset(k for k in nr.actual if k in pool.index)
    misses = []
    for key in sorted(nr.actual):
        if key in feasible:
            continue
        reason = NOT_IN_POOL if key not in pool.index else result.excluded.get(key, "UNKNOWN")
        misses.append({"night": nr.night.isoformat(), "target_key": key, "reason": reason})
    return NightOutcome(
        night=nr.night, tier=result.context.tier, moon_illum=result.context.moon_illum, rig_known=rig_known,
        actual=nr.actual, in_pool=in_pool, ranked=ranked,
        last_night={p.key: p.last_imaged for p in result.ranked},
        usable_h={p.key: p.usable_hours for p in result.ranked},
        misses=misses,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def rank_metrics(ranked: Sequence[str], actual: Iterable[str]) -> Dict[str, float]:
    actual = set(actual)
    best = next((i for i, k in enumerate(ranked) if k in actual), None)
    out = {f"hit@{k}": 1.0 if best is not None and best < k else 0.0 for k in KS}
    out["mrr"] = 1.0 / (best + 1) if best is not None else 0.0
    return out


def aggregate(outcomes: Sequence[NightOutcome], order: Callable[[NightOutcome], List[str]]) -> Dict[str, float]:
    if not outcomes:
        return {**{k: 0.0 for k in METRIC_KEYS}, "nights": 0}
    sums = defaultdict(float)
    pairs = hits = 0
    for o in outcomes:
        for k, v in rank_metrics(order(o), o.actual).items():
            sums[k] += v
        pairs += len(o.in_pool)
        hits += len(o.in_pool & set(o.ranked))
    n = len(outcomes)
    out = {k: round(sums[k] / n, 4) for k in METRIC_KEYS if k != "feasible_recall"}
    out["feasible_recall"] = round(hits / pairs, 4) if pairs else 0.0
    out["nights"] = n
    return out


def random_metrics(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS) -> Dict[str, float]:
    if not outcomes:
        return aggregate(outcomes, lambda o: o.ranked)
    acc = defaultdict(float)
    for run in range(runs):
        rng = random.Random(seed * 1000 + run)
        m = aggregate(outcomes, lambda o, rng=rng: o.baseline("random", rng))
        for k in METRIC_KEYS:
            acc[k] += m[k]
    out = {k: round(acc[k] / runs, 4) for k in METRIC_KEYS}
    out["nights"] = len(outcomes)
    return out


def all_metrics(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS) -> Dict[str, Dict[str, float]]:
    return {
        "engine": aggregate(outcomes, lambda o: o.ranked),
        "recency": aggregate(outcomes, lambda o: o.baseline("recency")),
        "altitude": aggregate(outcomes, lambda o: o.baseline("altitude")),
        "random": random_metrics(outcomes, seed, runs),
    }


def moon_bucket(illum: float) -> str:
    if illum < 0.25:
        return "<0.25"
    if illum <= 0.75:
        return "0.25-0.75"
    return ">0.75"


def breakdown(outcomes: Sequence[NightOutcome], seed: int = 0, runs: int = RANDOM_RUNS) -> Dict[str, Any]:
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
        out[dim] = {g: all_metrics(v, seed, runs) for g, v in sorted(groups.items())}
    return out


def miss_summary(outcomes: Sequence[NightOutcome]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for o in outcomes:
        for m in o.misses:
            counts[m["reason"]] += 1
    return dict(sorted(counts.items()))


def params_dict(params: Params) -> Dict[str, Any]:
    return {
        "weights": params.weights.as_dict(),
        "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in params.moon_rules.items()},
        "min_usable_h": params.min_usable_h,
        "min_night_seconds": MIN_NIGHT_SECONDS,
        "min_target_seconds": MIN_TARGET_SECONDS,
    }


def build_report(outcomes: Sequence[NightOutcome], params: Params, generated_at: Optional[datetime] = None,
                 seed: int = 0, runs: int = RANDOM_RUNS, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = all_metrics(outcomes, seed, runs)
    pairs = sum(len(o.actual) for o in outcomes)
    in_pool = sum(len(o.in_pool) for o in outcomes)
    report = {
        "generated_at": (generated_at or datetime.utcnow()).replace(microsecond=0).isoformat() + "Z",
        "nights": len(outcomes),
        "params": params_dict(params),
        "metrics": {k: m["engine"][k] for k in METRIC_KEYS},
        "baselines": {b: {k: m[b][k] for k in METRIC_KEYS} for b in ("recency", "altitude", "random")},
        "breakdown": breakdown(outcomes, seed, runs),
        "misses": [x for o in outcomes for x in o.misses],
        # Additive fields.
        "actual_pairs": pairs,
        "pool_coverage": round(in_pool / pairs, 4) if pairs else 0.0,
        "miss_reasons": miss_summary(outcomes),
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
               keep: Optional[Dict[date, Tuple[EngineInputs, str, bool, PreparedNight]]] = None) -> List[NightOutcome]:
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
        outcomes.append(outcome_from_result(nr, inputs, result, known))
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


def moon_rule_grid(base: Optional[Mapping[str, Tuple[float, float]]] = None,
                   factors: Sequence[float] = MOON_GRID_FACTORS) -> List[Dict[str, Tuple[float, float]]]:
    base = dict(base or MOON_RULES)
    return [{k: (round(d * f, 2), w) for k, (d, w) in base.items()} for f in factors]


def grid_search(nights: Sequence[ReplayNight], inputs_fn: InputsFn, base: Optional[Params] = None,
                weights: Optional[Sequence[Weights]] = None,
                moon_rules: Optional[Sequence[Mapping[str, Tuple[float, float]]]] = None,
                top: int = 5, progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    """
    Coarse grid over Weights (and optionally Moon-rule D values). Returns the
    top settings by hit@5, MRR as the tie-break. Nothing is applied.
    """
    base = base or Params()
    weights = list(weights or weight_grid(base.weights))
    moon_rules = list(moon_rules or [dict(base.moon_rules)])
    rows = []
    inputs_cache: Dict[date, Tuple[EngineInputs, str, bool]] = {}

    def cached_inputs(nr):
        if nr.night not in inputs_cache:
            inputs_cache[nr.night] = inputs_fn(nr)
        return inputs_cache[nr.night]

    for rules in moon_rules:
        keep: Dict[date, Any] = {}
        for i, w in enumerate(weights, start=1):
            p = replace(base, weights=w, moon_rules=dict(rules))
            outcomes = run_replay(nights, cached_inputs, p, keep=keep)
            m = aggregate(outcomes, lambda o: o.ranked)
            rows.append({"weights": w.as_dict(), "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in rules.items()},
                         "metrics": m})
            if progress:
                progress(f"grid {len(rows)}/{len(weights) * len(moon_rules)}: hit@5={m['hit@5']} mrr={m['mrr']}")
    rows.sort(key=lambda r: (-r["metrics"]["hit@5"], -r["metrics"]["mrr"]))
    return rows[:top]


def format_table(report: Dict[str, Any]) -> str:
    """A plain-text summary table for the console."""
    head = f"{'':10s}" + "".join(f"{k:>10s}" for k in METRIC_KEYS)
    lines = [f"Replay over {report['nights']} nights (pool coverage {report.get('pool_coverage', 0):.2%})", head]
    rows = [("engine", report["metrics"])] + list(report["baselines"].items())
    for name, m in rows:
        lines.append(f"{name:10s}" + "".join(f"{m[k]:>10.3f}" for k in METRIC_KEYS))
    if report.get("miss_reasons"):
        lines.append("misses by reason: " + ", ".join(f"{k} {v}" for k, v in report["miss_reasons"].items()))
    return "\n".join(lines)
