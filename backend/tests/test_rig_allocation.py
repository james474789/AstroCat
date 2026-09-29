"""
Tests for app.services.rig_allocation (R0b, docs/design/R0b-rig-allocation.md §7.1).
Pure: no DB.
"""

import random
from datetime import datetime

from app.services.equipment_assignment import RigInfo, assign_rig
from app.services.rig_allocation import group_unassigned, member_ids, suggest_rig

ASI2600 = ["zwo asi2600mm pro"]
# 3.76 um at 530 mm -> 1.463"/px; at 2000 mm -> 0.388"/px.
RASA = RigInfo(id=1, camera_id=10, patterns=ASI2600, sensor_width_px=6248, sensor_height_px=4176,
               pixel_size_um=3.76, focal_length_mm=530)
SCT = RigInfo(id=2, camera_id=10, patterns=ASI2600, sensor_width_px=6248, sensor_height_px=4176,
              pixel_size_um=3.76, focal_length_mm=2000)
SCT_OFF = RigInfo(id=3, camera_id=10, patterns=ASI2600, sensor_width_px=6248, sensor_height_px=4176,
                  pixel_size_um=3.76, focal_length_mm=2000, is_active=False)
RIGS = [RASA, SCT]


def _sample(camera="ZWO ASI2600MM Pro", w=6000, h=4000, scale=None, **kw):
    return {"camera_name": camera, "width_pixels": w, "height_pixels": h, "binning": None,
            "pixel_scale_arcsec": scale, "xpixsz": None, "focallen": None, "focal_length": None, **kw}


_next_id = iter(range(1, 10_000))


def _row(subtype="INTEGRATION_MASTER", scale=None, **kw):
    return {"id": next(_next_id), "subtype": subtype, "filter_name": "Ha", "exposure_time_seconds": 300.0,
            "capture_date": datetime(2026, 1, 1, 22, 0), **_sample(scale=scale, **kw)}


# --- suggest_rig -------------------------------------------------------------

def test_cropped_master_is_suggested_by_scale():
    sample = _sample(scale=1.47)
    assert assign_rig(sample, RIGS) == (None, "no_camera_match")
    assert suggest_rig(sample, RIGS) == (1, "scale")


def test_drizzled_master_is_suggested_by_focal_length():
    sample = _sample(w=12496, h=8352, scale=0.7317, focallen=530)
    assert suggest_rig(sample, RIGS) == (1, "focal")


def test_two_active_rigs_without_evidence_is_no_suggestion():
    assert suggest_rig(_sample(), RIGS) == (None, None)


def test_single_active_rig_on_camera_is_suggested():
    assert suggest_rig(_sample(), [RASA.__class__(**{**RASA.__dict__, "is_active": False}), SCT]) == (2, "camera")
    assert suggest_rig(_sample(), [SCT_OFF, RASA]) == (1, "camera")


def test_only_inactive_candidate_is_still_suggested():
    assert suggest_rig(_sample(), [SCT_OFF]) == (3, "camera")


def test_unknown_camera_is_no_suggestion():
    assert suggest_rig(_sample(camera="Canon EOS R7", scale=1.46), RIGS) == (None, None)
    assert suggest_rig(_sample(camera=None, scale=1.46), RIGS) == (None, None)


def test_exact_when_assign_rig_would_succeed():
    assert suggest_rig(_sample(w=6248, h=4176, scale=1.46), RIGS) == (1, "exact")


def test_scale_outside_suggest_tolerance_falls_through():
    # 1.0"/px is >25% from both rigs; no focal evidence; two active rigs.
    assert suggest_rig(_sample(scale=1.0), RIGS) == (None, None)


# --- group_unassigned --------------------------------------------------------

def test_subs_and_masters_are_separate_groups():
    rows = [_row("SUB_FRAME", scale=1.46), _row("INTEGRATION_MASTER", scale=1.46)]
    groups = group_unassigned(rows, RIGS)
    assert sorted(g["subtype"] for g in groups) == ["INTEGRATION_MASTER", "SUB_FRAME"]


def test_close_scales_cluster_and_far_scales_split():
    assert len(group_unassigned([_row(scale=s) for s in (1.20, 1.22, 1.24)], RIGS)) == 1
    assert len(group_unassigned([_row(scale=s) for s in (1.20, 1.40)], RIGS)) == 2


def test_unsolved_rows_form_their_own_group():
    groups = group_unassigned([_row(scale=1.46), _row(), _row()], RIGS)
    unsolved = [g for g in groups if g["scale_min"] is None]
    assert len(unsolved) == 1
    assert unsolved[0]["count"] == 2 and unsolved[0]["key"].endswith("|-")
    assert unsolved[0]["scale_max"] is None and unsolved[0]["scale_median"] is None


def test_null_camera_is_a_valid_partition():
    groups = group_unassigned([_row(camera=None, w=None, h=None, scale=1.3)], RIGS)
    assert len(groups) == 1
    g = groups[0]
    assert g["camera_name"] is None
    assert g["key"] == "INTEGRATION_MASTER|none|nonexnone|none|1.3000"
    assert g["reason"] == "no_camera_match" and g["suggested_rig_id"] is None


def test_groups_sorted_by_count_and_fields_filled():
    rows = ([_row("SUB_FRAME", scale=1.46, filter_name=f) for f in ("OIII", "Ha", "Ha", None)]
            + [_row(scale=0.39)])
    groups = group_unassigned(rows, RIGS)
    assert [g["count"] for g in groups] == [4, 1]
    g = groups[0]
    assert g["filters"] == ["Ha", "OIII"]
    assert g["total_exposure_s"] == 1200.0
    assert g["reason"] == "no_camera_match"
    assert (g["suggested_rig_id"], g["suggestion_basis"]) == (1, "scale")
    assert g["camera_name"] == "ZWO ASI2600MM Pro"
    assert g["first_capture"] == "2026-01-01T22:00:00"
    assert g["sample_image_id"] in {r["id"] for r in rows[:4]}
    assert groups[1]["suggested_rig_id"] == 2


def test_focal_mm_is_median_of_known_focals():
    rows = [_row(scale=1.46, focallen=530), _row(scale=1.46, focal_length=540), _row(scale=1.46)]
    assert group_unassigned(rows, RIGS)[0]["focal_mm"] == 535.0


# --- member_ids / keys -------------------------------------------------------

def _mixed_rows():
    return ([_row("SUB_FRAME", scale=s) for s in (1.45, 1.46, 1.47, 0.39, 0.40)]
            + [_row("INTEGRATION_MASTER", scale=s, w=12496, h=8352) for s in (0.73, 0.74)]
            + [_row("SUB_FRAME"), _row("SUB_FRAME", camera=None, w=None, h=None)]
            + [_row("SUB_FRAME", scale=1.46, binning="2")])


def test_member_ids_round_trip_every_group():
    rows = _mixed_rows()
    groups = group_unassigned(rows, RIGS)
    seen = []
    for g in groups:
        ids = member_ids(rows, RIGS, g["key"])
        assert ids is not None and len(ids) == g["count"]
        seen.extend(ids)
    assert sorted(seen) == sorted(r["id"] for r in rows)
    assert len({g["key"] for g in groups}) == len(groups)


def test_unknown_key_is_none():
    assert member_ids(_mixed_rows(), RIGS, "SUB_FRAME|nope|1x1|none|1.0000") is None


def test_keys_are_independent_of_row_order():
    rows = _mixed_rows()
    before = {g["key"]: sorted(member_ids(rows, RIGS, g["key"])) for g in group_unassigned(rows, RIGS)}
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    after = {g["key"]: sorted(member_ids(shuffled, RIGS, g["key"])) for g in group_unassigned(shuffled, RIGS)}
    assert before == after
    assert [g["key"] for g in group_unassigned(rows, RIGS)] == [g["key"] for g in group_unassigned(shuffled, RIGS)]
