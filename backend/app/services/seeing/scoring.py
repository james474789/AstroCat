"""
Planetary seeing scoring (design §2, §5). PURE: no I/O, no clocks, no coordinates.

Every threshold and weight is a constant below; each comment cites the design §2 evidence row (or §5 rule) it
comes from, so a later calibration (§9) has a clear trail. Bump SCORING_VERSION whenever any of them change
(it is part of the view-cache key).
"""

import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

SCORING_VERSION = 1

Knots = Sequence[Tuple[float, float]]

# ---------------------------------------------------------------------------
# §5.1 factor curves: piecewise-linear (input -> 0..1), clamped outside the first/last knot
# ---------------------------------------------------------------------------

# §2 row "Surface wind (10 m)": <=1 m/s best (50% hit rate), >=3 m/s worst (10%).
SURFACE_WIND_KNOTS: Knots = ((0, 1.0), (1, 1.0), (3, 0.35), (5, 0.1), (8, 0.0))
GUST_WEIGHT = 0.5                      # effective wind = max(wind, GUST_WEIGHT * gust)   (§5.1)

# §2 row "meteoblue seeing index 1": >=4 best (47%), <4 worst (20%). Index 1 -> 0 is an addition to the
# design's knots (which start at 2) so the lowest index is not scored like index 2.
SEEING_INDEX_KNOTS: Knots = ((1, 0.0), (2, 0.15), (3, 0.35), (4, 0.85), (5, 1.0))

# §2 rows "Ground temperature" (<=1 C best 48%, >5 C 0%) and "Relative humidity" (>=90% best 50%, <80% 7%).
STABILITY_TEMP_KNOTS: Knots = ((1, 1.0), (5, 0.4), (10, 0.1))
STABILITY_RH_KNOTS: Knots = ((70, 0.2), (80, 0.55), (90, 1.0))
STABILITY_BLH_BONUS = 0.1              # §2.1 stable surface layer: low boundary layer
STABILITY_BLH_MAX_M = 300.0

# §2 row "High cloud" (0% best 34%, >20% 14%); §2.1 thin high cloud marks a jet/front.
UPPER_CLOUD_KNOTS: Knots = ((0, 1.0), (20, 0.55), (50, 0.2), (80, 0.0))

# §2 rows "Upper wind 250/300 hPa" (<20 m/s best 40%) and "meteoblue jet stream" (<20 m/s best 43%).
JET_KNOTS: Knots = ((15, 1.0), (20, 0.85), (35, 0.55), (50, 0.3), (70, 0.1))
JET_LEVELS_HPA = (200, 250, 300)

# §2.1 wind shear matters more than jet speed: |850 hPa wind - 10 m wind|.
GROUND_SHEAR_KNOTS: Knots = ((5, 1.0), (10, 0.6), (20, 0.2))

# §5.1 weights. arcsec: §2 "no useful signal" -> 0. computed_seeing: experimental -> 0 until §9 calibration.
WEIGHTS: Dict[str, float] = {
    "surface_wind": 0.25,
    "seeing_index": 0.20,   # drops out (None) without meteoblue, weights renormalize
    "stability": 0.15,
    "upper_cloud": 0.15,
    "jet": 0.15,
    "ground_shear": 0.10,
    "arcsec": 0.0,
    "computed_seeing": 0.0,
}

# §5.1 clear-sky gate: a planet hour with clear < CLEAR_GATE is "Cloud" whatever its seeing.
CLEAR_GATE = 0.3

# §5.3 combining models and lead time.
PRIMARY_WEIGHT = 0.6
OTHERS_WEIGHT = 0.4
SPREAD_FULL_DISAGREE = 0.5
LEAD_FACTORS: Tuple[Tuple[float, float], ...] = ((24.0, 1.0), (48.0, 0.85), (72.0, 0.7))
LEAD_FACTOR_BEYOND = 0.5

# §5.4 per-planet windows.
ALT_KNOTS: Knots = ((15, 0.0), (20, 0.25), (30, 0.6), (40, 0.85), (50, 1.0))   # Jupiter-vs-Saturn finding
ALT_FLOOR_DEG = 15.0                    # horizon floor for planets: max(horizon(az), 15)
SUN_LIMIT_DEG = -6.0                    # §3.4: imaging allowed with the Sun below this
MIN_WINDOW_MIN = 45                     # §5.4 (configurable via the function argument)
DROP_WINDOW_MIN = 15                    # windows shorter than this are left out
WINDOW_FRACTION_OF_PEAK = 0.5

# §5.4 warnings.
DEW_RH = 95.0
DEW_SPREAD_C = 1.0
GUSTY_MS = 6.0
LOW_DISPERSION_ALT = 30.0
JET_OVERHEAD_MS = 35.0
DISAGREE_CONFIDENCE = 0.5

# §5.5 overall rating on the user's grade scale; the cut points are refit in §9.
GRADE_CUTS: Tuple[Tuple[float, str], ...] = ((0.15, "VVP"), (0.3, "VP"), (0.45, "P"), (0.6, "A"), (0.75, "G"))
GRADE_TOP = "VG"
GRADE_ORDER = ["VVP", "VP", "P", "A", "G", "VG"]
GO_GRADE_MIN = "A"
MAYBE_GRADE_MIN = "P"
GO_CHECKLIST_MIN_MIN = 60               # §5.5: checklist passes for at least 1 h of the best window

# §5.5 go checklist (§2 log rule, with forecast slack).
CHECK_WIND_MAX = 1.5
CHECK_TEMP_MAX = 3.0
CHECK_RH_MIN = 85.0
CHECK_BLH_MAX = 300.0
CHECK_UPPER_CLOUD_MAX = 20.0
CHECK_SEEING_INDEX_MIN = 4.0
CHECK_PLANET_ALT_MIN = 35.0             # degrees (design says "35%"; read as degrees, see S1 notes)

VERDICT_GO, VERDICT_MAYBE, VERDICT_NO_GO, VERDICT_CLOUD = "GO", "MAYBE", "NO_GO", "CLOUD"


# ---------------------------------------------------------------------------
# curves
# ---------------------------------------------------------------------------

def interp_knots(x: Optional[float], knots: Knots) -> Optional[float]:
    """Piecewise-linear value at x, clamped to the end knots; None in -> None out."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    return float(np.interp(float(x), xs, ys))


def _clean(v) -> Optional[float]:
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else v


def surface_wind_speed(wind: Optional[float], gust: Optional[float]) -> Optional[float]:
    wind, gust = _clean(wind), _clean(gust)
    if wind is None:
        return None
    return max(wind, GUST_WEIGHT * gust) if gust is not None else wind


def f_surface_wind(wind: Optional[float], gust: Optional[float] = None) -> Optional[float]:
    return interp_knots(surface_wind_speed(wind, gust), SURFACE_WIND_KNOTS)


def f_seeing_index(index: Optional[float]) -> Optional[float]:
    return interp_knots(index, SEEING_INDEX_KNOTS)


def f_stability(temp: Optional[float], rh: Optional[float], blh: Optional[float] = None) -> Optional[float]:
    parts = [p for p in (interp_knots(temp, STABILITY_TEMP_KNOTS), interp_knots(rh, STABILITY_RH_KNOTS))
             if p is not None]
    if not parts:
        return None
    score = sum(parts) / len(parts)
    blh = _clean(blh)
    if blh is not None and blh <= STABILITY_BLH_MAX_M:
        score += STABILITY_BLH_BONUS
    return min(1.0, score)


def upper_cloud_pct(mid: Optional[float], high: Optional[float]) -> Optional[float]:
    vals = [v for v in (_clean(mid), _clean(high)) if v is not None]
    return max(vals) if vals else None


def f_upper_cloud(mid: Optional[float], high: Optional[float]) -> Optional[float]:
    return interp_knots(upper_cloud_pct(mid, high), UPPER_CLOUD_KNOTS)


def f_jet(speed: Optional[float]) -> Optional[float]:
    return interp_knots(speed, JET_KNOTS)


def f_ground_shear(wind_850: Optional[float], wind_10m: Optional[float]) -> Optional[float]:
    w850, w10 = _clean(wind_850), _clean(wind_10m)
    if w850 is None or w10 is None:
        return None
    return interp_knots(abs(w850 - w10), GROUND_SHEAR_KNOTS)


def clear_fraction(low: Optional[float], total: Optional[float]) -> float:
    """§5.1 gate input: 1 - max(low, total)/100 in 0..1 (1.0 when cloud is unknown)."""
    vals = [v for v in (_clean(low), _clean(total)) if v is not None]
    if not vals:
        return 1.0
    return min(1.0, max(0.0, 1.0 - max(vals) / 100.0))


# ---------------------------------------------------------------------------
# §5.2 experimental computed seeing (weight 0 until calibrated)
# ---------------------------------------------------------------------------

_G = 9.80665
_RD = 287.05
COMPUTED_SEEING_BASE = 0.5      # arcsec floor
COMPUTED_SEEING_K = 3.0         # arcsec per unit S**0.6; uncalibrated, hence "experimental"
RI_CRITICAL = 0.25
_LEVEL_PAIRS = ((850, 700), (700, 500), (500, 300), (300, 250))


def _uv(speed: float, direction_deg: float) -> Tuple[float, float]:
    rad = math.radians(direction_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def computed_seeing(levels: Mapping[int, Mapping[str, Optional[float]]]) -> Optional[float]:
    """
    Experimental FWHM estimate (arcsec) from level winds/temperatures.

    levels: {hPa: {"speed": m/s, "dir": deg, "temp": C}}. For each adjacent pair the bulk Richardson number
    Ri = (g/theta) dtheta/dz / (dU/dz)^2 is formed (thickness from the hypsometric equation); a layer with
    Ri < 0.25 is turbulent with Cn2 weight ~ shear^2 * thickness. S = sum of weights; FWHM = base + k * S^0.6
    (FWHM ~ (integral Cn2)^(3/5)). Returns None when no layer pair has complete data.
    """
    s_total, used = 0.0, 0
    for p_lo, p_hi in _LEVEL_PAIRS:           # p_lo is the lower (higher-pressure) level
        a, b = levels.get(p_lo), levels.get(p_hi)
        if not a or not b:
            continue
        vals = [_clean(a.get(k)) for k in ("speed", "dir", "temp")] + [_clean(b.get(k)) for k in ("speed", "dir", "temp")]
        if any(v is None for v in vals):
            continue
        sa, da, ta, sb, db, tb = vals
        t_mean = (ta + tb) / 2.0 + 273.15
        dz = _RD * t_mean / _G * math.log(p_lo / p_hi)
        if dz <= 0:
            continue
        used += 1
        theta_a = (ta + 273.15) * (1000.0 / p_lo) ** 0.2854
        theta_b = (tb + 273.15) * (1000.0 / p_hi) ** 0.2854
        ua, va = _uv(sa, da)
        ub, vb = _uv(sb, db)
        shear2 = ((ub - ua) / dz) ** 2 + ((vb - va) / dz) ** 2
        if shear2 <= 0:
            continue
        theta_mean = (theta_a + theta_b) / 2.0
        ri = (_G / theta_mean) * ((theta_b - theta_a) / dz) / shear2
        if ri < RI_CRITICAL:
            s_total += shear2 * dz
    if used == 0:
        return None
    return COMPUTED_SEEING_BASE + COMPUTED_SEEING_K * s_total ** 0.6


# ---------------------------------------------------------------------------
# §5.3 hourly combination
# ---------------------------------------------------------------------------

def hour_factors(v: Mapping[str, Optional[float]], mb: Optional[Mapping[str, Optional[float]]] = None
                 ) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    {factor: (display value, score or None)} for one model-hour. `v` has Open-Meteo variable names, `mb` the
    meteoblue internal names (seeing_index1, jet_stream, seeing_arcsec). With meteoblue present its index and
    jet stream replace/augment the model's (§5.3 item 3).
    """
    mb = mb or {}
    wind, gust = v.get("wind_speed_10m"), v.get("wind_gusts_10m")
    eff_wind = surface_wind_speed(wind, gust)

    jet_model = [x for x in (_clean(v.get(f"wind_speed_{p}hPa")) for p in JET_LEVELS_HPA) if x is not None]
    jet_val = _clean(mb.get("jet_stream"))
    if jet_val is None and jet_model:
        jet_val = max(jet_model)

    mid, high = v.get("cloud_cover_mid"), v.get("cloud_cover_high")
    idx = _clean(mb.get("seeing_index1"))
    w850 = v.get("wind_speed_850hPa")

    levels = {p: {"speed": v.get(f"wind_speed_{p}hPa"), "dir": v.get(f"wind_direction_{p}hPa"),
                  "temp": v.get(f"temperature_{p}hPa")} for p in (850, 700, 500, 300, 250)}

    return {
        "surface_wind": (eff_wind, f_surface_wind(wind, gust)),
        "seeing_index": (idx, f_seeing_index(idx)),
        "stability": (_clean(v.get("temperature_2m")),
                      f_stability(v.get("temperature_2m"), v.get("relative_humidity_2m"),
                                  v.get("boundary_layer_height"))),
        "upper_cloud": (upper_cloud_pct(mid, high), f_upper_cloud(mid, high)),
        "jet": (jet_val, f_jet(jet_val)),
        "ground_shear": (None if _clean(w850) is None or _clean(wind) is None else abs(float(w850) - float(wind)),
                         f_ground_shear(w850, wind)),
        "arcsec": (_clean(mb.get("seeing_arcsec")), None),
        "computed_seeing": (computed_seeing(levels), None),
    }


def atmosphere_score(factors: Mapping[str, Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    """§5.3 item 1: sum(w*f)/sum(w) over the factors that are not None (weights renormalize)."""
    num = den = 0.0
    for name, (_, score) in factors.items():
        w = WEIGHTS.get(name, 0.0)
        if score is None or w <= 0:
            continue
        num += w * score
        den += w
    return num / den if den > 0 else None


def lead_factor(lead_h: float) -> float:
    for limit, factor in LEAD_FACTORS:
        if lead_h <= limit:
            return factor
    return LEAD_FACTOR_BEYOND


def blend(values: Mapping[str, Optional[float]], primary: str) -> Optional[float]:
    """Primary x 0.6 + mean(others) x 0.4 (§5.3 item 2); degrades to whichever side has data."""
    p = values.get(primary)
    others = [x for k, x in values.items() if k != primary and x is not None]
    o = sum(others) / len(others) if others else None
    if p is not None and o is not None:
        return PRIMARY_WEIGHT * p + OTHERS_WEIGHT * o
    return p if p is not None else o


def combine(per_model: Mapping[str, Optional[float]], primary: str, lead_h: float = 0.0
            ) -> Tuple[Optional[float], float]:
    """(A(t), confidence). Confidence = 1 - spread/0.5 clamped, times the lead-time factor."""
    a = blend(per_model, primary)
    vals = [x for x in per_model.values() if x is not None]
    if a is None:
        return None, 0.0
    spread = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    conf = min(1.0, max(0.0, 1.0 - spread / SPREAD_FULL_DISAGREE))
    return a, conf * lead_factor(lead_h)


# ---------------------------------------------------------------------------
# §5.4 per-planet windows
# ---------------------------------------------------------------------------

def altitude_factor(alt: np.ndarray, limit: np.ndarray) -> np.ndarray:
    """0 below the (horizon-aware) limit, else the ALT_KNOTS curve."""
    alt = np.asarray(alt, dtype=float)
    f = np.interp(alt, [k[0] for k in ALT_KNOTS], [k[1] for k in ALT_KNOTS])
    return np.where(alt >= np.maximum(np.asarray(limit, dtype=float), ALT_FLOOR_DEG), f, 0.0)


def body_scores(a: np.ndarray, clear: np.ndarray, alt: np.ndarray, limit: np.ndarray,
                allowed: np.ndarray) -> np.ndarray:
    """P_body(t) = A * alt_f * clear, zero outside the allowed (dark enough) hours and under the cloud gate."""
    p = np.asarray(a, dtype=float) * altitude_factor(alt, limit) * np.asarray(clear, dtype=float)
    p = np.where(np.asarray(clear) >= CLEAR_GATE, p, 0.0)
    return np.where(allowed, p, 0.0)


def best_window(p: np.ndarray, step_min: int, min_len_min: int = MIN_WINDOW_MIN
                ) -> Optional[Tuple[int, int]]:
    """
    Inclusive (i0, i1) of the contiguous span with the highest mean P among spans where P >= 0.5 x peak and
    P > 0, at least min_len_min long. If none is that long, the best span of at least DROP_WINDOW_MIN; shorter
    than that -> None.
    """
    p = np.asarray(p, dtype=float)
    peak = float(p.max()) if p.size else 0.0
    if peak <= 0:
        return None
    ok = (p >= WINDOW_FRACTION_OF_PEAK * peak) & (p > 0)
    spans, start = [], None
    for i, flag in enumerate(ok):
        if flag and start is None:
            start = i
        if not flag and start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(ok) - 1))

    def length(s): return (s[1] - s[0] + 1) * step_min

    for floor in (min_len_min, DROP_WINDOW_MIN):
        cands = [s for s in spans if length(s) >= floor]
        if cands:
            return max(cands, key=lambda s: float(p[s[0]:s[1] + 1].mean()))
    return None


# ---------------------------------------------------------------------------
# §5.4 warnings, §5.5 grade / checklist / verdict
# ---------------------------------------------------------------------------

def window_warnings(*, rh_max: Optional[float], dew_spread_min: Optional[float], gust_max: Optional[float],
                    peak_alt: Optional[float], jet_max: Optional[float],
                    confidence: Optional[float]) -> List[str]:
    out = []
    if (rh_max is not None and rh_max >= DEW_RH) or (dew_spread_min is not None and dew_spread_min <= DEW_SPREAD_C):
        out.append("dew_risk")
    if gust_max is not None and gust_max >= GUSTY_MS:
        out.append("gusty")
    if peak_alt is not None and peak_alt < LOW_DISPERSION_ALT:
        out.append("low_dispersion")
    if jet_max is not None and jet_max > JET_OVERHEAD_MS:
        out.append("jet_overhead")
    if confidence is not None and confidence < DISAGREE_CONFIDENCE:
        out.append("models_disagree")
    return out


def grade_for(score: Optional[float]) -> str:
    s = 0.0 if score is None else score
    for cut, grade in GRADE_CUTS:
        if s < cut:
            return grade
    return GRADE_TOP


def grade_at_least(grade: str, minimum: str) -> bool:
    return GRADE_ORDER.index(grade) >= GRADE_ORDER.index(minimum)


def checklist(*, wind: Optional[float], temp: Optional[float], rh: Optional[float], blh: Optional[float],
              upper_cloud: Optional[float], seeing_index: Optional[float], peak_alt: Optional[float]
              ) -> List[dict]:
    """
    §5.5 go checklist. `pass` is True/False, or None when the input is unavailable (meteoblue index without a
    key). The temperature/humidity item passes on (T <= 3 and RH >= 85) or BLH <= 300 m.
    """
    def lt(v, lim): return None if v is None else v <= lim
    def ge(v, lim): return None if v is None else v >= lim

    if temp is None or rh is None:
        th = None if blh is None else (True if blh <= CHECK_BLH_MAX else None)
    else:
        th = (temp <= CHECK_TEMP_MAX and rh >= CHECK_RH_MIN) or (blh is not None and blh <= CHECK_BLH_MAX)
    return [
        {"key": "surface_wind", "pass": lt(wind, CHECK_WIND_MAX), "value": wind},
        {"key": "temp_humidity", "pass": th, "value": temp, "rh": rh, "blh": blh},
        {"key": "upper_cloud", "pass": lt(upper_cloud, CHECK_UPPER_CLOUD_MAX), "value": upper_cloud},
        {"key": "seeing_index", "pass": ge(seeing_index, CHECK_SEEING_INDEX_MIN), "value": seeing_index},
        {"key": "planet_altitude", "pass": ge(peak_alt, CHECK_PLANET_ALT_MIN), "value": peak_alt},
    ]


def checklist_passes(items: Sequence[dict]) -> bool:
    """Passes when no item is False (an unavailable item, `pass` None, does not fail it)."""
    return all(i["pass"] is not False for i in items)


def verdict_for(grade: str, checklist_pass_min: float, *, any_body: bool, all_cloud_blocked: bool) -> str:
    if any_body and all_cloud_blocked:
        return VERDICT_CLOUD
    checklist_ok = checklist_pass_min >= GO_CHECKLIST_MIN_MIN
    if grade_at_least(grade, GO_GRADE_MIN) and checklist_ok:
        return VERDICT_GO
    if grade_at_least(grade, MAYBE_GRADE_MIN) or checklist_ok:
        return VERDICT_MAYBE
    return VERDICT_NO_GO
