"""
Capture-time provenance (P0 §3.2).

Pure helpers that decide where an image's capture time came from and, when
the source allows it, derive a trustworthy naive-UTC `capture_date_utc`.
`images.capture_date` keeps its existing meaning; this only adds the two
provenance columns alongside it.

| source        | when                                            | capture_date_utc        |
|---------------|-------------------------------------------------|-------------------------|
| FITS_UTC      | FITS/XISF DATE-OBS present                      | parsed DATE-OBS         |
| FITS_LOCAL    | only DATE-LOC                                   | NULL (R0 site timezone) |
| GPS_UTC       | EXIF GPSDate + GPSTimeStamp                     | GPS date + time         |
| EXIF_OFFSET   | EXIF DateTimeOriginal + OffsetTimeOriginal      | local - offset          |
| EXIF_LOCAL    | EXIF DateTimeOriginal only                      | NULL (R0 site timezone) |
| FILE_MTIME    | the indexer fell back to file mtime             | NULL, never astronomy   |
| OTHER         | capture_date came from some other field (FITS   | NULL                    |
|               | DATE, EXIF Image DateTime, ...)                 |                         |

Raw-header quirk: exifread string tags are sometimes stored as JSON arrays
of single characters (["2","0","1","8",":",...]); `unchar` joins them.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

FITS_UTC = "FITS_UTC"
FITS_LOCAL = "FITS_LOCAL"
GPS_UTC = "GPS_UTC"
EXIF_OFFSET = "EXIF_OFFSET"
EXIF_LOCAL = "EXIF_LOCAL"
FILE_MTIME = "FILE_MTIME"
OTHER = "OTHER"

# Sources whose capture_date_utc stays NULL until a site timezone is known (R0).
LOCAL_SOURCES = frozenset({FITS_LOCAL, EXIF_LOCAL})


def unchar(value: Any) -> Any:
    """Join a list of single-character strings back into a string; else return as-is."""
    if isinstance(value, (list, tuple)) and value and all(isinstance(c, str) and len(c) <= 1 for c in value):
        return "".join(value)
    return value


def _as_text(value: Any) -> Optional[str]:
    value = unchar(value)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return None
    text = str(value).strip().strip("\x00").strip()
    return text or None


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


_FITS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
)
_FRACTION_RE = re.compile(r"(\d{2}:\d{2}:\d{2})\.(\d+)")


def parse_fits_datetime(value: Any) -> Optional[datetime]:
    """
    Parse a FITS DATE-OBS/DATE-LOC value, mirroring FITSExtractor._get_date
    (leap-second ':60' capped to ':59', ISO first, then common formats).
    Timezone-aware values are converted to naive UTC.
    """
    text = _as_text(value)
    if not text:
        return None
    text = text.rstrip("Z") if text.endswith("Z") and "T" in text else text
    if "T" in text and ":60" in text:
        date_part, _, time_part = text.partition("T")
        parts = time_part.split(":")
        if len(parts) >= 3 and parts[2].startswith("60"):
            parts[2] = "59" + parts[2][2:]
            text = date_part + "T" + ":".join(parts)
    # Trim fractional seconds beyond microseconds (e.g. N.I.N.A. 7 digits).
    text = _FRACTION_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)[:6]}", text)
    try:
        return _to_naive_utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    for fmt in _FITS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_exif_datetime(value: Any) -> Optional[datetime]:
    """Parse an EXIF 'YYYY:MM:DD HH:MM:SS' value (char-arrays tolerated)."""
    text = _as_text(value)
    if not text:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


_OFFSET_RE = re.compile(r"^([+-])(\d{1,2}):?(\d{2})?$")


def parse_offset(value: Any) -> Optional[timedelta]:
    """Parse an EXIF OffsetTime value like '+02:00' / '-0530' into a timedelta."""
    text = _as_text(value)
    if not text:
        return None
    m = _OFFSET_RE.match(text)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    if hours > 14 or minutes >= 60:
        return None
    return sign * timedelta(hours=hours, minutes=minutes)


def _parse_gps_date(value: Any):
    text = _as_text(value)
    if not text:
        return None
    for fmt in ("%Y:%m:%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> Optional[float]:
    """A number, a 'num/den' ratio string, or a [num, den] pair."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        return float(value[0]) / float(value[1]) if value[1] else None
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


def parse_gps_time(date_value: Any, time_value: Any) -> Optional[datetime]:
    """
    Combine an EXIF GPSDate ('2018:12:23', char-arrays tolerated) and a
    GPSTimeStamp ([22.0, 54.0, 37.0]) into a naive UTC datetime.
    """
    date = _parse_gps_date(date_value)
    if date is None or not isinstance(time_value, (list, tuple)) or len(time_value) != 3:
        return None
    parts = [_number(v) for v in time_value]
    if any(p is None for p in parts):
        return None
    h, m, s = parts
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 61):
        return None
    return datetime(date.year, date.month, date.day) + timedelta(hours=h, minutes=m, seconds=s)


def _first(raw_header: dict, *keys):
    for key in keys:
        if key in raw_header and raw_header[key] not in (None, "", []):
            return raw_header[key]
    return None


def _pil_gps(raw_header: dict, tag: int):
    info = raw_header.get("PIL:GPSInfo")
    if isinstance(info, dict):
        return info.get(str(tag), info.get(tag))
    return None


def derive_capture_time(metadata: Optional[dict], raw_header: Optional[dict],
                        used_mtime_fallback: bool) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Return (capture_date_utc, capture_time_source). Pure; never raises on
    odd header values. `metadata` may be empty (backfill works from
    raw_header only).
    """
    if used_mtime_fallback:
        return (None, FILE_MTIME)

    raw = raw_header if isinstance(raw_header, dict) else {}
    metadata = metadata or {}

    # FITS / XISF
    date_obs = _first(raw, "DATE-OBS")
    if date_obs is not None:
        parsed = parse_fits_datetime(date_obs)
        if parsed is not None:
            return (parsed, FITS_UTC)
    if _first(raw, "DATE-LOC") is not None and parse_fits_datetime(_first(raw, "DATE-LOC")) is not None:
        return (None, FITS_LOCAL)

    # EXIF GPS (UTC by definition)
    gps = parse_gps_time(
        _first(raw, "EXIF:GPS GPSDate") or _pil_gps(raw, 29),
        _first(raw, "EXIF:GPS GPSTimeStamp") or _pil_gps(raw, 7),
    )
    if gps is not None:
        return (gps, GPS_UTC)

    # EXIF DateTimeOriginal (+ offset)
    local = parse_exif_datetime(_first(raw, "EXIF:EXIF DateTimeOriginal", "PIL:DateTimeOriginal"))
    if local is not None:
        offset = parse_offset(_first(raw, "EXIF:EXIF OffsetTimeOriginal", "PIL:OffsetTimeOriginal"))
        if offset is not None:
            return (local - offset, EXIF_OFFSET)
        return (None, EXIF_LOCAL)

    # A capture date from some other field (FITS DATE, EXIF Image DateTime...)
    # or none at all: provenance unknown, never used as UTC.
    return (None, OTHER)


_HEADER_DATE_KEYS = (
    ("DATE-OBS", parse_fits_datetime),
    ("DATE-LOC", parse_fits_datetime),
    ("DATE", parse_fits_datetime),
    ("EXIF:EXIF DateTimeOriginal", parse_exif_datetime),
    ("EXIF:Image DateTime", parse_exif_datetime),
    ("PIL:DateTimeOriginal", parse_exif_datetime),
    ("PIL:DateTime", parse_exif_datetime),
)


def has_header_date(raw_header: Optional[dict]) -> bool:
    """True when raw_header holds any date field an extractor could have used for capture_date."""
    if not isinstance(raw_header, dict):
        return False
    return any(key in raw_header and parser(raw_header[key]) is not None for key, parser in _HEADER_DATE_KEYS)


def derive_capture_time_for_row(raw_header: Optional[dict], capture_date: Optional[datetime],
                                file_last_modified: Optional[datetime]) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Backfill variant (no metadata, no file IO): the indexer's mtime fallback
    is recognised as capture_date == file_last_modified with no header date.
    """
    used_mtime = (
        not has_header_date(raw_header)
        and (capture_date is None or capture_date == file_last_modified)
    )
    return derive_capture_time({}, raw_header, used_mtime_fallback=used_mtime)


def apply_capture_time(image, metadata: Optional[dict]) -> None:
    """
    Indexer hook: set image.capture_date_utc / capture_time_source from the
    extracted metadata. The mtime fallback is detected the same way the
    indexer applies it (metadata has no capture_date).
    """
    metadata = metadata or {}
    utc, source = derive_capture_time(
        metadata, metadata.get("raw_header"), used_mtime_fallback=not metadata.get("capture_date"))
    image.capture_date_utc = utc
    image.capture_time_source = source
