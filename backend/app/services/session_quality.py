"""
Session quality timeline (Q1c, docs/design/Q1-star-quality.md §6, §7.4).

Pure functions over one night's Light subs (plain dicts, ordered by time):
altitude/airmass, inferred events (autofocus, filter/target change, meridian
flip, gap), suspect-sub flags relative to the night's own rig+filter medians,
and per-rig/filter summaries with a focus-drift slope. The API gathers rows.

Point dict keys used here:
  id, t (naive UTC datetime), exposure_s, filter (normalized), target_key,
  rig_id, ra, dec, lat, lon, rotation, status, fwhm_px, hfr_px, ecc, stars,
  scale, hints (dict: FOCPOS, FOCTEMP, PIERSIDE, ...)
"""

import math
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from app.utils.horizon import alt_az, julian_date, sun_altitude

# Suspect-sub flags (§6). Relative to the median of the same night, rig and filter.
SOFT_FACTOR = 1.3          # FWHM above this x median  -> SOFT
CLOUD_FACTOR = 0.5         # star count below this x median -> CLOUD
TRAILED_MIN_ECC = 0.6      # eccentricity above max(this, median + margin) -> TRAILED
TRAILED_MARGIN = 0.15
FLAG_MIN_GROUP = 5         # fewer measured subs in the group -> no flags

GAP_MIN = timedelta(minutes=10)
GAP_EXPOSURES = 3          # a gap is > max(GAP_MIN, this x exposure)
FLIP_CONFIRM = 4           # rotation-based flips need this many subs either side
SLOPE_MIN_POINTS = 6
SLOPE_MIN_HOURS = 0.75     # no drift figure for shorter runs (too noisy)
SLOPE_MAX_POINTS = 400     # Theil-Sen is O(n^2); subsample beyond this


def airmass(alt_deg: Optional[float]) -> Optional[float]:
    """Kasten & Young (1989); None below the horizon."""
    if alt_deg is None or alt_deg <= 0:
        return None
    return round(1.0 / (math.sin(math.radians(alt_deg)) + 0.50572 * (alt_deg + 6.07995) ** -1.6364), 3)


def add_altitudes(points: List[Dict[str, Any]]) -> None:
    """Fill alt_deg/airmass in place for points with a time, pointing and site."""
    # Unsolved subs borrow the median pointing of their target that night.
    by_target: Dict[Any, List[Tuple[float, float]]] = {}
    for p in points:
        if p.get("ra") is not None and p.get("dec") is not None:
            by_target.setdefault(p.get("target_key"), []).append((p["ra"], p["dec"]))
    fallback = {k: (median(r for r, _ in v), median(d for _, d in v)) for k, v in by_target.items()}

    for p in points:
        p["alt_deg"] = p["airmass"] = None
        ra, dec = p.get("ra"), p.get("dec")
        if (ra is None or dec is None) and p.get("target_key") in fallback:
            ra, dec = fallback[p["target_key"]]
        if ra is None or dec is None or p.get("lat") is None or p.get("lon") is None or p.get("t") is None:
            continue
        # Mid-exposure time.
        t = p["t"] + timedelta(seconds=(p.get("exposure_s") or 0) / 2.0)
        alt, _ = alt_az(np.array([ra]), np.array([dec]), julian_date([t]), p["lat"], p["lon"])
        p["alt_deg"] = round(float(alt[0]), 2)
        p["airmass"] = airmass(p["alt_deg"])


def dark_window(t0: datetime, t1: datetime, lat: Optional[float], lon: Optional[float],
                step_min: int = 5) -> Optional[Dict[str, Any]]:
    """
    The astronomical-darkness interval (sun below -18 deg) of the session's
    night, as naive-UTC ISO strings: the dark run overlapping [t0, t1], else
    the nearest one. start/end are None when the night never gets dark
    (high-latitude summer).
    """
    if lat is None or lon is None:
        return None
    start, end = t0 - timedelta(hours=14), t1 + timedelta(hours=14)
    n = max(2, int((end - start).total_seconds() // (step_min * 60)) + 1)
    times = [start + timedelta(minutes=step_min * i) for i in range(n)]
    dark = sun_altitude(julian_date(times), lat, lon) < -18.0
    runs, run_start = [], None
    for i, d in enumerate(dark):
        if d and run_start is None:
            run_start = i
        if (not d or i == n - 1) and run_start is not None:
            runs.append((times[run_start], times[i if d else i - 1]))
            run_start = None
    if not runs:
        return {"start": None, "end": None}

    def distance(run):
        a, b = run
        if b >= t0 and a <= t1:
            return timedelta(0)
        return min(abs(a - t1), abs(b - t0))
    a, b = min(runs, key=distance)
    return {"start": a.isoformat(), "end": b.isoformat()}


def _pierside(p) -> Optional[str]:
    v = (p.get("hints") or {}).get("PIERSIDE")
    return str(v).upper() if v else None


def _rotation_flips(points: List[Dict[str, Any]]) -> set:
    """
    Ids of subs where the rotation says the mount changed pier side, per
    rig+target run. Header solves can report a frame 180 deg apart from its
    neighbours (seen on real data: -87.6 / 92.0 alternating sub to sub), so a
    flip needs FLIP_CONFIRM subs mostly on one side before and mostly on the
    other side after (>= 3/4 each).
    """
    runs: Dict[Tuple, List[Dict[str, Any]]] = {}
    for p in points:
        if p.get("rotation") is not None:
            runs.setdefault((p.get("rig_id"), p.get("target_key")), []).append(p)
    flips = set()
    k = FLIP_CONFIRM
    for members in runs.values():
        ref = members[0]["rotation"]
        side = [1 if abs((m["rotation"] - ref + 180.0) % 360.0 - 180.0) > 90.0 else 0 for m in members]
        i = k
        while i <= len(members) - k:
            before = sum(side[i - k:i]) / k
            after = sum(side[i:i + k]) / k
            if (before <= 0.25 and after >= 0.75) or (before >= 0.75 and after <= 0.25):
                # The flip happens at the first sub on the new side.
                j = i
                new_side = 1 if after >= 0.75 else 0
                while j < i + k and side[j] != new_side:
                    j += 1
                flips.add(members[j]["id"])
                i += k
            else:
                i += 1
    return flips


def infer_events(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Events between consecutive subs of the same rig (points ordered by time)."""
    events: List[Dict[str, Any]] = []
    last: Dict[Any, Dict[str, Any]] = {}
    flips = _rotation_flips(points)
    for p in points:
        prev = last.get(p.get("rig_id"))
        last[p.get("rig_id")] = p
        if prev is None or p.get("t") is None or prev.get("t") is None:
            continue
        base = {"t": p["t"].isoformat(), "rig_id": p.get("rig_id"), "image_id": p["id"]}
        gap = p["t"] - (prev["t"] + timedelta(seconds=prev.get("exposure_s") or 0))
        if gap > max(GAP_MIN, timedelta(seconds=GAP_EXPOSURES * (prev.get("exposure_s") or 0))):
            events.append({**base, "type": "GAP", "detail": f"{int(gap.total_seconds() // 60)} min gap"})
        if p.get("target_key") != prev.get("target_key"):
            events.append({**base, "type": "TARGET_CHANGE", "detail": f"{prev.get('target_key') or '?'} → {p.get('target_key') or '?'}"})
        elif p.get("filter") != prev.get("filter"):
            events.append({**base, "type": "FILTER_CHANGE", "detail": f"{prev.get('filter')} → {p.get('filter')}"})
        f0, f1 = (prev.get("hints") or {}).get("FOCPOS"), (p.get("hints") or {}).get("FOCPOS")
        if f0 is not None and f1 is not None and f0 != f1:
            events.append({**base, "type": "AUTOFOCUS", "detail": f"focuser {int(f0)} → {int(f1)}"})
        s0, s1 = _pierside(prev), _pierside(p)
        if s0 and s1:
            flipped = s0 != s1
        else:
            flipped = p["id"] in flips
        if flipped:
            events.append({**base, "type": "MERIDIAN_FLIP", "detail": "meridian flip"})
    return events


def _group_key(p) -> Tuple[Any, Any]:
    return (p.get("rig_id"), p.get("filter"))


def flag_points(points: List[Dict[str, Any]], soft: float = SOFT_FACTOR, cloud: float = CLOUD_FACTOR,
                trailed: float = TRAILED_MIN_ECC) -> None:
    """Set p['flag'] (SOFT | CLOUD | TRAILED | None) against the night's rig+filter medians."""
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}
    for p in points:
        p["flag"] = None
        if p.get("status") == "OK" and p.get("fwhm_px") is not None:
            groups.setdefault(_group_key(p), []).append(p)
    for members in groups.values():
        if len(members) < FLAG_MIN_GROUP:
            continue
        med_fwhm = median(m["fwhm_px"] for m in members)
        stars = [m["stars"] for m in members if m.get("stars") is not None]
        med_stars = median(stars) if stars else None
        eccs = [m["ecc"] for m in members if m.get("ecc") is not None]
        ecc_limit = max(trailed, median(eccs) + TRAILED_MARGIN) if eccs else None
        for m in members:
            if med_stars and m.get("stars") is not None and m["stars"] < cloud * med_stars:
                m["flag"] = "CLOUD"
            elif m["fwhm_px"] > soft * med_fwhm:
                m["flag"] = "SOFT"
            elif ecc_limit is not None and m.get("ecc") is not None and m["ecc"] > ecc_limit:
                m["flag"] = "TRAILED"


def theil_sen_slope(xs: List[float], ys: List[float]) -> Optional[float]:
    """Median pairwise slope: robust to the odd bad sub."""
    n = len(xs)
    if n < SLOPE_MIN_POINTS:
        return None
    if n > SLOPE_MAX_POINTS:
        idx = np.linspace(0, n - 1, SLOPE_MAX_POINTS).astype(int)
        xs, ys, n = [xs[i] for i in idx], [ys[i] for i in idx], SLOPE_MAX_POINTS
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    i, j = np.triu_indices(n, k=1)
    dx = x[j] - x[i]
    ok = dx > 0
    if not np.any(ok):
        return None
    return float(np.median((y[j] - y[i])[ok] / dx[ok]))


def _pct(values: List[float], q: float) -> Optional[float]:
    return round(float(np.percentile(values, q)), 3) if values else None


def summarize(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per rig+filter: counts, median/best/worst FWHM (px and arcsec) and drift per hour."""
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}
    for p in points:
        groups.setdefault(_group_key(p), []).append(p)
    out = []
    for (rig_id, filt), members in groups.items():
        measured = [m for m in members if m.get("status") == "OK" and m.get("fwhm_px") is not None]
        px = [m["fwhm_px"] for m in measured]
        arc = [m["fwhm_px"] * m["scale"] for m in measured if m.get("scale")]
        slope_px = slope_arc = None
        timed = [m for m in measured if m.get("t") is not None]
        if timed and (timed[-1]["t"] - timed[0]["t"]).total_seconds() >= SLOPE_MIN_HOURS * 3600:
            t0 = timed[0]["t"]
            hours = [(m["t"] - t0).total_seconds() / 3600.0 for m in timed]
            slope_px = theil_sen_slope(hours, [m["fwhm_px"] for m in timed])
            with_scale = [(h, m["fwhm_px"] * m["scale"]) for h, m in zip(hours, timed) if m.get("scale")]
            if with_scale:
                slope_arc = theil_sen_slope([h for h, _ in with_scale], [v for _, v in with_scale])
        out.append({
            "rig_id": rig_id,
            "filter": filt,
            "subs": len(members),
            "measured": len(measured),
            "flagged": sum(1 for m in members if m.get("flag")),
            "median_fwhm_px": _pct(px, 50), "best_fwhm_px": _pct(px, 10), "worst_fwhm_px": _pct(px, 90),
            "median_fwhm_arcsec": _pct(arc, 50), "best_fwhm_arcsec": _pct(arc, 10), "worst_fwhm_arcsec": _pct(arc, 90),
            "drift_px_per_hour": round(slope_px, 3) if slope_px is not None else None,
            "drift_arcsec_per_hour": round(slope_arc, 3) if slope_arc is not None else None,
        })
    return out


def _r(v, digits=3):
    return None if v is None else round(float(v), digits)


def serialize_point(p: Dict[str, Any]) -> Dict[str, Any]:
    scale = p.get("scale")
    measured = p.get("status") == "OK"
    fwhm = p.get("fwhm_px") if measured else None
    hfr = p.get("hfr_px") if measured else None
    hints = p.get("hints") or {}
    return {
        "image_id": p["id"],
        "t": p["t"].isoformat() if p.get("t") else None,
        "time_is_utc": p.get("time_is_utc", True),
        "exposure_s": p.get("exposure_s"),
        "filter": p.get("filter"),
        "target_key": p.get("target_key"),
        "rig_id": p.get("rig_id"),
        "status": p.get("status"),
        "fwhm_px": _r(fwhm), "hfr_px": _r(hfr),
        "fwhm_arcsec": _r(fwhm * scale) if fwhm is not None and scale else None,
        "hfr_arcsec": _r(hfr * scale) if hfr is not None and scale else None,
        "eccentricity": _r(p.get("ecc")) if measured else None,
        "star_count": p.get("stars") if measured else None,
        "bkg_adu": _r(p.get("bkg"), 1) if measured else None,
        "alt_deg": p.get("alt_deg"),
        "airmass": p.get("airmass"),
        "focuser_pos": hints.get("FOCPOS"),
        "focuser_temp": hints.get("FOCTEMP"),
        "guide_rms": hints.get("GUIDE_RMS"),
        "flag": p.get("flag"),
    }


def build_timeline(points: List[Dict[str, Any]], night: str) -> Dict[str, Any]:
    """Everything the timeline chart needs for one night (points ordered by time)."""
    add_altitudes(points)
    flag_points(points)
    events = infer_events(points)
    timed = [p for p in points if p.get("t") is not None]
    site = next(((p["lat"], p["lon"]) for p in points if p.get("lat") is not None and p.get("lon") is not None), (None, None))
    dark = dark_window(timed[0]["t"], timed[-1]["t"], *site) if timed else None
    return {
        "night": night,
        "points": [serialize_point(p) for p in points],
        "events": events,
        "summary": summarize(points),
        "dark": dark,
        "start": timed[0]["t"].isoformat() if timed else None,
        "end": timed[-1]["t"].isoformat() if timed else None,
    }
