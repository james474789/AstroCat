"""
Tests for app.services.rig_allocation (R0b): buckets by camera x derived
binning x calculated focal length, rig suggestions and key round-trips. Pure: no DB.
"""

import random
from datetime import datetime

from app.services.equipment_assignment import RigInfo
from app.services.rig_allocation import frame_optics, group_unassigned, members_by_key, reference_sensor, known_sensors

ASI2600 = ["zwo asi2600mm pro"]
ASI294 = ["zwo asi294mm pro"]
# 3.76 um at 530 mm -> 1.463"/px; at 2000 mm -> 0.388"/px.
RASA = RigInfo(id=1, camera_id=10, patterns=ASI2600, sensor_width_px=6248, sensor_height_px=4176,
               pixel_size_um=3.76, focal_length_mm=530)
SCT = RigInfo(id=2, camera_id=10, patterns=ASI2600, sensor_width_px=6248, sensor_height_px=4176,
              pixel_size_um=3.76, focal_length_mm=2000)
# The ASI294MM: locked 4.63 um 4144x2822 and unlocked 2.315 um 8288x5644 modes.
C11_LOCKED = RigInfo(id=3, camera_id=11, patterns=ASI294, sensor_width_px=4144, sensor_height_px=2822,
                     pixel_size_um=4.63, focal_length_mm=900)
EF200_UNLOCKED = RigInfo(id=4, camera_id=12, patterns=ASI294, sensor_width_px=8288, sensor_height_px=5644,
                         pixel_size_um=2.315, focal_length_mm=200)
RIGS = [RASA, SCT, C11_LOCKED, EF200_UNLOCKED]

_ids = iter(range(1, 100_000))


def _row(camera="ZWO ASI2600MM Pro", w=6248, h=4176, scale=None, subtype="SUB_FRAME", **kw):
    row = {"id": next(_ids), "camera_name": camera, "width_pixels": w, "height_pixels": h, "binning": None,
           "pixel_scale_arcsec": scale, "xpixsz": None, "focallen": None, "focal_length": None,
           "subtype": subtype, "filter_name": "Ha", "exposure_time_seconds": 300.0,
           "capture_date": datetime(2026, 1, 1, 22, 0)}
    row.update(kw)
    return row


def _one(rows, rigs=RIGS):
    groups = group_unassigned(rows, rigs)
    assert len(groups) == 1, [g["key"] for g in groups]
    return groups[0]


# --- per-frame optics --------------------------------------------------------

def test_binning_is_derived_from_the_native_sensor_not_the_header():
    ref = reference_sensor("ZWO ASI294MM Pro", known_sensors(RIGS))
    assert (ref.width, ref.height) == (8288, 5644)
    # Older and newer drivers label the same 4144x2822 mode bin 1 and bin 2.
    a = frame_optics(_row("ZWO ASI294MM Pro", 4144, 2822, scale=1.06, binning="1x1", xpixsz=4.63), ref)
    b = frame_optics(_row("ZWO ASI294MM Pro", 4144, 2822, scale=1.06, binning="2x2", xpixsz=4.63), ref)
    assert a["bin"] == b["bin"] == 2
    assert round(a["focal"]) == round(b["focal"]) == 901


def test_focal_is_calculated_from_scale_then_header():
    ref = reference_sensor("ZWO ASI2600MM Pro", known_sensors(RIGS))
    solved = frame_optics(_row(scale=1.463), ref)
    assert solved["focal_basis"] == "scale" and abs(solved["focal"] - 530) < 1
    unsolved = frame_optics(_row(focallen=530), ref)
    assert unsolved["focal_basis"] == "header" and unsolved["focal"] == 530
    assert frame_optics(_row(), ref)["focal"] is None


def test_unknown_camera_uses_xpixsz_and_header_binning():
    opt = frame_optics(_row("ASI Camera (1)", 4656, 3520, scale=1.77, binning="1x1", xpixsz=3.8), None)
    assert opt["bin"] == 1 and round(opt["focal"]) == 443


# --- buckets -----------------------------------------------------------------

def test_subs_and_cropped_masters_share_a_bucket_by_focal():
    g = _one([_row(scale=1.46), _row(scale=1.47), _row(w=6000, h=4000, scale=1.465, subtype="INTEGRATION_MASTER")])
    assert (g["count"], g["sub_count"], g["master_count"]) == (3, 2, 1)
    assert g["frame_size_count"] == 2 and g["frame_sizes"][0] == "6248x4176"
    assert (g["suggested_rig_id"], g["suggestion_basis"]) == (1, "focal")


def test_header_binning_does_not_split_one_rig():
    rows = ([_row("ZWO ASI294MM Pro", 4144, 2822, scale=1.06, binning="1x1", xpixsz=4.63) for _ in range(3)]
            + [_row("ZWO ASI294MM Pro", 4144, 2822, scale=1.06, binning="2x2", xpixsz=4.63) for _ in range(2)])
    g = _one(rows)
    assert g["count"] == 5 and g["binning"] == 2
    assert g["suggested_rig_id"] == 3  # the locked-mode rig: same 4.63 um effective pixel, 900 mm


def test_focal_clusters_split_beyond_five_percent():
    assert len(group_unassigned([_row(scale=s) for s in (1.46, 1.48, 1.50)], RIGS)) == 1
    assert len(group_unassigned([_row(scale=s) for s in (1.46, 1.60)], RIGS)) == 2


def test_drizzled_master_lands_in_its_own_bucket():
    rows = [_row(scale=1.463), _row(w=12496, h=8352, scale=0.7315, xpixsz=3.76, subtype="INTEGRATION_MASTER")]
    groups = group_unassigned(rows, RIGS)
    assert len(groups) == 2
    drizzled = next(g for g in groups if g["master_count"])
    assert round(drizzled["focal_mm"]) == 1060 and drizzled["suggested_rig_id"] is None


def test_unknown_focal_is_one_bucket_per_camera_and_binning():
    groups = group_unassigned([_row(), _row(), _row("QHY5LII-C", 1280, 960)], RIGS)
    assert sorted(g["count"] for g in groups) == [1, 2]
    assert all(g["key"].endswith("|-") and g["focal_mm"] is None for g in groups)
    assert all(g["suggested_rig_id"] is None for g in groups)


def test_null_camera_is_a_valid_partition():
    g = _one([_row(camera=None, w=None, h=None, scale=1.3, xpixsz=3.76)])
    assert g["camera_name"] is None and g["key"].startswith("none|b1|")
    assert g["reason"] == "no_camera_match" and g["suggested_rig_id"] is None


def test_no_suggestion_outside_focal_tolerance_or_for_other_binning():
    assert _one([_row(scale=1.30)])["suggested_rig_id"] is None  # ~596 mm: 12% from the 530 mm rig
    # Unlocked full-res frame at 900 mm: 2.315 um pixel doesn't match the locked 900 mm rig.
    g = _one([_row("ZWO ASI294MM Pro", 8288, 5644, scale=0.5305, xpixsz=2.315)])
    assert g["binning"] == 1 and g["suggested_rig_id"] is None


def test_exact_when_assign_rig_would_succeed():
    g = _one([_row(scale=0.388)])
    assert (g["suggested_rig_id"], g["suggestion_basis"]) == (2, "exact")


def test_fields_and_sort_order():
    rows = ([_row(scale=1.46, filter_name=f) for f in ("OIII", "Ha", "Ha", None)] + [_row(scale=0.39)])
    groups = group_unassigned(rows, RIGS)
    assert [g["count"] for g in groups] == [4, 1]
    g = groups[0]
    assert g["filters"] == ["Ha", "OIII"]
    assert g["total_exposure_s"] == 1200.0
    assert g["focal_basis"] == "scale"
    assert g["first_capture"] == "2026-01-01T22:00:00"
    assert g["sample_image_id"] in {r["id"] for r in rows[:4]}


# --- keys --------------------------------------------------------------------

def _mixed_rows():
    return ([_row(scale=s) for s in (1.45, 1.46, 1.47, 0.39, 0.40)]
            + [_row(w=12496, h=8352, scale=s, xpixsz=3.76, subtype="INTEGRATION_MASTER") for s in (0.73, 0.74)]
            + [_row(), _row(camera=None, w=None, h=None)]
            + [_row("ZWO ASI294MM Pro", 4144, 2822, scale=1.06, binning=b, xpixsz=4.63) for b in ("1x1", "2x2")]
            + [_row("ASI Camera (1)", 4656, 3520, focallen=400)])


def test_members_by_key_round_trip_every_bucket():
    rows = _mixed_rows()
    groups = group_unassigned(rows, RIGS)
    found = members_by_key(rows, RIGS, [g["key"] for g in groups])
    assert len({g["key"] for g in groups}) == len(groups) == len(found)
    for g in groups:
        assert len(found[g["key"]]) == g["count"]
    assert sorted(i for ids in found.values() for i in ids) == sorted(r["id"] for r in rows)


def test_unknown_keys_are_left_out():
    rows = _mixed_rows()
    key = group_unassigned(rows, RIGS)[0]["key"]
    assert set(members_by_key(rows, RIGS, [key, "nope|b1|1.0"])) == {key}


def test_keys_are_independent_of_row_order():
    rows = _mixed_rows()
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    a, b = group_unassigned(rows, RIGS), group_unassigned(shuffled, RIGS)
    assert [g["key"] for g in a] == [g["key"] for g in b]
    keys = [g["key"] for g in a]
    ma, mb = members_by_key(rows, RIGS, keys), members_by_key(shuffled, RIGS, keys)
    assert {k: sorted(v) for k, v in ma.items()} == {k: sorted(v) for k, v in mb.items()}
