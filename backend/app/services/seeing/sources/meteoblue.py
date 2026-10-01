"""
meteoblue astronomy seeing source (design §3.2). Optional: only used when METEOBLUE_API_KEY is set.

TODO(S1 §3.2 / §12): no key was available when this was written, so the package name and JSON keys below are
the design's *assumed* ones and are UNCONFIRMED. Confirm both against a live key's package list, then fix
PACKAGE and the MB_* names; nothing outside this module depends on them. All response mapping is confined to
parse_meteoblue(). Never scrape meteoblue.com: this only reads the API.
"""

import logging
from datetime import datetime
from typing import Optional

import httpx

from app.services.seeing.privacy import quiet_http_loggers, redact_url, round_coords, strip_location
from app.services.seeing.sources import HourlyFrame, SourceError

quiet_http_loggers()
logger = logging.getLogger(__name__)

API_URL = "https://my.meteoblue.com/packages/{package}"
TIMEOUT_S = 20.0

PACKAGE = "seeing-1h"             # TODO: confirm the astronomy-seeing package name (assumed)

# TODO: confirm these JSON keys under MB_BLOCK (assumed). internal name -> meteoblue key.
MB_KEYS = {
    "seeing_arcsec": "seeing_arcsec",
    "seeing_index1": "seeing1",
    "seeing_index2": "seeing2",
    "jet_stream": "jetstream",
    "badlayer_bottom": "badlayer_bottom",
    "badlayer_top": "badlayer_top",
    "badlayer_gradient": "badlayer_gradient",
    "cloud_low": "lowclouds",
    "cloud_mid": "midclouds",
    "cloud_high": "highclouds",
}
MB_BLOCK = "data_1h"
MB_TIME_KEY = "time"


def fetch_meteoblue(lat: float, lon: float, key: str, client: Optional[httpx.Client] = None) -> dict:
    """Raw response, location keys stripped. The key is never logged; errors are redacted."""
    rlat, rlon = round_coords(lat, lon)
    params = {"lat": rlat, "lon": rlon, "apikey": key, "format": "json", "tz": "UTC", "temperature": "C",
              "windspeed": "ms-1"}
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        resp = client.get(API_URL.format(package=PACKAGE), params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        hint = {401: "invalid key", 402: "out of credits", 403: "key not allowed for this package",
                429: "out of credits / rate limited"}.get(code, "")
        raise SourceError(redact_url(f"meteoblue HTTP {code} {hint}".strip())) from None
    except (httpx.HTTPError, ValueError) as e:
        raise SourceError(redact_url(f"meteoblue request failed: {type(e).__name__}")) from None
    finally:
        if own:
            client.close()
    if not isinstance(data, dict) or MB_BLOCK not in data:
        raise SourceError("meteoblue returned an unexpected response (package or keys unconfirmed)")
    return strip_location(data)


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("T", " ")[:16])


def parse_meteoblue(raw: dict) -> HourlyFrame:
    """Map a meteoblue response onto internal variable names. Missing keys are simply absent."""
    block = (raw or {}).get(MB_BLOCK) or {}
    times = [_parse_time(t) for t in block.get(MB_TIME_KEY, [])]
    values = {}
    for internal, key in MB_KEYS.items():
        series = block.get(key)
        if series is not None:
            values[internal] = [None if v is None else float(v) for v in series]
    return HourlyFrame(times=times, values=values)
