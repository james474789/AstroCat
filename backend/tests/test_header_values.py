"""
Tests for app.utils.header_values (P0 §3.3), the site backfill (mocked
session), and the indexer's P0 hooks (capture time + site) on both the
existing-image and new-image branches, in the style of
test_frame_type_integration.py. No DB.

Coordinates are generic examples (Greenwich-ish), not real observing sites.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.utils.header_values import (
    derive_site,
    parse_exif_gps,
    parse_sexagesimal,
    site_name_from_header,
    valid_site,
)


def approx(x):
    return pytest.approx(x, abs=1e-6)


# ---------------------------------------------------------------------------
# parse_sexagesimal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (51.48, 51.48),
    (-0.5, -0.5),
    (0, 0.0),
    ("51.48", 51.48),
    ("-0.0015", -0.0015),
    ("56d0m0.000s N", 56.0),
    ("0d30m0.000s W", -0.5),
    ("3d0m0.000s E", 3.0),
    ("33d30m0.000s S", -33.5),
    ("+51 28 40", 51 + 28 / 60 + 40 / 3600),
    ("51:28:40", 51 + 28 / 60 + 40 / 3600),
    ("-0:00:05", -5 / 3600),
    ("-51 28 40", -(51 + 28 / 60 + 40 / 3600)),
    ("51°28'40\" N", 51 + 28 / 60 + 40 / 3600),
    ("N 51 28", 51 + 28 / 60),
    (list("56d0m0.000s N"), 56.0),
    (list("51.48"), 51.48),
])
def test_parse_sexagesimal(raw, expected):
    assert parse_sexagesimal(raw) == approx(expected)


@pytest.mark.parametrize("raw", [None, "", "   ", "unknown", "51 61 00", "1 2 3 4", "51 -28 40", True, [1, 2]])
def test_parse_sexagesimal_rejects(raw):
    assert parse_sexagesimal(raw) is None


# ---------------------------------------------------------------------------
# EXIF GPS
# ---------------------------------------------------------------------------

def test_parse_exif_gps_exifread_lists():
    raw = {
        "EXIF:GPS GPSLatitude": [51.0, 28.6667, 0.0],
        "EXIF:GPS GPSLatitudeRef": "N",
        "EXIF:GPS GPSLongitude": [0.0, 0.5, 0.0],
        "EXIF:GPS GPSLongitudeRef": "W",
    }
    lat, lon = parse_exif_gps(raw)
    assert lat == approx(51 + 28.6667 / 60)
    assert lon == approx(-0.5 / 60)


def test_parse_exif_gps_south_east_ratio_strings_and_char_refs():
    raw = {
        "EXIF:GPS GPSLatitude": ["33/1", "30/1", "0/1"],
        "EXIF:GPS GPSLatitudeRef": ["S"],
        "EXIF:GPS GPSLongitude": ["151/1", "12/1", "36/1"],
        "EXIF:GPS GPSLongitudeRef": "E",
    }
    lat, lon = parse_exif_gps(raw)
    assert lat == approx(-33.5)
    assert lon == approx(151 + 12 / 60 + 36 / 3600)


def test_parse_exif_gps_pillow_gpsinfo():
    raw = {"PIL:GPSInfo": {"1": "N", "2": [51.0, 30.0, 0.0], "3": "W", "4": [0.0, 6.0, 0.0]}}
    assert parse_exif_gps(raw) == (approx(51.5), approx(-0.1))


def test_parse_exif_gps_zero_zero_is_missing():
    raw = {
        "EXIF:GPS GPSLatitude": [0.0, 0.0, 0.0], "EXIF:GPS GPSLatitudeRef": "N",
        "EXIF:GPS GPSLongitude": [0.0, 0.0, 0.0], "EXIF:GPS GPSLongitudeRef": "E",
    }
    assert parse_exif_gps(raw) is None


def test_parse_exif_gps_missing():
    assert parse_exif_gps({}) is None
    assert parse_exif_gps(None) is None
    assert parse_exif_gps({"EXIF:GPS GPSLatitude": [51.0, 0, 0]}) is None


# ---------------------------------------------------------------------------
# valid_site / derive_site
# ---------------------------------------------------------------------------

def test_valid_site():
    assert valid_site(51.48, -0.0) == (51.48, -0.0)
    assert valid_site(0.0, 0.0) is None
    assert valid_site(91.0, 0.0) is None
    assert valid_site(51.0, 181.0) is None
    assert valid_site(None, 1.0) is None
    assert valid_site(float("nan"), 1.0) is None


def test_derive_site_metadata_wins():
    lat, lon, name = derive_site(
        {"site_lat": 51.48, "site_long": -0.01, "site_name": "Backyard"},
        {"SITELAT": "10d0m0.000s N", "SITELONG": "10d0m0.000s E"},
    )
    assert (lat, lon, name) == (51.48, -0.01, "Backyard")


def test_derive_site_from_fits_sexagesimal_header():
    lat, lon, name = derive_site({}, {"SITELAT": "56d0m0.000s N", "SITELONG": "0d30m0.000s W", "SITENAME": "Backyard"})
    assert (lat, lon, name) == (approx(56.0), approx(-0.5), "Backyard")


def test_derive_site_from_char_array_header_and_observat():
    lat, lon, name = derive_site({}, {"SITELAT": list("51.48"), "SITELONG": "-0.01", "OBSERVAT": "Club"})
    assert (lat, lon, name) == (approx(51.48), approx(-0.01), "Club")


def test_derive_site_falls_back_to_exif_gps():
    raw = {
        "EXIF:GPS GPSLatitude": [51.0, 30.0, 0.0], "EXIF:GPS GPSLatitudeRef": "N",
        "EXIF:GPS GPSLongitude": [0.0, 6.0, 0.0], "EXIF:GPS GPSLongitudeRef": "W",
    }
    assert derive_site({}, raw) == (approx(51.5), approx(-0.1), None)


def test_derive_site_zero_zero_and_out_of_range_are_missing():
    assert derive_site({"site_lat": 0.0, "site_long": 0.0}, {}) == (None, None, None)
    assert derive_site({}, {"SITELAT": "95", "SITELONG": "10"}) == (None, None, None)


def test_site_name_truncated_and_blank_ignored():
    assert site_name_from_header({"SITENAME": "   ", "OBSERVAT": "X" * 150}) == "X" * 100
    assert site_name_from_header(None) is None


# ---------------------------------------------------------------------------
# Site backfill (mocked session)
# ---------------------------------------------------------------------------

def test_backfill_image_sites_mocked_session():
    from app.scripts.backfill_sites import backfill_image_sites

    rows = [
        (1, {"SITELAT": "56d0m0.000s N", "SITELONG": "0d30m0.000s W", "SITENAME": "Backyard"}, None, None, None),
        (2, {"SITELAT": 0.0, "SITELONG": 0.0}, None, None, None),        # (0,0) -> nothing to write
        (3, {"EXIF:GPS GPSLatitude": [51.0, 30.0, 0.0], "EXIF:GPS GPSLatitudeRef": "N",
             "EXIF:GPS GPSLongitude": [0.0, 6.0, 0.0], "EXIF:GPS GPSLongitudeRef": "W"}, None, None, None),
    ]
    session = MagicMock()
    session.execute.return_value.all.side_effect = [rows]

    summary = backfill_image_sites(session=session, batch_size=1000)

    assert summary == {"processed": 3, "updated": 2, "with_coordinates": 2}
    update_call = [c for c in session.execute.call_args_list if len(c.args) == 2][0]
    batch = update_call.args[1]
    assert [b["id"] for b in batch] == [1, 3]
    assert batch[0]["site_latitude"] == approx(56.0) and batch[0]["site_longitude"] == approx(-0.5)
    assert batch[0]["site_name"] == "Backyard"
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Indexer hooks (capture time + site), both branches
# ---------------------------------------------------------------------------

from app.models.image import FrameType, Image, ImageSubtype  # noqa: E402
from app.tasks.indexer import _process_image_impl  # noqa: E402
from app.worker import celery_app  # noqa: E402,F401  (import order ensures app/worker loads)

FITS_HEADER = {
    "IMAGETYP": "LIGHT",
    "DATE-OBS": "2019-10-24T19:03:15",
    "DATE-LOC": "2019-10-24T20:03:15",
    "SITELAT": "56d0m0.000s N",
    "SITELONG": "0d30m0.000s W",
    "SITENAME": "Backyard",
}


def _fake_extractor(metadata, modified_at=None):
    extractor = MagicMock()
    extractor.extract.return_value = metadata
    extractor.get_file_stats.return_value = {"file_size_bytes": 123, "created_at": None, "modified_at": modified_at}
    return extractor


def _run_indexer(target, existing, extractor, generate_thumbnail=True):
    fake_session = MagicMock()
    fake_session.execute.return_value.scalar_one_or_none.return_value = existing
    added = []
    fake_session.add.side_effect = added.append
    with patch("app.tasks.indexer.get_extractor", return_value=extractor), \
         patch("app.tasks.indexer.SessionLocal") as mock_sl, \
         patch("app.services.thumbnails.ThumbnailGenerator.generate", return_value=None), \
         patch("app.services.targets.assign_target_sync"):
        mock_sl.return_value.__enter__.return_value = fake_session
        result = _process_image_impl(str(target), generate_thumbnail=generate_thumbnail)
    assert result["status"] == "completed"
    return existing if existing is not None else added[0]


def test_indexer_existing_branch_sets_capture_time_and_site(tmp_path):
    target = tmp_path / "Light_0001.fits"
    target.write_bytes(b"SIMULATED")
    existing = Image(id=1, file_path=str(target), file_name=target.name, frame_type=FrameType.LIGHT,
                     frame_type_source="HEADER", subtype=ImageSubtype.SUB_FRAME,
                     astrometry_status="NONE", is_plate_solved=False)
    metadata = {
        "raw_header": FITS_HEADER, "width_pixels": 100, "height_pixels": 100,
        "capture_date": datetime(2019, 10, 24, 19, 3, 15),
        "site_lat": parse_sexagesimal(FITS_HEADER["SITELAT"]),
        "site_long": parse_sexagesimal(FITS_HEADER["SITELONG"]),
        "site_name": "Backyard",
    }

    image = _run_indexer(target, existing, _fake_extractor(metadata))

    # sanitize_metadata turns datetimes into ISO strings before they reach the row.
    assert str(image.capture_date) == "2019-10-24T19:03:15"
    assert image.capture_date_utc == datetime(2019, 10, 24, 19, 3, 15)
    assert image.capture_time_source == "FITS_UTC"
    assert image.site_latitude == approx(56.0)
    assert image.site_longitude == approx(-0.5)
    assert image.site_name == "Backyard"


def test_indexer_new_branch_mtime_fallback_and_exif_gps(tmp_path):
    target = tmp_path / "planet_0001.png"
    target.write_bytes(b"SIMULATED")
    mtime = 1_600_000_000.0
    metadata = {
        "raw_header": {
            "EXIF:GPS GPSLatitude": [51.0, 30.0, 0.0], "EXIF:GPS GPSLatitudeRef": "N",
            "EXIF:GPS GPSLongitude": [0.0, 6.0, 0.0], "EXIF:GPS GPSLongitudeRef": "W",
        },
        "width_pixels": 100, "height_pixels": 100,
    }

    image = _run_indexer(target, None, _fake_extractor(metadata, modified_at=mtime))

    assert image.capture_date == datetime.fromtimestamp(mtime)
    assert image.capture_date_utc is None
    assert image.capture_time_source == "FILE_MTIME"
    assert image.site_latitude == approx(51.5)
    assert image.site_longitude == approx(-0.1)


def test_indexer_new_branch_without_thumbnail_does_not_crash(tmp_path):
    # Regression: a local `from app.models.image import ImageSubtype` inside the
    # thumbnail block made the name function-local, so inserting a new row
    # raised UnboundLocalError whenever that block's import didn't run.
    target = tmp_path / "planet_0002.png"
    target.write_bytes(b"SIMULATED")
    metadata = {"raw_header": {}, "width_pixels": 100, "height_pixels": 100}

    image = _run_indexer(target, None, _fake_extractor(metadata, modified_at=1_600_000_000.0),
                         generate_thumbnail=False)

    assert image.subtype == ImageSubtype.SUB_FRAME
