"""
Library-wide star quality statistics (Q1d, docs/design/20260927-Q1-star-quality.md §7.6).

Pure functions over measured Light subs of one rig (dicts with fwhm_px,
hfr_px, scale, filter, t, alt_deg, foc_temp). The API gathers the rows.
"""

from collections import defaultdict
from statistics import median
from typing import Any, Dict, List, Optional

import numpy as np

MIN_BIN = 5            # fewer subs in a bin -> left out
FILTER_ORDER = ["L", "R", "G", "B", "Ha", "OIII", "SII", "Hb", "Duo", "None"]


def _arc(p) -> Optional[float]:
    return p["fwhm_px"] * p["scale"] if p.get("fwhm_px") is not None and p.get("scale") else None


def _value(p, use_arcsec: bool) -> Optional[float]:
    return _arc(p) if use_arcsec else p.get("fwhm_px")


def use_arcsec_for(rows: List[Dict[str, Any]]) -> bool:
    """Arcsec when most subs have a scale, else px."""
    return bool(rows) and sum(1 for r in rows if r.get("scale")) >= 0.5 * len(rows)


def values(rows, use_arcsec: bool) -> List[float]:
    return [v for v in (_value(r, use_arcsec) for r in rows) if v is not None]


def histogram(vals: List[float], bins: int = 24) -> List[Dict[str, Any]]:
    if len(vals) < MIN_BIN:
        return []
    lo, hi = np.percentile(vals, 1), np.percentile(vals, 99)
    if hi <= lo:
        hi = lo + 1e-6
    counts, edges = np.histogram(np.clip(vals, lo, hi), bins=bins, range=(lo, hi))
    return [{"from": round(float(edges[i]), 3), "to": round(float(edges[i + 1]), 3), "count": int(c)}
            for i, c in enumerate(counts) if c >= MIN_BIN]


def monthly(rows, use_arcsec: bool) -> List[Dict[str, Any]]:
    groups: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        v = _value(r, use_arcsec)
        if v is not None and r.get("t") is not None:
            groups[r["t"].strftime("%Y-%m")].append(v)
    return [{"month": m, "median": round(median(v), 3), "n": len(v)}
            for m, v in sorted(groups.items()) if len(v) >= MIN_BIN]


def by_altitude(rows, use_arcsec: bool, step: int = 10) -> List[Dict[str, Any]]:
    groups: Dict[int, List[float]] = defaultdict(list)
    for r in rows:
        v = _value(r, use_arcsec)
        if v is not None and r.get("alt_deg") is not None and r["alt_deg"] > 0:
            groups[int(r["alt_deg"] // step) * step].append(v)
    return [{"alt_from": a, "alt_to": a + step, "median": round(median(v), 3), "n": len(v)}
            for a, v in sorted(groups.items()) if len(v) >= MIN_BIN]


def filter_offsets(rows, use_arcsec: bool) -> List[Dict[str, Any]]:
    """
    Median FWHM per filter and its offset from L (or the most-used filter when
    there's no L). A consistent offset means that filter focuses differently:
    set a focus offset in the capture software.
    """
    groups: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        v = _value(r, use_arcsec)
        if v is not None:
            groups[r.get("filter") or "None"].append(v)
    groups = {f: v for f, v in groups.items() if len(v) >= MIN_BIN}
    if not groups:
        return []
    ref = "L" if "L" in groups else max(groups, key=lambda f: len(groups[f]))
    ref_med = median(groups[ref])
    order = {f: i for i, f in enumerate(FILTER_ORDER)}
    out = []
    for f in sorted(groups, key=lambda f: (order.get(f, 99), f)):
        med = median(groups[f])
        out.append({"filter": f, "median": round(med, 3), "n": len(groups[f]),
                    "offset_pct": round((med - ref_med) / ref_med * 100.0, 1) if ref_med else None,
                    "reference": f == ref})
    return out


def hfr_vs_temperature(rows, step: float = 1.0) -> Dict[str, Any]:
    """
    Median HFR (px) per 1 degC of focuser temperature, plus a least-squares
    slope (px per degC). A clear slope says focus drifts with temperature:
    use temperature compensation or refocus on temperature change.
    """
    pts = [(r["foc_temp"], r["hfr_px"]) for r in rows
           if r.get("foc_temp") is not None and r.get("hfr_px") is not None and -40 < r["foc_temp"] < 50]
    groups: Dict[float, List[float]] = defaultdict(list)
    for t, h in pts:
        groups[round(np.floor(t / step) * step, 1)].append(h)
    bins = [{"temp_from": t, "temp_to": round(t + step, 1), "median_hfr_px": round(median(v), 3), "n": len(v)}
            for t, v in sorted(groups.items()) if len(v) >= MIN_BIN]
    slope = None
    if len(pts) >= 20:
        temps = np.array([t for t, _ in pts])
        if np.ptp(temps) >= 2.0:   # need a real temperature range
            slope = round(float(np.polyfit(temps, np.array([h for _, h in pts]), 1)[0]), 4)
    return {"bins": bins, "slope_px_per_degc": slope, "n": len(pts)}


def by_rig(rows: List[Dict[str, Any]], names: Dict[Any, Optional[str]], prefer_arcsec: bool = True) -> List[Dict[str, Any]]:
    """
    Q2: median FWHM and HFR per rig, for cross-rig comparison. `rows` are the
    per-rig SQL aggregates (rig_id, n, n_scaled, fwhm_px/hfr_px medians and the
    same in arcsec). A rig reports arcsec when at least half its subs have a
    scale (same rule as use_arcsec_for), else px. Rigs under MIN_BIN are left out.
    """
    out = []
    for r in rows:
        if r["rig_id"] is None or r["n"] < MIN_BIN:
            continue
        arc = prefer_arcsec and r["n_scaled"] >= 0.5 * r["n"] and r.get("fwhm_arcsec") is not None
        f, h = (r["fwhm_arcsec"], r["hfr_arcsec"]) if arc else (r["fwhm_px"], r["hfr_px"])
        out.append({
            "rig_id": r["rig_id"], "rig_name": names.get(r["rig_id"]), "n": int(r["n"]),
            "units": "ARCSEC" if arc else "PX",
            "fwhm_median": round(float(f), 3) if f is not None else None,
            "hfr_median": round(float(h), 3) if h is not None else None,
        })
    return sorted(out, key=lambda x: (x["fwhm_median"] is None, x["fwhm_median"] or 0))


def build_stats(rows: List[Dict[str, Any]], prefer_arcsec: bool = True) -> Dict[str, Any]:
    use_arcsec = prefer_arcsec and use_arcsec_for(rows)
    vals = values(rows, use_arcsec)
    return {
        "units": "ARCSEC" if use_arcsec else "PX",
        "measured": len(rows),
        "median": round(median(vals), 3) if vals else None,
        "histogram": histogram(vals),
        "monthly": monthly(rows, use_arcsec),
        "by_altitude": by_altitude(rows, use_arcsec),
        "filters": filter_offsets(rows, use_arcsec),
        "temperature": hfr_vs_temperature(rows),
    }
