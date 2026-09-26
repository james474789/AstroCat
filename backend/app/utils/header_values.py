"""
Header value parsing for site coordinates (P0 §3.3).

Pure helpers shared by the extractors, the indexer and the
`backfill_sites` script:

- `parse_sexagesimal(v)`: decimal or sexagesimal angle -> float degrees.
  Accepts 51.48, "51.48", "56d0m0.000s N", "0d30m0.000s W", "+51 28 40",
  "51:28:40", "-0:00:05" and JSON char-arrays of any of those.
- `parse_exif_gps(raw_header)`: EXIF GPSLatitude/GPSLongitude + Ref -> (lat, lon).
- `valid_site(lat, lon)`: range check; (0, 0) counts as missing.
- `derive_site(metadata, raw_header)` / `apply_site(image, metadata)`.
"""

import re
from typing import Any, Optional, Tuple

from app.utils.capture_time import unchar

_HEMISPHERES = {"N": 1, "E": 1, "S": -1, "W": -1}
_UNITS = {"D", "DEG", "°", "M", "MIN", "'", "′", "S", "SEC", '"', "″", "''"}
_TOKEN_RE = re.compile(r"\d+(?:\.\d*)?|\.\d+|[A-Za-z]+|[°'′\"″]+|[+-]|:|,|\S")


def _number(value: Any) -> Optional[float]:
    """A number, a 'num/den' ratio string, or a [num, den] pair."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            if "/" in text:
                num, den = text.split("/", 1)
                return float(num) / float(den) if float(den) else None
            return float(text)
        except ValueError:
            return None
    return None


def parse_sexagesimal(value: Any) -> Optional[float]:
    """Parse a latitude/longitude header value into signed decimal degrees, or None."""
    value = unchar(value)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass

    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return None

    sign = 1
    numbers = []
    for i, tok in enumerate(tokens):
        is_edge = i == 0 or i == len(tokens) - 1
        if tok[0].isdigit() or tok[0] == ".":
            numbers.append(float(tok))
        elif tok in ("+", "-"):
            if numbers:
                return None  # sign in the middle of a value
            if tok == "-":
                sign = -sign
        elif tok == ":":
            continue
        elif is_edge and tok in _HEMISPHERES:
            # Upper-case single letter at either end = hemisphere.
            sign *= _HEMISPHERES[tok]
        elif tok.upper() in _UNITS:
            continue
        else:
            return None

    if not numbers or len(numbers) > 3:
        return None
    deg = numbers[0]
    minutes = numbers[1] if len(numbers) > 1 else 0.0
    seconds = numbers[2] if len(numbers) > 2 else 0.0
    if minutes >= 60 or seconds >= 60:
        return None
    return sign * (deg + minutes / 60.0 + seconds / 3600.0)


def _dms(value: Any) -> Optional[float]:
    """EXIF GPS angle: [deg, min, sec] list (numbers or ratio strings) or a single number."""
    value = unchar(value)
    if isinstance(value, (list, tuple)):
        if not 1 <= len(value) <= 3:
            return None
        parts = [_number(v) for v in value]
        if any(p is None for p in parts):
            return None
        parts += [0.0] * (3 - len(parts))
        return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
    if isinstance(value, str):
        return parse_sexagesimal(value)
    return _number(value)


def _ref_sign(ref: Any, positive: str, negative: str) -> Optional[int]:
    ref = unchar(ref)
    if ref is None:
        return 1
    text = str(ref).strip().upper()[:1]
    if text == positive:
        return 1
    if text == negative:
        return -1
    return 1 if not text else None


def valid_site(lat: Optional[float], lon: Optional[float]) -> Optional[Tuple[float, float]]:
    """(lat, lon) if both are in range and not exactly (0, 0); else None."""
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat != lat or lon != lon:  # NaN
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return (lat, lon)


def _pil_gps(raw_header: dict, tag: int):
    info = raw_header.get("PIL:GPSInfo")
    if isinstance(info, dict):
        return info.get(str(tag), info.get(tag))
    return None


def parse_exif_gps(raw_header: Optional[dict]) -> Optional[Tuple[float, float]]:
    """(lat, lon) from EXIF GPS tags in raw_header (exifread 'EXIF:GPS ...' or Pillow GPSInfo)."""
    if not isinstance(raw_header, dict):
        return None
    lat_v = raw_header.get("EXIF:GPS GPSLatitude")
    lon_v = raw_header.get("EXIF:GPS GPSLongitude")
    lat_ref = raw_header.get("EXIF:GPS GPSLatitudeRef")
    lon_ref = raw_header.get("EXIF:GPS GPSLongitudeRef")
    if lat_v is None or lon_v is None:
        lat_v, lon_v = _pil_gps(raw_header, 2), _pil_gps(raw_header, 4)
        lat_ref, lon_ref = _pil_gps(raw_header, 1), _pil_gps(raw_header, 3)
    if lat_v is None or lon_v is None:
        return None

    lat, lon = _dms(lat_v), _dms(lon_v)
    lat_sign, lon_sign = _ref_sign(lat_ref, "N", "S"), _ref_sign(lon_ref, "E", "W")
    if lat is None or lon is None or lat_sign is None or lon_sign is None:
        return None
    return valid_site(lat_sign * abs(lat), lon_sign * abs(lon))


_SITE_LAT_KEYS = ("SITELAT", "OBSGEO-B", "LAT-OBS")
_SITE_LON_KEYS = ("SITELONG", "OBSGEO-L", "LONG-OBS")
_SITE_NAME_KEYS = ("SITENAME", "OBSERVAT")


def _first_parsed(raw_header: dict, keys) -> Optional[float]:
    for key in keys:
        parsed = parse_sexagesimal(raw_header.get(key))
        if parsed is not None:
            return parsed
    return None


def site_name_from_header(raw_header: Optional[dict]) -> Optional[str]:
    if not isinstance(raw_header, dict):
        return None
    for key in _SITE_NAME_KEYS:
        value = unchar(raw_header.get(key))
        if isinstance(value, str) and value.strip():
            return value.strip()[:100]
    return None


def derive_site(metadata: Optional[dict], raw_header: Optional[dict]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    (site_latitude, site_longitude, site_name) for an image. Extractor
    metadata (site_lat/site_long/site_name) wins; raw_header FITS keys and
    then EXIF GPS are the fallback, so the backfill (raw_header only) and the
    indexer agree. Invalid or (0, 0) coordinates -> (None, None).
    """
    metadata = metadata or {}
    raw = raw_header if isinstance(raw_header, dict) else {}

    coords = valid_site(parse_sexagesimal(metadata.get("site_lat")), parse_sexagesimal(metadata.get("site_long")))
    if coords is None:
        coords = valid_site(_first_parsed(raw, _SITE_LAT_KEYS), _first_parsed(raw, _SITE_LON_KEYS))
    if coords is None:
        coords = parse_exif_gps(raw)

    name = metadata.get("site_name")
    name = name.strip()[:100] if isinstance(name, str) and name.strip() else site_name_from_header(raw)

    lat, lon = coords if coords else (None, None)
    return (lat, lon, name)


def apply_site(image, metadata: Optional[dict]) -> None:
    """Indexer hook: persist site_latitude/site_longitude/site_name on the image."""
    metadata = metadata or {}
    image.site_latitude, image.site_longitude, image.site_name = derive_site(metadata, metadata.get("raw_header"))
