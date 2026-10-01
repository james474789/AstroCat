"""
Privacy guards for the seeing forecast (docs/design/S1-planetary-seeing-forecast.md §8).

Coordinates only ever flow Site row -> SiteSpec (memory) -> round_coords -> HTTP query. They are never
returned, cached, stored or logged. Anything that can echo a URL or a provider response passes through
here first.
"""

import logging
import re
from typing import Any, Optional, Tuple

DEFAULT_DECIMALS = 2

# Keys a provider may echo back. Matched as whole "_"-separated tokens, so e.g. "relative_humidity" is safe.
_LOCATION_TOKENS = frozenset({"lat", "lon", "lng", "latitude", "longitude", "elevation"})

_URL_QUERY_RE = re.compile(r"(https?://[^\s?#'\"<>)]*)\?[^\s'\"<>)]*")
_COORD_PARAM_RE = re.compile(r"(?i)\b(latitude|longitude|lat|lon|apikey|api_key|key)=[^\s&'\"]+")


def round_coords(lat: float, lon: float, decimals: Optional[int] = None) -> Tuple[float, float]:
    """Round to `decimals` (default settings.seeing_coord_decimals, 2 = ~1 km) before any external request."""
    if decimals is None:
        try:
            from app.config import settings
            decimals = int(settings.seeing_coord_decimals)
        except Exception:
            decimals = DEFAULT_DECIMALS
    return round(float(lat), decimals), round(float(lon), decimals)


def redact_url(text: Any) -> str:
    """Drop the query string from every URL in `text` (and any stray lat/lon/key=value pair)."""
    out = _URL_QUERY_RE.sub(r"\1", str(text))
    return _COORD_PARAM_RE.sub(r"\1=<redacted>", out)


def _is_location_key(key: Any) -> bool:
    return any(tok in _LOCATION_TOKENS for tok in str(key).lower().split("_"))


def strip_location(raw: Any) -> Any:
    """A copy of provider JSON without latitude/longitude/elevation or any lat/lon-named key (recursive)."""
    if isinstance(raw, dict):
        return {k: strip_location(v) for k, v in raw.items() if not _is_location_key(k)}
    if isinstance(raw, list):
        return [strip_location(v) for v in raw]
    return raw


def quiet_http_loggers() -> None:
    """httpx logs full request URLs (query string included) at INFO."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
