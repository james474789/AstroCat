"""
Tests for app.utils.horizon (R0 learned horizon and N.I.N.A. .hrz, spec §4.7 / §6).
"""

import random
from datetime import datetime

import numpy as np
import pytest

from app.utils.horizon import (
    alt_az, altitude_at, format_hrz, horizon_samples, julian_date, learn_horizon, learn_horizon_profile,
    parse_hrz, sun_altitude,
)


def _samples():
    """Most bins imaged down to ~32 deg; east (90) and south-west (225) down to ~24."""
    rng = random.Random(1)
    out = []
    for _ in range(6000):
        az = rng.uniform(0, 360)
        low = 24.0 if (75 <= az < 105 or 210 <= az < 240) else 33.0
        out.append((az, rng.uniform(low, 80)))
    return out


def test_learn_horizon_floor_and_low_bins():
    profile = learn_horizon_profile(_samples())
    assert profile["sample_count"] == 6000
    assert 30 <= profile["floor_deg"] <= 36
    points = dict((round(az), alt) for az, alt in profile["points"])
    assert len(profile["points"]) == 24                     # 15 deg bins
    assert points[98] < 28 and points[218] < 28             # E and SW reach lower
    assert points[8] > 30                                   # elsewhere near the floor


def test_learn_horizon_cap_and_min_count():
    # One bin only ever imaged high up: capped at floor + 10.
    samples = [(az, 30.0 + (az % 7)) for az in np.linspace(0, 359, 3000)]
    samples += [(185.0, 75.0)] * 200
    profile = learn_horizon_profile([s for s in samples if not 180 <= s[0] < 195] + [(185.0, 75.0)] * 200)
    by_az = dict((round(a, 1), h) for a, h in profile["points"])
    assert by_az[187.5] == pytest.approx(profile["floor_deg"] + 10, abs=0.11)

    # A bin with fewer than min_count samples falls back to the floor.
    sparse = [(az, 40.0) for az in np.linspace(0, 179, 2000)] + [(300.0, 20.0)] * 10
    prof = learn_horizon_profile(sparse)
    assert dict((round(a, 1), h) for a, h in prof["points"])[307.5] == prof["floor_deg"]


def test_learn_horizon_min_altitude_clip_and_empty():
    low = [(az, 5.0) for az in np.linspace(0, 359, 2000)]
    assert all(alt >= 5.0 for _, alt in learn_horizon(low))
    assert all(alt == 15.0 for _, alt in learn_horizon(low, cap_above_floor=20))
    assert learn_horizon([]) == []


def test_hrz_round_trip_and_wrap():
    text = "# my horizon\n0 20\n90 25.5\n\n180\t30 # comment\n270,22\n360 21\n"
    points = parse_hrz(text)
    assert points == [[0.0, 20.0], [90.0, 25.5], [180.0, 30.0], [270.0, 22.0]]  # 360 wraps onto 0 (first wins)
    assert parse_hrz(format_hrz(points)) == points
    assert parse_hrz("-10 18\n370 19\n") == [[10.0, 19.0], [350.0, 18.0]]
    with pytest.raises(ValueError):
        parse_hrz("abc def\n")


def test_altitude_at_wraps():
    pts = [[10.0, 20.0], [350.0, 40.0]]
    assert altitude_at(pts, 10) == 20.0
    assert altitude_at(pts, 0) == pytest.approx(30.0)    # halfway across the 350 -> 10 wrap
    assert altitude_at(pts, 180) == pytest.approx(30.0)


def test_alt_az_and_sun():
    # Polaris-ish: altitude ~ latitude, azimuth ~ north.
    jd = julian_date([datetime(2026, 1, 1, 0, 0)])
    alt, az = alt_az(np.array([37.95]), np.array([89.26]), jd, 51.48, 0.0)
    assert alt[0] == pytest.approx(51.48, abs=1.0)
    assert min(az[0], 360 - az[0]) < 2.0
    # Sun: high at local noon in June, well below the horizon at midnight in December.
    assert sun_altitude(julian_date([datetime(2026, 6, 21, 12, 0)]), 51.48, 0.0)[0] == pytest.approx(62, abs=1.0)
    assert sun_altitude(julian_date([datetime(2026, 12, 21, 0, 0)]), 51.48, 0.0)[0] < -50


def test_horizon_samples_header_first_and_daylight_dropped():
    night = datetime(2026, 1, 10, 22, 0)
    day = datetime(2026, 6, 21, 12, 0)
    rows = [
        {"utc": night, "centalt": "33.5", "centaz": 120.0, "ra": None, "dec": None},
        {"utc": night, "centalt": None, "centaz": None, "ra": 37.95, "dec": 89.26},
        {"utc": night, "objctra": "02 31 49", "objctdec": "+89 15 51"},
        {"utc": day, "centalt": 40.0, "centaz": 180.0},                         # Sun up: dropped
        {"utc": night},                                                          # nothing usable
        {"utc": None, "centalt": 40.0, "centaz": 180.0},
    ]
    samples = horizon_samples(rows, 51.48, 0.0)
    assert len(samples) == 3
    assert samples[0] == (120.0, 33.5)
    assert samples[1][1] == pytest.approx(51.48, abs=1.0)
    assert samples[2][1] == pytest.approx(51.48, abs=1.0)
