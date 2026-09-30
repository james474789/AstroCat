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

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

import numpy as np

from app.services.recommend.candidates import WF_CATALOG, Candidate, CandidatePool
from app.services.recommend.context import (
    TIER_NONE, HorizonSpec, NightContext, SiteSpec, build_context,
)
from app.services.recommend.ephemeris import night_sky
from app.services.recommend.history import CLASSES, GoalModel, TargetHistory
from app.services.recommend.lanes import (
    DEFAULT_PER_LANE, MARGINAL_MIN_HOURS, Pick, build_lanes, pick_reasons, verdict,
)
from app.services.recommend.scoring import (
    BRIGHT_BROADBAND_FACTOR, CLASS_INDEX, DEFAULT_MIN_USABLE_H, EXCL_NONE, EXCLUDED_CODES, EXCLUDED_REASONS,
    MOMENTUM_TAU_DAYS, MOON_HARD_FRACTION, MOON_RULES, RigEval, RigSpec, Weights, best_rigs, evaluate_rig,
    future_usable_hours, history_arrays, night_features, urgency_scores, useful_mask,
)

RIG_MODE_MOUNTED = "MOUNTED"
RIG_MODE_ALL = "ALL"
RIG_MODE_ALL_FALLBACK = "ALL_FALLBACK"
RIG_MODE_SINGLE = "SINGLE"
CURVE_STRIDE = 3  # 15-minute samples on the 5-minute grid

__all__ = [
    "EngineInputs", "Params", "Result", "PreparedNight", "recommend", "prepare_night", "explain_target",
    "result_to_dict", "result_payload", "render_payload", "assign_rig_plan", "pick_to_dict", "Weights", "RigSpec", "SiteSpec", "HorizonSpec", "Candidate",
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
    min_usable_h: float = DEFAULT_MIN_USABLE_H          # hard filter, hours above the relaxed limit
    per_lane: int = DEFAULT_PER_LANE
    momentum_tau_days: float = MOMENTUM_TAU_DAYS
    moon_hard_fraction: float = MOON_HARD_FRACTION
    bright_broadband_factor: float = BRIGHT_BROADBAND_FACTOR
    include_excluded: bool = False
    light: bool = False            # replay: ranked LightPicks only (no lanes, reasons, curves or verdict)


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


class LightPick(NamedTuple):
    """A ranked pick in replay (light) mode."""
    key: str
    score: float
    usable_hours: float
    last_imaged: Optional[date]
    rig_index: int
    idx: int


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
    excluded_names: Dict[str, str] = field(repr=False, default_factory=dict)   # R2a: names for `excluded` keys
    rig_summary: List[Dict[str, Any]] = field(default_factory=list)            # R1b: per-rig feasibility counts


def _moon_key(rules: Mapping[str, Tuple[float, float]], hard_fraction: float = MOON_HARD_FRACTION) -> tuple:
    return tuple(sorted((k, tuple(v)) for k, v in rules.items())) + (("hard", hard_fraction),)


def prepare_night(inputs: EngineInputs, params: Params) -> PreparedNight:
    pool = inputs.pool
    site = inputs.site
    sky = night_sky(pool, inputs.night, site.latitude, site.longitude)
    ctx = build_context(sky.eph, site, inputs.horizon)
    feats = night_features(ctx, sky, params.moon_rules, params.moon_hard_fraction)
    if ctx.tier == TIER_NONE or len(pool) == 0:
        future = np.zeros((len(pool), 12), dtype=np.float32)
    else:
        future = future_usable_hours(pool, site.latitude, site.longitude, inputs.night, ctx.horizon, ctx.floor_deg)
    urgency, weeks_left = urgency_scores(feats.usable_h, future)
    return PreparedNight(ctx=ctx, sky=sky, feats=feats, future=future, urgency=urgency, weeks_left=weeks_left,
                         moon_rules_key=_moon_key(params.moon_rules, params.moon_hard_fraction),
                         useful=useful_mask(pool))


def _prepared(inputs: EngineInputs, params: Params, prepared: Optional[PreparedNight]) -> PreparedNight:
    if (prepared is None or prepared.ctx.night != inputs.night or prepared.ctx.site.id != inputs.site.id
            or prepared.moon_rules_key != _moon_key(params.moon_rules, params.moon_hard_fraction)):
        prepared = prepare_night(inputs, params)
    return prepared


def _evaluate(inputs: EngineInputs, params: Params, prep: PreparedNight):
    pool = inputs.pool
    hist = history_arrays(pool, inputs.history, inputs.goals, inputs.night, params.momentum_tau_days)
    evals = [evaluate_rig(r, pool, prep.feats, hist, prep.urgency, prep.ctx.tier, params.weights, prep.useful,
                          params.min_usable_h, params.bright_broadband_factor) for r in inputs.rigs]
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
        if params.light:
            # Replay fast path: only what the metrics and baselines need.
            usable = prep.feats.usable_h
            hist_get = inputs.history.get
            for i in order:
                key = pool.candidates[i].key
                h = hist_get(key)
                ranked.append(LightPick(key, float(best_score[i]), float(usable[i]),
                                        h.last_night if h else None, int(best[i]), int(i)))
            order = []
        for i in order:
            ev = evals[int(best[i])]
            alts = []
            if len(evals) > 1:
                for e in evals:
                    if e is not ev and not np.isnan(e.score[i]):
                        alt_mode = int(e.mode[i])
                        # mode / fill_ratio / available_hours are additive: the rig plan
                        # re-labels a pick for the rig it is assigned to.
                        alts.append({"rig_id": e.rig.id, "rig_name": e.rig.name, "score": round(float(e.score[i]), 3),
                                     "mode": CLASSES[alt_mode] if alt_mode >= 0 else None,
                                     "fill_ratio": None if np.isnan(e.ratio[i]) else round(float(e.ratio[i]), 2),
                                     "available_hours": round(float(e.avail_h[i]), 1)})
                alts.sort(key=lambda a: -a["score"])
            ranked.append(_build_pick(inputs, prep, hist, ev, int(i), alts))
        for i in np.flatnonzero(best < 0):
            reason = EXCLUDED_CODES.get(int(furthest[i]))
            if reason:
                counts[reason] += 1
                excluded[pool.candidates[i].key] = reason
    excluded_names = {} if params.light else {k: pool.get(k).name for k in excluded}
    rig_summary = [] if params.light else [_rig_summary(ev) for ev in evals]

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
    elif params.light:
        v = ("", [])
    else:
        v = verdict(hero, ctx.tier)
    return Result(context=ctx, hero=hero, verdict=v, lanes=lanes, excluded_counts=counts, ranked=ranked,
                  excluded=excluded, rigs=list(inputs.rigs), skipped_rigs=list(inputs.skipped_rigs),
                  params=params, prepared=prep, evals=evals, excluded_names=excluded_names,
                  rig_summary=rig_summary)


def _rig_summary(ev: RigEval) -> Dict[str, Any]:
    """
    What one rig can do tonight (R1b): exclusion counts over the whole pool, and over the targets
    inside its size window (where the horizon / Moon / tier codes are the real story), plus how
    many feasible targets fall short of the rig-plan quality bar.
    """
    def counts(mask) -> Dict[str, int]:
        return {name: int(((ev.excluded == code) & mask).sum()) for code, name in EXCLUDED_CODES.items()}

    everything = np.ones(len(ev.excluded), dtype=bool)
    ok = ev.excluded == EXCL_NONE
    in_window = ev.size_ok if ev.size_ok is not None else everything
    return {
        "rig_id": ev.rig.id, "feasible": int(ok.sum()), "in_window": int(in_window.sum()),
        "excluded": counts(everything), "in_window_excluded": counts(in_window),
        "short": int((ok & (ev.avail_h < MARGINAL_MIN_HOURS)).sum()),
        "best_hours": round(float(ev.avail_h[ok].max()), 1) if ok.any() else 0.0,
    }


# ---------------------------------------------------------------------------
# One target, every rig ("why / why not")
# ---------------------------------------------------------------------------

def pair_details(prep: PreparedNight, ev: RigEval, i: int, params: Params) -> Dict[str, Any]:
    """Numbers behind one (target, rig) verdict (per-target endpoint and replay misses)."""
    feats = prep.feats
    mode_idx = int(ev.mode[i])
    mode = CLASSES[mode_idx] if mode_idx >= 0 else None

    def r(v, nd=2):
        return None if v is None or np.isnan(v) else round(float(v), nd)

    return {
        "tier": prep.ctx.tier,
        "rig_id": ev.rig.id,
        "rig_name": ev.rig.name,
        "usable_hours": round(float(feats.usable_h[i]), 2),
        "usable_hours_hard": r(feats.usable_hard_h[i]) if feats.usable_hard_h is not None else None,
        "available_hours": round(float(ev.avail_h[i]), 2),
        "available_hours_hard": r(ev.avail_hard_h[i]) if ev.avail_hard_h is not None else None,
        "mode": mode,
        "fill_ratio": r(ev.ratio[i], 3),
        "target_px": r(ev.px[i], 1),
        "max_alt_deg": round(float(feats.max_alt[i]), 1) if feats.max_alt[i] > -90 else None,
        "moon_sep_min_deg": r(feats.sep_min[i], 1),
        "required_sep_deg": round(float(feats.required[mode]), 1) if mode else None,
        "moon_clear_hours": {c: round(float(feats.moon_ok_h[i, CLASS_INDEX[c]]), 2)
                             for c in CLASSES if ev.pair_mask[i, CLASS_INDEX[c]]},
        "required_moon_sep_deg": {c: round(float(v), 1) for c, v in feats.required.items()},
        "rig_classes": sorted(ev.rig.classes),
        "min_usable_hours": params.min_usable_h,
        "size_window_arcmin": [round(v, 1) for v in ev.rig.size_window()],
        "imaged_on_rig": bool(ev.imaged[i]) if ev.imaged is not None else False,
    }


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
        details = pair_details(prep, ev, i, params)
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


def _curve_steps(prep: PreparedNight) -> np.ndarray:
    eph = prep.ctx.eph
    night_idx = np.flatnonzero(eph.sun_alt < 0.0)
    if night_idx.size:
        return np.arange(night_idx[0], night_idx[-1] + 1, CURVE_STRIDE)
    return np.arange(0, len(eph.times), CURVE_STRIDE)


def _curve_common(prep: PreparedNight) -> Dict[str, list]:
    """The parts of a pick curve every target shares (times, Moon, darkness)."""
    eph = prep.ctx.eph
    steps = _curve_steps(prep)
    return {
        "t_utc": [iso_utc(eph.times[s]) for s in steps],
        "moon_alt": [round(float(eph.moon_alt[s]), 1) for s in steps],
        "dark": [bool(prep.ctx.dark_mask[s]) for s in steps],
    }


def _curve_own(prep: PreparedNight, i: int, steps: Optional[np.ndarray] = None) -> Dict[str, list]:
    """The per-target parts of a pick curve (altitude and horizon limit)."""
    steps = _curve_steps(prep) if steps is None else steps
    return {
        "alt": [round(float(prep.sky.alt[i, s]), 1) for s in steps],
        "limit": [round(float(prep.feats.limit[i, s]), 1) for s in steps],
    }


def _merge_curve(common: Mapping[str, list], own: Mapping[str, list]) -> Dict[str, list]:
    return {"t_utc": common["t_utc"], "alt": own["alt"], "moon_alt": common["moon_alt"], "limit": own["limit"],
            "dark": common["dark"]}


def curve_for(prep: PreparedNight, i: int) -> Dict[str, list]:
    return _merge_curve(_curve_common(prep), _curve_own(prep, i))


def pick_to_dict(p: Pick, prep: Optional[PreparedNight] = None) -> Dict[str, Any]:
    c = p.candidate
    d = {
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
    if c.catalog == WF_CATALOG:
        d["members"] = list(c.members)       # a WF region has no images of its own: the UI links its members
    return d


def _context_dict(result: Result) -> Dict[str, Any]:
    ctx = result.context
    return {
        "night": ctx.night.isoformat(),
        "site": {"id": ctx.site.id, "name": ctx.site.name, "timezone": ctx.site.timezone},
        "tier": ctx.tier, "tier_note": ctx.tier_note,
        "dark_start_utc": iso_utc(ctx.dark_start_utc), "dark_end_utc": iso_utc(ctx.dark_end_utc),
        "moon": {"illumination": round(ctx.moon_illum, 3), "age_days": round(ctx.moon_age_days, 1),
                 "up_fraction": round(ctx.moon_up_dark_frac, 2)},
        "horizon_source": ctx.horizon_source, "floor_deg": round(ctx.floor_deg, 1),
        "rig_mode": result.params.rig_mode,
        "rigs": [{"id": r.id, "name": r.name, "classes": [c for c in CLASSES if c in r.classes],
                  "size_window_arcmin": [round(v, 1) for v in r.size_window()]}
                 for r in result.rigs],
        "weights": result.params.weights.as_dict(),
        # Additive: tier thresholds, dark hours and the Moon rules in force.
        "tier_thresholds": {"ASTRO": -18.0, "NAUTICAL": -12.0, "BRIGHT": -9.0},
        "dark_hours": round(ctx.dark_hours, 2),
        "moon_rules": {k: {"D": v[0], "W": v[1]} for k, v in result.params.moon_rules.items()},
    }


# ---------------------------------------------------------------------------
# Cacheable engine payload (R2a §4) and rendering with per-user feedback
# ---------------------------------------------------------------------------
#
# The Redis result cache holds a user-independent *payload*: every feasible pick
# (not just the lane-sized lists), the exclusions, and the exact values the lane
# rebuild needs. Each request renders it with that user's FeedbackState, so
# feedback never invalidates or fragments the cache.

PAYLOAD_FORMAT = 4     # 4: rig_summary, per-rig size window (R1b); 3: richer alternatives for the rig plan


def result_payload(result: Result, generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    """The user-independent, JSON-serialisable form of a Result (see render_payload)."""
    ctx = result.context
    prep = result.prepared
    has_curves = prep is not None and prep.sky is not None and prep.feats.limit is not None
    steps = _curve_steps(prep) if has_curves else None
    ranked = []
    for p in result.ranked:
        if not p.reasons:
            p.reasons = pick_reasons(p, ctx.tier)
        d = pick_to_dict(p, None)
        d["curve"] = _curve_own(prep, p.idx, steps) if has_curves and p.idx >= 0 else None
        # Exact values for the rebuild (the display values are rounded).
        d["_raw"] = {"score": float(p.score), "available_hours": float(p.available_hours), "lane": p.lane}
        ranked.append(d)
    return {
        "format": PAYLOAD_FORMAT,
        "generated_at": iso_utc(generated_at or datetime.utcnow()),
        "context": _context_dict(result),
        "moon_up_dark_frac": float(ctx.moon_up_dark_frac),
        "moon_illum": float(ctx.moon_illum),
        "no_rigs": not result.rigs,
        "verdict": {"level": result.verdict[0], "reasons": result.verdict[1]},
        "excluded_counts": dict(result.excluded_counts),
        "skipped_rigs": list(result.skipped_rigs),
        "rig_summary": list(result.rig_summary),
        "curve_common": _curve_common(prep) if has_curves else None,
        "ranked": ranked,
        "excluded": {k: [v, result.excluded_names.get(k, k)] for k, v in result.excluded.items()},
    }


@dataclass
class CachedPick:
    """A payload pick, duck-typed for apply_feedback / build_lanes / verdict."""
    candidate: Candidate
    score: float
    available_hours: float
    mode: Optional[str]
    lane: Optional[str]
    data: Dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def key(self) -> str:
        return self.candidate.key

    @property
    def name(self) -> str:
        return self.candidate.name


def _cached_pick(d: Mapping[str, Any]) -> CachedPick:
    raw = d.get("_raw") or {}
    cand = Candidate(key=d["target_key"], name=d["name"], ra_deg=d["ra_deg"], dec_deg=d["dec_deg"],
                     size_arcmin=d.get("size_arcmin"), kind=d.get("kind"), catalog=d.get("catalog"),
                     magnitude=d.get("magnitude"), aliases=frozenset())
    return CachedPick(candidate=cand, score=float(raw.get("score", d.get("score") or 0.0)),
                      available_hours=float(raw.get("available_hours", d.get("available_hours") or 0.0)),
                      mode=d.get("mode"), lane=raw.get("lane", d.get("lane")), data=d)


RIG_PLAN_PER_RIG = 3   # a primary target + backups for each concurrently mounted rig


def _pair_hours(p: Any, alt: Optional[Mapping[str, Any]]) -> float:
    """Moon-clear hours of a (pick, alt) pair on the rig it would be planned on."""
    h = alt.get("available_hours") if alt is not None else getattr(p, "available_hours", None)
    return float(h) if h is not None else 0.0


def assign_rig_plan(picks: Sequence[Any], rig_ids: Sequence[int], pinned: frozenset = frozenset(),
                    per_rig: int = RIG_PLAN_PER_RIG,
                    min_hours: float = MARGINAL_MIN_HOURS) -> Dict[int, List[Tuple[Any, Optional[Dict[str, Any]]]]]:
    """
    Distinct targets for rigs imaging at the same time: {rig_id: [(pick, alt)]}.

    A pair with fewer than `min_hours` Moon-clear hours on its rig is not eligible (R1b): a rig
    with no eligible pair gets nothing rather than the least-bad option.

    Every (target, rig) pair a pick offers (its best rig, alt None, plus its
    `alternatives`) competes by that rig's score, pinned targets first. Rounds
    hand each rig one target per round, so every rig gets a primary before any
    gets a backup, and no target is planned on two rigs.
    """
    plan: Dict[int, List[Tuple[Any, Optional[Dict[str, Any]]]]] = {rid: [] for rid in rig_ids}
    pairs = []
    for p in picks:
        pin = p.key in pinned
        if _pair_hours(p, None) >= min_hours:
            pairs.append((pin, float(p.score), p, None))
        for a in p.data.get("alternatives") or []:
            if a.get("rig_id") in plan and a.get("score") is not None and _pair_hours(p, a) >= min_hours:
                pairs.append((pin, float(a["score"]), p, a))
    pairs.sort(key=lambda t: (not t[0], -t[1]))
    used = set()
    for rnd in range(per_rig):
        for pin, score, p, alt in pairs:
            rid = alt["rig_id"] if alt is not None else p.data["rig"]["id"]
            if p.key in used or rid not in plan or len(plan[rid]) != rnd:
                continue
            plan[rid].append((p, alt))
            used.add(p.key)
    return plan


def _as_rig_pick(d: Dict[str, Any], alt: Mapping[str, Any]) -> Dict[str, Any]:
    """A rendered pick re-labelled for an alternative rig (its best rig becomes an alternative)."""
    best = {"rig_id": d["rig"]["id"], "rig_name": d["rig"]["name"], "score": d["score"], "mode": d.get("mode"),
            "fill_ratio": d.get("fill_ratio"), "available_hours": d.get("available_hours")}
    d = dict(d)
    d["rig"] = {"id": alt["rig_id"], "name": alt["rig_name"]}
    d["score"] = alt["score"]
    d["mode"] = alt.get("mode") or d.get("mode")
    d["fill_ratio"] = alt.get("fill_ratio")
    if alt.get("available_hours") is not None:
        d["available_hours"] = alt["available_hours"]
    others = [a for a in d.get("alternatives") or [] if a.get("rig_id") != alt["rig_id"]]
    d["alternatives"] = sorted([best] + others, key=lambda a: -(a.get("score") or 0.0))
    fill = d["fill_ratio"]
    framing = (f"fills {fill * 100:.0f}% of {alt['rig_name']}" if fill is not None
               else f"size unknown on {alt['rig_name']}")
    d["reasons"] = [{"code": "FRAMING", "text": framing} if r.get("code") == "FRAMING" else r
                    for r in d.get("reasons") or []]
    return d


def fmt_arcmin(v: float) -> str:
    """'14′' under a degree, else '3°' / '16.5°'."""
    if v < 60.0:
        return f"{v:.0f}′"
    deg = f"{v / 60.0:.1f}".rstrip("0").rstrip(".")
    return f"{deg}°"


def _offered_pairs(picks: Sequence[Any], rig_id: int, pinned: frozenset = frozenset()) -> List[float]:
    """Moon-clear hours of every feasible (target, rig) pair the picks offer on this rig."""
    hours = []
    for p in picks:
        if p.data["rig"]["id"] == rig_id:
            hours.append(_pair_hours(p, None))
        for a in p.data.get("alternatives") or []:
            if a.get("rig_id") == rig_id:
                hours.append(_pair_hours(p, a))
    return hours


def _moon_phrase(ctx: Mapping[str, Any], classes: Sequence[str], illum: float, up_frac: float) -> Optional[str]:
    """'Moon 78% lit and up all night: broadband/OSC needs 120°', or None when the Moon isn't the story."""
    if illum < 0.4 or up_frac < 0.3:
        return None
    rules = ctx.get("moon_rules") or {}
    broad = [c for c in classes if c in ("BB", "OSC")]
    if broad and len(broad) == len(classes):
        label, dist = "broadband/OSC", rules.get(broad[0], {}).get("D")
    else:
        best = min((c for c in classes if c in rules), key=lambda c: rules[c]["D"], default=None)
        label, dist = (best, rules[best]["D"]) if best else ("the rig's filters", None)
    up = "up all night" if up_frac >= 0.9 else f"up {up_frac * 100:.0f}% of the night"
    need = f" needs {dist:.0f}°" if dist else " is limited"
    return f"Moon {illum * 100:.0f}% lit and {up}: {label}{need}"


def rig_note(rig: Mapping[str, Any], window: Optional[Sequence[float]], offered_hours: Sequence[float],
             summary: Optional[Mapping[str, Any]], payload: Mapping[str, Any], ctx: Mapping[str, Any]) -> str:
    """Why a mounted rig has no target tonight (R1b): names the main reason in one line."""
    bar = f"{MARGINAL_MIN_HOURS:g} h"
    span = f"{fmt_arcmin(window[0])}–{fmt_arcmin(window[1])}" if window else "its size window"
    moon = _moon_phrase(ctx, rig.get("classes") or [], float(payload.get("moon_illum") or 0.0),
                        float(payload.get("moon_up_dark_frac") or 0.0))
    if any(h >= MARGINAL_MIN_HOURS for h in offered_hours):
        return "Every target that suits this rig is already planned on another rig"
    if offered_hours:
        return f"{moon}; nothing clears it for {bar}" if moon else f"Nothing between {span} has {bar} of usable sky tonight"
    if ctx.get("tier") == "NONE":
        return "Too bright tonight: the Sun never gets below -9°"
    inside = (summary or {}).get("in_window_excluded") or {}
    if summary is not None and not summary.get("in_window"):
        return f"No target between {span} is in the candidate list"
    main = max(inside, key=lambda k: inside[k], default=None) if any(inside.values()) else None
    if main == "MOON" and moon:
        return f"{moon}; nothing between {span} clears it for {bar}"
    if main == "TIER":
        return f"Too bright tonight for this rig's filters between {span}"
    return f"Nothing between {span} is well placed tonight"


def render_payload(payload: Mapping[str, Any], feedback=None, per_lane: int = DEFAULT_PER_LANE,
                   cached: bool = False) -> Dict[str, Any]:
    """
    The §7 body (+ R2a additions) for one user: apply feedback, then rebuild
    the lanes, hero and verdict with the R1 rules. An empty FeedbackState
    reproduces the R1 body. `payload` is not modified.
    """
    from app.services.recommend.feedback import (
        EXCL_DISMISSED, EXCL_SNOOZED, FeedbackState, apply_feedback, assemble_lanes, choose_hero,
    )

    feedback = feedback or FeedbackState.empty()
    ctx = payload["context"]
    night = date.fromisoformat(ctx["night"])
    picks = [_cached_pick(d) for d in payload["ranked"]]
    excluded = {k: v[0] for k, v in payload["excluded"].items()}
    names = {k: v[1] for k, v in payload["excluded"].items()}
    kept, hidden, unavailable = apply_feedback(picks, excluded, feedback, night, names,
                                               bool(payload.get("no_rigs")))
    lanes = assemble_lanes(kept, feedback.pinned, payload["moon_up_dark_frac"], payload["moon_illum"], per_lane,
                           preassigned=True)
    hero = choose_hero(kept, feedback.pinned)
    if payload.get("no_rigs"):
        v = (payload["verdict"]["level"], payload["verdict"]["reasons"])
    else:
        v = verdict(hero, ctx["tier"])
    common = payload.get("curve_common")

    def out(p: CachedPick) -> Dict[str, Any]:
        d = dict(p.data)
        d.pop("_raw", None)
        d["lane"] = p.lane
        own = d.get("curve")
        d["curve"] = _merge_curve(common, own) if own and common else None
        d["feedback"] = feedback.pick_feedback(p.key)
        return d

    # Several mounted rigs image concurrently: give each its own targets.
    plan = []
    if ctx.get("rig_mode") == RIG_MODE_MOUNTED and len(ctx.get("rigs") or []) > 1:
        assigned = assign_rig_plan(kept, [r["id"] for r in ctx["rigs"]], feedback.pinned)
        summaries = {s["rig_id"]: s for s in payload.get("rig_summary") or []}
        for r in ctx["rigs"]:
            items = []
            for p, alt in assigned[r["id"]]:
                d = out(p)
                d = _as_rig_pick(d, alt) if alt is not None else d
                d["best_rig"] = alt is None
                items.append(d)
            window = r.get("size_window_arcmin")
            note = None
            if items:
                primary, alt = assigned[r["id"]][0]
                if alt is not None:
                    primary = replace(primary, available_hours=_pair_hours(primary, alt),
                                      mode=alt.get("mode") or primary.mode)
                v_level, v_reasons = verdict(primary, ctx["tier"])
            else:
                note = rig_note(r, window, _offered_pairs(kept, r["id"], pinned=feedback.pinned),
                                summaries.get(r["id"]), payload, ctx)
                v_level, v_reasons = "DONT_BOTHER", [{"code": "NOTHING_GOOD", "text": note}]
            plan.append({"rig": {"id": r["id"], "name": r["name"]}, "items": items,
                         "verdict": {"level": v_level, "reasons": v_reasons}, "note": note,
                         "size_window_arcmin": window})

    counts = dict(payload["excluded_counts"])
    counts[EXCL_SNOOZED] = hidden[EXCL_SNOOZED]
    counts[EXCL_DISMISSED] = hidden[EXCL_DISMISSED]
    return {
        "generated_at": payload["generated_at"],
        "cached": cached,
        "context": {**ctx, "feedback_counts": feedback.counts(night)},
        "hero": out(hero) if hero is not None else None,
        "verdict": {"level": v[0], "reasons": v[1]},
        "lanes": [{"id": lane["id"], "title": lane["title"], "items": [out(p) for p in lane["items"]]}
                  for lane in lanes],
        "excluded_counts": counts,
        "skipped_rigs": list(payload["skipped_rigs"]),
        "pinned_unavailable": unavailable,
        "rig_plan": plan,
    }


def result_to_dict(result: Result, generated_at: Optional[datetime] = None, cached: bool = False,
                   feedback=None) -> Dict[str, Any]:
    """The §7 body for a Result, through the same payload path the API uses (feedback optional)."""
    return render_payload(result_payload(result, generated_at), feedback, result.params.per_lane, cached)
