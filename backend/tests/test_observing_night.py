"""R1 §3.1: the shared observing-night definition."""

from datetime import date, datetime, timezone

from app.utils.observing_night import NIGHT_JOIN_SQL, NIGHT_SQL, night_bounds_utc, night_of


def test_early_morning_utc_belongs_to_previous_night():
    assert night_of(datetime(2026, 9, 27, 1, 30), None, -3.0) == date(2026, 9, 26)


def test_afternoon_utc_is_same_date():
    assert night_of(datetime(2026, 9, 26, 13, 0), None, -3.0) == date(2026, 9, 26)


def test_boundary_is_local_solar_noon():
    # lon -3: local solar noon = 12:12 UTC.
    assert night_of(datetime(2026, 9, 26, 12, 10), None, -3.0) == date(2026, 9, 25)
    assert night_of(datetime(2026, 9, 26, 12, 14), None, -3.0) == date(2026, 9, 26)


def test_east_longitude_shifts_boundary():
    # 02:00 UTC at lon +150 is local solar noon (12:00): a new night starts.
    assert night_of(datetime(2026, 9, 27, 2, 0), None, 150.0) == date(2026, 9, 27)
    assert night_of(datetime(2026, 9, 27, 1, 50), None, 150.0) == date(2026, 9, 26)
    assert night_of(datetime(2026, 9, 27, 1, 50), None, 0.0) == date(2026, 9, 26)


def test_utc_without_longitude_uses_greenwich():
    assert night_of(datetime(2026, 9, 26, 11, 59), None, None) == date(2026, 9, 25)
    assert night_of(datetime(2026, 9, 26, 12, 0), None, None) == date(2026, 9, 26)


def test_aware_utc_is_normalised():
    aware = datetime(2026, 9, 27, 1, 30, tzinfo=timezone.utc)
    assert night_of(aware, None, -3.0) == date(2026, 9, 26)


def test_no_utc_falls_back_to_local_minus_12h():
    assert night_of(None, datetime(2026, 9, 27, 2, 0), -3.0) == date(2026, 9, 26)
    assert night_of(None, datetime(2026, 9, 27, 13, 0), None) == date(2026, 9, 27)
    assert night_of(None, None, 0.0) is None


def test_night_bounds():
    start, end = night_bounds_utc(date(2026, 9, 26), -3.0)
    assert start == datetime(2026, 9, 26, 12, 12)
    assert end == datetime(2026, 9, 27, 12, 12)
    # Every time inside the bounds maps back to the night.
    assert night_of(start, None, -3.0) == date(2026, 9, 26)
    assert night_of(end.replace(minute=11), None, -3.0) == date(2026, 9, 26)
    assert night_of(end, None, -3.0) == date(2026, 9, 27)


def test_sql_mirror_mentions_the_same_inputs():
    assert "capture_date_utc" in NIGHT_SQL and "s.longitude" in NIGHT_SQL and "240" in NIGHT_SQL
    assert "12 hours" in NIGHT_SQL and "LEFT JOIN sites s" in NIGHT_JOIN_SQL
