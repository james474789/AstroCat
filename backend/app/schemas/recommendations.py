"""
Response models for /api/recommendations (R1 spec §7).

The contract is binding (the Tonight page is built against it). Nullable
fields are null, never omitted. Extra (additive) fields are allowed.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict

ExcludedReason = Literal["BELOW_HORIZON", "TOO_SMALL", "TOO_BIG", "MOON", "TIER"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class Reason(_Model):
    code: str
    text: str


class RigRef(_Model):
    id: int
    name: str


class Alternative(_Model):
    rig_id: int
    rig_name: str
    score: float


class Components(_Model):
    observability: float
    framing: float
    project: float
    momentum: float
    urgency: float
    prior: float


class Curve(_Model):
    t_utc: List[str]
    alt: List[float]
    moon_alt: List[float]
    limit: List[float]
    dark: List[bool]


class Pick(_Model):
    target_key: str
    name: str
    kind: str
    ra_deg: float
    dec_deg: float
    size_arcmin: Optional[float]
    rig: RigRef
    alternatives: List[Alternative]
    mode: str
    score: float
    components: Components
    usable_hours: float
    available_hours: float
    best_time_utc: Optional[str]
    max_alt_deg: Optional[float]
    moon_sep_min_deg: Optional[float]
    fill_ratio: Optional[float]
    have_hours: float
    have_by_filter: Dict[str, float]
    nights: int
    last_imaged: Optional[str]
    goal_hours: float
    goal_source: Literal["SET", "INFERRED", "DEFAULT"]
    reasons: List[Reason]
    curve: Optional[Curve]


class SiteRef(_Model):
    id: int
    name: str
    timezone: str


class Moon(_Model):
    illumination: float
    age_days: float
    up_fraction: float


class ContextRig(_Model):
    id: int
    name: str
    classes: List[str]


class Context(_Model):
    night: str
    site: SiteRef
    tier: Literal["ASTRO", "NAUTICAL", "BRIGHT", "NONE"]
    tier_note: Optional[str]
    dark_start_utc: Optional[str]
    dark_end_utc: Optional[str]
    moon: Moon
    horizon_source: Literal["SAVED", "LEARNED", "DEFAULT"]
    floor_deg: float
    rig_mode: Literal["MOUNTED", "ALL", "ALL_FALLBACK", "SINGLE"]
    rigs: List[ContextRig]
    weights: Dict[str, float]


class Verdict(_Model):
    level: Literal["GO", "MARGINAL", "DONT_BOTHER"]
    reasons: List[Reason]


class Lane(_Model):
    id: Literal["active", "continue", "last_chance", "moon_proof", "other"]
    title: str
    items: List[Pick]


class SkippedRig(_Model):
    id: int
    name: str
    reason: Optional[str]


class RecommendationsResponse(_Model):
    generated_at: str
    cached: bool
    context: Context
    hero: Optional[Pick]
    verdict: Verdict
    lanes: List[Lane]
    excluded_counts: Dict[ExcludedReason, int]
    skipped_rigs: List[SkippedRig]


class TargetRigResult(_Model):
    rig_id: int
    rig_name: str
    pick: Optional[Pick]
    excluded_reason: Optional[ExcludedReason]
    details: Dict


class TargetExplanation(_Model):
    target_key: str
    name: str
    night: str
    results: List[TargetRigResult]


class ReplayReport(_Model):
    generated_at: str
    nights: int
    params: Dict
    metrics: Dict[str, float]
    baselines: Dict[str, Dict[str, float]]
    breakdown: Dict
    misses: List
