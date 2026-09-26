"""
Tests for app.utils.capture_time (P0 §3.2) and the capture-time backfill.
Pure-function tests plus a mocked-session backfill test - no DB.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.utils.capture_time import (
    EXIF_LOCAL,
    EXIF_OFFSET,
    FILE_MTIME,
    FITS_LOCAL,
    FITS_UTC,
    GPS_UTC,
    OTHER,
    apply_capture_time,
    derive_capture_time,
    derive_capture_time_for_row,
    has_header_date,
    parse_exif_datetime,
    parse_fits_datetime,
    parse_gps_time,
    parse_offset,
    unchar,
)


def chars(s):
    """The raw_header quirk: a string stored as a JSON array of characters."""
    return list(s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_unchar():
    assert unchar(chars("+02:00")) == "+02:00"
    assert unchar("+02:00") == "+02:00"
    assert unchar([22.0, 54.0, 37.0]) == [22.0, 54.0, 37.0]
    assert unchar(None) is None


@pytest.mark.parametrize("raw,expected", [
    ("2019-10-24T19:03:15", datetime(2019, 10, 24, 19, 3, 15)),
    ("2019-10-24T19:03:15.1234567", datetime(2019, 10, 24, 19, 3, 15, 123456)),
    ("2019-10-24T19:03:15Z", datetime(2019, 10, 24, 19, 3, 15)),
    ("2019-10-24T19:03:60", datetime(2019, 10, 24, 19, 3, 59)),
    ("2019-10-24", datetime(2019, 10, 24)),
    ("garbage", None),
    (None, None),
])
def test_parse_fits_datetime(raw, expected):
    assert parse_fits_datetime(raw) == expected


def test_parse_exif_datetime_char_array():
    assert parse_exif_datetime(chars("2018:12:23 22:54:39")) == datetime(2018, 12, 23, 22, 54, 39)
    assert parse_exif_datetime("0000:00:00 00:00:00") is None


@pytest.mark.parametrize("raw,expected", [
    ("+02:00", timedelta(hours=2)),
    (chars("+02:00"), timedelta(hours=2)),
    ("-05:30", -timedelta(hours=5, minutes=30)),
    ("+0100", timedelta(hours=1)),
    ("Z", None),
    ("", None),
])
def test_parse_offset(raw, expected):
    assert parse_offset(raw) == expected


def test_parse_gps_time():
    assert parse_gps_time("2018:12:23", [22, 54, 37]) == datetime(2018, 12, 23, 22, 54, 37)
    assert parse_gps_time(chars("2018:12:23"), [22.0, 54.0, 37.0]) == datetime(2018, 12, 23, 22, 54, 37)
    assert parse_gps_time("2018:12:23", ["22/1", "54/1", "375/10"]) == datetime(2018, 12, 23, 22, 54, 37, 500000)
    assert parse_gps_time(None, [22, 54, 37]) is None
    assert parse_gps_time("2018:12:23", [25, 0, 0]) is None


# ---------------------------------------------------------------------------
# derive_capture_time: one test per source row in spec §3.2
# ---------------------------------------------------------------------------

def test_fits_utc_from_date_obs():
    raw = {"DATE-OBS": "2019-10-24T19:03:15", "DATE-LOC": "2019-10-24T20:03:15"}
    assert derive_capture_time({}, raw, False) == (datetime(2019, 10, 24, 19, 3, 15), FITS_UTC)


def test_fits_local_only_date_loc():
    raw = {"DATE-LOC": "2019-10-24T20:03:15"}
    assert derive_capture_time({}, raw, False) == (None, FITS_LOCAL)


def test_gps_utc():
    raw = {
        "EXIF:GPS GPSDate": chars("2018:12:23"),
        "EXIF:GPS GPSTimeStamp": [22.0, 54.0, 37.0],
        "EXIF:EXIF DateTimeOriginal": chars("2018:12:23 23:54:39"),
    }
    assert derive_capture_time({}, raw, False) == (datetime(2018, 12, 23, 22, 54, 37), GPS_UTC)


def test_exif_offset_is_subtracted():
    raw = {
        "EXIF:EXIF DateTimeOriginal": chars("2018:12:23 22:54:39"),
        "EXIF:EXIF OffsetTimeOriginal": chars("+02:00"),
    }
    assert derive_capture_time({}, raw, False) == (datetime(2018, 12, 23, 20, 54, 39), EXIF_OFFSET)


def test_exif_local_without_offset():
    raw = {"EXIF:EXIF DateTimeOriginal": chars("2018:12:23 22:54:39")}
    assert derive_capture_time({}, raw, False) == (None, EXIF_LOCAL)


def test_pillow_exif_keys():
    raw = {"PIL:DateTimeOriginal": "2018:12:23 22:54:39", "PIL:OffsetTimeOriginal": "-01:00"}
    assert derive_capture_time({}, raw, False) == (datetime(2018, 12, 23, 23, 54, 39), EXIF_OFFSET)


def test_mtime_fallback():
    raw = {"DATE-OBS": "2019-10-24T19:03:15"}
    assert derive_capture_time({}, raw, True) == (None, FILE_MTIME)
    assert derive_capture_time({}, None, True) == (None, FILE_MTIME)


def test_other_date_field():
    # FITS DATE (file write time) / EXIF Image DateTime: provenance unknown.
    assert derive_capture_time({}, {"DATE": "2019-10-24T19:03:15"}, False) == (None, OTHER)
    assert derive_capture_time({}, {"EXIF:Image DateTime": "2018:12:23 22:54:39"}, False) == (None, OTHER)


def test_unparseable_date_obs_falls_through():
    raw = {"DATE-OBS": "not a date", "DATE-LOC": "2019-10-24T20:03:15"}
    assert derive_capture_time({}, raw, False) == (None, FITS_LOCAL)


# ---------------------------------------------------------------------------
# Backfill row variant + indexer hook
# ---------------------------------------------------------------------------

MTIME = datetime(2021, 3, 4, 12, 0, 0)


def test_row_variant_detects_mtime_fallback():
    # PNG/TIF with no header date: capture_date == file_last_modified.
    assert derive_capture_time_for_row({"PIL:Software": "SharpCap"}, MTIME, MTIME) == (None, FILE_MTIME)
    assert derive_capture_time_for_row(None, None, MTIME) == (None, FILE_MTIME)


def test_row_variant_header_date_wins_even_if_equal_to_mtime():
    raw = {"DATE-OBS": "2021-03-04T12:00:00"}
    assert derive_capture_time_for_row(raw, MTIME, MTIME) == (MTIME, FITS_UTC)


def test_row_variant_no_header_date_but_different_capture_date_is_other():
    assert derive_capture_time_for_row({}, datetime(2020, 1, 1), MTIME) == (None, OTHER)


def test_has_header_date():
    assert has_header_date({"EXIF:EXIF DateTimeOriginal": chars("2018:12:23 22:54:39")})
    assert not has_header_date({"EXIF:EXIF DateTimeOriginal": "garbage"})
    assert not has_header_date(None)


def test_apply_capture_time_sets_both_columns():
    image = SimpleNamespace(capture_date=datetime(2019, 10, 24, 19, 3, 15))
    apply_capture_time(image, {
        "capture_date": datetime(2019, 10, 24, 19, 3, 15),
        "raw_header": {"DATE-OBS": "2019-10-24T19:03:15"},
    })
    assert image.capture_date_utc == datetime(2019, 10, 24, 19, 3, 15)
    assert image.capture_time_source == FITS_UTC

    apply_capture_time(image, {"raw_header": {"DATE-OBS": "2019-10-24T19:03:15"}})  # no capture_date
    assert (image.capture_date_utc, image.capture_time_source) == (None, FILE_MTIME)


def test_backfill_capture_time_mocked_session():
    from app.scripts.backfill_capture_time import backfill_capture_time

    rows = [
        (1, {"DATE-OBS": "2019-10-24T19:03:15"}, datetime(2019, 10, 24, 19, 3, 15), MTIME, None, None),
        (2, {}, MTIME, MTIME, None, None),
        (3, {"EXIF:EXIF DateTimeOriginal": chars("2018:12:23 22:54:39")}, datetime(2018, 12, 23, 22, 54, 39), MTIME, None, None),
    ]
    session = MagicMock()
    session.execute.return_value.all.side_effect = [rows]

    summary = backfill_capture_time(session=session, batch_size=1000)

    assert summary == {"processed": 3, "updated": 3,
                       "sources": {FITS_UTC: 1, FILE_MTIME: 1, EXIF_LOCAL: 1}}
    update_call = [c for c in session.execute.call_args_list if len(c.args) == 2][0]
    assert update_call.args[1] == [
        {"id": 1, "capture_date_utc": datetime(2019, 10, 24, 19, 3, 15), "capture_time_source": FITS_UTC},
        {"id": 2, "capture_date_utc": None, "capture_time_source": FILE_MTIME},
        {"id": 3, "capture_date_utc": None, "capture_time_source": EXIF_LOCAL},
    ]
    session.commit.assert_called_once()
