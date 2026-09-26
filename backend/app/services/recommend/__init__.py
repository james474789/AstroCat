"""
Local recommendation engine (R1, docs/design/R1-recommendation-engine.md §4).

Public entry point:

    recommend(inputs: EngineInputs, params: Params) -> Result

Pure core (no SQLAlchemy): candidates, ephemeris, context, history, scoring,
lanes, replay. `loader.py` builds EngineInputs from the DB and caches them.

The night-level work (ephemeris, target geometry, Moon-clear hours per class,
seasonal usable hours) is done once per (site, night) by `prepare_night` and
shared by every rig and by weight re-scoring (replay --grid).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from app.services.recommend.candidates import Candidate, CandidatePool
from app.services.recommend.context import (
    TIER_NONE, HorizonSpec, NightContext, SiteSpec, build_context,
)
from app.services.recommend.ephemeris import night_sky
from app.services.recommend.history import CLASSES, GoalModel, TargetHistory
from app.services.recommend.lanes import (
    DEFAULT_PER_LANE, Pick, build_lanes, pick_reasons, verdict,
)
from app.services.recommend.scoring import (
    CLASS_INDEX, DEFAULT_MIN_USABLE_H, EXCL_NONE, EXCLUDED_CODES, EXCLUDED_REASONS, MOON_RULES, RigEval, RigSpec,
    Weights, best_rigs, evaluate_rig, future_usable_hours, history_arrays, night_features, urgency_scores,
    useful_mask,
)

RIG_MODE_MOUNTED = "MOUNTED"
RIG_MODE_ALL = "ALL"
RIG_MODE_ALL_FALLBACK = "ALL_FALLBACK"
RIG_MODE_SINGLE = "SINGLE"
CURVE_STRIDE = 3  # 15-minute samples on the 5-minute grid

__all__ = [
    "EngineInputs", "Params", "Result", "PreparedNight", "recommend", "prepare_night", "explain_target",
    "result_to_dict", "pick_to_dict", "Weights", "RigSpec", "SiteSpec", "HorizonSpec", "Candidate",
    "CandidatePool", "MOON_RULES",
]


@dataclass
class EngineInputs:
    candidates: Union[CandidatePool, Sequence[Candidate]]
    history: Mapping[str, TargetHistory]
    site: SiteSpec
    horizon: HorizonSpec
    rigs: Sequence[RigSpec]
    goals: GoalModel
    night: date
    now: Optional[datetime] = None
    skipped_rigs: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def pool(self) -> CandidatePool:
        if not isinstance(self.candidates, CandidatePool):
            self.candidates = CandidatePool(self.candidates)
        return self.candidates


@dataclass
class Params:
    rig_mode: str = RIG_MODE_ALL
    weights: Weights = field(default_factory=Weights)
    moon_rules: Mapping[str, Tuple[float, float]] = field(default_factory=lambda: dict(MOON_RULES))
    min_usable_h: float = DEFAULT_MIN_USABLE_H
    per_lane: int = DEFAULT_PER_LANE
    include_excluded: bool = False
    light: bool = False            # replay: ranked list only (no lanes, reasons or curves)


@dataclass
class PreparedNight:
    ctx: NightContext
    sky: Any
    feats: Any
    future: np.ndarray
    urgency: np.ndarray
    weeks_left: np.ndarray
    moon_rules_key: tuple
    useful: np.ndarray


@dataclass
class Result:
    context: NightContext
    hero: Optional[Pick]
    verdict: Tuple[str, List[Dict[str, str]]]
    lanes: List[Dict[str, Any]]
    excluded_counts: Dict[str, int]
    ranked: List[Pick]
    excluded: Dict[str, str]
    rigs: List[RigSpec]
    skipped_rigs: List[Dict[str, Any]]
    params: Params
    prepared: PreparedNight = field(repr=False, default=None)
    evals: List[RigEval] = field(repr=False, default_factory=list)


def _moon_key(rules: Mapping[str, Tuple[float, float]]) -> tuple:
    return tuple(sorted((k, tuple(v)) for k, v in rules.items()))


def prepare_night(inputs: EngineInputs, params: Params) -> PreparedNight:
    pool = inputs.pool
    site = inputs.site
    sky = night_sky(pool, inputs.night, site.latitude, site.longitude)
    ctx = build_context(sky.eph, site, inputs.horizon)
    feats = night_features(ctx, sky, params.moon_rules)
    if ctx.tier == TIER_NONE or len(pool) == 0:
        future = np.zeros((len(pool), 12), dtype=np.float32)
    else:
        future = future_usable_hours(pool, site.latitude, site.longitude, inputs.night, ctx.horizon, ctx.floor_deg)
    urgency, weeks_left = urgency_scores(feats.usable_h, future, params.min_usable_h)
    return PreparedNight(ctx=ctx, sky=sky, feats=feats, future=future, urgency=urgency, weeks_left=weeks_left,
                         moon_rules_key=_moon_key(params.moon_rules), useful=useful_mask(pool))


def _prepared(inputs: EngineInputs, params: Params, prepared: Optional[PreparedNight]) -> PreparedNight:
    if (prepared is None or prepared.ctx.night != inputs.night or prepared.ctx.site.id != inputs.site.id
            or prepared.moon_rules_key != _moon_key(params.moon_rules)):
        prepared = prepare_night(inputs, params)
    return prepared


def _evaluate(inputs: EngineInputs, params: Params, prep: PreparedNight):
    pool = inputs.pool
    hist = history_arrays(pool, inputs.history, inputs.goals, inputs.night)
    evals = [evaluate_rig(r, pool, prep.feats, hist, prep.urgency, prep.ctx.tier, params.weights, prep.useful,
                          params.min_usable_h) for r in inputs.rigs]
    return hist, evals


def _build_pick(inputs: EngineInputs, prep: PreparedNight, hist, ev: RigEval, i: int,
                alternatives: Optional[List[Dict[str, Any]]] = None) -> Pick:
    pool = inputs.pool
    cand = pool.candidates[i]
    feats = prep.feats
    h = inputs.history.get(cand.key)
    mode_idx = int(ev.mode[i])
    mode = CLASSES[mode_idx] if mode_idx >= 0 else None
    usable = float(feats.usable_h[i])
    # The marginal class on this rig: of the usable classes the Moon cuts below
    # half the usable time, the one it cuts least (e.g. OIII next to a fine Ha).
    limited = None
    if not np.isnan(feats.sep_min[i]):
        marginal = None
        for cls in CLASSES:
            ci = CLASS_INDEX[cls]
            if ev.pair_mask[i, ci] and feats.moon_ok_h[i, ci] < 0.5 * usable:
                if marginal is None or feats.moon_ok_h[i, ci] > marginal[1]:
                    marginal = (cls, feats.moon_ok_h[i, ci])
        if marginal is not None:
            limited = (marginal[0], float(feats.sep_min[i]))
    ratio = ev.ratio[i]
    days = hist.days_since[i]
    has_usable = feats.max_alt[i] > -90
    return Pick(
        candidate=cand, rig_id=ev.rig.id, rig_name=ev.rig.name, mode=mode,
        score=float(ev.score[i]) if not np.isnan(ev.score[i]) else float("nan"),
        components={k: float(v[i]) for k, v in ev.components.items()},
        usable_hours=usable, available_hours=float(ev.avail_h[i]),
        best_time_utc=prep.ctx.eph.times[int(feats.best_idx[i])] if usable > 0 else None,
        max_alt_deg=float(feats.max_alt[i]) if has_usable else None,
        moon_sep_min_deg=None if np.isnan(feats.sep_min[i]) else float(feats.sep_min[i]),
        fill_ratio=None if np.isnan(ratio) else float(ratio),
        have_hours=float(hist.total_h[i]),
        have_by_filter={f: s / 3600.0 for f, s in sorted(h.seconds_by_filter.items())} if h else {},
        nights=int(hist.nights[i]),
        last_imaged=h.last_night if h else None,
        goal_hours=float(hist.goal_h[i]), goal_source=hist.goal_source[i],
        committed=bool(hist.committed[i]),
        days_since=None if np.isnan(days) else int(days),
        weeks_left=float(prep.weeks_left[i]),
        moon_limited=limited,
        alternatives=alternatives or [],
        idx=i,
    )


def recommend(inputs: EngineInputs, params: Optional[Params] = None,
              prepared: Optional[PreparedNight] = None) -> Result:
    params = params or Params()
    prep = _prepared(inputs, params, prepared)
    pool = inputs.pool
    ctx = prep.ctx
    hist, evals = _evaluate(inputs, params, prep)

    counts = {r: 0 for r in EXCLUDED_REASONS}
    excluded: Dict[str, str] = {}
    ranked: List[Pick] = []
    if evals and len(pool):
        best, best_score, furthest = best_rigs(evals)
        feasible = np.flatnonzero(best >= 0)
        order = feasible[np.argsort(-best_score[feasible], kind="stable")]
        for i in order:
            ev = evals[int(best[i])]
            alts = []
            if len(evals) > 1:
                for e in evals:
                    if e is not ev and not np.isnan(e.score[i]):
                        alts.append({"rig_id": e.rig.id, "rig_name": e.rig.name, "score": round(float(e.score[i]), 3)})
                alts.sort(key=lambda a: -a["score"])
            ranked.append(_build_pick(inputs, prep, hist, ev, int(i), alts))
        for i in np.flatnonzero(best < 0):
            reason = EXCLUDED_CODES.get(int(furthest[i]))
            if reason:
                counts[reason] += 1
                excluded[pool.candidates[i].key] = reason

    lanes: List[Dict[str, Any]] = []
    hero = ranked[0] if ranked else None
    if not params.light:
        lanes = build_lanes(ranked, ctx.moon_up_dark_frac, ctx.moon_illum, params.per_lane)
        for lane in lanes:
            for p in lane["items"]:
                p.reasons = pick_reasons(p, ctx.tier)
        if hero is not None and not hero.reasons:
            hero.reasons = pick_reasons(hero, ctx.tier)
    if not inputs.rigs:
        v = ("DONT_BOTHER", [{"code": "NO_RIGS", "text": "No rig with a known pixel scale and field of view"}])
    else:
        v = verdict(hero, ctx.tier)
    return Result(context=ctx, hero=hero, verdict=v, lanes=lanes, excluded_counts=counts, ranked=ranked,
                  excluded=excluded, rigs=list(inputs.rigs), skipped_rigs=list(inputs.skipped_rigs),
                  params=params, prepared=prep, evals=evals)


# ---------------------------------------------------------------------------
# One target, every rig ("why / why not")
# ---------------------------------------------------------------------------

def explain_target(inputs: EngineInputs, params: Params, key: str,
                   prepared: Optional[PreparedNight] = None) -> Optional[Dict[str, Any]]:
    """The §7 per-target shape, or None when the key isn't in the candidate pool."""
    pool = inputs.pool
    i = pool.index.get(key)
    if i is None:
        return None
    prep = _prepared(inputs, params, prepared)
    hist, evals = _evaluate(inputs, params, prep)
    feats = prep.feats
    cand = pool.candidates[i]
    results = []
    for ev in evals:
        code = int(ev.excluded[i])
        pick = None
        if code == EXCL_NONE:
            p = _build_pick(inputs, prep, hist, ev, i)
            p.reasons = pick_reasons(p, prep.ctx.tier)
            pick = pick_to_dict(p, prep)
        mode_idx = int(ev.mode[i])
        details = {
            "tier": prep.ctx.tier,
            "usable_hours": round(float(feats.usable_h[i]), 2),
            "available_hours": round(float(ev.avail_h[i]), 2),
            "mode": CLASSES[mode_idx] if mode_idx >= 0 else None,
            "fill_ratio": None if np.isnan(ev.ratio[i]) else round(float(ev.ratio[i]), 3),
            "target_px": None if np.isnan(ev.px[i]) else round(float(ev.px[i]), 1),
            "max_alt_deg": round(float(feats.max_alt[i]), 1) if feats.max_alt[i] > -90 else None,
            "moon_sep_min_deg": None if np.isnan(feats.sep_min[i]) else round(float(feats.sep_min[i]), 1),
            "moon_clear_hours": {c: round(float(feats.moon_ok_h[i, CLASS_INDEX[c]]), 2)
                                 for c in CLASSES if ev.pair_mask[i, CLASS_INDEX[c]]},
            "required_moon_sep_deg": {c: round(float(v), 1) for c, v in feats.required.items()},
            "rig_classes": sorted(ev.rig.classes),
            "min_usable_hours": params.min_usable_h,
        }
        results.append({"rig_id": ev.rig.id, "rig_name": ev.rig.name, "pick": pick,
                        "excluded_reason": EXCLUDED_CODES.get(code), "details": details})
    return {"target_key": cand.key, "name": cand.name, "night": inputs.night.isoformat(), "results": results}


# ---------------------------------------------------------------------------
# Serialisation (§7 contract)
# ---------------------------------------------------------------------------

def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.replace(tzinfo=None, microsecond=0).isoformat() + "Z"


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    if v is None:
        return None
    try:
        if np.isnan(v):
            return None
    except TypeError:
        pass
    return round(float(v), nd)


def curve_for(prep: PreparedNight, i: int) -> Dict[str, list]:
    eph = prep.ctx.eph
    night_idx = np.flatnonzero(eph.sun_alt < 0.0)
    if night_idx.size:
        steps = np.arange(night_idx[0], night_idx[-1] + 1, CURVE_STRIDE)
    else:
        steps = np.arange(0, len(eph.times), CURVE_STRIDE)
    return {
        "t_utc": [iso_utc(eph.times[s]) for s in steps],
        "alt": [round(float(prep.sky.alt[i, s]), 1) for s in steps],
        "moon_alt": [round(float(eph.moon_alt[s]), 1) for s in steps],
        "limit": [round(float(prep.feats.limit[i, s]), 1) for s in steps],
        "dark": [bool(prep.ctx.dark_mask[s]) for s in steps],
    }


def pick_to_dict(p: Pick, prep: Optional[PreparedNight] = None) -> Dict[str, Any]:
    c = p.candidate
    return {
        "target_key": c.key, "name": c.name, "kind": c.kind,
        "ra_deg": round(c.ra_deg, 4), "dec_deg": round(c.dec_deg, 4), "size_arcmin": c.size_arcmin,
        "rig": {"id": p.rig_id, "name": p.rig_name}, "alternatives": p.alternatives,
        "mode": p.mode, "score": _r(p.score, 3),
        "components": {k: round(v, 3) for k, v in p.components.items()},
        "usable_hours": _r(p.usable_hours, 1), "available_hours": _r(p.available_hours, 1),
        "best_time_utc": iso_utc(p.best_time_utc),
        "max_alt_deg": None if p.max_alt_deg is None else round(p.max_alt_deg),
        "moon_sep_min_deg": None if p.moon_sep_min_deg is None else round(p.moon_sep_min_deg),
        "fill_ratio": _r(p.fill_ratio, 2),
        "have_hours": _r(p.have_hours, 1),
        "have_by_filter": {k: round(v, 2) for k, v in p.have_by_filter.items()},
        "nights": p.nights,
        "last_imaged": p.last_imaged.isoformat() if p.last_imaged else None,
        "goal_hours": _r(p.goal_hours, 1), "goal_source": p.goal_source,
        "reasons": p.reasons,
        "curve": curve_for(prep, p.idx) if prep is not None and p.idx >= 0 else None,
        # Additive (not in the §7 example): lane id, and the candidate's catalog/magnitude.
        "lane": p.lane, "catalog": c.catalog, "magnitude": c.magnitude,
    }


def result_to_dict(result: Result, generated_at: Optional[datetime] = None, cached: bool = False) -> Dict[str, Any]:
    ctx = result.context
    prep = result.prepared
    level, reasons = result.verdict
    return {
        "generated_at": iso_utc(generated_at or datetime.utcnow()),
        "cached": cached,
        "context": {
            "night": ctx.night.isoformat(),
            "site": {"id": ctx.site.id, "name": ctx.site.name, "timezone": ctx.site.timezone},
            "tier": ctx.tier, "tier_note": ctx.tier_note,
            "dark_start_utc": iso_utc(ctx.dark_start_utc), "dark_end_utc": iso_utc(ctx.dark_end_utc),
            "moon": {"illumination": round(ctx.moon_illum, 3), "age_days": round(ctx.moon_age_days, 1),
                     "up_fraction": round(ctx.moon_up_dark_frac, 2)},
            "horizon_source": ctx.horizon_source, "floor_deg": round(ctx.floor_deg, 1),
            "rig_mode": result.params.rig_mode,
            "rigs": [{"id": r.id, "name": r.name, "classes": [c for c in CLASSES if c in r.classes]}
                     for r in result.rigs],
            "weights": result.params.weights.as_dict(),
            # Additive: tier thresholds, dark hours and the Moon rules in force.
            "tier_thresholds": {"ASTRO": -18.0, "NAUTICAL": -12.0, "BRIGHT": -9.0},
            "dark_hours": round(ctx.dark_hours, 2),
            "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in result.params.moon_rules.items()},
        },
        "hero": pick_to_dict(result.hero, prep) if result.hero is not None else None,
        "verdict": {"level": level, "reasons": reasons},
        "lanes": [{"id": lane["id"], "title": lane["title"], "items": [pick_to_dict(p, prep) for p in lane["items"]]}
                  for lane in result.lanes],
        "excluded_counts": dict(result.excluded_counts),
        "skipped_rigs": list(result.skipped_rigs),
    }
