"""
Planet and Moon tracks for the seeing forecast (design §3.4).

astropy get_body at 15-minute steps over the observing night (the same approach as the Moon in
services/recommend/ephemeris.py), topocentric alt/az via utils/horizon.alt_az, the site horizon via
services/recommend/context.horizon_limit (with the 15 degree planetary floor). Pure functions of
(lat, lon, night, horizon): the coordinates stay in memory and are never part of a return value.
"""

import math
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.services.recommend.context import horizon_limit
from app.services.recommend.ephemeris import MOON_HORIZONTAL_PARALLAX_DEG, night_grid
from app.services.seeing.scoring import ALT_FLOOR_DEG, SUN_LIMIT_DEG
from app.utils.horizon import alt_az, julian_date, sun_altitude

STEP_MIN = 15
AU_KM = 149597870.7

# Mercury is deliberately left out (design §5.4).
BODIES = ("jupiter", "saturn", "mars", "venus", "uranus", "neptune", "moon")
EQUATORIAL_RADIUS_KM = {
    "jupiter": 71492.0, "saturn": 60268.0, "mars": 3396.2, "venus": 6051.8,
    "uranus": 25559.0, "neptune": 24764.0, "moon": 1737.4,
}
SHOW_ILLUM = ("venus", "mars", "moon")
DAYLIGHT_OK = ("venus",)      # allowed with the Sun above -6 degrees (with a note)


@dataclass
class BodyTrack:
    body: str
    alt: np.ndarray            # T, degrees
    az: np.ndarray             # T, degrees (kept in memory; never serialised)
    limit: np.ndarray          # T, horizon limit incl. the 15 degree floor
    allowed: np.ndarray        # T bool: Sun below -6 (always for daylight-capable bodies)
    daylight: np.ndarray       # T bool: allowed only because the body is daylight-capable
    diameter_arcsec: Optional[float]
    illum: Optional[float]
    elongation_deg: Optional[float]


@dataclass
class PlanetTracks:
    times: List[datetime]      # naive UTC, 15-minute steps
    sun_alt: np.ndarray
    bodies: Dict[str, BodyTrack] = field(default_factory=dict)
    step_min: int = STEP_MIN


def _body_vectors(name: str, jd: np.ndarray):
    """(geocentric cartesian km (3xT), sun geocentric cartesian km (3xT), ra deg, dec deg)."""
    from astropy.coordinates import get_body
    from astropy.time import Time

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = Time(np.asarray(jd, dtype=float), format="jd", scale="utc")
        body = get_body(name, t)
        sun = get_body("sun", t)
        bv = np.asarray(body.cartesian.xyz.to("km").value, dtype=float)
        sv = np.asarray(sun.cartesian.xyz.to("km").value, dtype=float)
        return bv, sv, np.asarray(body.ra.deg, dtype=float), np.asarray(body.dec.deg, dtype=float)


def _angle_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    num = (a * b).sum(axis=0)
    den = np.linalg.norm(a, axis=0) * np.linalg.norm(b, axis=0)
    return np.degrees(np.arccos(np.clip(num / den, -1.0, 1.0)))


def planet_tracks(lat: float, lon: float, night: date, horizon_points: Sequence[Sequence[float]] = (),
                  bodies: Sequence[str] = BODIES, step_min: int = STEP_MIN) -> PlanetTracks:
    """Alt/az, allowed-imaging mask, apparent diameter, illuminated fraction and elongation for each body."""
    times = night_grid(night, lon, step_min)
    jd = julian_date(times)
    sun_alt = sun_altitude(jd, lat, lon)
    out = PlanetTracks(times=times, sun_alt=sun_alt, step_min=step_min)
    dark = sun_alt < SUN_LIMIT_DEG
    mid = len(times) // 2

    for name in bodies:
        try:
            bv, sv, ra, dec = _body_vectors(name, jd)
        except Exception:
            continue      # one failing body must not take down the others
        alt, az = alt_az(ra, dec, jd, lat, lon)
        if name == "moon":
            alt = alt - MOON_HORIZONTAL_PARALLAX_DEG * np.cos(np.radians(alt))
        dist_km = np.linalg.norm(bv, axis=0)
        diameter = float(np.degrees(2.0 * np.arcsin(EQUATORIAL_RADIUS_KM[name] / dist_km[mid])) * 3600.0)
        elong = _angle_deg(bv, sv)
        illum = None
        if name in SHOW_ILLUM:
            helio = bv - sv                                  # body from the Sun
            phase = _angle_deg(-helio, -bv)                  # Sun-body-Earth angle at the body
            illum = float((1.0 + np.cos(np.radians(phase[mid]))) / 2.0)
        daylight_ok = name in DAYLIGHT_OK
        allowed = np.ones_like(dark) if daylight_ok else dark
        out.bodies[name] = BodyTrack(
            body=name, alt=alt, az=az, limit=horizon_limit(list(horizon_points), ALT_FLOOR_DEG, az).astype(float),
            allowed=allowed, daylight=(allowed & ~dark) if daylight_ok else np.zeros_like(dark),
            diameter_arcsec=round(diameter, 1) if math.isfinite(diameter) else None,
            illum=None if illum is None else round(illum, 3), elongation_deg=round(float(elong[mid]), 1))
    return out
