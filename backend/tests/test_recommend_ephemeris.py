"""R1 §4.2-4.3: night grid, Sun/Moon, target geometry, darkness tier."""

import warnings
from datetime import date, datetime

import numpy as np
import pytest

from app.services.recommend.context import (
    TIER_ASTRO, TIER_BRIGHT, TIER_NAUTICAL, TIER_NONE, build_context, darkness_tier, horizon_limit, resolve_horizon,
)
from app.services.recommend.ephemeris import (
    compute_night_ephemeris, moon_radec_astropy, moon_radec_lowprec, night_grid, target_altaz,
)
from app.utils.horizon import julian_date, sun_altitude

from _recommend_helpers import FLAT, SITE


def test_night_grid_is_288_five_minute_steps_from_local_noon():
    grid = night_grid(date(2026, 9, 26), -3.0)
    assert len(grid) == 288
    assert grid[0] == datetime(2026, 9, 26, 12, 12)
    assert (grid[1] - grid[0]).total_seconds() == 300


def test_sun_altitude_matches_astropy():
    from astropy.coordinates import AltAz, EarthLocation, get_sun
    from astropy.time import Time
    from astropy.utils import iers
    import astropy.units as u

    iers.conf.auto_download = False
    when = datetime(2020, 3, 15, 19, 30)
    ours = float(sun_altitude(julian_date([when]), 56.0, 0.0)[0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        loc = EarthLocation(lat=56.0 * u.deg, lon=0.0 * u.deg, height=0 * u.m)
        t = Time(when)
        ref = float(get_sun(t).transform_to(AltAz(obstime=t, location=loc)).alt.deg)
    assert ours == pytest.approx(ref, abs=0.5)


def test_polaris_altitude_is_latitude():
    eph = compute_night_ephemeris(date(2026, 9, 26), 56.0, 0.0, moon_fn=moon_radec_lowprec)
    alt, az = target_altaz(eph, np.array([37.95]), np.array([89.264]))
    assert alt.shape == (1, 288) and alt.dtype == np.float32
    assert np.all(np.abs(alt - 56.0) < 1.0)


def test_moon_full_on_2026_09_26():
    eph = compute_night_ephemeris(date(2026, 9, 26), 56.0, 0.0)
    ctx = build_context(eph, SITE, FLAT)
    assert ctx.moon_illum > 0.99
    assert 13.5 < ctx.moon_age_days < 16.0
    assert ctx.moon_up_dark_frac > 0.9


def test_lowprec_moon_agrees_with_astropy():
    jd = julian_date([datetime(2026, 9, 26, 22, 0), datetime(2026, 12, 1, 3, 0), datetime(2025, 3, 3, 12, 0)])
    ra_a, dec_a = moon_radec_astropy(jd)
    ra_l, dec_l = moon_radec_lowprec(jd)
    dra = (ra_a - ra_l + 180.0) % 360.0 - 180.0
    assert np.all(np.abs(dra * np.cos(np.radians(dec_a))) < 0.6)
    assert np.all(np.abs(dec_a - dec_l) < 0.6)


def test_midsummer_at_56n_is_never_astro():
    eph = compute_night_ephemeris(date(2026, 6, 21), 56.0, 0.0, moon_fn=moon_radec_lowprec)
    tier, mask = darkness_tier(eph.sun_alt, eph.step_h)
    assert tier in (TIER_BRIGHT, TIER_NONE)


def test_midwinter_at_56n_is_astro_over_12h():
    eph = compute_night_ephemeris(date(2026, 12, 21), 56.0, 0.0, moon_fn=moon_radec_lowprec)
    ctx = build_context(eph, SITE, FLAT)
    assert ctx.tier == TIER_ASTRO
    assert ctx.dark_hours > 12.0
    assert ctx.dark_start_utc < ctx.dark_end_utc
    assert ctx.tier_note is None


def test_tier_thresholds():
    step = 5 / 60
    sun = np.full(288, -10.0)
    assert darkness_tier(sun, step)[0] == TIER_BRIGHT
    sun[:12] = -13.0            # exactly 1 h below -12
    assert darkness_tier(sun, step)[0] == TIER_NAUTICAL
    sun[:11] = -19.0            # 55 min below -18: not enough
    assert darkness_tier(sun, step)[0] == TIER_NAUTICAL
    assert darkness_tier(np.full(288, 5.0), step)[0] == TIER_NONE
    assert not darkness_tier(np.full(288, 5.0), step)[1].any()


def test_horizon_resolution_and_limit():
    saved = resolve_horizon([[0, 20], [180, 40]], [[90, 25]], 32.3)
    assert saved.source == "SAVED" and saved.floor_deg == 32.3
    learned = resolve_horizon(None, [[90, 25]], 32.3)
    assert learned.source == "LEARNED"
    default = resolve_horizon(None, [], None)
    assert default.source == "DEFAULT" and default.floor_deg == 30.0 and default.points == []

    az = np.array([0.0, 90.0, 180.0, 270.0, 359.0])
    lim = horizon_limit([[0, 20], [180, 40]], 25.0, az)
    assert list(np.round(lim, 1)) == [25.0, 30.0, 40.0, 30.0, 25.0]
    assert np.all(horizon_limit([], 30.0, az) == 30.0)
