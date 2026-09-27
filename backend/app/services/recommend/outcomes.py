"""
Advice outcomes (R2a, docs/design/R2a-feedback-dashboard.md §5). Pure.

An impression (user, night N, key) is *acted on* when the library has a light
sub of that key (after the R1 §14.1 key folding) whose observing night is N.

- Only nights in [since, today - 1] are evaluated; later impression nights are
  "pending".
- The rates count only impressions on nights with imaging: on a cloudy night
  nothing could have been acted on, so those nights don't dilute them.
- `imaged_not_shown`: evaluated impression nights on which something was
  imaged that the page never showed.
- Self-reported IMAGED events (deduplicated per key and night) are confirmed
  when the library has that key on the event night or the night before (the
  button is often pressed the next day, when "tonight" has moved on).

The SQL that feeds this lives in loader.py.
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, Iterable, Mapping, NamedTuple, Optional, Set, Tuple

DEFAULT_DAYS = 90


class Impression(NamedTuple):
    night: date
    target_key: str
    lane: Optional[str]
    is_hero: bool


class ImagedRow(NamedTuple):
    """One (raw target key, night) with light subs; key may be None (untargeted frames still mark a night)."""
    target_key: Optional[str]
    night: date


def _rate(shown: int, acted: int) -> Dict[str, Any]:
    return {"shown": shown, "acted": acted, "rate": round(acted / shown, 3) if shown else 0.0}


def imaged_by_night(rows: Iterable[ImagedRow], key_map: Optional[Mapping[str, str]] = None
                    ) -> Tuple[Dict[date, Set[str]], Set[date]]:
    """({night: {canonical keys}}, {nights with any imaging}), folding stray keys through `key_map`."""
    key_map = key_map or {}
    by_night: Dict[date, Set[str]] = defaultdict(set)
    nights: Set[date] = set()
    for r in rows:
        if r.night is None:
            continue
        nights.add(r.night)
        if r.target_key:
            by_night[r.night].add(key_map.get(r.target_key, r.target_key))
    return dict(by_night), nights


def compute_outcomes(impressions: Iterable[Impression], imaged: Mapping[date, Set[str]], imaged_nights: Set[date],
                     events: Iterable[Tuple[date, str]], since: date, today: date) -> Dict[str, Any]:
    """The §5 GET /api/recommendations/outcomes body."""
    until = today - timedelta(days=1)
    evaluated = []
    pending = set()
    for imp in impressions:
        if imp.night > until:
            pending.add(imp.night)
        elif imp.night >= since:
            evaluated.append(imp)

    impression_nights = {imp.night for imp in evaluated}
    counted_nights = impression_nights & set(imaged_nights)
    counted = [imp for imp in evaluated if imp.night in counted_nights]

    def acted(imp: Impression) -> bool:
        return imp.target_key in imaged.get(imp.night, ())

    hero_shown = hero_acted = any_acted = 0
    acted_nights = set()
    lanes: Dict[str, list] = defaultdict(lambda: [0, 0])
    for imp in counted:
        a = acted(imp)
        if imp.is_hero:
            hero_shown += 1
            hero_acted += a
        any_acted += a
        if a:
            acted_nights.add(imp.night)
        lane = lanes[imp.lane or "unknown"]
        lane[0] += 1
        lane[1] += a

    shown_by_night: Dict[date, Set[str]] = defaultdict(set)
    for imp in evaluated:
        shown_by_night[imp.night].add(imp.target_key)
    not_shown = sum(1 for n in counted_nights if imaged.get(n, set()) - shown_by_night[n])

    reported = {(n, k) for n, k in events if n is not None and since <= n <= until}
    confirmed = sum(1 for n, k in reported
                    if k in imaged.get(n, ()) or k in imaged.get(n - timedelta(days=1), ()))

    any_rate = _rate(len(counted), any_acted)
    any_rate["nights_with_any_acted"] = len(acted_nights)
    return {
        "since": since.isoformat(),
        "nights_with_impressions": len(impression_nights),
        "nights_imaged": len(counted_nights),
        "hero": _rate(hero_shown, hero_acted),
        "any": any_rate,
        "by_lane": {lane: _rate(v[0], v[1]) for lane, v in sorted(lanes.items())},
        "imaged_not_shown": not_shown,
        "self_reported": {"imaged_events": len(reported), "confirmed_by_library": confirmed},
        "pending_nights": len(pending),
    }
