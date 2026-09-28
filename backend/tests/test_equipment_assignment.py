"""
Tests for app.services.equipment_assignment (R0 rig/site assignment and
timezone fill, spec §4.5 / §6). Generic site coordinates only.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.equipment_assignment import (
    CLOCK_LOCAL, CLOCK_UTC, RigInfo, SiteInfo, apply_equipment, assign_equipment_sync, assign_rig, assign_site,
    default_site_eligible_rigs, fill_capture_utc, infer_clock_mode, local_capture_time, local_to_utc,
    measured_scale_normalized,
)
from app.utils.rig_optics import build_filter_rig_rows_with_rigs, known_pixel_size, seed_sensor

ASI294 = ["zwo asi294mm pro"]
C11 = RigInfo(id=1, camera_id=10, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
              pixel_size_um=4.63, focal_length_mm=2800)
C11_REDUCED = RigInfo(id=2, camera_id=10, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
                      pixel_size_um=4.63, focal_length_mm=2800, modifier_factor=0.7)
EF200 = RigInfo(id=3, camera_id=11, patterns=ASI294, sensor_width_px=8288, sensor_height_px=5644,
                pixel_size_um=2.315, focal_length_mm=200)
R7 = RigInfo(id=4, camera_id=12, patterns=["canon eos r7"], sensor_width_px=6960, sensor_height_px=4640,
             pixel_size_um=3.2, focal_length_mm=105)

LONDON = SiteInfo(id=1, latitude=51.48, longitude=0.0, timezone="Europe/London", is_default=True)
PARIS = SiteInfo(id=2, latitude=48.85, longitude=2.35, timezone="Europe/Paris")

# Mirrors the live rig set: two ASI294MM Pro sensor modes sharing one match
# pattern (locked 4.63 um 4144x2822, unlocked 2.315 um 8288x5644).
LOCKED, UNLOCKED = 10, 11
RIG1 = RigInfo(id=1, camera_id=LOCKED, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
               pixel_size_um=4.63, focal_length_mm=2809)
RIG5 = RigInfo(id=5, camera_id=UNLOCKED, patterns=ASI294, sensor_width_px=8288, sensor_height_px=5644,
               pixel_size_um=2.315, focal_length_mm=194)
RIG6 = RigInfo(id=6, camera_id=LOCKED, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
               pixel_size_um=4.63, focal_length_mm=677)
RIG7 = RigInfo(id=7, camera_id=LOCKED, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
               pixel_size_um=4.63, focal_length_mm=28)
RIG8 = RigInfo(id=8, camera_id=LOCKED, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
               pixel_size_um=4.63, focal_length_mm=345, is_active=False)
ASI294_RIGS = [RIG1, RIG5, RIG6, RIG7, RIG8]


def _img(camera="ZWO ASI294MM Pro", w=4144, h=2822, scale=None, **kw):
    return {"camera_name": camera, "width_pixels": w, "height_pixels": h, "binning": None,
            "pixel_scale_arcsec": scale, **kw}


# --- rigs -------------------------------------------------------------------

def test_scale_picks_the_right_rig_of_two_on_one_camera():
    assert assign_rig(_img(scale=0.34), [C11, C11_REDUCED]) == (1, "scale_match")
    assert assign_rig(_img(scale=0.49), [C11, C11_REDUCED]) == (2, "scale_match")
    assert assign_rig(_img(scale=1.5), [C11, C11_REDUCED]) == (None, "scale_mismatch")


def test_dims_separate_sensor_modes():
    assert assign_rig(_img(w=8288, h=5644, scale=2.46), [C11, EF200]) == (3, "scale_match")
    assert assign_rig(_img(w=5644, h=8288, scale=2.46), [C11, EF200])[0] == 3   # portrait
    assert assign_rig(_img(w=4144, h=2822, scale=2.46), [C11, EF200]) == (None, "scale_mismatch")


def test_without_scale_two_rigs_is_ambiguous_one_rig_is_assigned():
    assert assign_rig(_img(), [C11, C11_REDUCED]) == (None, "ambiguous")
    assert assign_rig(_img(), [C11]) == (1, "single_rig_for_camera")
    inactive = RigInfo(**{**C11_REDUCED.__dict__, "is_active": False})
    assert assign_rig(_img(), [C11, inactive]) == (1, "single_rig_for_camera")


def test_without_scale_focallen_breaks_the_tie():
    assert assign_rig(_img(focallen="1960"), [C11, C11_REDUCED]) == (2, "focallen_match")
    assert assign_rig(_img(focallen=["2", "8", "0", "0"]), [C11, C11_REDUCED]) == (1, "focallen_match")


def test_camera_names_are_normalised_for_matching():
    r7 = _img(camera="Canon Canon EOS R7", w=6960, h=4640, scale=6.5)
    assert assign_rig(r7, [C11, R7]) == (4, "scale_match")
    assert assign_rig(_img(camera=None), [C11]) == (None, "no_camera_match")
    assert assign_rig(_img(camera="Canon EOS R8", w=6000, h=4000), [R7]) == (None, "no_camera_match")


def test_measured_scale_used_when_pixel_size_unknown():
    rig = RigInfo(id=9, camera_id=1, patterns=["mystery cam"], focal_length_mm=500, measured_scale_arcsec=1.5)
    assert assign_rig(_img(camera="Mystery Cam", scale=1.52), [rig]) == (9, "scale_match")


def test_relative_binning_not_header_picks_the_unlocked_rig():
    # 4144x2822 at 4.78"/px is bin 2 of the unlocked (2.315 um) sensor, not
    # bin 1 of the locked (4.63 um) one - regardless of what XBINNING says.
    img = _img(w=4144, h=2822, scale=4.78, binning="2")
    assert assign_rig(img, ASI294_RIGS) == (5, "scale_match")


def test_relative_binning_ignores_stale_xbinning_for_the_native_rig():
    # Same physical frame (4144x2822 native to rig 6's own camera): still
    # rig 6, whatever XBINNING an older or newer driver wrote.
    for header_binning in (None, "1", "2"):
        img = _img(w=4144, h=2822, scale=1.41, binning=header_binning)
        assert assign_rig(img, ASI294_RIGS) == (6, "scale_match")


def test_unlocked_native_frames_go_to_rig5():
    img = _img(w=8288, h=5644, scale=2.46)
    assert assign_rig(img, ASI294_RIGS) == (5, "scale_match")


def test_measured_scale_normalized_puts_bin1_and_bin2_on_one_basis():
    # Rig 5 (binning=1): a bin-1 frame at 2.46 and a bin-2 frame at 4.78
    # should normalise close together, not average out to something else.
    bin1 = measured_scale_normalized(_img(w=8288, h=5644, scale=2.46), RIG5)
    bin2 = measured_scale_normalized(_img(w=4144, h=2822, scale=4.78), RIG5)
    assert bin1 == pytest.approx(2.46, abs=0.01)
    assert bin2 == pytest.approx(2.39, abs=0.02)

    import statistics
    assert statistics.median([bin1, bin2]) == pytest.approx(2.4, abs=0.05)


def test_manual_rig_is_untouched():
    image = SimpleNamespace(
        rig_id=7, rig_source="MANUAL", camera_name="ZWO ASI294MM Pro", width_pixels=4144, height_pixels=2822,
        binning=None, pixel_scale_arcsec=0.34, raw_header={}, focal_length=None,
        site_latitude=None, site_longitude=None, site_id=None, capture_time_source="FITS_UTC",
        capture_date=None, capture_date_utc=None)
    apply_equipment(image, [C11], [LONDON])
    assert image.rig_id == 7 and image.rig_source == "MANUAL"

    image.rig_source, image.rig_id = None, None
    apply_equipment(image, [C11], [LONDON])
    assert image.rig_id == 1 and image.rig_source == "AUTO"


# --- sites ------------------------------------------------------------------

def test_site_within_10_km():
    assert assign_site(51.50, 0.05, [LONDON, PARIS]) == 1          # ~4 km
    assert assign_site(51.60, 0.20, [LONDON, PARIS]) is None       # ~19 km
    assert assign_site(48.86, 2.34, [LONDON, PARIS]) == 2
    assert assign_site(None, 0.0, [LONDON]) is None
    assert assign_site(0.0, 0.0, [LONDON]) is None


def test_default_site_rule_for_coordinateless_images():
    stats = [(1, 1, 95), (1, 2, 5), (2, 1, 50), (2, 2, 50), (3, None, 10)]
    assert default_site_eligible_rigs(stats, 1) == [1]
    assert default_site_eligible_rigs(stats, None) == []


def test_coordinateless_image_keeps_its_site():
    image = SimpleNamespace(
        rig_id=None, rig_source=None, camera_name=None, width_pixels=None, height_pixels=None, binning=None,
        pixel_scale_arcsec=None, raw_header=None, focal_length=None, site_latitude=None, site_longitude=None,
        site_id=1, capture_time_source="OTHER", capture_date=None, capture_date_utc=None)
    apply_equipment(image, [], [LONDON])
    assert image.site_id == 1


# --- timezone fill ----------------------------------------------------------

def test_exif_local_london_summer_and_winter():
    july = datetime(2026, 7, 10, 23, 30)
    jan = datetime(2026, 1, 10, 23, 30)
    assert fill_capture_utc("EXIF_LOCAL", july, "Europe/London") == datetime(2026, 7, 10, 22, 30)
    assert fill_capture_utc("EXIF_LOCAL", jan, "Europe/London") == jan
    assert local_to_utc(datetime(2026, 7, 1, 2, 0), "Europe/Paris") == datetime(2026, 7, 1, 0, 0)
    assert local_to_utc(july, "Not/AZone") is None


def test_utc_and_offset_sources_are_never_clobbered():
    t = datetime(2026, 7, 10, 23, 30)
    assert fill_capture_utc("FITS_UTC", t, "Europe/London") is None
    assert fill_capture_utc("EXIF_OFFSET", t, "Europe/London") is None
    assert fill_capture_utc("GPS_UTC", t, "Europe/London") is None
    assert fill_capture_utc("EXIF_LOCAL", t, "Europe/London", offset_value="+02:00") is None
    assert fill_capture_utc("EXIF_LOCAL", t, None) is None


def test_utc_clock_cameras():
    t = datetime(2026, 7, 10, 23, 30)
    assert fill_capture_utc("EXIF_LOCAL", t, "Europe/London", clock_mode=CLOCK_UTC) == t
    assert fill_capture_utc("EXIF_LOCAL", t, "Europe/London", clock_mode=CLOCK_LOCAL) == datetime(2026, 7, 10, 22, 30)


def test_infer_clock_mode():
    summer = [(datetime(2026, 7, d, 23, 0, 5), datetime(2026, 7, d, 23, 0), "Europe/London") for d in range(1, 6)]
    assert infer_clock_mode(summer) == CLOCK_UTC
    local = [(datetime(2026, 7, d, 23, 0), datetime(2026, 7, d, 22, 0), "Europe/London") for d in range(1, 6)]
    assert infer_clock_mode(local) == CLOCK_LOCAL
    winter = [(datetime(2026, 1, d, 23, 0), datetime(2026, 1, d, 23, 0), "Europe/London") for d in range(1, 9)]
    assert infer_clock_mode(winter) is None            # UTC == local in winter: no evidence
    assert infer_clock_mode(summer[:2]) is None        # too few votes


def test_local_capture_time_sources():
    assert local_capture_time("FITS_LOCAL", None, "2026-07-10T23:30:00") == datetime(2026, 7, 10, 23, 30)
    assert local_capture_time("EXIF_LOCAL", None, None, list("2026:07:10 23:30:00")) == datetime(2026, 7, 10, 23, 30)
    fallback = datetime(2026, 7, 10, 23, 30)
    assert local_capture_time("EXIF_LOCAL", fallback, None, None) == fallback
    assert local_capture_time("FITS_UTC", fallback) is None


def test_apply_fills_utc_once_site_known():
    image = SimpleNamespace(
        rig_id=None, rig_source=None, camera_name="Canon EOS R7", width_pixels=6960, height_pixels=4640,
        binning=None, pixel_scale_arcsec=None, focal_length=105.0,
        raw_header={"EXIF:EXIF DateTimeOriginal": "2026:07:10 23:30:00"},
        site_latitude=51.49, site_longitude=0.01, site_id=None, capture_time_source="EXIF_LOCAL",
        capture_date=datetime(2026, 7, 10, 23, 30), capture_date_utc=None)
    apply_equipment(image, [R7], [LONDON])
    assert (image.rig_id, image.rig_source, image.site_id) == (4, "AUTO", 1)
    assert image.capture_date_utc == datetime(2026, 7, 10, 22, 30)

    apply_equipment(image, [R7], [LONDON], {"canon eos r7": CLOCK_UTC})
    assert image.capture_date_utc == datetime(2026, 7, 10, 23, 30)


def test_indexer_hook_never_raises():
    session = MagicMock()
    session.begin_nested.side_effect = RuntimeError("db is down")
    from app.services import equipment_assignment as ea
    ea.invalidate_cache()
    assert assign_equipment_sync(session, SimpleNamespace(id=1)) is False


# --- targets integration (§4.8) ---------------------------------------------

def test_known_pixel_size_prefers_camera_lookup():
    assert known_pixel_size("ZWO ASI294MM Pro", lambda name: 2.315) == 2.315
    assert known_pixel_size("ZWO ASI294MM Pro", lambda name: None) == 4.63
    assert known_pixel_size("ZWO ASI294MM Pro", lambda name: 1 / 0) == 4.63
    assert seed_sensor("ZWO ASI294MM Pro", 8288, 5644)["pixel_um"] == 2.315
    assert seed_sensor("ZWO ASI294MM Pro", 4144, 2822)["pixel_um"] == 4.63


def test_filter_rig_rows_use_declared_rigs():
    buckets = [
        {"filter": "Ha", "camera": "ZWO ASI294MM Pro", "pixel_scale": 0.34, "pixel_size_um": 4.63,
         "subs": 10, "seconds": 3000.0, "rig_id": 1},
        {"filter": "Ha", "camera": "ZWO ASI294MM Pro", "pixel_scale": 0.35, "pixel_size_um": 4.63,
         "subs": 5, "seconds": 1500.0, "rig_id": 1},
        {"filter": "Ha", "camera": "ZWO ASI1600MM Pro", "pixel_scale": 2.27, "pixel_size_um": 3.8,
         "subs": 4, "seconds": 1200.0, "rig_id": None},
    ]
    rows = build_filter_rig_rows_with_rigs(
        buckets, {1: {"name": "C11 + ASI294", "camera": "ZWO ASI294MM Pro", "focal_length": 2800.0}})
    by_rig = {r["rig_id"]: r for r in rows}
    assert by_rig[1]["rig_name"] == "C11 + ASI294"
    assert by_rig[1]["subs"] == 15 and by_rig[1]["seconds"] == 4500.0
    assert by_rig[1]["focal_length"] == 2800 and by_rig[1]["pixel_scale"] == 0.34
    assert by_rig[None]["rig_name"] is None and by_rig[None]["focal_length"] == 345
    assert set(by_rig[None]) == set(by_rig[1])
