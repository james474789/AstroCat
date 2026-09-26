"""
One definition of an "observing night" (R1, docs/design/R1-recommendation-engine.md §3.1).

A night is labelled with the local *solar* date of the noon that precedes it,
so a whole dusk-to-dawn session gets one date. With a UTC time and a site
longitude that is `(utc + lon/15 h - 12 h).date()`; with UTC only the
longitude defaults to 0; without UTC it falls back to the camera-local
`(local - 12 h).date()` used before R1.

The recommendation engine, the replay test and the Targets API all use it.
`NIGHT_SQL` is the SQL mirror (images aliased `images`, sites LEFT JOINed as `s`).
"""

from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

# SQL mirror of night_of(). Requires `LEFT JOIN sites s ON s.id = images.site_id`.
NIGHT_SQL = (
    "date(coalesce(images.capture_date_utc + make_interval(secs => coalesce(s.longitude, 0) * 240), "
    "images.capture_date) - interval '12 hours')"
)
NIGHT_JOIN_SQL = "LEFT JOIN sites s ON s.id = images.site_id"


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def night_of(utc: Optional[datetime], local: Optional[datetime], lon_deg: Optional[float]) -> Optional[date]:
    """The observing night (local solar date of the preceding noon)."""
    if utc is not None:
        lon = float(lon_deg) if lon_deg is not None else 0.0
        return (_naive(utc) + timedelta(hours=lon / 15.0) - timedelta(hours=12)).date()
    if local is not None:
        return (local.replace(tzinfo=None) - timedelta(hours=12)).date()
    return None


def night_bounds_utc(night: date, lon_deg: float) -> Tuple[datetime, datetime]:
    """[local solar noon of `night`, next local solar noon) as naive UTC datetimes."""
    start = datetime.combine(night, time(12, 0)) - timedelta(hours=float(lon_deg or 0.0) / 15.0)
    return start, start + timedelta(days=1)
