"""
Planetary seeing forecast (S1, docs/design/S1-planetary-seeing-forecast.md).

build_forecast() is pure orchestration: weather frames + planet tracks in, the §7.1 payload out. It does no
I/O and keeps no coordinates in the payload (the site is referenced by id only). Fetching and caching live in
services/seeing/service.py.
"""

import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Mapping, Optional

import numpy as np

from app.services.seeing import scoring as sc
from app.services.seeing.planets import PlanetTracks, planet_tracks
from app.services.seeing.privacy import quiet_http_loggers
from app.services.seeing.sources import HourlyFrame

quiet_http_loggers()

SCORING_VERSION = sc.SCORING_VERSION

# Variables read from a model frame for each hour (Open-Meteo names).
_FRAME_VARS = (
    "wind_speed_10m", "wind_gusts_10m", "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "boundary_layer_height",
    "wind_speed_200hPa", "wind_speed_250hPa", "wind_speed_300hPa", "wind_speed_500hPa", "wind_speed_700hPa",
    "wind_speed_850hPa", "wind_direction_500hPa", "wind_direction_700hPa", "wind_direction_850hPa",
    "wind_direction_300hPa", "wind_direction_250hPa",
    "temperature_250hPa", "temperature_300hPa", "temperature_500hPa", "temperature_700hPa", "temperature_850hPa",
)
_MB_VARS = ("seeing_arcsec", "seeing_index1", "jet_stream")


def _num(x, nd: int = 3) -> Optional[float]:
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, nd)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat() + "Z"


def _to_steps(hour_times: List[datetime], series: List[Optional[float]], step_times: List[datetime],
              default: Optional[float] = None) -> np.ndarray:
    """Linearly interpolate an hourly series (None = gap) onto the 15-minute grid (nan when no data and no default)."""
    pts = [(t.timestamp(), v) for t, v in zip(hour_times, series) if v is not None]
    if not pts:
        return np.full(len(step_times), np.nan if default is None else default)
    xs, ys = zip(*pts)
    return np.interp([t.timestamp() for t in step_times], xs, ys)


def _nanmean(a: np.ndarray) -> Optional[float]:
    a = a[~np.isnan(a)]
    return float(a.mean()) if a.size else None


def _nanmax(a: np.ndarray) -> Optional[float]:
    a = a[~np.isnan(a)]
    return float(a.max()) if a.size else None


def _nanmin(a: np.ndarray) -> Optional[float]:
    a = a[~np.isnan(a)]
    return float(a.min()) if a.size else None


def _val(a: np.ndarray, i: int) -> Optional[float]:
    return None if math.isnan(a[i]) else float(a[i])


def build_forecast(site, night: date, frames: Mapping[str, HourlyFrame], primary: str, *,
                   now: datetime, mb: Optional[HourlyFrame] = None, sources: Optional[list] = None,
                   horizon_points=(), fetched_at: Optional[datetime] = None, stale: bool = False,
                   tracks: Optional[PlanetTracks] = None, min_window_min: int = sc.MIN_WINDOW_MIN) -> dict:
    """The design §7.1 payload for one site and night. `site` is read for its coordinates in memory only."""
    base = {"night": night.isoformat(), "site": {"id": site.id, "timezone": site.timezone},
            "scoring_version": SCORING_VERSION, "fetched_at": _iso(fetched_at) if fetched_at else None,
            "stale": bool(stale), "sources": list(sources or [])}
    if not frames:
        return {**base, "available": False, "reason": "no_data"}
    if primary not in frames:
        primary = next(iter(frames))

    tr = tracks or planet_tracks(site.latitude, site.longitude, night, horizon_points)
    step_times = tr.times
    step_min = tr.step_min
    t0, t1 = step_times[0], step_times[-1] + timedelta(minutes=step_min)

    axis = next(iter(frames.values())).times
    hours = [t for t in axis if t0 - timedelta(hours=1) <= t <= t1 + timedelta(hours=1)]
    in_night = [t for t in hours if t0 <= t < t1]
    if len(in_night) < 6:
        return {**base, "available": False, "reason": "out_of_range"}

    # ---- per hour, per model: factors and atmosphere score --------------------------------------------
    names = list(frames)
    A_h: List[Optional[float]] = []
    conf_h: List[float] = []
    clear_h: List[Optional[float]] = []
    factor_h: List[Dict[str, tuple]] = []
    per_model_h: List[Dict[str, Optional[float]]] = []
    raw_h: Dict[str, List[Optional[float]]] = {k: [] for k in ("rh", "blh", "gust", "dew_spread")}

    for t in hours:
        mbv = {k: mb.at(k, t) for k in _MB_VARS} if mb is not None else None
        per_model: Dict[str, Optional[float]] = {}
        facs: Dict[str, Dict[str, tuple]] = {}
        clears: Dict[str, Optional[float]] = {}
        raws: Dict[str, Dict[str, Optional[float]]] = {k: {} for k in raw_h}
        for m in names:
            f = frames[m]
            v = {k: f.at(k, t) for k in _FRAME_VARS}
            facs[m] = sc.hour_factors(v, mbv)
            per_model[m] = sc.atmosphere_score(facs[m])
            has_cloud = v["cloud_cover_low"] is not None or v["cloud_cover"] is not None
            clears[m] = sc.clear_fraction(v["cloud_cover_low"], v["cloud_cover"]) if has_cloud else None
            raws["rh"][m] = v["relative_humidity_2m"]
            raws["blh"][m] = v["boundary_layer_height"]
            raws["gust"][m] = v["wind_gusts_10m"]
            t2, td = v["temperature_2m"], v["dew_point_2m"]
            raws["dew_spread"][m] = None if t2 is None or td is None else t2 - td
        lead_h = (t - now).total_seconds() / 3600.0
        a, conf = sc.combine(per_model, primary, lead_h)
        A_h.append(a)
        conf_h.append(conf)
        clear_h.append(sc.blend(clears, primary))
        per_model_h.append(per_model)
        blended = {}
        for fname in sc.WEIGHTS:
            blended[fname] = (sc.blend({m: facs[m][fname][0] for m in names}, primary),
                              sc.blend({m: facs[m][fname][1] for m in names}, primary))
        factor_h.append(blended)
        for k in raw_h:
            raw_h[k].append(sc.blend(raws[k], primary))

    # ---- onto the 15-minute grid ---------------------------------------------------------------------
    A15 = _to_steps(hours, A_h, step_times, default=0.0)
    conf15 = _to_steps(hours, conf_h, step_times, default=0.0)
    clear15 = _to_steps(hours, clear_h, step_times, default=1.0)
    series = {name: _to_steps(hours, [fh[name][0] for fh in factor_h], step_times)
              for name in ("surface_wind", "stability", "upper_cloud", "seeing_index", "jet")}
    rh15 = _to_steps(hours, raw_h["rh"], step_times)
    blh15 = _to_steps(hours, raw_h["blh"], step_times)
    gust15 = _to_steps(hours, raw_h["gust"], step_times)
    spread15 = _to_steps(hours, raw_h["dew_spread"], step_times)

    # ---- bodies ----------------------------------------------------------------------------------
    body_rows = []
    for name, tk in tr.bodies.items():
        visible = tk.allowed & (tk.alt >= np.maximum(tk.limit, sc.ALT_FLOOR_DEG))
        P = sc.body_scores(A15, clear15, tk.alt, tk.limit, tk.allowed)
        win = sc.best_window(P, step_min, min_window_min)
        cloud_blocked = bool(visible.any() and np.all(clear15[visible] < sc.CLEAR_GATE))
        if win is None and not visible.any():
            continue
        row = {"name": name, "tk": tk, "P": P, "win": win, "visible": visible, "cloud_blocked": cloud_blocked,
               "score": float(P[win[0]:win[1] + 1].mean()) if win else 0.0}
        body_rows.append(row)
    body_rows.sort(key=lambda r: -r["score"])

    bodies_out = []
    for r in body_rows:
        tk, P, win = r["tk"], r["P"], r["win"]
        window = None
        warnings: List[str] = []
        notes: List[str] = []
        if win:
            i0, i1 = win
            sl = slice(i0, i1 + 1)
            pk = i0 + int(np.argmax(tk.alt[sl]))   # peak = the window's highest point
            window = {"start_utc": _iso(step_times[i0]), "end_utc": _iso(step_times[i1] + timedelta(minutes=step_min)),
                      "peak_utc": _iso(step_times[pk]), "peak_alt": _num(tk.alt[pk], 1), "score": _num(r["score"])}
            warnings = sc.window_warnings(
                rh_max=_nanmax(rh15[sl]), dew_spread_min=_nanmin(spread15[sl]), gust_max=_nanmax(gust15[sl]),
                peak_alt=float(tk.alt[pk]), jet_max=_nanmax(series["jet"][sl]), confidence=float(conf15[sl].mean()))
            if tk.daylight[sl].any():
                notes.append("imaging_in_daylight")
        track = [{"t": _iso(step_times[i]), "alt": _num(tk.alt[i], 1), "score": _num(P[i], 3)}
                 for i in range(len(step_times)) if tk.alt[i] > 0]
        bodies_out.append({"body": r["name"], "diameter_arcsec": tk.diameter_arcsec, "illum": tk.illum,
                           "elongation_deg": tk.elongation_deg, "window": window, "warnings": warnings,
                           "notes": notes, "cloud_blocked": r["cloud_blocked"], "track": track})

    # ---- overall -----------------------------------------------------------------------------------
    best = next((r for r in body_rows if r["win"]), None)
    all_blocked = bool(body_rows) and all(r["cloud_blocked"] for r in body_rows)
    if best:
        i0, i1 = best["win"]
        sl = slice(i0, i1 + 1)
        tk = best["tk"]
        pk = i0 + int(np.argmax(tk.alt[sl]))
        items = sc.checklist(wind=_nanmean(series["surface_wind"][sl]), temp=_nanmean(series["stability"][sl]),
                             rh=_nanmean(rh15[sl]), blh=_nanmean(blh15[sl]),
                             upper_cloud=_nanmean(series["upper_cloud"][sl]),
                             seeing_index=_nanmean(series["seeing_index"][sl]), peak_alt=float(tk.alt[pk]))
        pass_steps = 0
        for i in range(i0, i1 + 1):
            step_items = sc.checklist(wind=_val(series["surface_wind"], i), temp=_val(series["stability"], i),
                                      rh=_val(rh15, i), blh=_val(blh15, i), upper_cloud=_val(series["upper_cloud"], i),
                                      seeing_index=_val(series["seeing_index"], i), peak_alt=float(tk.alt[i]))
            pass_steps += sc.checklist_passes(step_items)
        grade = sc.grade_for(best["score"])
        verdict = sc.verdict_for(grade, pass_steps * step_min, any_body=True, all_cloud_blocked=all_blocked)
        overall = {"grade": grade, "score": _num(best["score"]), "verdict": verdict,
                   "confidence": _num(float(conf15[sl].mean())), "best_body": best["name"],
                   "checklist": [{**i, **{k: _num(v, 2) for k, v in i.items() if isinstance(v, float)}}
                                 for i in items]}
    else:
        verdict = sc.verdict_for("VVP", 0, any_body=bool(body_rows), all_cloud_blocked=all_blocked)
        overall = {"grade": "VVP", "score": 0.0, "verdict": verdict, "confidence": _num(float(conf15.mean())),
                   "best_body": None, "checklist": []}

    # ---- hourly strip (the night only) -------------------------------------------------------------
    hourly = []
    for k, t in enumerate(hours):
        if not (t0 <= t < t1):
            continue
        facs = {}
        for fname, (value, score) in factor_h[k].items():
            facs[fname] = {"value": _num(value, 2), "score": _num(score)}
        facs["stability"]["rh"] = _num(raw_h["rh"][k], 1)
        hourly.append({"t": _iso(t), "score": _num(A_h[k]), "confidence": _num(conf_h[k]),
                       "clear": _num(clear_h[k]), "factors": facs,
                       "per_model": {m: _num(s) for m, s in per_model_h[k].items()}})

    return {**base, "available": True, "overall": overall, "bodies": bodies_out, "hourly": hourly}
