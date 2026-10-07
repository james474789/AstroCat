"""
Sky overlay: catalog objects projected through an image's own WCS (no DB).

The ground truth for the coordinate contract is an independent astropy WCS of
the same solution written directly on the native grid.
"""

import math
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.wcs import WCS

from app.services import sky_overlay as so

W, H = 4096, 2732          # native frame
GW, GH = 1024, 683         # downsampled solve grid (what astrometry.net saw)
SCALE = 4.0 / 3600.0       # deg per grid pixel


def _solver_header(cd=((-SCALE, 0.0), (0.0, SCALE)), crval=(83.8, -5.4), crpix=(500.25, 340.75),
                   sip=False, imagew=True):
    h = {
        "WCSAXES": 2, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "EQUINOX": 2000.0,
        "CRVAL1": crval[0], "CRVAL2": crval[1], "CRPIX1": crpix[0], "CRPIX2": crpix[1],
        "CUNIT1": "deg", "CUNIT2": "deg",
        "CD1_1": cd[0][0], "CD1_2": cd[0][1], "CD2_1": cd[1][0], "CD2_2": cd[1][1],
        "NAXIS": 0,
    }
    if imagew:
        h.update({"IMAGEW": GW, "IMAGEH": GH})
    if sip:
        h.update({
            "CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP",
            "A_ORDER": 2, "A_0_2": 2.0e-6, "A_1_1": -1.5e-6, "A_2_0": 3.0e-6,
            "B_ORDER": 2, "B_0_2": -2.5e-6, "B_1_1": 1.0e-6, "B_2_0": 1.2e-6,
        })
    return h


def _img(wcs_header=None, raw_header=None, w=W, h=H, file_format="FITS", pixel_scale=None):
    return SimpleNamespace(wcs_header=wcs_header, raw_header=raw_header, width_pixels=w, height_pixels=h,
                           file_format=file_format, pixel_scale_arcsec=pixel_scale)


def _row(catalog, designation, ra, dec, aliases=(), major=None, minor=None, pa=None, mag=None, otype=None, name=None):
    return {"catalog": catalog, "designation": designation, "aliases": list(aliases), "ra": ra, "dec": dec,
            "major": major, "minor": minor, "pa": pa, "magnitude": mag, "object_type": otype, "common_name": name}


# ---- coordinate contract -------------------------------------------------------------------

def test_reference_pixel_lands_on_rescaled_crpix():
    frame = so.resolve_frame(_img(_solver_header()))
    x, y = frame.project([83.8], [-5.4])
    assert x[0] == pytest.approx((500.25 - 0.5) * W / GW, abs=1e-6)
    assert y[0] == pytest.approx((340.75 - 0.5) * H / GH, abs=1e-6)


def test_matches_independent_native_wcs():
    """Same solution written on the native grid: CD shrinks by the factor, CRPIX moves by the contract."""
    hdr = _solver_header(cd=((-SCALE * 0.94, SCALE * 0.34), (SCALE * 0.34, SCALE * 0.94)))
    frame = so.resolve_frame(_img(hdr))
    fx, fy = W / GW, H / GH
    native = WCS(naxis=2)
    native.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    native.wcs.crval = [hdr["CRVAL1"], hdr["CRVAL2"]]
    native.wcs.crpix = [(hdr["CRPIX1"] - 0.5) * fx + 0.5, (hdr["CRPIX2"] - 0.5) * fy + 0.5]
    native.wcs.cd = [[hdr["CD1_1"] / fx, hdr["CD1_2"] / fy], [hdr["CD2_1"] / fx, hdr["CD2_2"] / fy]]

    rng = np.random.default_rng(1)
    px = rng.uniform(0, W, 50)
    py = rng.uniform(0, H, 50)
    ra, dec = native.all_pix2world(px - 0.5, py - 0.5, 0)  # continuous -> 0-based centres
    x, y = frame.project(ra, dec)
    assert np.max(np.abs(x - px)) < 1e-6
    assert np.max(np.abs(y - py)) < 1e-6


def test_sip_round_trip_is_sub_hundredth_pixel():
    frame = so.resolve_frame(_img(_solver_header(sip=True)))
    rng = np.random.default_rng(2)
    px = rng.uniform(0, W, 200)
    py = rng.uniform(0, H, 200)
    ra, dec = frame.unproject(px, py)
    x, y = frame.project(ra, dec)
    assert np.max(np.hypot(x - px, y - py)) < 0.01


def test_sip_distortion_is_applied():
    """With SIP the corner moves by native pixels versus the linear solution (~0.7 grid px here)."""
    lin = so.resolve_frame(_img(_solver_header()))
    sip = so.resolve_frame(_img(_solver_header(sip=True)))
    ra, dec = lin.unproject([10.0], [10.0])
    x, y = sip.project(ra, dec)
    assert math.hypot(x[0] - 10.0, y[0] - 10.0) > 1.0


def test_point_behind_tangent_plane_is_nan():
    frame = so.resolve_frame(_img(_solver_header()))
    x, y = frame.project([83.8 + 180.0], [5.4])
    assert np.isnan(x[0]) and np.isnan(y[0])


def test_field_circle_covers_corners():
    frame = so.resolve_frame(_img(_solver_header()))
    ra, dec, radius = frame.field_circle()
    half_diag = math.hypot(GW, GH) / 2 * 4.0 / 3600.0
    assert radius == pytest.approx(half_diag, rel=0.05)


# ---- orientation (FITS row 1 is the top display row) ---------------------------------------

def test_orientation_follows_cd_matrix():
    # CD2_2 > 0: Dec increases with FITS y, which runs DOWN the display
    frame = so.resolve_frame(_img(_solver_header()))
    _, y_n = frame.project([83.8], [-5.3])
    _, y_0 = frame.project([83.8], [-5.4])
    assert y_n[0] > y_0[0]
    # CD1_1 < 0: east (higher RA) is to the left
    x_e, _ = frame.project([83.9], [-5.4])
    x_0, _ = frame.project([83.8], [-5.4])
    assert x_e[0] < x_0[0]
    # Mirrored solution: east to the right
    mirrored = so.resolve_frame(_img(_solver_header(cd=((SCALE, 0.0), (0.0, SCALE)))))
    x_e, _ = mirrored.project([83.9], [-5.4])
    x_0, _ = mirrored.project([83.8], [-5.4])
    assert x_e[0] > x_0[0]


def _axis_angle(e):
    """Major-axis direction in display degrees, folded into [0, 180)."""
    return e["angle_deg"] % 180.0


@pytest.mark.parametrize("pa,expected", [(0.0, 90.0), (90.0, 0.0), (45.0, 135.0)])
def test_ellipse_position_angle(pa, expected):
    # North down, east left on the display: PA 0 (N-S) is vertical, PA 90 (E-W) horizontal,
    # PA 45 (towards NE = down-left) runs top-right to bottom-left
    frame = so.resolve_frame(_img(_solver_header()))
    e = so.ellipse_for(frame, 83.8, -5.4, 10.0, 4.0, pa)
    assert e["pa_known"]
    assert _axis_angle(e) == pytest.approx(expected, abs=0.5)
    assert e["rx"] == pytest.approx(5.0 * 60 / 4.0 * W / GW, rel=0.01)  # 5' semi-major at 4"/grid px
    assert e["ry"] == pytest.approx(2.0 * 60 / 4.0 * W / GW, rel=0.01)


def test_ellipse_follows_rotation_and_parity():
    rot = math.radians(30.0)
    c, s = math.cos(rot), math.sin(rot)
    rotated = so.resolve_frame(_img(_solver_header(cd=((-SCALE * c, SCALE * s), (SCALE * s, SCALE * c)))))
    base = so.resolve_frame(_img(_solver_header()))
    a0 = _axis_angle(so.ellipse_for(base, 83.8, -5.4, 10.0, 4.0, 20.0))
    a1 = _axis_angle(so.ellipse_for(rotated, 83.8, -5.4, 10.0, 4.0, 20.0))
    assert abs(((a1 - a0) + 90) % 180 - 90) == pytest.approx(30.0, abs=0.5)
    mirrored = so.resolve_frame(_img(_solver_header(cd=((SCALE, 0.0), (0.0, SCALE)))))
    am = _axis_angle(so.ellipse_for(mirrored, 83.8, -5.4, 10.0, 4.0, 20.0))
    assert am == pytest.approx((180.0 - a0) % 180.0, abs=0.5)


def test_ellipse_without_pa_is_mean_circle():
    frame = so.resolve_frame(_img(_solver_header()))
    e = so.ellipse_for(frame, 83.8, -5.4, 10.0, 6.0, None)
    assert not e["pa_known"]
    assert e["rx"] == pytest.approx(e["ry"])
    assert e["rx"] == pytest.approx(4.0 * 60 / 4.0 * W / GW, rel=0.01)
    assert so.ellipse_for(frame, 83.8, -5.4, None, None, None) is None


# ---- source selection ----------------------------------------------------------------------

def test_solver_header_is_not_merged_with_raw_header():
    raw = {"CRVAL1": 10.0, "CRVAL2": 10.0, "CDELT1": -0.1, "CDELT2": 0.1, "PC1_1": 0.0, "PC1_2": 1.0,
           "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRPIX1": 1.0, "CRPIX2": 1.0, "NAXIS1": W, "NAXIS2": H}
    alone = so.resolve_frame(_img(_solver_header()))
    both = so.resolve_frame(_img(_solver_header(), raw_header=raw))
    assert both.source == so.SOURCE_SOLVER and both.accuracy_warning is None
    assert np.allclose(both.project([83.85], [-5.35]), alone.project([83.85], [-5.35]))


def test_missing_imagew_is_inferred_from_plate_scale_and_flagged():
    frame = so.resolve_frame(_img(_solver_header(imagew=False), pixel_scale=4.0 * GW / W))
    assert frame.grid_w == pytest.approx(GW)
    assert frame.grid_h == pytest.approx(GW * H / W)
    assert so.WARN_GRID_INFERRED in frame.accuracy_warning


def test_missing_imagew_without_scale_assumes_full_frame():
    frame = so.resolve_frame(_img(_solver_header(imagew=False)))
    assert (frame.grid_w, frame.grid_h) == (W, H)
    assert so.WARN_GRID_ASSUMED in frame.accuracy_warning


def _raw_wcs(**extra):
    h = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": "83.8", "CRVAL2": "-5.4",
         "CRPIX1": W / 2, "CRPIX2": H / 2, "CD1_1": -SCALE / 4, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": SCALE / 4,
         "NAXIS1": W, "NAXIS2": H, "COMMENT": ["a", "b"], "OBJECT": "M42"}
    h.update(extra)
    return h


def test_header_wcs_is_used_and_warned():
    frame = so.resolve_frame(_img(raw_header=_raw_wcs()))
    assert frame.source == so.SOURCE_HEADER
    assert (frame.grid_w, frame.grid_h) == (W, H)
    assert so.WARN_HEADER in frame.accuracy_warning and so.WARN_NO_SIP in frame.accuracy_warning
    x, y = frame.project([83.8], [-5.4])
    # CRPIX is 1-based: its pixel centre sits at native CRPIX - 0.5
    assert (x[0], y[0]) == pytest.approx((W / 2 - 0.5, H / 2 - 0.5))


def test_header_wcs_on_asiair_downsampled_grid():
    raw = _raw_wcs(IMAGEW=W // 4, IMAGEH=H // 4, CRPIX1=W / 8, CRPIX2=H / 8, CD1_1=-SCALE, CD2_2=SCALE)
    frame = so.resolve_frame(_img(raw_header=raw))
    assert (frame.grid_w, frame.grid_h) == (W // 4, H // 4)
    x, y = frame.project([83.8], [-5.4])
    assert x[0] == pytest.approx((W / 8 - 0.5) * W / (W // 4))


def test_xisf_header_wcs_flagged():
    frame = so.resolve_frame(_img(raw_header=_raw_wcs(), file_format=SimpleNamespace(value="XISF")))
    assert so.WARN_XISF in frame.accuracy_warning


def test_no_usable_wcs():
    assert so.resolve_frame(_img()) is None
    assert so.resolve_frame(_img(raw_header={"CRVAL1": 1.0, "CRVAL2": 2.0})) is None  # no scale
    assert so.resolve_frame(_img(_solver_header(), w=None)) is None
    assert so.describe(_img()) == (None, None)
    assert so.describe(_img(_solver_header())) == (so.SOURCE_SOLVER, None)


# ---- objects -------------------------------------------------------------------------------

def test_norm_and_pretty_designations():
    assert so.norm_designation("NGC 0224") == "NGC224"
    assert so.norm_designation("M031") == "M31"
    assert so.norm_designation("Sh2-001") == "SH2-1"
    assert so.pretty_designation("NGC", "NGC0224") == "NGC 224"
    assert so.pretty_designation("IC", "IC0434") == "IC 434"
    assert so.pretty_designation("MESSIER", "M031") == "M31"


def test_cross_catalog_rows_merge_into_one_marker():
    frame = so.resolve_frame(_img(_solver_header()))
    ra, dec = frame.unproject([W / 2], [H / 2])
    ra, dec = float(ra[0]), float(dec[0])
    rows = [
        _row("NGC", "NGC1976", ra, dec, aliases=["M042"], major=85.0, minor=60.0, pa=0.0, mag=4.0,
             otype="Cl+N", name="Great Orion Nebula,Orion Nebula"),
        _row("MESSIER", "M42", ra + 1e-4, dec, aliases=["NGC1976"], major=85.0, mag=4.0, name="Orion Nebula"),
        _row("CALDWELL", "C99", ra, dec + 0.01, aliases=["NGC1977"]),
        _row("NGC", "NGC1977", ra, dec + 0.01, otype="RfN"),
        _row("NGC", "NGC1975", ra, dec + 0.02, otype="Dup"),
    ]
    objs = so.build_objects(frame, rows)
    assert len(objs) == 2
    m42 = next(o for o in objs if o["catalog"] == "MESSIER")
    assert m42["label"] == "M42"
    assert m42["designations"] == ["M42", "NGC 1976"]
    assert m42["catalogs"] == ["MESSIER", "NGC"]
    assert m42["common_name"] == "Orion Nebula"
    assert m42["search_name"] == "M42"
    assert m42["ellipse"]["pa_known"]  # geometry from the NGC row (has a PA)
    c99 = next(o for o in objs if o["catalog"] == "CALDWELL")
    assert c99["designations"] == ["C99", "NGC 1977"]


def test_far_apart_cross_reference_is_not_merged():
    frame = so.resolve_frame(_img(_solver_header()))
    ra, dec = frame.unproject([W / 2, W / 4], [H / 2, H / 2])
    rows = [_row("NGC", "NGC1", float(ra[0]), float(dec[0]), aliases=["M99"]),
            _row("MESSIER", "M99", float(ra[0]) + 2.0, float(dec[0]))]
    frame_wide = so.resolve_frame(_img(_solver_header(cd=((-SCALE * 10, 0), (0, SCALE * 10)))))
    assert len(so.build_objects(frame_wide, rows)) == 2


def test_objects_outside_frame_unless_ellipse_reaches_in():
    frame = so.resolve_frame(_img(_solver_header()))
    (ra_out,), (dec_out,) = frame.unproject([-200.0], [H / 2])
    rows = [
        _row("NGC", "NGC1", float(ra_out), float(dec_out)),                       # point outside: dropped
        _row("NGC", "NGC2", float(ra_out), float(dec_out) + 1e-3, major=60.0),     # 30' radius reaches in: kept
        _row("NAMED_STAR", "Alnitak", float(ra_out), float(dec_out) - 1e-3, mag=1.7),
    ]
    objs = so.build_objects(frame, rows)
    assert [o["label"] for o in objs] == ["NGC 2"]


def test_stars_have_no_ellipse_and_sort_by_priority_then_magnitude():
    frame = so.resolve_frame(_img(_solver_header()))
    (ra,), (dec,) = frame.unproject([W / 2], [H / 2])
    rows = [
        _row("NAMED_STAR", "Star A", float(ra), float(dec) + 0.01, mag=2.0),
        _row("NGC", "NGC10", float(ra) + 0.01, float(dec), mag=12.0, major=2.0),
        _row("NGC", "NGC11", float(ra) - 0.01, float(dec), mag=9.0),
        _row("IC", "IC5", float(ra), float(dec) - 0.01),
    ]
    objs = so.build_objects(frame, rows)
    assert [o["label"] for o in objs] == ["NGC 11", "NGC 10", "IC 5", "Star A"]
    assert objs[0]["search_name"] == "NGC11"
    assert objs[-1]["ellipse"] is None


# ---- API row normalisation -----------------------------------------------------------------

def test_catalog_row_normalisation_and_route():
    from app.api import images as api

    messier = SimpleNamespace(catalog="MESSIER", designation="M31", alias="NGC224", aliases=None,
                              common_name="Andromeda Galaxy", object_type="Galaxy", mag=3.44, size="199.53",
                              axis_ratio=2.82, major=None, minor=None, pa=35.0, ra=10.68, dec=41.27)
    row = api._sky_catalog_row(messier)
    assert row["major"] == pytest.approx(199.53)
    assert row["minor"] == pytest.approx(199.53 / 2.82)
    assert row["aliases"] == ["NGC224"]
    sh2 = SimpleNamespace(catalog="SH2", designation="Sh2-155", alias="Sh 2-155", aliases="Cave Nebula,C9",
                          common_name=None, object_type="HII", mag=None, size=None, axis_ratio=None,
                          major=50.0, minor=30.0, pa=None, ra=344.0, dec=62.6)
    assert api._sky_catalog_row(sh2)["aliases"] == ["Cave Nebula", "C9", "Sh 2-155"]
    assert any(r.path == "/{image_id}/sky-overlay" for r in api.router.routes)


# ---- sidecar .ini WCS ------------------------------------------------------------------------

ASTROMETRY_INI = """[Astrometry]
source = {source}
ra = 323.376653399
dec = 39.39045610589
pixscale = 6.938314418159394
orientation = 89.03706094826508

[WCS]
crpix1 = 2736.5
crpix2 = 1824.5
crval1 = 323.376653399
crval2 = 39.39045610589
cd1_1 = 3.238972032315e-05
cd1_2 = 0.001927037375973
cd2_1 = -0.001926940868906
cd2_2 = 3.33509186234e-05
"""


def _parse_ini(tmp_path, text):
    from app.extractors.ini_parser import SidecarParser

    img = tmp_path / "frame.cr2"
    img.write_bytes(b"")
    (tmp_path / "frame.ini").write_text(text, encoding="utf-8")
    return SidecarParser.parse(img)


@pytest.mark.parametrize("source,order", [("astap_local", "BOTTOM_UP"), ("", "TOP_DOWN")])
def test_ini_wcs_section_is_kept_with_row_order(tmp_path, source, order):
    data = _parse_ini(tmp_path, ASTROMETRY_INI.format(source=source))
    assert data["wcs"]["CRPIX1"] == 2736.5 and data["wcs"]["CD1_2"] == pytest.approx(0.001927037375973)
    assert data["wcs"]["CTYPE1"] == "RA---TAN"
    assert data["wcs_row_order"] == order
    assert data["rotation"] == pytest.approx(89.037, abs=1e-3)  # existing fields untouched


def test_ini_without_wcs_section_has_no_wcs(tmp_path):
    text = ASTROMETRY_INI.format(source="").split("[WCS]")[0]
    data = _parse_ini(tmp_path, text)
    assert data["is_plate_solved"] and "wcs" not in data


def test_astap_flat_ini_wcs_is_bottom_up(tmp_path):
    text = "\n".join(["PLTSOLVD=T", "CRPIX1=2736.5", "CRPIX2=1824.5", "CRVAL1=323.37", "CRVAL2=39.39",
                      "CD1_1=3.2e-05", "CD1_2=0.001927", "CD2_1=-0.001927", "CD2_2=3.3e-05"])
    data = _parse_ini(tmp_path, text)
    assert data["wcs_source"] == "astap" and data["wcs_row_order"] == "BOTTOM_UP"


def _sidecar_img(order, source="astap_local", w=5472, h=3648, **extra):
    cards = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRPIX1": 2736.5, "CRPIX2": 1824.5,
             "CRVAL1": 323.376653399, "CRVAL2": 39.39045610589,
             "CD1_1": 3.238972032315e-05, "CD1_2": 0.001927037375973,
             "CD2_1": -0.001926940868906, "CD2_2": 3.33509186234e-05}
    sidecar = {"wcs_type": "SIDECAR_INI", "wcs": cards, "wcs_source": source, "wcs_row_order": order}
    return _img(raw_header={"SIDECAR": sidecar, **extra}, w=w, h=h, file_format="CR2")


def test_sidecar_frame_mirrors_bottom_up_solves():
    top = so.resolve_frame(_sidecar_img("TOP_DOWN", source=""))
    bottom = so.resolve_frame(_sidecar_img("BOTTOM_UP"))
    assert top.source == bottom.source == so.SOURCE_SIDECAR
    assert so.WARN_SIDECAR in bottom.accuracy_warning
    assert (bottom.grid_w, bottom.grid_h) == (5472, 3648)
    rng = np.random.default_rng(3)
    px, py = rng.uniform(0, 5472, 20), rng.uniform(0, 3648, 20)
    ra, dec = top.unproject(px, py)
    bx, by = bottom.project(ra, dec)
    assert np.allclose(bx, px, atol=1e-6) and np.allclose(by, 3648 - py, atol=1e-6)
    # Round trip through the flipped frame itself
    ra, dec = bottom.unproject(px, py)
    x, y = bottom.project(ra, dec)
    assert np.max(np.hypot(x - px, y - py)) < 1e-6
    # Reference pixel: FITS row 1824.5 from the bottom
    x, y = bottom.project([323.376653399], [39.39045610589])
    assert (x[0], y[0]) == pytest.approx((2736.0, 3648 - 1824.0))


def test_sidecar_ellipse_mirrors_with_frame():
    top = so.resolve_frame(_sidecar_img("TOP_DOWN", source=""))
    bottom = so.resolve_frame(_sidecar_img("BOTTOM_UP"))
    a_top = so.ellipse_for(top, 323.38, 39.39, 20.0, 8.0, 30.0)["angle_deg"] % 180.0
    a_bot = so.ellipse_for(bottom, 323.38, 39.39, 20.0, 8.0, 30.0)["angle_deg"] % 180.0
    assert a_bot == pytest.approx((180.0 - a_top) % 180.0, abs=0.5)


def test_sidecar_source_priority_and_flat_astap_warning():
    solver = _img(_solver_header(), raw_header=_sidecar_img("BOTTOM_UP").raw_header)
    assert so.resolve_frame(solver).source == so.SOURCE_SOLVER
    with_header = _sidecar_img("BOTTOM_UP", **_raw_wcs())
    assert so.resolve_frame(with_header).source == so.SOURCE_SIDECAR
    flat = so.resolve_frame(_sidecar_img("BOTTOM_UP", source="astap"))
    assert so.WARN_SIDECAR_UNVERIFIED in flat.accuracy_warning
    # A sidecar dict without WCS cards (older rows) leaves the image without a usable frame
    bare = _img(raw_header={"SIDECAR": {"wcs_type": "SIDECAR_INI", "ra_center": 1.0}})
    assert so.resolve_frame(bare) is None
