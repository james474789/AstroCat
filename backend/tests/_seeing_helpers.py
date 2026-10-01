"""
Shared synthetic fixtures for the S1 seeing tests. Privacy: only obviously synthetic coordinates appear here
(0.0 / 0.0 and the sentinel pair), never anything resembling a real site.
"""

import fnmatch
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np

# The seeing modules read settings lazily; give them a harmless environment.
os.environ.setdefault("SECRET_KEY", "k9x2Zq7LmP4vB8nR1sT6wY3uH5jC0aQe")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

from app.services.recommend.context import SiteSpec  # noqa: E402
from app.services.recommend.ephemeris import night_grid  # noqa: E402
from app.services.seeing.planets import BodyTrack, PlanetTracks  # noqa: E402
from app.services.seeing.sources import HourlyFrame  # noqa: E402

SENTINEL_LAT = 12.3456
SENTINEL_LON = -65.4321
SENTINEL_STRINGS = ("12.3456", "65.4321")

SENTINEL_SITE = SiteSpec(id=7, name="Sentinel", latitude=SENTINEL_LAT, longitude=SENTINEL_LON, timezone="UTC",
                         is_default=True)
ZERO_SITE = SiteSpec(id=1, name="Zero", latitude=0.0, longitude=0.0, timezone="UTC", is_default=True)

NIGHT = date(2030, 1, 1)
NOW = datetime(2030, 1, 1, 14, 0)     # 09:38 local solar at the sentinel longitude -> default night is NIGHT


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def set(self, key, value, *a, **k):
        self.store[key] = value

    def exists(self, key):
        return int(key in self.store)

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def scan_iter(self, match="*"):
        return iter([k for k in list(self.store) if fnmatch.fnmatch(k, match)])


def hours_from(start: datetime, n: int):
    return [start + timedelta(hours=i) for i in range(n)]


CALM = {
    "wind_speed_10m": 0.5, "wind_gusts_10m": 1.0, "temperature_2m": 0.0, "relative_humidity_2m": 92.0,
    "dew_point_2m": -1.5, "cloud_cover": 0.0, "cloud_cover_low": 0.0, "cloud_cover_mid": 0.0,
    "cloud_cover_high": 0.0, "boundary_layer_height": 200.0,
    "wind_speed_200hPa": 10.0, "wind_speed_250hPa": 10.0, "wind_speed_300hPa": 10.0,
    "wind_speed_850hPa": 2.0,
}


def make_frame(start=datetime(2029, 12, 31), n=96, **overrides) -> HourlyFrame:
    vals = {**CALM, **overrides}
    times = hours_from(start, n)
    return HourlyFrame(times=times, values={k: [v] * n for k, v in vals.items()})


def make_frames(models=("ecmwf_ifs025", "icon_seamless", "gfs_seamless"), **overrides):
    return {m: make_frame(**overrides) for m in models}


def make_tracks(night=NIGHT, lon=SENTINEL_LON, bodies=("jupiter",), peak_alt=80.0, daylight=False):
    """Deterministic tracks: each body rises and sets over the night grid, peaking mid-night."""
    times = night_grid(night, lon, 15)
    n = len(times)
    x = np.linspace(0.0, np.pi, n)
    sun = np.where(np.abs(np.linspace(-1, 1, n)) > 0.9, 10.0, -30.0)       # bright only at the very ends
    out = PlanetTracks(times=times, sun_alt=sun, step_min=15)
    for name in bodies:
        alt = peak_alt * np.sin(x) - 5.0
        allowed = np.ones(n, dtype=bool) if daylight else sun < -6.0
        out.bodies[name] = BodyTrack(
            body=name, alt=alt, az=np.linspace(90, 270, n), limit=np.full(n, 15.0), allowed=allowed,
            daylight=(allowed & (sun >= -6.0)) if daylight else np.zeros(n, dtype=bool),
            diameter_arcsec=44.0, illum=None, elongation_deg=120.0)
    return out


def cfg(**kw):
    base = dict(meteoblue_api_key=None, meteoblue_min_interval_h=6, seeing_coord_decimals=2, seeing_enabled=True)
    base.update(kw)
    return SimpleNamespace(**base)
