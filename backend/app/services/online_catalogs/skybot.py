"""
Asteroids and comets in the frame at the capture time, from IMCCE SkyBoT's cone search.

Geocentric positions (observer code 500): topocentric parallax is a few arcsec for
main-belt objects and only matters for close near-Earth objects. SkyBoT slows down with
cone area (about 30 s for a 3 degree radius), so wider fields are refused.
"""

import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.services.online_catalogs.vizier import OnlineCatalogError

logger = logging.getLogger(__name__)

URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
TIMEOUT_S = 90.0
MAX_RADIUS_DEG = 5.0
JD_UNIX_EPOCH = 2440587.5


def julian_date(dt: datetime) -> float:
    """Naive UTC datetime -> Julian date."""
    return JD_UNIX_EPOCH + (dt - datetime(1970, 1, 1)).total_seconds() / 86400.0


def epoch_for(capture_utc: Optional[datetime], exposure_s: Optional[float]) -> Optional[datetime]:
    """Mid-exposure time: capture time (exposure start) plus half the exposure."""
    if capture_utc is None:
        return None
    if exposure_s and exposure_s > 0 and math.isfinite(exposure_s):
        return capture_utc + timedelta(seconds=exposure_s / 2.0)
    return capture_utc


def query_params(jd: float, ra: float, dec: float, radius_deg: float) -> Dict[str, str]:
    return {
        "-ep": f"{jd:.6f}", "-ra": f"{ra % 360.0:.6f}", "-dec": f"{dec:.6f}", "-rd": f"{radius_deg:.4f}",
        "-mime": "json", "-output": "all", "-loc": "500", "-filter": "120", "-objFilter": "111",
        "-refsys": "EQJ2000", "-from": "AstroCat",
    }


def _sexagesimal(text: Any, hours: bool) -> Optional[float]:
    parts = re.findall(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    if len(parts) != 3:
        return None
    sign = -1.0 if str(text).strip().startswith("-") else 1.0
    d, m, s = (abs(float(p)) for p in parts)
    value = sign * (d + m / 60.0 + s / 3600.0)
    return value * 15.0 if hours else value


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def designation(num: Any, name: str) -> str:
    """'(433) Eros' for numbered bodies, else the name ('2019 SC34', 'C/2023 A3 (Tsuchinshan-ATLAS)')."""
    n = _num(num)
    return f"({int(n)}) {name}" if n is not None else name


def to_rows(records: Any, mag_limit: Optional[float]) -> List[Dict[str, Any]]:
    """SkyBoT JSON records -> normalised rows, brighter than mag_limit (bodies without V are kept)."""
    if isinstance(records, dict):
        # Errors come back as an object with flag -1 and a message, often with HTTP 200
        raise OnlineCatalogError(f"SkyBoT error: {records.get('message') or records}")
    rows = []
    for rec in records or []:
        name = str(rec.get("Name") or "").strip()
        ra = _sexagesimal(rec.get("RA (hms)"), hours=True)
        dec = _sexagesimal(rec.get("DEC (dms)"), hours=False)
        if not name or ra is None or dec is None:
            continue
        vmag = _num(rec.get("VMag (mag)"))
        if mag_limit is not None and vmag is not None and vmag > mag_limit:
            continue
        cls = str(rec.get("Class") or "").strip() or None
        dra, ddec = _num(rec.get("dRA (arcsec/h)")), _num(rec.get("dDEC (arcsec/h)"))
        motion = math.hypot(dra, ddec) if dra is not None and ddec is not None else None
        label = designation(rec.get("Num"), name)
        lookup = str(int(_num(rec["Num"]))) if _num(rec.get("Num")) is not None else name
        rows.append({
            "catalog": "SKYBOT", "designation": label, "aliases": [], "common_name": None,
            "object_type": cls.replace(">", " › ") if cls else None,
            "magnitude": vmag, "ra": ra, "dec": dec, "major": None, "minor": None, "pa": None,
            "url": f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={quote(lookup)}",
            "motion_arcsec_h": round(motion, 1) if motion is not None else None,
            "is_comet": bool(cls and cls.lower().startswith("comet")),
        })
    return rows


async def cone(jd: float, ra: float, dec: float, radius_deg: float, mag_limit: Optional[float]) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        try:
            resp = await client.get(URL, params=query_params(jd, ra, dec, radius_deg))
        except httpx.HTTPError as e:
            raise OnlineCatalogError(f"SkyBoT unavailable: {e.__class__.__name__}") from e
    if resp.status_code == 204:          # nothing in the cone
        return []
    if resp.status_code != 200:
        raise OnlineCatalogError(f"SkyBoT unavailable: HTTP {resp.status_code}")
    try:
        records = resp.json()
    except ValueError as e:
        raise OnlineCatalogError("SkyBoT returned an unreadable answer") from e
    return to_rows(records, mag_limit)
