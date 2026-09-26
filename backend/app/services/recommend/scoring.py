"""
Feasibility and scoring (R1 spec §4.5). Vectorised over the candidate pool.

Night features (rig-independent, computed once per night and shared by every
rig): the usable mask U = dark & alt > limit(az), and per Moon-rule class the
Moon-clear hours and the airmass-weighted Moon-clear hours.

Per rig, per candidate:
  mode       the best class by Moon-clear hours among rig classes that are
             useful for the target kind (and allowed by the tier);
  framing    ratio = size / short side of the FOV, px = size*60/scale;
  feasibility (first failure recorded): BELOW_HORIZON, TOO_SMALL, TOO_BIG,
             TIER (no usable class), MOON (< 0.5 h clear);
  components observability, framing, project, momentum, urgency, prior;
  score      sum(w_i * c_i).

Note on the exclusion order: the spec lists MOON before TIER, but with no
usable class the available hours are always 0, so MOON would hide TIER. TIER
is therefore checked first.
"""

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.services.recommend.candidates import KIND_EMISSION, KIND_PN, CandidatePool
from app.services.recommend.context import (
    TIER_BRIGHT, TIER_NONE, NightContext, darkness_tier, horizon_limit,
)
from app.services.recommend.ephemeris import SYNODIC_DAYS, _LRU, compute_night_ephemeris
from app.utils.horizon import alt_az
from app.services.recommend.history import (
    BROADBAND, CLASS_BB, CLASS_HA, CLASS_OIII, CLASS_OSC, CLASS_SII, CLASSES, NARROWBAND, GoalModel,
    TargetHistory,
)

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# class -> (D: avoidance distance at full Moon in deg, W: width in days)
MOON_RULES: Dict[str, Tuple[float, float]] = {
    CLASS_BB: (120.0, 14.0),
    CLASS_OSC: (120.0, 14.0),
    CLASS_HA: (40.0, 10.0),
    CLASS_SII: (40.0, 10.0),
    CLASS_OIII: (70.0, 10.0),
}
MOON_SOFT_WIDTH_DEG = 5.0

DEFAULT_MIN_USABLE_H = 1.0
MIN_AVAILABLE_H = 0.5
OBSERVABILITY_FULL_H = 5.0
PROJECT_DELTA_FRACTION = 0.7
PROJECT_OVER_GOAL_FACTOR = 0.4
PROJECT_DONE_FACTOR = 1.5
MOMENTUM_TAU_DAYS = 45.0
MOMENTUM_MAX_DAYS = 180
URGENCY_WEEKS = 12
URGENCY_TONIGHT_FRACTION = 0.8
SEASON_STEP_MIN = 30
FRAMING_BEST_RATIO = 0.5
FRAMING_SIGMA = 0.7
FRAMING_UNKNOWN_FIT = 0.4
MIN_TARGET_PX = 40.0
MAX_FILL_RATIO = 3.0

EXCL_NONE = 0
EXCL_BELOW_HORIZON = 1
EXCL_TOO_SMALL = 2
EXCL_TOO_BIG = 3
EXCL_TIER = 4
EXCL_MOON = 5
EXCLUDED_CODES = {
    EXCL_BELOW_HORIZON: "BELOW_HORIZON",
    EXCL_TOO_SMALL: "TOO_SMALL",
    EXCL_TOO_BIG: "TOO_BIG",
    EXCL_TIER: "TIER",
    EXCL_MOON: "MOON",
}
EXCLUDED_REASONS = tuple(EXCLUDED_CODES[c] for c in sorted(EXCLUDED_CODES))

CLASS_INDEX = {c: i for i, c in enumerate(CLASSES)}

# Target kind -> classes that are useful for it.
USEFUL_CLASSES = {
    KIND_EMISSION: frozenset({CLASS_HA, CLASS_SII, CLASS_OIII, CLASS_BB, CLASS_OSC}),
    KIND_PN: frozenset({CLASS_OIII, CLASS_HA, CLASS_BB, CLASS_OSC}),
}
DEFAULT_USEFUL = BROADBAND

COMPONENTS = ("observability", "framing", "project", "momentum", "urgency", "prior")


@dataclass(frozen=True)
class Weights:
    observability: float = 0.25
    framing: float = 0.20
    project: float = 0.20
    momentum: float = 0.15
    urgency: float = 0.15
    prior: float = 0.10

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class RigSpec:
    id: int
    name: str
    scale_arcsec: float
    fov_w_deg: float
    fov_h_deg: float
    classes: FrozenSet[str]
    is_color: bool = False

    @property
    def short_side_arcmin(self) -> float:
        return min(self.fov_w_deg, self.fov_h_deg) * 60.0


# ---------------------------------------------------------------------------
# Moon
# ---------------------------------------------------------------------------

def required_separation(age_days: float, d_deg: float, w_days: float) -> float:
    """Lorentzian Moon-avoidance distance: D / (1 + ((0.5 - age/P) / (W/P))^2)."""
    x = (0.5 - age_days / SYNODIC_DAYS) / (w_days / SYNODIC_DAYS)
    return d_deg / (1.0 + x * x)


def moon_factor(sep: np.ndarray, moon_up: np.ndarray, required: float,
                width: float = MOON_SOFT_WIDTH_DEG) -> np.ndarray:
    """1 while the Moon is down, else sigmoid((sep - required) / width)."""
    z = np.clip((np.asarray(sep, dtype=np.float32) - required) / width, -40.0, 40.0)
    f = 1.0 / (1.0 + np.exp(-z))
    return np.where(moon_up, f, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Night features (rig independent)
# ---------------------------------------------------------------------------

@dataclass
class NightFeatures:
    usable_h: np.ndarray        # N
    moon_ok_h: np.ndarray       # N x C
    weighted_h: np.ndarray      # N x C (x sin(alt))
    max_alt: np.ndarray         # N (during usable steps, else during the night)
    best_idx: np.ndarray        # N (grid step of max usable altitude)
    sep_min: np.ndarray         # N (min Moon separation while usable and Moon up; nan if never)
    limit: np.ndarray           # N x T horizon limit
    required: Dict[str, float]  # class -> required separation tonight
    step_h: float


def night_features(ctx: NightContext, sky, moon_rules: Mapping[str, Tuple[float, float]] = MOON_RULES) -> NightFeatures:
    eph = ctx.eph
    step_h = eph.step_h
    alt, az, sep = sky.alt, sky.az, sky.sep
    limit = horizon_limit(ctx.horizon, ctx.floor_deg, az)
    U = (alt > limit) & ctx.dark_mask[None, :]
    usable_h = U.sum(axis=1) * step_h
    moon_up = (eph.moon_alt > 0.0)[None, :]
    w_alt = np.sin(np.radians(np.clip(alt, 0.0, 90.0))).astype(np.float32)
    Uf = U.astype(np.float32)
    n = alt.shape[0]
    moon_ok = np.zeros((n, len(CLASSES)), dtype=np.float32)
    weighted = np.zeros((n, len(CLASSES)), dtype=np.float32)
    required = {}
    for cls, ci in CLASS_INDEX.items():
        d, w = moon_rules.get(cls, MOON_RULES[cls])
        req = required_separation(ctx.moon_age_days, d, w)
        required[cls] = req
        f = moon_factor(sep, moon_up, req) * Uf
        moon_ok[:, ci] = f.sum(axis=1) * step_h
        weighted[:, ci] = (f * w_alt).sum(axis=1) * step_h
    masked_alt = np.where(U, alt, -99.0)
    best_idx = np.argmax(masked_alt, axis=1)
    max_alt = np.where(U.any(axis=1), masked_alt.max(axis=1),
                       np.where(ctx.dark_mask[None, :], alt, -99.0).max(axis=1))
    sep_mask = U & moon_up
    sep_min = np.where(sep_mask.any(axis=1), np.where(sep_mask, sep, 999.0).min(axis=1), np.nan)
    return NightFeatures(usable_h=usable_h.astype(float), moon_ok_h=moon_ok, weighted_h=weighted,
                         max_alt=max_alt.astype(float), best_idx=best_idx, sep_min=sep_min.astype(float),
                         limit=limit, required=required, step_h=step_h)


# ---------------------------------------------------------------------------
# Seasonal usable hours (urgency)
# ---------------------------------------------------------------------------

_SEASON_CACHE = _LRU(512)


def future_usable_hours(pool: CandidatePool, lat: float, lon: float, night: date,
                        horizon: Sequence, floor_deg: float, weeks: int = URGENCY_WEEKS,
                        step_min: int = SEASON_STEP_MIN) -> np.ndarray:
    """N x weeks: usable hours on nights +7, +14, ... (coarse grid, each night at its own tier)."""
    out = np.zeros((len(pool), weeks), dtype=np.float32)
    hz = tuple(tuple(p) for p in horizon)
    for wk in range(1, weeks + 1):
        fut = night + timedelta(days=7 * wk)
        key = (id(pool), len(pool), round(lat, 5), round(lon, 5), fut, hz, round(floor_deg, 3), step_min)
        col = _SEASON_CACHE.get(key)
        if col is None:
            eph = compute_night_ephemeris(fut, lat, lon, step_min, moon_fn=_no_moon)
            tier, mask = darkness_tier(eph.sun_alt, eph.step_h)
            if tier == TIER_NONE or not mask.any():
                col = np.zeros(len(pool), dtype=np.float32)
            else:
                jd = eph.jd[np.flatnonzero(mask)]
                alt, az = alt_az(pool.ra[:, None], pool.dec[:, None], jd[None, :], lat, lon)
                lim = horizon_limit(horizon, floor_deg, az)
                col = ((alt > lim).sum(axis=1) * eph.step_h).astype(np.float32)
            _SEASON_CACHE.put(key, col)
        out[:, wk - 1] = col
    return out


def _no_moon(jd):
    z = np.zeros(len(jd))
    return z, z


def urgency_scores(usable_h: np.ndarray, future: np.ndarray,
                   min_usable_h: float = DEFAULT_MIN_USABLE_H) -> Tuple[np.ndarray, np.ndarray]:
    """(urgency, weeks_left). weeks_left = first future week (1-based) below min_usable_h, else `weeks`."""
    weeks = future.shape[1]
    below = future < min_usable_h
    weeks_left = np.where(below.any(axis=1), np.argmax(below, axis=1) + 1, weeks).astype(float)
    peak = future.max(axis=1) if weeks else np.zeros(len(usable_h))
    good_tonight = usable_h >= URGENCY_TONIGHT_FRACTION * peak
    urgency = np.where(good_tonight, 1.0 - weeks_left / float(weeks), 0.0)
    return np.clip(urgency, 0.0, 1.0), weeks_left


# ---------------------------------------------------------------------------
# History arrays
# ---------------------------------------------------------------------------

@dataclass
class HistoryArrays:
    has: np.ndarray           # N bool
    total_h: np.ndarray       # N
    class_h: np.ndarray       # N x C
    nights: np.ndarray        # N
    days_since: np.ndarray    # N (nan when never imaged)
    momentum: np.ndarray      # N
    committed: np.ndarray     # N bool
    goal_h: np.ndarray        # N
    goal_source: List[str]    # N


def momentum_score(days_since) -> np.ndarray:
    d = np.asarray(days_since, dtype=float)
    with np.errstate(invalid="ignore"):
        m = np.where((d >= 0) & (d <= MOMENTUM_MAX_DAYS), np.exp(-np.nan_to_num(d, nan=1e9) / MOMENTUM_TAU_DAYS), 0.0)
    return np.where(np.isnan(d), 0.0, m)


def history_arrays(pool: CandidatePool, history: Mapping[str, TargetHistory], goals: GoalModel,
                   night: date) -> HistoryArrays:
    n = len(pool)
    has = np.zeros(n, dtype=bool)
    total = np.zeros(n)
    class_h = np.zeros((n, len(CLASSES)))
    nights = np.zeros(n)
    days = np.full(n, np.nan)
    goal_h = np.zeros(n)
    goal_src: List[str] = [""] * n
    for i, c in enumerate(pool.candidates):
        g, src = goals.goal_for(c.key, c.kind)
        goal_h[i], goal_src[i] = g, src
    for key, h in history.items():
        i = pool.index.get(key)
        if i is None:
            continue
        has[i] = True
        total[i] = h.total_hours
        for cls, ci in CLASS_INDEX.items():
            class_h[i, ci] = h.seconds_by_class.get(cls, 0.0) / 3600.0
        nights[i] = len(h.nights)
        if h.last_night is not None:
            days[i] = (night - h.last_night).days
    mom = momentum_score(days)
    committed = has & ((nights >= 2) | (total >= 2.0) | (mom > 0))
    return HistoryArrays(has=has, total_h=total, class_h=class_h, nights=nights, days_since=days, momentum=mom,
                         committed=committed, goal_h=goal_h, goal_source=goal_src)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def framing(size_arcmin: np.ndarray, rig: RigSpec) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(fit, ratio, px); unknown size -> fit 0.4, ratio/px nan."""
    size = np.asarray(size_arcmin, dtype=float)
    known = ~np.isnan(size) & (size > 0)
    safe = np.where(known, size, 1.0)
    ratio = np.where(known, safe / rig.short_side_arcmin, np.nan)
    px = np.where(known, safe * 60.0 / rig.scale_arcsec, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        fit = np.exp(-np.log(np.where(known, ratio, 1.0) / FRAMING_BEST_RATIO) ** 2 / (2 * FRAMING_SIGMA ** 2))
    fit = np.where(known, fit, FRAMING_UNKNOWN_FIT)
    return fit, ratio, px


# ---------------------------------------------------------------------------
# Per-rig evaluation
# ---------------------------------------------------------------------------

def useful_mask(pool: CandidatePool) -> np.ndarray:
    """N x C: class useful for the candidate's kind."""
    m = np.zeros((len(pool), len(CLASSES)), dtype=bool)
    for i, kind in enumerate(pool.kind):
        for cls in USEFUL_CLASSES.get(kind, DEFAULT_USEFUL):
            m[i, CLASS_INDEX[cls]] = True
    return m


def tier_class_mask(tier: str) -> np.ndarray:
    ok = np.ones(len(CLASSES), dtype=bool)
    if tier == TIER_BRIGHT:
        for cls in BROADBAND:
            ok[CLASS_INDEX[cls]] = False
    if tier == TIER_NONE:
        ok[:] = False
    return ok


@dataclass
class RigEval:
    rig: RigSpec
    score: np.ndarray            # N (nan when excluded)
    excluded: np.ndarray         # N int
    mode: np.ndarray             # N int class index (-1: none)
    avail_h: np.ndarray          # N
    pair_mask: np.ndarray        # N x C usable classes for the pair
    fit: np.ndarray
    ratio: np.ndarray
    px: np.ndarray
    have_h: np.ndarray           # N hours in the classes usable tonight (fallback: all)
    components: Dict[str, np.ndarray]


def evaluate_rig(rig: RigSpec, pool: CandidatePool, feats: NightFeatures, hist: HistoryArrays,
                 urgency: np.ndarray, tier: str, weights: Weights, useful: np.ndarray,
                 min_usable_h: float = DEFAULT_MIN_USABLE_H) -> RigEval:
    n = len(pool)
    rig_classes = np.asarray([c in rig.classes for c in CLASSES], dtype=bool)
    pair = useful & rig_classes[None, :] & tier_class_mask(tier)[None, :]
    cand = np.where(pair, feats.moon_ok_h, -1.0)
    mode = np.argmax(cand, axis=1)
    avail = cand[np.arange(n), mode]
    has_class = pair.any(axis=1)
    mode = np.where(has_class, mode, -1)
    avail = np.where(has_class, avail, 0.0)

    fit, ratio, px = framing(pool.size, rig)
    excluded = np.zeros(n, dtype=np.int8)
    rules = (
        (EXCL_BELOW_HORIZON, feats.usable_h < min_usable_h),
        (EXCL_TOO_SMALL, np.nan_to_num(px, nan=np.inf) < MIN_TARGET_PX),
        (EXCL_TOO_BIG, np.nan_to_num(ratio, nan=0.0) > MAX_FILL_RATIO),
        (EXCL_TIER, ~has_class),
        (EXCL_MOON, avail < MIN_AVAILABLE_H),
    )
    for code, cond in rules:
        excluded = np.where((excluded == 0) & cond, code, excluded)

    safe_mode = np.maximum(mode, 0)
    observability = np.minimum(feats.weighted_h[np.arange(n), safe_mode] / OBSERVABILITY_FULL_H, 1.0)
    observability = np.where(has_class, observability, 0.0)

    have_u = (hist.class_h * pair).sum(axis=1)
    have = np.where(have_u > 0, have_u, hist.total_h)
    delta = PROJECT_DELTA_FRACTION * avail
    with np.errstate(divide="ignore", invalid="ignore"):
        gain = np.minimum(1.0, (np.sqrt((have + delta) / np.where(have > 0, have, 1.0)) - 1.0) / 0.5)
    gain = np.where(have > 0, gain, 0.0)
    under_goal = hist.total_h < hist.goal_h
    project = np.where(hist.committed, gain * np.where(under_goal, 1.0, PROJECT_OVER_GOAL_FACTOR), 0.0)
    project = np.where(hist.total_h >= PROJECT_DONE_FACTOR * hist.goal_h, 0.0, project)

    comps = {
        "observability": observability,
        "framing": fit,
        "project": project,
        "momentum": hist.momentum,
        "urgency": urgency,
        "prior": pool.prior,
    }
    w = weights.as_dict()
    score = sum(w[k] * comps[k] for k in COMPONENTS)
    score = np.where(excluded == 0, score, np.nan)
    return RigEval(rig=rig, score=score, excluded=excluded, mode=mode, avail_h=avail, pair_mask=pair, fit=fit,
                   ratio=ratio, px=px, have_h=have, components=comps)


def best_rigs(evals: Sequence[RigEval]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(best rig index per candidate or -1, best score, furthest exclusion code for fully excluded candidates)."""
    if not evals:
        return np.zeros(0, dtype=int), np.zeros(0), np.zeros(0, dtype=np.int8)
    scores = np.vstack([np.nan_to_num(e.score, nan=-np.inf) for e in evals])
    best = np.argmax(scores, axis=0)
    best_score = scores[best, np.arange(scores.shape[1])]
    feasible = np.isfinite(best_score)
    furthest = np.vstack([e.excluded for e in evals]).max(axis=0)
    return np.where(feasible, best, -1), np.where(feasible, best_score, np.nan), np.where(feasible, 0, furthest)
