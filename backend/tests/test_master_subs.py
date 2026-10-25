"""
Master -> subs matching (no DB for the rules; one route/intersection check through the API helper).
"""

import asyncio
import sys
import types
from types import SimpleNamespace

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.services import master_subs as ms  # noqa: E402


def _img(id, ra=312.55, dec=45.17, scale=6.5, rot=92.8, w=6984, h=4660, solved=True,
         target="NGC7000", rig=9, camera="Canon EOS R7"):
    return SimpleNamespace(
        id=id, is_plate_solved=solved, ra_center_degrees=ra if solved else None,
        dec_center_degrees=dec if solved else None, field_radius_degrees=None,
        pixel_scale_arcsec=scale if solved else None, rotation_degrees=rot if solved else None,
        width_pixels=w, height_pixels=h, target_key=target, rig_id=rig, camera_name=camera,
    )


MASTER = _img(1)


def test_same_framing_matches():
    assert ms.is_sub_of(MASTER, _img(2, ra=312.6, dec=45.1, scale=6.47))


def test_master_is_not_its_own_sub():
    assert not ms.is_sub_of(MASTER, MASTER)


def test_meridian_flip_matches():
    assert ms.is_sub_of(MASTER, _img(2, rot=-88.5))


def test_mirrored_rotation_matches():
    # IC1805-style sidecar solves: sub = 180 - master (mod 180)
    master = _img(1, rot=320.0)
    assert ms.is_sub_of(master, _img(2, rot=40.0))
    assert ms.is_sub_of(master, _img(3, rot=220.0))
    assert ms.is_sub_of(master, _img(4, rot=140.0))


def test_different_rotation_does_not_match():
    assert not ms.is_sub_of(MASTER, _img(2, rot=60.0))


def test_unknown_rotation_skips_the_check():
    assert ms.is_sub_of(MASTER, _img(2, rot=None))


def test_cropped_master_matches_within_window():
    master = _img(1, w=int(5184 * 0.9), h=int(3456 * 0.9))
    assert ms.is_sub_of(master, _img(2, w=5184, h=3456))
    heavy = _img(1, w=int(5184 * 0.8), h=int(3456 * 0.8))
    assert not ms.is_sub_of(heavy, _img(2, w=5184, h=3456))


def test_master_larger_than_sub_does_not_match():
    assert not ms.is_sub_of(_img(1, w=6984 * 2, h=4660 * 2), _img(2))


def test_scale_off_by_five_percent_does_not_match():
    assert not ms.is_sub_of(MASTER, _img(2, scale=6.5 * 1.05))


def test_centre_offset_beyond_tenth_of_radius_does_not_match():
    # field radius ~7.6 deg -> limit ~0.76 deg
    assert ms.is_sub_of(MASTER, _img(2, dec=45.17 + 0.5))
    assert not ms.is_sub_of(MASTER, _img(2, dec=45.17 + 1.0))


def test_unsolved_sub_matches_on_target_rig_and_size():
    assert ms.is_sub_of(MASTER, _img(2, solved=False))
    assert not ms.is_sub_of(MASTER, _img(2, solved=False, rig=3))
    assert not ms.is_sub_of(MASTER, _img(2, solved=False, target="M31"))
    assert not ms.is_sub_of(MASTER, _img(2, solved=False, w=4000))


def test_unsolved_path_falls_back_to_camera_without_rig():
    master = _img(1, rig=None)
    assert ms.is_sub_of(master, _img(2, solved=False, rig=None))
    assert not ms.is_sub_of(master, _img(2, solved=False, rig=None, camera="Canon EOS 6D"))


def test_unsolved_master_matches_solved_subs_by_target():
    master = _img(1, solved=False)
    assert ms.is_sub_of(master, _img(2))
    assert not ms.is_sub_of(master, _img(2, rig=3))


def test_master_without_target_or_solve_matches_nothing():
    master = _img(1, solved=False, target=None)
    assert not ms.is_sub_of(master, _img(2, solved=False, target=None))
    assert not ms.is_sub_of(master, _img(2))


def test_restrict_ids_intersects_bucket_and_subs(monkeypatch):
    from app.api import equipment, images as api

    async def bucket(db, key):
        return [5, 3, 1]

    async def subs(db, master_id):
        assert master_id == 42
        return [1, 5, 7]

    monkeypatch.setattr(equipment, "bucket_image_ids", bucket)
    monkeypatch.setattr(ms, "find_sub_ids", subs)
    assert asyncio.run(api._restrict_ids(object(), None, 42)) == [1, 5, 7]
    assert asyncio.run(api._restrict_ids(object(), "k", 42)) == [5, 1]


def test_list_route_accepts_subs_of():
    import inspect
    from app.api import images as api

    for fn in (api.list_images, api.export_images_csv):
        assert "subs_of" in inspect.signature(fn).parameters
