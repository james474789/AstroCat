"""
Response models for /api/recommendations (R1 spec §7, R2a spec §5-§6).

The contract is binding (the Tonight page is built against it). Nullable
fields are null, never omitted. Extra (additive) fields are allowed.
"""

from datetime import date
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict

# SNOOZED / DISMISSED (R2a): hidden by the user's feedback, never infeasible.
ExcludedReason = Literal["BELOW_HORIZON", "TOO_SMALL", "TOO_BIG", "MOON", "TIER", "SNOOZED", "DISMISSED"]


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
    mode: Optional[str] = None               # additive (multi-mounted rig plan)
    fill_ratio: Optional[float] = None
    available_hours: Optional[float] = None


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


class PickFeedback(_Model):
    pinned: bool
    snoozed_until: Optional[str]


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
    feedback: Optional[PickFeedback] = None      # R2a


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
    feedback_counts: Optional[Dict[str, int]] = None   # R2a: {"pinned", "snoozed", "dismissed"}


class Verdict(_Model):
    level: Literal["GO", "MARGINAL", "DONT_BOTHER"]
    reasons: List[Reason]


class Lane(_Model):
    id: Literal["pinned", "active", "continue", "last_chance", "moon_proof", "other"]
    title: str
    items: List[Pick]


class SkippedRig(_Model):
    id: int
    name: str
    reason: Optional[str]


class PinnedUnavailable(_Model):
    target_key: str
    name: str
    excluded_reason: str        # an ExcludedReason, or NOT_IN_POOL / NO_RIGS


class RigPlanPick(Pick):
    best_rig: bool          # False: the target scores higher on another rig, but that rig has better work


class RigPlan(_Model):
    rig: RigRef
    items: List[RigPlanPick]    # first = primary target, the rest backups; no target on two rigs


class RecommendationsResponse(_Model):
    generated_at: str
    cached: bool
    context: Context
    hero: Optional[Pick]
    verdict: Verdict
    lanes: List[Lane]
    excluded_counts: Dict[ExcludedReason, int]
    skipped_rigs: List[SkippedRig]
    pinned_unavailable: List[PinnedUnavailable] = []     # R2a
    rig_plan: List[RigPlan] = []    # rig=mounted with several mounted rigs: distinct targets per rig


class TargetRigResult(_Model):
    rig_id: int
    rig_name: str
    pick: Optional[Pick]
    excluded_reason: Optional[ExcludedReason]
    details: Dict


class TargetFeedback(_Model):
    pinned: bool
    snoozed_until: Optional[str]
    dismissed: bool
    dismiss_reason: Optional[str]


class TargetExplanation(_Model):
    target_key: str
    name: str
    night: str
    results: List[TargetRigResult]
    feedback: Optional[TargetFeedback] = None     # R2a


class ReplayReport(_Model):
    generated_at: str
    nights: int
    params: Dict
    metrics: Dict[str, float]
    baselines: Dict[str, Dict[str, float]]
    breakdown: Dict
    misses: List


# ---------------------------------------------------------------------------
# R2a: feedback and outcomes (docs/design/R2a-feedback-dashboard.md §5-§6)
# ---------------------------------------------------------------------------

class FeedbackContext(BaseModel):
    """Where the action was taken (all optional; stored on the event)."""
    night: Optional[date] = None
    lane: Optional[str] = None
    rank: Optional[int] = None
    score: Optional[float] = None
    rig_id: Optional[int] = None


class FeedbackRequest(BaseModel):
    """
    POST /api/recommendations/feedback. `action`, `nights`, `reason` and `note`
    are validated by the endpoint (400, not 422): PIN | UNPIN | SNOOZE (nights
    1/7/30) | UNSNOOZE | DISMISS (optional reason) | UNDISMISS | IMAGED.
    """
    target_key: str
    action: str
    nights: Optional[int] = None
    reason: Optional[str] = None
    note: Optional[str] = None
    context: Optional[FeedbackContext] = None


class FeedbackItem(_Model):
    target_key: str
    name: str
    pinned: bool
    snoozed_until: Optional[str]
    dismissed: bool
    dismiss_reason: Optional[str]
    note: Optional[str]
    updated_at: Optional[str]


class FeedbackList(_Model):
    items: List[FeedbackItem]


class Rate(_Model):
    shown: int
    acted: int
    rate: float


class AnyRate(Rate):
    nights_with_any_acted: int


class SelfReported(_Model):
    imaged_events: int
    confirmed_by_library: int


class OutcomesResponse(_Model):
    since: str
    nights_with_impressions: int
    nights_imaged: int
    hero: Rate
    any: AnyRate
    by_lane: Dict[str, Rate]
    imaged_not_shown: int
    self_reported: SelfReported
    pending_nights: int
