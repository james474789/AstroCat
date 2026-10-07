"""
Field overlaps: footprints of other images inside an image's field (no DB).
"""

import math
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

try:  # auth_service imports PyJWT; it isn't needed for these tests.
    import jwt  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("jwt")
    _stub.PyJWTError = Exception
    sys.modules["jwt"] = _stub

from app.services import field_overlaps as fo  # noqa: E402


def _img(id, ra, dec, scale, rot, w=3000, h=2000, parity=1, subtype="SUB_FRAME", captured=None, **kw):
    return SimpleNamespace(
        id=id, file_name=f"img{id}.fits", object_name=kw.get("object_name"), subtype=subtype,
        capture_date=captured, exposure_time_seconds=300.0, filter_name="Ha",
        ra_center_degrees=ra, dec_center_degrees=dec, field_radius_degrees=None,
        pixel_scale_arcsec=scale, rotation_degrees=rot, width_pixels=w, height_pixels=h, parity=parity,
    )


# Wide field: 30"/px at 4000x3000 -> ~33 x 25 deg
WIDE = _img(1, 10.0, 40.0, 30.0, 20.0, w=4000, h=3000)


def _sky_at(img, x, y):
    return fo.pixel_to_sky(fo.tan_params(img), [[x, y]])[0]


@pytest.mark.parametrize("ra,dec,rot,parity", [(10.0, 40.0, 20.0, 1), (0.2, -10.0, 135.0, -1), (200.0, 85.0, 300.0, 1)])
def test_tan_matches_astropy_and_round_trips(ra, dec, rot, parity):
    from astropy.wcs import WCS

    img = _img(9, ra, dec, 10.0, rot, w=4000, h=3000, parity=parity)
    p = fo.tan_params(img)
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [p["w"] / 2.0 + 1, p["h"] / 2.0 + 1]  # FITS 1-based == our 0-based centre
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cd = p["cd"]

    xy = np.array([[0, 0], [4000, 0], [4000, 3000], [0, 3000], [1234, 2345], [2000, 1500]], dtype=float)
    sky = fo.pixel_to_sky(p, xy)
    a_ra, a_dec = wcs.pixel_to_world_values(xy[:, 0], xy[:, 1])
    dra = (sky[:, 0] - a_ra + 180) % 360 - 180
    assert np.max(np.abs(dra * np.cos(np.radians(a_dec)))) * 3600 / 10.0 < 0.5
    assert np.max(np.abs(sky[:, 1] - a_dec)) * 3600 / 10.0 < 0.5

    back = fo.sky_to_pixel(p, sky)
    assert np.allclose(back, xy, atol=0.01)


def test_small_field_inside_wide_field_is_kept():
    cx, cy = 1500, 1000
    ra, dec = _sky_at(WIDE, cx, cy)
    cand = _img(2, ra, dec, 1.0, 70.0)
    groups = fo.compute_field_overlaps(WIDE, [cand])["groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["shape"] == "polygon" and g["id"] == 2 and g["count"] == 1
    corners = np.array(g["corners"])
    assert np.allclose(corners.mean(axis=0), [cx, cy], atol=1.0)
    assert ((corners >= 0) & (corners <= [4000, 3000])).all()


def test_partial_overlap_kept_and_disjoint_dropped():
    # 5"/px 3000x2000 candidate is ~500x333 px in WIDE
    partial = _img(2, *_sky_at(WIDE, -100, 1500), 5.0, 0.0)
    disjoint = _img(3, *_sky_at(WIDE, -1000, 1500), 5.0, 0.0)
    ids = {g["id"] for g in fo.compute_field_overlaps(WIDE, [partial, disjoint])["groups"]}
    assert ids == {2}


def test_same_size_candidate_excluded():
    same = _img(2, *_sky_at(WIDE, 2100, 1500), 30.0, 20.0, w=4000, h=3000)
    assert fo.compute_field_overlaps(WIDE, [same])["groups"] == []


def test_same_framing_grouped_with_master_as_representative():
    ra, dec = _sky_at(WIDE, 1000, 800)
    t0 = datetime(2026, 1, 1)
    subs = [
        _img(10 + i, ra + 0.005 * (i % 3), dec - 0.004 * (i % 2), 2.0, 45.0 if i < 5 else 225.5,
             captured=t0 + timedelta(minutes=i))
        for i in range(10)
    ]
    master = _img(99, ra, dec, 2.0, 44.0, subtype="INTEGRATION_MASTER", captured=t0 - timedelta(days=1))
    other = _img(50, *_sky_at(WIDE, 3000, 2000), 2.0, 45.0)
    groups = fo.compute_field_overlaps(WIDE, subs + [master, other])["groups"]
    assert len(groups) == 2
    big = next(g for g in groups if g["count"] > 1)
    assert big["count"] == 11 and big["id"] == 99 and big["master_count"] == 1
    assert big["members"][0]["id"] == 99
    assert {m["id"] for m in big["members"]} == {99, *range(10, 20)}


def test_groups_sorted_largest_first():
    big = _img(2, *_sky_at(WIDE, 1000, 1000), 8.0, 0.0)
    small = _img(3, *_sky_at(WIDE, 3000, 2000), 1.0, 0.0)
    groups = fo.compute_field_overlaps(WIDE, [small, big])["groups"]
    assert [g["id"] for g in groups] == [2, 3]


def test_rotationless_candidate_becomes_circle():
    # half-diagonal of 3000x2000 at 5"/px = ~2.5 deg = ~300 px in WIDE (30"/px)
    inside = _img(2, *_sky_at(WIDE, 1500, 1500), 5.0, None)
    corner = _img(3, *_sky_at(WIDE, -100, -100), 5.0, None)
    disjoint = _img(4, *_sky_at(WIDE, -1000, -1000), 5.0, None)
    groups = {g["id"]: g for g in fo.compute_field_overlaps(WIDE, [inside, corner, disjoint])["groups"]}
    assert set(groups) == {2, 3}
    g = groups[2]
    assert g["shape"] == "circle" and g["corners"] is None
    assert np.allclose(g["center"], [1500, 1500], atol=0.5)
    expected_r = (np.hypot(3000, 2000) / 2 * 5.0 / 3600) * 3600 / 30.0
    assert g["radius_px"] == pytest.approx(expected_r, rel=1e-6)


def test_circles_and_polygons_not_grouped_together():
    ra, dec = _sky_at(WIDE, 1500, 1500)
    groups = fo.compute_field_overlaps(WIDE, [_img(2, ra, dec, 5.0, None), _img(3, ra, dec, 5.0, 10.0)])["groups"]
    assert sorted(g["shape"] for g in groups) == ["circle", "polygon"]


def test_member_ids_uncapped_for_circle_groups():
    ra, dec = _sky_at(WIDE, 1500, 1500)
    n = fo.MAX_MEMBERS + 10
    cands = [_img(100 + i, ra, dec, 5.0, None) for i in range(n)]
    (g,) = fo.compute_field_overlaps(WIDE, cands)["groups"]
    assert g["shape"] == "circle"
    assert len(g["members"]) == fo.MAX_MEMBERS
    assert sorted(g["member_ids"]) == [100 + i for i in range(n)]


def test_current_without_rotation_reports_no_rotation():
    cur = _img(1, 10.0, 40.0, 30.0, None, w=4000, h=3000)
    res = fo.compute_field_overlaps(cur, [_img(2, 10.0, 40.0, 1.0, 0.0)])
    assert res == {"groups": [], "reason": "no_rotation"}


def test_parity_from_text_column():
    img = _img(2, 10.0, 40.0, 1.0, 0.0, parity="-1")
    assert fo.tan_params(img)["cd"][0][0] > 0  # s_x = -scale * parity


def test_route_registered():
    from app.api import images as api

    paths = {r.path for r in api.router.routes}
    assert "/{image_id}/field-overlaps" in paths
    assert "/{image_id}/field-overlaps/solve" in paths


# ---- exact projection through the stored plate solution ---------------------------------------

from app.services import sky_overlay as so  # noqa: E402


def _solved_frame(ra, dec, scale_deg, w, h, crpix_offset=(0.0, 0.0), rot_deg=0.0):
    """A SkyFrame from a linear TAN solve whose reference pixel can sit off the image centre."""
    t = math.radians(rot_deg)
    c, s = math.cos(t), math.sin(t)
    header = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": ra, "CRVAL2": dec,
              "CRPIX1": w / 2 + 0.5 + crpix_offset[0], "CRPIX2": h / 2 + 0.5 + crpix_offset[1],
              "CD1_1": -scale_deg * c, "CD1_2": scale_deg * s, "CD2_1": scale_deg * s, "CD2_2": scale_deg * c,
              "IMAGEW": w, "IMAGEH": h}
    return so.resolve_frame(SimpleNamespace(wcs_header=header, raw_header=None, width_pixels=w, height_pixels=h,
                                            file_format="FITS", pixel_scale_arcsec=scale_deg * 3600))


def _corners_via(frame_cur, frame_cand):
    cw, ch = frame_cand.width, frame_cand.height
    ra, dec = frame_cand.unproject([0, cw, cw, 0], [0, 0, ch, ch])
    x, y = frame_cur.project(ra, dec)
    return np.column_stack([x, y])


def test_current_frame_is_used_instead_of_rebuilt_tan():
    # Very wide field whose reference pixel sits ~2 deg left of centre: the rebuilt TAN is turned
    cur_frame = _solved_frame(184.0, 58.0, 72 / 3600, 2000, 1335, crpix_offset=(-110, 0))
    cra, cdec = cur_frame.unproject([1000], [667])
    cur = _img(1, float(cra[0]), float(cdec[0]), 72.0, 0.0, w=2000, h=1335)
    # Small candidate near the edge, where the rotation error is largest
    kra, kdec = cur_frame.unproject([1800], [300])
    cand = _img(2, float(kra[0]), float(kdec[0]), 1.0, 0.0, w=3000, h=2000)
    rebuilt = fo.compute_field_overlaps(cur, [cand])["groups"][0]
    exact = fo.compute_field_overlaps(cur, [cand], cur_frame)["groups"][0]
    # The candidate's centre lands where the exact solution puts it
    cx, cy = np.mean(exact["corners"], axis=0)
    assert (cx, cy) == pytest.approx((1800, 300), abs=0.5)
    assert np.hypot(*(np.mean(rebuilt["corners"], axis=0) - [1800, 300])) > 5  # the old path was off


def test_current_frame_needs_no_rotation():
    cur_frame = _solved_frame(10.0, 40.0, 30 / 3600, 4000, 3000)
    cur = _img(1, 10.0, 40.0, 30.0, None, w=4000, h=3000)
    cand = _img(2, 10.2, 40.1, 1.0, 15.0)
    assert fo.compute_field_overlaps(cur, [cand])["reason"] == "no_rotation"
    result = fo.compute_field_overlaps(cur, [cand], cur_frame)
    assert result["reason"] is None and len(result["groups"]) == 1


def test_refine_corners_uses_candidate_solutions():
    cur_frame = _solved_frame(10.0, 40.0, 30 / 3600, 4000, 3000, crpix_offset=(-200, 50), rot_deg=5)
    cra, cdec = cur_frame.unproject([2000], [1500])
    cur = _img(1, float(cra[0]), float(cdec[0]), 30.0, 5.0, w=4000, h=3000)
    kra, kdec = cur_frame.unproject([1000, 3000, 1500], [1000, 1000, 2900])
    solved = _solved_frame(float(kra[0]), float(kdec[0]), 1 / 3600, 3000, 2000, rot_deg=33)
    unrotated = _solved_frame(float(kra[1]), float(kdec[1]), 1 / 3600, 3000, 2000, rot_deg=-20)
    groups = [
        {"id": 2, "shape": "polygon", "corners": [[0, 0], [1, 0], [1, 1], [0, 1]], "center": None,
         "radius_px": None, "area": 1.0, "member_ids": [2]},
        {"id": 3, "shape": "circle", "corners": None, "center": [3000, 1000], "radius_px": 20.0,
         "area": 1256.0, "member_ids": [3]},
        {"id": 4, "shape": "polygon", "corners": [[5, 5], [6, 5], [6, 6], [5, 6]], "center": None,
         "radius_px": None, "area": 1.0, "member_ids": [4]},
    ]
    # Projects fine (well inside the tangent hemisphere) but falls outside the 33 x 25 deg field: dropped
    (fra,), (fdec,) = cur_frame.unproject([-1500.0], [1500.0])
    far = _solved_frame(float(fra), float(fdec), 1 / 3600, 3000, 2000)
    out = fo.refine_corners(groups, cur, cur_frame, {2: solved, 3: unrotated, 5: far})
    by_id = {g["id"]: g for g in out}
    assert np.allclose(by_id[2]["corners"], _corners_via(cur_frame, solved), atol=1e-6)
    assert by_id[3]["shape"] == "polygon" and by_id[3]["center"] is None
    assert np.allclose(by_id[3]["corners"], _corners_via(cur_frame, unrotated), atol=1e-6)
    assert by_id[4]["corners"] == [[5, 5], [6, 5], [6, 6], [5, 6]]  # no solution: rebuilt outline kept
    assert by_id[3]["member_ids"] == [3]
    assert [g["area"] for g in out] == sorted((g["area"] for g in out), reverse=True)
    groups_far = [{**groups[0], "id": 5}]
    assert fo.refine_corners(groups_far, cur, cur_frame, {5: far}) == []
    # Can't be projected at all (other hemisphere): the rebuilt outline is kept rather than lost
    behind = _solved_frame(200.0, -40.0, 1 / 3600, 3000, 2000)
    assert fo.refine_corners(groups_far, cur, cur_frame, {5: behind}) == groups_far


# ---- Seen in (reverse lookup) --------------------------------------------------------------------

def _small_at(wide, x, y, id=50):
    """A 1"/px 3000x2000 image (~0.5 deg radius) centred on pixel (x, y) of `wide`."""
    return _img(id, *_sky_at(wide, x, y), 1.0, 70.0)


def test_seen_in_fully_covered_image_reports_full_coverage():
    small = _small_at(WIDE, 2000, 1500)
    out = fo.compute_seen_in(small, [WIDE])
    assert out["reason"] is None and len(out["groups"]) == 1
    g = out["groups"][0]
    assert g["id"] == WIDE.id and g["count"] == 1 and g["coverage"] == pytest.approx(1.0)


def test_seen_in_partial_overlap_and_disjoint():
    edge = _small_at(WIDE, 0, 1500)
    out = fo.compute_seen_in(edge, [WIDE])["groups"]
    assert len(out) == 1 and 0.3 < out[0]["coverage"] < 0.7
    outside = _small_at(WIDE, -3000, 1500)
    assert fo.compute_seen_in(outside, [WIDE])["groups"] == []


def test_seen_in_ignores_smaller_and_similar_sized_images():
    small = _small_at(WIDE, 2000, 1500)
    same_size = _img(3, small.ra_center_degrees, small.dec_center_degrees, 1.0, 70.0)
    smaller = _img(4, small.ra_center_degrees, small.dec_center_degrees, 0.2, 70.0)
    assert fo.compute_seen_in(small, [same_size, smaller, small])["groups"] == []


def test_seen_in_unrotated_candidate_uses_circle_and_needs_current_rotation():
    small = _small_at(WIDE, 2000, 1500)
    unrotated = _img(5, WIDE.ra_center_degrees, WIDE.dec_center_degrees, 30.0, None, w=4000, h=3000)
    unrotated.ra_center_degrees, unrotated.dec_center_degrees = small.ra_center_degrees, small.dec_center_degrees
    g = fo.compute_seen_in(small, [unrotated])["groups"]
    assert len(g) == 1 and g[0]["coverage"] == pytest.approx(1.0)
    assert fo.compute_seen_in(_img(6, 10.0, 40.0, 1.0, None), [WIDE])["reason"] == "no_rotation"


def test_seen_in_groups_same_framing_and_sorts_by_coverage():
    small = _small_at(WIDE, 2000, 1500)
    dup = _img(7, WIDE.ra_center_degrees, WIDE.dec_center_degrees, 30.0, 20.0, w=4000, h=3000,
               captured=datetime(2024, 1, 1))
    edge_wide = _img(8, *_sky_at(WIDE, 2000, 1500), 30.0, 20.0, w=4000, h=3000)
    # A differently framed wide image that only clips the small one
    edge = _img(9, *_sky_at(small, 9000, 1000), 5.0, 70.0, w=3000, h=2000)
    groups = fo.compute_seen_in(small, [WIDE, dup, edge, edge_wide])["groups"]
    assert [g["coverage"] for g in groups] == sorted((g["coverage"] for g in groups), reverse=True)
    top = groups[0]
    assert top["coverage"] == pytest.approx(1.0) and top["count"] == 3
    assert {m["id"] for m in top["members"]} == {1, 7, 8}
    assert all(g["coverage"] < 1.0 for g in groups[1:])
