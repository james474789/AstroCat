"""
Per-user recommendation feedback (R2a, docs/design/R2a-feedback-dashboard.md §3-§4). Pure.

Feedback is applied *after* scoring, on top of the shared (user-independent)
engine result, so it never invalidates or fragments the result cache:

    kept, hidden, pinned_unavailable = apply_feedback(ranked, excluded, state, night)
    hero = choose_hero(kept, state.pinned)
    lanes = assemble_lanes(kept, state.pinned, moon_up_dark_frac, moon_illum, per_lane)

- Snoozed (for this night) and dismissed targets leave `ranked`; they're
  counted as SNOOZED / DISMISSED, never as infeasible.
- Pinned feasible picks form the `pinned` lane, first, by score, with no
  diversity reordering and no cap; they appear in no other lane. Pins that
  aren't shown tonight are listed in `pinned_unavailable` with the reason.
- The hero is the highest-scoring pick; a pinned pick within HERO_PIN_TIE of it
  wins the tie. Pins never change scores.

An empty FeedbackState returns the R1 result unchanged (replay uses it).

The state machine for the POST endpoint (`transition`) is here too, so the
rules are testable without a database.
"""

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.services.recommend.lanes import LANE_PINNED, LANE_PINNED_TITLE, build_lanes

# Actions (recommendation_events.action)
PIN = "PIN"
UNPIN = "UNPIN"
SNOOZE = "SNOOZE"
UNSNOOZE = "UNSNOOZE"
DISMISS = "DISMISS"
UNDISMISS = "UNDISMISS"
IMAGED = "IMAGED"
ACTIONS = (PIN, UNPIN, SNOOZE, UNSNOOZE, DISMISS, UNDISMISS, IMAGED)
INVERSE = {PIN: UNPIN, UNPIN: PIN, SNOOZE: UNSNOOZE, DISMISS: UNDISMISS, UNDISMISS: DISMISS}

SNOOZE_NIGHTS = (1, 7, 30)
DISMISS_REASONS = ("DONE", "NOT_MY_TYPE", "TOO_HARD", "OTHER")
NOTE_MAX = 200

EXCL_SNOOZED = "SNOOZED"
EXCL_DISMISSED = "DISMISSED"
HIDDEN_REASONS = (EXCL_SNOOZED, EXCL_DISMISSED)
# pinned_unavailable reasons beyond the engine's exclusion codes
PIN_NOT_IN_POOL = "NOT_IN_POOL"
PIN_NO_RIGS = "NO_RIGS"

HERO_PIN_TIE = 0.02


class FeedbackError(Exception):
    """A request the API maps to an HTTP error (status + detail)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedbackState:
    """One user's feedback, keyed on canonical target keys."""
    pinned: frozenset = frozenset()
    snoozed: Mapping[str, date] = field(default_factory=dict)            # key -> snoozed_until
    dismissed: Mapping[str, Optional[str]] = field(default_factory=dict)  # key -> dismiss_reason

    @classmethod
    def empty(cls) -> "FeedbackState":
        return cls()

    @classmethod
    def from_rows(cls, rows: Iterable[Any]) -> "FeedbackState":
        """From recommendation_target_state rows (objects or mappings)."""
        pinned, snoozed, dismissed = set(), {}, {}
        for r in rows:
            get = r.get if isinstance(r, Mapping) else (lambda k, _r=r: getattr(_r, k))
            key = get("target_key")
            if get("pinned"):
                pinned.add(key)
            if get("snoozed_until") is not None:
                snoozed[key] = get("snoozed_until")
            if get("dismissed"):
                dismissed[key] = get("dismiss_reason")
        return cls(pinned=frozenset(pinned), snoozed=snoozed, dismissed=dismissed)

    @property
    def is_empty(self) -> bool:
        return not self.pinned and not self.snoozed and not self.dismissed

    def hidden_reason(self, key: str, night: date) -> Optional[str]:
        """DISMISSED (no expiry) or SNOOZED (snoozed_until > night), else None."""
        if key in self.dismissed:
            return EXCL_DISMISSED
        until = self.snoozed.get(key)
        if until is not None and until > night:
            return EXCL_SNOOZED
        return None

    def counts(self, night: date) -> Dict[str, int]:
        return {"pinned": len(self.pinned),
                "snoozed": sum(1 for u in self.snoozed.values() if u > night),
                "dismissed": len(self.dismissed)}

    def pick_feedback(self, key: str) -> Dict[str, Any]:
        """The per-Pick `feedback` object (§6)."""
        until = self.snoozed.get(key)
        return {"pinned": key in self.pinned, "snoozed_until": until.isoformat() if until else None}

    def target_feedback(self, key: str) -> Dict[str, Any]:
        """The explain endpoint's top-level `feedback` object (§6)."""
        until = self.snoozed.get(key)
        return {"pinned": key in self.pinned, "snoozed_until": until.isoformat() if until else None,
                "dismissed": key in self.dismissed, "dismiss_reason": self.dismissed.get(key)}


# ---------------------------------------------------------------------------
# Applying feedback to a ranked list
# ---------------------------------------------------------------------------

def apply_feedback(ranked: Sequence[Any], excluded: Mapping[str, str], feedback: FeedbackState, night: date,
                   names: Optional[Mapping[str, str]] = None, no_rigs: bool = False
                   ) -> Tuple[List[Any], Dict[str, int], List[Dict[str, Any]]]:
    """
    (ranked', hidden_counts, pinned_unavailable).

    `ranked` is the engine's feasible list (anything with `.key`), in score
    order; `excluded` maps infeasible keys to their exclusion code; `names`
    gives display names for keys outside `ranked`. Order is preserved.
    """
    hidden = {EXCL_SNOOZED: 0, EXCL_DISMISSED: 0}
    if feedback.is_empty:
        return list(ranked), hidden, []
    kept: List[Any] = []
    shown_pins = set()
    hidden_pins: Dict[str, str] = {}
    for p in ranked:
        why = feedback.hidden_reason(p.key, night)
        if why is None:
            kept.append(p)
            if p.key in feedback.pinned:
                shown_pins.add(p.key)
        else:
            hidden[why] += 1
            if p.key in feedback.pinned:
                hidden_pins[p.key] = why
    names = dict(names or {})
    for p in ranked:
        name = getattr(p, "name", None) or getattr(getattr(p, "candidate", None), "name", None)
        if name:
            names.setdefault(p.key, name)
    unavailable = []
    for key in sorted(feedback.pinned):
        if key in shown_pins:
            continue
        reason = hidden_pins.get(key) or feedback.hidden_reason(key, night) or excluded.get(key)
        if reason is None:
            reason = PIN_NO_RIGS if no_rigs else PIN_NOT_IN_POOL
        unavailable.append({"target_key": key, "name": names.get(key, key), "excluded_reason": reason})
    return kept, hidden, unavailable


def choose_hero(ranked: Sequence[Any], pinned: frozenset, tie: float = HERO_PIN_TIE) -> Optional[Any]:
    """The highest-scoring pick; a pinned pick within `tie` of it wins."""
    if not ranked:
        return None
    best = ranked[0]
    if not pinned or best.key in pinned:
        return best
    for p in ranked:
        if p.score < best.score - tie - 1e-12:
            break
        if p.key in pinned:
            return p
    return best


def assemble_lanes(ranked: Sequence[Any], pinned: frozenset, moon_up_dark_frac: float, moon_illum: float,
                   per_lane: int, preassigned: bool = False) -> List[Dict[str, Any]]:
    """The pinned lane (every pinned pick, by score) first, then the R1 lanes over the rest."""
    pins = [p for p in ranked if p.key in pinned] if pinned else []
    rest = [p for p in ranked if p.key not in pinned] if pinned else list(ranked)
    lanes = build_lanes(rest, moon_up_dark_frac, moon_illum, per_lane, preassigned=preassigned)
    if pins:
        for p in pins:
            p.lane = LANE_PINNED
        lanes.insert(0, {"id": LANE_PINNED, "title": LANE_PINNED_TITLE, "items": pins})
    return lanes


# ---------------------------------------------------------------------------
# State machine (POST /api/recommendations/feedback)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetState:
    pinned: bool = False
    snoozed_until: Optional[date] = None
    dismissed: bool = False
    dismiss_reason: Optional[str] = None
    note: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.pinned or self.dismissed or self.snoozed_until is not None


def validate_action(action: Optional[str], nights: Optional[int] = None, reason: Optional[str] = None,
                    note: Optional[str] = None) -> str:
    """The upper-cased action; FeedbackError(400) for a bad action, nights, reason or note."""
    act = (action or "").strip().upper()
    if act not in ACTIONS:
        raise FeedbackError(400, f"action must be one of {', '.join(ACTIONS)}")
    if act == SNOOZE and nights not in SNOOZE_NIGHTS:
        raise FeedbackError(400, "SNOOZE needs nights = 1, 7 or 30")
    if act == DISMISS and reason is not None and reason not in DISMISS_REASONS:
        raise FeedbackError(400, f"reason must be one of {', '.join(DISMISS_REASONS)}")
    if note is not None and len(note) > NOTE_MAX:
        raise FeedbackError(400, f"note is limited to {NOTE_MAX} characters")
    return act


def transition(state: TargetState, action: str, night: date, nights: Optional[int] = None,
               reason: Optional[str] = None, note: Optional[str] = None) -> Tuple[TargetState, bool]:
    """
    (new state, changed). IMAGED never changes state (the caller still logs
    its event). A `note` is stored with any state action that passes one.
    Snooze: hidden for nights < night + nights.
    """
    new = state
    if action == PIN:
        new = replace(state, pinned=True)
    elif action == UNPIN:
        new = replace(state, pinned=False)
    elif action == SNOOZE:
        new = replace(state, snoozed_until=night + timedelta(days=int(nights)))
    elif action == UNSNOOZE:
        new = replace(state, snoozed_until=None)
    elif action == DISMISS:
        new = replace(state, dismissed=True, dismiss_reason=reason)
    elif action == UNDISMISS:
        new = replace(state, dismissed=False, dismiss_reason=None)
    if note is not None and action != IMAGED:
        new = replace(new, note=note or None)
    return new, new != state


def event_payload(action: str, nights: Optional[int] = None, reason: Optional[str] = None,
                  note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {}
    if action == SNOOZE and nights is not None:
        payload["nights"] = int(nights)
    if reason is not None and action == DISMISS:
        payload["reason"] = reason
    if note:
        payload["note"] = note
    return payload or None


# ---------------------------------------------------------------------------
# Impressions (§5)
# ---------------------------------------------------------------------------

def impression_rows(body: Mapping[str, Any], per_lane: int) -> List[Dict[str, Any]]:
    """
    What GET /api/recommendations showed: the hero plus the first `per_lane`
    items of each lane, one row per key (first showing wins), with the 1-based
    rank in overall display order (hero first, then lanes in order).
    """
    ctx = body.get("context") or {}
    night = ctx.get("night")
    site_id = (ctx.get("site") or {}).get("id")
    rig_mode = ctx.get("rig_mode")
    rows: List[Dict[str, Any]] = []
    seen = set()
    hero = body.get("hero")
    hero_key = hero["target_key"] if hero else None

    def add(p, lane, is_hero):
        if p["target_key"] in seen:
            return
        seen.add(p["target_key"])
        rows.append({"night": night, "target_key": p["target_key"], "site_id": site_id, "rig_mode": rig_mode,
                     "rig_id": (p.get("rig") or {}).get("id"), "lane": lane, "rank": len(rows) + 1,
                     "score": p.get("score"), "is_hero": is_hero})

    if hero:
        add(hero, hero.get("lane"), True)
    for lane in body.get("lanes") or []:
        for p in lane["items"][:per_lane]:
            add(p, lane["id"], p["target_key"] == hero_key)
    return rows
