"""
Open-Meteo "Windy-equivalent models" source (design §3.1). Free, no key. Server-side only.

Coordinates are rounded (privacy.round_coords) before the request; latitude/longitude/elevation are stripped
from the response before it is returned, so they never reach the cache.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import httpx

from app.services.seeing.privacy import quiet_http_loggers, redact_url, round_coords, strip_location
from app.services.seeing.sources import HourlyFrame, SourceError

quiet_http_loggers()
logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 20.0
FORECAST_DAYS = 7

# Names checked against https://open-meteo.com/en/docs (unknown names return 400).
SURFACE_VARS = [
    "wind_speed_10m", "wind_gusts_10m", "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "surface_pressure",
    "boundary_layer_height",
]
UPPER_LEVELS = (200, 250, 300, 500, 700, 850)
UPPER_VARS = (
    [f"wind_speed_{p}hPa" for p in UPPER_LEVELS]
    + [f"wind_direction_{p}hPa" for p in UPPER_LEVELS]
    + [f"temperature_{p}hPa" for p in (250, 300, 500, 700, 850)]
)
HOURLY_VARS = SURFACE_VARS + UPPER_VARS


def fetch_open_meteo(lat: float, lon: float, models: Sequence[str], client: Optional[httpx.Client] = None) -> dict:
    """Raw response with location keys stripped. Raises SourceError (message redacted) on any failure."""
    rlat, rlon = round_coords(lat, lon)
    params = {
        "latitude": rlat, "longitude": rlon, "timezone": "UTC", "wind_speed_unit": "ms",
        "forecast_days": FORECAST_DAYS, "models": ",".join(models), "hourly": ",".join(HOURLY_VARS),
    }
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        resp = client.get(API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        reason = ""
        try:
            reason = str(e.response.json().get("reason", ""))
        except Exception:
            pass
        raise SourceError(redact_url(f"Open-Meteo HTTP {e.response.status_code} {reason}".strip())) from None
    except (httpx.HTTPError, ValueError) as e:
        raise SourceError(redact_url(f"Open-Meteo request failed: {type(e).__name__}")) from None
    finally:
        if own:
            client.close()
    if not isinstance(data, dict) or "hourly" not in data:
        raise SourceError("Open-Meteo returned an unexpected response")
    return strip_location(data)


def parse_open_meteo(raw: dict, models: Sequence[str]) -> Dict[str, HourlyFrame]:
    """One HourlyFrame per model that has data. Multi-model responses suffix each variable with `_<model>`."""
    hourly = (raw or {}).get("hourly") or {}
    times = [datetime.fromisoformat(t) for t in hourly.get("time", [])]
    single = len(models) == 1
    frames: Dict[str, HourlyFrame] = {}
    for model in models:
        values: Dict[str, List[Optional[float]]] = {}
        for var in HOURLY_VARS:
            series = hourly.get(f"{var}_{model}")
            if series is None and single:
                series = hourly.get(var)
            if series is None:
                continue
            values[var] = [None if v is None else float(v) for v in series]
        frame = HourlyFrame(times=times, values=values)
        if frame.has_data():
            frames[model] = frame
    return frames


def fill_upper_levels(frames: Dict[str, HourlyFrame], reference: str) -> None:
    """Models without pressure-level winds/temperatures take them from the reference model (design §12)."""
    ref = frames.get(reference)
    if ref is None:
        return
    for name, frame in frames.items():
        if name == reference:
            continue
        for var in UPPER_VARS:
            have = frame.values.get(var)
            if have is not None and any(v is not None for v in have):
                continue
            src = ref.values.get(var)
            if src is None:
                continue
            if frame.times == ref.times:
                frame.values[var] = list(src)
            else:
                frame.values[var] = [ref.at(var, t) for t in frame.times]
