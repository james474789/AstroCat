"""
Site horizon profiles (R0, docs/design/P0-R0-equipment-sites.md §4.7).

Pure helpers:
- `learn_horizon(samples)`: a per-azimuth-bin horizon learned from where
  the user has actually imaged (altitude of past light frames).
- `horizon_samples(rows, lat, lon)`: (az, alt) of past frames from header
  CENTALT/CENTAZ, else computed from RA/Dec + UTC time + site (vectorised
  numpy), dropping frames taken with the Sun above -6 deg (mis-timed files).
- `parse_hrz` / `format_hrz`: N.I.N.A. horizon files (one "az alt" pair per
  line, '#' comments), sorted by azimuth, azimuth wrapped into [0, 360).
- `altitude_at(points, az)`: interpolated horizon altitude with wrap-around.

Absence of data is not an obstruction: learned bins are capped at
floor + cap_above_floor.
"""

import math
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.utils.capture_time import unchar
from app.utils.header_values import parse_sexagesimal

Point = List[float]

MIN_HORIZON_ALT = 15.0
SUN_MAX_ALT = -6.0


# ---------------------------------------------------------------------------
# Learned profile
# ---------------------------------------------------------------------------

def learn_horizon_profile(samples: Iterable[Sequence[float]], floor_pct: float = 5, bin_deg: float = 15,
                          min_count: int = 50, cap_above_floor: float = 10) -> Dict[str, Any]:
    """
    {"points": [[az_center, alt], ...], "floor_deg": float|None, "sample_count": int}.

    floor = the floor_pct percentile of every sample altitude. A bin with at
    least min_count samples gets that bin's floor_pct percentile, clipped to
    [15, floor + cap_above_floor]; other bins get the floor.
    """
    arr = np.asarray([(float(a) % 360.0, float(h)) for a, h in samples
                      if a is not None and h is not None and math.isfinite(a) and math.isfinite(h)],
                     dtype=float).reshape(-1, 2)
    if arr.shape[0] == 0:
        return {"points": [], "floor_deg": None, "sample_count": 0}

    floor = float(np.percentile(arr[:, 1], floor_pct))
    upper = floor + cap_above_floor
    nbins = max(1, int(round(360.0 / bin_deg)))
    width = 360.0 / nbins
    idx = np.minimum((arr[:, 0] // width).astype(int), nbins - 1)

    points: List[Point] = []
    for b in range(nbins):
        alts = arr[idx == b, 1]
        if alts.size >= min_count:
            alt = float(np.percentile(alts, floor_pct))
            alt = min(max(alt, MIN_HORIZON_ALT), max(upper, MIN_HORIZON_ALT))
        else:
            alt = floor
        points.append([round(b * width + width / 2.0, 2), round(alt, 1)])
    return {"points": points, "floor_deg": round(floor, 1), "sample_count": int(arr.shape[0])}


def learn_horizon(samples: Iterable[Sequence[float]], floor_pct: float = 5, bin_deg: float = 15,
                  min_count: int = 50, cap_above_floor: float = 10) -> List[Point]:
    """Points only; see learn_horizon_profile."""
    return learn_horizon_profile(samples, floor_pct, bin_deg, min_count, cap_above_floor)["points"]


# ---------------------------------------------------------------------------
# Samples from frames
# ---------------------------------------------------------------------------

def julian_date(dts: Sequence[datetime]) -> np.ndarray:
    """Julian dates for naive-UTC datetimes."""
    epoch = datetime(2000, 1, 1, 12, 0, 0)
    return np.asarray([2451545.0 + (dt - epoch).total_seconds() / 86400.0 for dt in dts], dtype=float)


def _gmst_deg(jd: np.ndarray) -> np.ndarray:
    d = jd - 2451545.0
    return np.mod(280.46061837 + 360.98564736629 * d, 360.0)


def alt_az(ra_deg: np.ndarray, dec_deg: np.ndarray, jd: np.ndarray,
           lat_deg: float, lon_deg: float) -> Tuple[np.ndarray, np.ndarray]:
    """(alt, az) in degrees; az from north through east. lon east-positive."""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    lat = math.radians(lat_deg)
    ha = np.radians(np.mod(_gmst_deg(jd) + lon_deg, 360.0)) - ra
    sin_alt = np.sin(dec) * math.sin(lat) + np.cos(dec) * math.cos(lat) * np.cos(ha)
    alt = np.arcsin(np.clip(sin_alt, -1.0, 1.0))
    y = -np.sin(ha) * np.cos(dec)
    x = np.sin(dec) * math.cos(lat) - np.cos(dec) * math.sin(lat) * np.cos(ha)
    az = np.mod(np.degrees(np.arctan2(y, x)), 360.0)
    return np.degrees(alt), az


def sun_radec(jd: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Low-precision (~0.01 deg) solar RA/Dec (Astronomical Almanac approximation)."""
    n = jd - 2451545.0
    L = np.mod(280.460 + 0.9856474 * n, 360.0)
    g = np.radians(np.mod(357.528 + 0.9856003 * n, 360.0))
    lam = np.radians(L + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g))
    eps = np.radians(23.439 - 0.0000004 * n)
    ra = np.degrees(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam)))
    dec = np.degrees(np.arcsin(np.sin(eps) * np.sin(lam)))
    return np.mod(ra, 360.0), dec


def sun_altitude(jd: np.ndarray, lat_deg: float, lon_deg: float) -> np.ndarray:
    ra, dec = sun_radec(jd)
    alt, _ = alt_az(ra, dec, jd, lat_deg, lon_deg)
    return alt


def _float(value: Any) -> Optional[float]:
    value = unchar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ra_from_header(value: Any) -> Optional[float]:
    """OBJCTRA is hours ("05 35 17.3" or 5.588); returns degrees."""
    hours = parse_sexagesimal(value)
    if hours is None or not (0 <= hours < 24):
        return None
    return hours * 15.0


def horizon_samples(rows: Iterable[Dict[str, Any]], lat: float, lon: float) -> List[Tuple[float, float]]:
    """
    rows: {"utc": datetime, "centalt", "centaz", "ra", "dec", "objctra", "objctdec"}.
    Returns [(az, alt)] for frames taken with the Sun at or below -6 deg.
    Header CENTALT/CENTAZ win; otherwise alt/az is computed from RA/Dec
    (the plate solve, else OBJCTRA/OBJCTDEC).
    """
    utc, alt_h, az_h, ra, dec = [], [], [], [], []
    for r in rows:
        when = r.get("utc")
        if not isinstance(when, datetime):
            continue
        ca, cz = _float(r.get("centalt")), _float(r.get("centaz"))
        rr, dd = r.get("ra"), r.get("dec")
        if rr is None or dd is None:
            rr, dd = _ra_from_header(r.get("objctra")), parse_sexagesimal(r.get("objctdec"))
        has_header = ca is not None and cz is not None and -90 <= ca <= 90
        if not has_header and (rr is None or dd is None):
            continue
        utc.append(when.replace(tzinfo=None))
        alt_h.append(ca if has_header else np.nan)
        az_h.append(cz if has_header else np.nan)
        ra.append(float(rr) if rr is not None else np.nan)
        dec.append(float(dd) if dd is not None else np.nan)
    if not utc:
        return []

    jd = julian_date(utc)
    alt_c, az_c = alt_az(np.asarray(ra), np.asarray(dec), jd, lat, lon)
    alt_h, az_h = np.asarray(alt_h), np.asarray(az_h)
    use_header = ~np.isnan(alt_h)
    alt = np.where(use_header, alt_h, alt_c)
    az = np.where(use_header, np.mod(az_h, 360.0), az_c)
    dark = sun_altitude(jd, lat, lon) <= SUN_MAX_ALT
    keep = dark & ~np.isnan(alt) & ~np.isnan(az)
    return [(float(a), float(h)) for a, h in zip(az[keep], alt[keep])]


# ---------------------------------------------------------------------------
# N.I.N.A. .hrz
# ---------------------------------------------------------------------------

def normalize_points(points: Iterable[Sequence[Any]]) -> List[Point]:
    """Validate, wrap az into [0, 360), clamp alt to [-90, 90], sort by az, drop duplicate azimuths (first wins)."""
    seen = {}
    for p in points:
        if p is None or len(p) < 2:
            raise ValueError("each horizon point must be [az, alt]")
        az, alt = float(p[0]), float(p[1])
        if not (math.isfinite(az) and math.isfinite(alt)):
            raise ValueError("horizon values must be finite numbers")
        az = round(az % 360.0, 4)
        if az not in seen:
            seen[az] = max(-90.0, min(90.0, alt))
    return [[az, seen[az]] for az in sorted(seen)]


def parse_hrz(text: str) -> List[Point]:
    """Parse a N.I.N.A. .hrz file: 'az alt' per line (space, tab or comma separated), '#' comments."""
    points = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.replace(",", " ").replace(";", " ").split()
        if len(parts) < 2:
            raise ValueError(f"line {n}: expected 'azimuth altitude'")
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            raise ValueError(f"line {n}: not a number")
    return normalize_points(points)


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def format_hrz(points: Iterable[Sequence[Any]], title: Optional[str] = None) -> str:
    lines = ["# Horizon profile exported by AstroCat" + (f": {title}" if title else ""),
             "# azimuth altitude (degrees)"]
    for az, alt in normalize_points(points):
        lines.append(f"{_fmt(az)} {_fmt(alt)}")
    return "\n".join(lines) + "\n"


def altitude_at(points: Sequence[Sequence[float]], az: float) -> Optional[float]:
    """Linear interpolation of a horizon profile at azimuth az, wrapping through 360."""
    pts = normalize_points(points) if points else []
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    az = az % 360.0
    for (a0, h0), (a1, h1) in zip(pts, pts[1:]):
        if a0 <= az <= a1:
            return h0 + (h1 - h0) * (az - a0) / (a1 - a0) if a1 != a0 else h0
    # Wrap segment: last point -> first point + 360.
    (a0, h0), (a1, h1) = pts[-1], pts[0]
    a1 += 360.0
    x = az if az >= a0 else az + 360.0
    return h0 + (h1 - h0) * (x - a0) / (a1 - a0)
