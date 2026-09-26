"""
Night context (R1 spec §4.3): darkness tier, dark window, horizon, Moon.

Tier (latitude ~56 deg N has no astronomical darkness from about May to
mid-August, and at midsummer not even nautical):
  ASTRO     Sun < -18 deg for >= 1 h
  NAUTICAL  Sun < -12 deg for >= 1 h
  BRIGHT    Sun <  -9 deg for >= 1 h (narrowband only)
  NONE      otherwise (empty result)

Horizon: the site's saved profile (SAVED), else the learned one (LEARNED),
else a flat 30 deg (DEFAULT). The effective limit at azimuth az is
max(horizon(az), floor_deg).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

import numpy as np

from app.services.recommend.ephemeris import NightEphemeris

TIER_ASTRO = "ASTRO"
TIER_NAUTICAL = "NAUTICAL"
TIER_BRIGHT = "BRIGHT"
TIER_NONE = "NONE"
TIER_THRESHOLDS = ((TIER_ASTRO, -18.0), (TIER_NAUTICAL, -12.0), (TIER_BRIGHT, -9.0))
MIN_TIER_HOURS = 1.0

TIER_NOTES = {
    TIER_ASTRO: None,
    TIER_NAUTICAL: "No astronomical darkness tonight; using nautical twilight",
    TIER_BRIGHT: "No nautical darkness tonight; the Sun only gets below -9°, so narrowband only",
    TIER_NONE: "Too bright tonight (Sun never below -9°)",
}

HORIZON_SAVED = "SAVED"
HORIZON_LEARNED = "LEARNED"
HORIZON_DEFAULT = "DEFAULT"
DEFAULT_FLOOR_DEG = 30.0


@dataclass
class SiteSpec:
    id: int
    name: str
    latitude: float
    longitude: float
    timezone: str
    is_default: bool = False
    horizon: Optional[list] = None          # saved profile [[az, alt], ...]
    horizon_source: Optional[str] = None    # LEARNED | IMPORTED | MANUAL (as stored)


@dataclass
class HorizonSpec:
    points: List[List[float]]
    source: str             # SAVED | LEARNED | DEFAULT
    floor_deg: float


@dataclass
class NightContext:
    night: date
    site: SiteSpec
    tier: str
    dark_mask: np.ndarray
    dark_start_utc: Optional[datetime]
    dark_end_utc: Optional[datetime]
    horizon: List[List[float]]
    horizon_source: str
    floor_deg: float
    moon_illum: float
    moon_age_days: float
    moon_up_dark_frac: float
    eph: NightEphemeris = field(repr=False, default=None)

    @property
    def tier_note(self) -> Optional[str]:
        return TIER_NOTES.get(self.tier)

    @property
    def dark_hours(self) -> float:
        return float(self.dark_mask.sum()) * self.eph.step_h


def darkness_tier(sun_alt: np.ndarray, step_h: float, min_hours: float = MIN_TIER_HOURS) -> Tuple[str, np.ndarray]:
    """(tier, dark mask) for a night's Sun altitude curve."""
    for tier, threshold in TIER_THRESHOLDS:
        mask = sun_alt < threshold
        if mask.sum() * step_h >= min_hours - 1e-9:
            return tier, mask
    return TIER_NONE, np.zeros_like(sun_alt, dtype=bool)


def resolve_horizon(saved: Optional[Sequence], learned_points: Optional[Sequence],
                    learned_floor: Optional[float]) -> HorizonSpec:
    """Saved profile, else learned, else a flat default. floor = learned floor, else 30 deg."""
    floor = float(learned_floor) if learned_floor is not None else DEFAULT_FLOOR_DEG
    if saved:
        return HorizonSpec([[float(a), float(h)] for a, h in saved], HORIZON_SAVED, floor)
    if learned_points:
        return HorizonSpec([[float(a), float(h)] for a, h in learned_points], HORIZON_LEARNED, floor)
    return HorizonSpec([], HORIZON_DEFAULT, DEFAULT_FLOOR_DEG)


def horizon_limit(points: Sequence[Sequence[float]], floor_deg: float, az: np.ndarray) -> np.ndarray:
    """max(horizon(az), floor) with linear interpolation wrapping through 360 deg."""
    az = np.asarray(az)
    if not points:
        return np.full(az.shape, float(floor_deg), dtype=np.float32)
    pts = sorted((float(a) % 360.0, float(h)) for a, h in points)
    xp = np.asarray([p[0] for p in pts])
    fp = np.asarray([p[1] for p in pts])
    if len(pts) == 1:
        prof = np.full(az.shape, fp[0])
    else:
        prof = np.interp(az, xp, fp, period=360.0)
    return np.maximum(prof, float(floor_deg)).astype(np.float32)


def build_context(eph: NightEphemeris, site: SiteSpec, horizon: HorizonSpec) -> NightContext:
    tier, mask = darkness_tier(eph.sun_alt, eph.step_h)
    idx = np.flatnonzero(mask)
    dark_start = eph.times[idx[0]] if idx.size else None
    dark_end = eph.times[idx[-1]] if idx.size else None
    if idx.size:
        from datetime import timedelta
        dark_end = dark_end + timedelta(minutes=eph.step_min)
        illum = float(np.mean(eph.moon_illum[mask]))
        age = float(eph.moon_age[idx[idx.size // 2]])
        up_frac = float(np.mean(eph.moon_alt[mask] > 0.0))
    else:
        mid = len(eph.times) // 2
        illum, age = float(eph.moon_illum[mid]), float(eph.moon_age[mid])
        up_frac = 0.0
    return NightContext(
        night=eph.night, site=site, tier=tier, dark_mask=mask, dark_start_utc=dark_start, dark_end_utc=dark_end,
        horizon=horizon.points, horizon_source=horizon.source, floor_deg=horizon.floor_deg,
        moon_illum=illum, moon_age_days=age, moon_up_dark_frac=up_frac, eph=eph,
    )
