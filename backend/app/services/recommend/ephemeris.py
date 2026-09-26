"""
Night ephemeris (R1 spec §4.2).

- Night grid: night_bounds_utc(night, lon) (local solar noon to noon) in
  5-minute steps (288 steps).
- Sun altitude: utils/horizon.sun_altitude (numpy).
- Moon: astropy get_body (geocentric, the built-in ephemeris) at 15-minute
  resolution, interpolated to the grid; a topocentric parallax correction is
  applied to the altitude. Illuminated fraction and phase age come from the
  Sun-Moon elongation (waxing when the Moon is east of the Sun). If astropy
  fails (e.g. offline leap-second trouble) a low-precision analytic Moon
  (~0.3 deg) is used instead.
- Targets: vectorised alt/az (utils/horizon.alt_az) for N candidates x T steps,
  plus the Moon separation, as float32.

Pure functions of (RA/Dec arrays, lat, lon, night). `night_ephemeris` and
`night_sky` are LRU-cached per (site, night[, pool]).
"""

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import numpy as np

from app.utils.horizon import alt_az, julian_date, sun_altitude, sun_radec
from app.utils.observing_night import night_bounds_utc

STEP_MIN = 5
MOON_STEP_MIN = 15
SYNODIC_DAYS = 29.530589
MOON_HORIZONTAL_PARALLAX_DEG = 0.95


@dataclass
class NightEphemeris:
    night: date
    lat: float
    lon: float
    step_min: int
    times: List[datetime]          # naive UTC, T steps
    jd: np.ndarray                 # T
    sun_alt: np.ndarray            # T
    moon_alt: np.ndarray           # T (topocentric approx.)
    moon_ra: np.ndarray            # T (deg)
    moon_dec: np.ndarray           # T (deg)
    moon_illum: np.ndarray         # T (0..1)
    moon_age: np.ndarray           # T (days since new)

    @property
    def step_h(self) -> float:
        return self.step_min / 60.0


# ---------------------------------------------------------------------------
# Moon
# ---------------------------------------------------------------------------

def moon_radec_lowprec(jd: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Low-precision geocentric Moon RA/Dec in degrees (Astronomical Almanac, ~0.3 deg)."""
    T = (np.asarray(jd, dtype=float) - 2451545.0) / 36525.0
    d = np.radians
    lam = (218.32 + 481267.881 * T
           + 6.29 * np.sin(d(135.0 + 477198.87 * T)) - 1.27 * np.sin(d(259.3 - 413335.36 * T))
           + 0.66 * np.sin(d(235.7 + 890534.22 * T)) + 0.21 * np.sin(d(269.9 + 954397.74 * T))
           - 0.19 * np.sin(d(357.5 + 35999.05 * T)) - 0.11 * np.sin(d(186.5 + 966404.03 * T)))
    beta = (5.13 * np.sin(d(93.3 + 483202.02 * T)) + 0.28 * np.sin(d(228.2 + 960400.89 * T))
            - 0.28 * np.sin(d(318.3 + 6003.15 * T)) - 0.17 * np.sin(d(217.6 - 407332.21 * T)))
    lam, beta = d(np.mod(lam, 360.0)), d(beta)
    eps = d(23.439 - 0.0130 * T)
    x = np.cos(beta) * np.cos(lam)
    y = np.cos(eps) * np.cos(beta) * np.sin(lam) - np.sin(eps) * np.sin(beta)
    z = np.sin(eps) * np.cos(beta) * np.sin(lam) + np.cos(eps) * np.sin(beta)
    ra = np.mod(np.degrees(np.arctan2(y, x)), 360.0)
    dec = np.degrees(np.arcsin(np.clip(z, -1.0, 1.0)))
    return ra, dec


def moon_radec_astropy(jd: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Geocentric apparent Moon RA/Dec (degrees) from astropy's built-in ephemeris."""
    import warnings

    from astropy.coordinates import get_body
    from astropy.time import Time

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = Time(np.asarray(jd, dtype=float), format="jd", scale="utc")
        moon = get_body("moon", t)
        return np.asarray(moon.ra.deg, dtype=float), np.asarray(moon.dec.deg, dtype=float)


def moon_radec(jd: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    try:
        return moon_radec_astropy(jd)
    except Exception:
        return moon_radec_lowprec(jd)


def angular_sep_deg(ra1, dec1, ra2, dec2) -> np.ndarray:
    """Great-circle separation (degrees), broadcasting."""
    r1, d1, r2, d2 = np.radians(ra1), np.radians(dec1), np.radians(ra2), np.radians(dec2)
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def moon_phase(moon_ra, moon_dec, sun_ra, sun_dec) -> Tuple[np.ndarray, np.ndarray]:
    """(illuminated fraction, age in days since new) from the Sun-Moon elongation."""
    elong = angular_sep_deg(moon_ra, moon_dec, sun_ra, sun_dec)
    illum = (1.0 - np.cos(np.radians(elong))) / 2.0
    waxing = np.mod(np.asarray(moon_ra) - np.asarray(sun_ra), 360.0) < 180.0
    frac = elong / 360.0
    age = np.where(waxing, frac, 1.0 - frac) * SYNODIC_DAYS
    return illum, age


def _interp_angle(x_new, x, deg):
    """Interpolate an angle in degrees without wrap artefacts."""
    un = np.degrees(np.unwrap(np.radians(deg)))
    return np.mod(np.interp(x_new, x, un), 360.0)


# ---------------------------------------------------------------------------
# Night grid
# ---------------------------------------------------------------------------

def night_grid(night: date, lon: float, step_min: int = STEP_MIN) -> List[datetime]:
    start, end = night_bounds_utc(night, lon)
    n = int(round((end - start).total_seconds() / 60.0 / step_min))
    return [start + timedelta(minutes=step_min * i) for i in range(n)]


def compute_night_ephemeris(night: date, lat: float, lon: float, step_min: int = STEP_MIN,
                            moon_fn=None) -> NightEphemeris:
    times = night_grid(night, lon, step_min)
    jd = julian_date(times)
    sun = sun_altitude(jd, lat, lon)

    # Moon at coarse resolution, interpolated.
    stride = max(1, MOON_STEP_MIN // step_min)
    idx = np.arange(0, len(jd), stride)
    if idx[-1] != len(jd) - 1:
        idx = np.append(idx, len(jd) - 1)
    mra_c, mdec_c = (moon_fn or moon_radec)(jd[idx])
    mra = _interp_angle(jd, jd[idx], mra_c)
    mdec = np.interp(jd, jd[idx], mdec_c)
    malt, _ = alt_az(mra, mdec, jd, lat, lon)
    malt = malt - MOON_HORIZONTAL_PARALLAX_DEG * np.cos(np.radians(malt))
    sra, sdec = sun_radec(jd)
    illum, age = moon_phase(mra, mdec, sra, sdec)
    return NightEphemeris(night=night, lat=lat, lon=lon, step_min=step_min, times=times, jd=jd,
                          sun_alt=sun, moon_alt=malt, moon_ra=mra, moon_dec=mdec, moon_illum=illum,
                          moon_age=age)


def target_altaz(eph: NightEphemeris, ra_deg: np.ndarray, dec_deg: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(alt, az) as N x T float32 arrays."""
    ra = np.asarray(ra_deg, dtype=float)[:, None]
    dec = np.asarray(dec_deg, dtype=float)[:, None]
    alt, az = alt_az(ra, dec, eph.jd[None, :], eph.lat, eph.lon)
    return alt.astype(np.float32), az.astype(np.float32)


def moon_separation(eph: NightEphemeris, ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    """N x T float32 Moon separation (degrees)."""
    return angular_sep_deg(np.asarray(ra_deg, dtype=float)[:, None], np.asarray(dec_deg, dtype=float)[:, None],
                           eph.moon_ra[None, :], eph.moon_dec[None, :]).astype(np.float32)


# ---------------------------------------------------------------------------
# Caches (per process)
# ---------------------------------------------------------------------------

class _LRU:
    """
    Small thread-safe LRU (the API runs the engine in a threadpool). An entry
    may be tied to an owner object (the candidate pool): it only hits for
    that same object, so a recycled id() can never serve stale arrays.
    """

    def __init__(self, size: int):
        self.size = size
        self.data: "OrderedDict" = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, owner=None):
        with self.lock:
            hit = self.data.get(key)
            if hit is None or hit[0] is not owner:
                return None
            self.data.move_to_end(key)
            return hit[1]

    def put(self, key, value, owner=None):
        with self.lock:
            self.data[key] = (owner, value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()


_EPH_CACHE = _LRU(64)
_SKY_CACHE = _LRU(6)


def night_ephemeris(night: date, lat: float, lon: float, step_min: int = STEP_MIN) -> NightEphemeris:
    key = (night, round(lat, 5), round(lon, 5), step_min)
    eph = _EPH_CACHE.get(key)
    if eph is None:
        eph = compute_night_ephemeris(night, lat, lon, step_min)
        _EPH_CACHE.put(key, eph)
    return eph


@dataclass
class NightSky:
    """Target geometry for one pool on one night."""
    eph: NightEphemeris
    alt: np.ndarray     # N x T float32
    az: np.ndarray      # N x T float32
    sep: np.ndarray     # N x T float32 (Moon separation)


def night_sky(pool, night: date, lat: float, lon: float, step_min: int = STEP_MIN, cache: bool = True) -> NightSky:
    key = (id(pool), len(pool), night, round(lat, 5), round(lon, 5), step_min)
    sky = _SKY_CACHE.get(key, owner=pool) if cache else None
    if sky is None:
        eph = night_ephemeris(night, lat, lon, step_min)
        alt, az = target_altaz(eph, pool.ra, pool.dec)
        sep = moon_separation(eph, pool.ra, pool.dec)
        sky = NightSky(eph=eph, alt=alt, az=az, sep=sep)
        if cache:
            _SKY_CACHE.put(key, sky, owner=pool)
    return sky


def clear_caches() -> None:
    _EPH_CACHE.clear()
    _SKY_CACHE.clear()
