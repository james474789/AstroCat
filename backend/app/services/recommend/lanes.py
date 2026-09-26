"""
Lanes, diversity, hero verdict and reasons (R1 spec §4.7).

Each target lands in exactly one lane, checked in priority order:
  active       momentum >= 0.37 (<= 45 days) and committed
  continue     committed, project > 0.15, have < goal
  last_chance  urgency >= 0.5
  moon_proof   Moon up > 50% of dark and illum > 0.5, mode narrowband
  other        the rest
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.recommend.candidates import Candidate
from app.services.recommend.context import TIER_ASTRO, TIER_BRIGHT, TIER_NAUTICAL, TIER_NONE
from app.services.recommend.history import NARROWBAND

LANE_ACTIVE = "active"
LANE_CONTINUE = "continue"
LANE_LAST_CHANCE = "last_chance"
LANE_MOON_PROOF = "moon_proof"
LANE_OTHER = "other"
LANES = (
    (LANE_ACTIVE, "Active projects"),
    (LANE_CONTINUE, "Continue a project"),
    (LANE_LAST_CHANCE, "Last chance this season"),
    (LANE_MOON_PROOF, "Moon-proof tonight"),
    (LANE_OTHER, "Other good options"),
)
LANE_TITLES = dict(LANES)

ACTIVE_MIN_MOMENTUM = 0.37
CONTINUE_MIN_PROJECT = 0.15
LAST_CHANCE_MIN_URGENCY = 0.5
MOON_PROOF_UP_FRACTION = 0.5
MOON_PROOF_ILLUM = 0.5
DIVERSITY_RADIUS_DEG = 10.0
DIVERSITY_MAX_NEIGHBOURS = 2
DEFAULT_PER_LANE = 6
OTHER_LANE_SIZE = 10

VERDICT_GO = "GO"
VERDICT_MARGINAL = "MARGINAL"
VERDICT_DONT_BOTHER = "DONT_BOTHER"
GO_MIN_HOURS = 3.0
MARGINAL_MIN_HOURS = 1.5

MODE_LABELS = {"HA": "Ha", "SII": "SII", "OIII": "OIII", "BB": "broadband", "OSC": "OSC"}


@dataclass
class Pick:
    candidate: Candidate
    rig_id: int
    rig_name: str
    mode: str
    score: float
    components: Dict[str, float]
    usable_hours: float
    available_hours: float
    best_time_utc: Optional[datetime]
    max_alt_deg: Optional[float]
    moon_sep_min_deg: Optional[float]
    fill_ratio: Optional[float]
    have_hours: float
    have_by_filter: Dict[str, float]
    nights: int
    last_imaged: Optional[date]
    goal_hours: float
    goal_source: str
    committed: bool = False
    days_since: Optional[int] = None
    weeks_left: Optional[float] = None
    moon_limited: Optional[Tuple[str, float]] = None   # (class, min separation) when a rig class is Moon-limited
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    lane: Optional[str] = None
    reasons: List[Dict[str, str]] = field(default_factory=list)
    idx: int = -1

    @property
    def key(self) -> str:
        return self.candidate.key


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

def lane_for(pick: Pick, moon_up_dark_frac: float, moon_illum: float) -> str:
    c = pick.components
    under_goal = pick.have_hours < pick.goal_hours
    if pick.committed and c.get("momentum", 0.0) >= ACTIVE_MIN_MOMENTUM:
        return LANE_ACTIVE
    if pick.committed and c.get("project", 0.0) > CONTINUE_MIN_PROJECT and under_goal:
        return LANE_CONTINUE
    if c.get("urgency", 0.0) >= LAST_CHANCE_MIN_URGENCY:
        return LANE_LAST_CHANCE
    if moon_up_dark_frac > MOON_PROOF_UP_FRACTION and moon_illum > MOON_PROOF_ILLUM and pick.mode in NARROWBAND:
        return LANE_MOON_PROOF
    return LANE_OTHER


def _sep(a: Candidate, b: Candidate) -> float:
    r1, d1, r2, d2 = (math.radians(v) for v in (a.ra_deg, a.dec_deg, b.ra_deg, b.dec_deg))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def diversify(picks: Sequence[Pick], radius_deg: float = DIVERSITY_RADIUS_DEG,
              max_neighbours: int = DIVERSITY_MAX_NEIGHBOURS) -> List[Pick]:
    """
    Picks (sorted by score) reordered so one within radius of `max_neighbours`
    higher-ranked picks is pushed below the next pick that isn't crowded.
    """
    pending = list(picks)
    out: List[Pick] = []
    while pending:
        chosen = 0
        for i, p in enumerate(pending):
            close = sum(1 for q in out if _sep(p.candidate, q.candidate) < radius_deg)
            if close < max_neighbours:
                chosen = i
                break
        out.append(pending.pop(chosen))
    return out


def build_lanes(ranked: Sequence[Pick], moon_up_dark_frac: float, moon_illum: float,
                per_lane: int = DEFAULT_PER_LANE, other_size: int = OTHER_LANE_SIZE) -> List[Dict[str, Any]]:
    """[{"id", "title", "items": [Pick]}] for non-empty lanes, in lane order. Sets pick.lane."""
    buckets: Dict[str, List[Pick]] = {lane: [] for lane, _ in LANES}
    for p in ranked:
        p.lane = lane_for(p, moon_up_dark_frac, moon_illum)
        buckets[p.lane].append(p)
    lanes = []
    for lane, title in LANES:
        items = buckets[lane]
        if not items:
            continue
        limit = other_size if lane == LANE_OTHER else per_lane
        head = diversify(items[: max(limit * 4, limit)])[:limit]
        lanes.append({"id": lane, "title": title, "items": head})
    return lanes


# ---------------------------------------------------------------------------
# Verdict and reasons
# ---------------------------------------------------------------------------

def verdict(hero: Optional[Pick], tier: str) -> Tuple[str, List[Dict[str, str]]]:
    if tier == TIER_NONE:
        return VERDICT_DONT_BOTHER, [{"code": "TIER", "text": "Too bright tonight (Sun never below -9°)"}]
    if hero is None:
        return VERDICT_DONT_BOTHER, [{"code": "NO_PICKS", "text": "No target passes the feasibility checks tonight"}]
    label = MODE_LABELS.get(hero.mode, hero.mode)
    hours = {"code": "MOON_CLEAR", "text": f"{hero.available_hours:.1f} h clear of Moon ({label}) on {hero.candidate.name}"}
    reasons = [hours]
    if tier == TIER_NAUTICAL:
        reasons.append({"code": "TIER", "text": "nautical darkness only"})
    elif tier == TIER_BRIGHT:
        reasons.append({"code": "TIER", "text": "bright twilight only (broadband penalised)"})
    if hero.available_hours >= GO_MIN_HOURS and tier in (TIER_ASTRO, TIER_NAUTICAL):
        return VERDICT_GO, reasons
    if hero.available_hours >= MARGINAL_MIN_HOURS or tier == TIER_BRIGHT:
        return VERDICT_MARGINAL, reasons
    reasons.append({"code": "SHORT", "text": f"only {hero.available_hours:.1f} h usable"})
    return VERDICT_DONT_BOTHER, reasons


def pick_reasons(pick: Pick, tier: str) -> List[Dict[str, str]]:
    label = MODE_LABELS.get(pick.mode, pick.mode)
    out = [{"code": "MOON_CLEAR", "text": f"{pick.available_hours:.1f} h clear of Moon ({label})"}]
    if pick.usable_hours < 0.5 and pick.max_alt_deg is not None:
        out.append({"code": "LOW", "text": f"below your usual horizon (max {pick.max_alt_deg:.0f}°)"})
    if pick.components.get("momentum", 0.0) > 0 and pick.days_since is not None:
        days = pick.days_since
        out.append({"code": "ACTIVE", "text": f"last imaged {days} day{'s' if days != 1 else ''} ago"})
    if pick.have_hours > 0:
        src = {"SET": "set", "INFERRED": "inferred", "DEFAULT": "default"}.get(pick.goal_source, pick.goal_source.lower())
        out.append({"code": "HAVE", "text": f"{pick.have_hours:.1f} h over {pick.nights} night{'s' if pick.nights != 1 else ''}; "
                                            f"goal ≈ {pick.goal_hours:.0f} h ({src})"})
    if pick.components.get("urgency", 0.0) > 0.3 and pick.weeks_left is not None:
        out.append({"code": "WINDOW", "text": f"window closes in ~{int(pick.weeks_left)} wk"})
    if pick.moon_limited is not None:
        cls, sep = pick.moon_limited
        out.append({"code": "MOON_MARGINAL", "text": f"Moon {sep:.0f}° away; {MODE_LABELS.get(cls, cls)} limited"})
    if pick.fill_ratio is not None:
        out.append({"code": "FRAMING", "text": f"fills {pick.fill_ratio * 100:.0f}% of {pick.rig_name}"})
    else:
        out.append({"code": "FRAMING", "text": f"size unknown on {pick.rig_name}"})
    if tier == TIER_NAUTICAL:
        out.append({"code": "TIER", "text": "nautical darkness only"})
    elif tier == TIER_BRIGHT:
        out.append({"code": "TIER", "text": "bright twilight: broadband penalised" if pick.mode in ("BB", "OSC")
                    else "bright twilight only"})
    return out
