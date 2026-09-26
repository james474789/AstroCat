"""
Tests for app.services.equipment_detection (R0 detect-then-confirm, spec §4.3 / §6).

Bucket fixtures mimic the live library's light subs (generic values, no
real site coordinates).
"""

from datetime import datetime

import pytest

from app.services.equipment_detection import (
    is_junk_camera, normalize_camera_name, plan_apply, propose, propose_sites,
)

LAST = datetime(2026, 8, 20, 23, 0)


def _b(camera, w, h, scale, count, filt=None, xpix=None, bayer=False, binning=None, focallen=None, last=LAST):
    return {"camera_name": camera, "width_pixels": w, "height_pixels": h, "binning": binning,
            "xpixsz": xpix, "bayer": bayer, "focallen": focallen, "pixel_scale": scale,
            "filter_name": filt, "count": count, "last_used": last}


def library_buckets():
    b = []
    # ZS73 + ASI1600MM Pro (3.8 um), ~2.27"
    for filt, n in (("Ha", 400), ("OIII", 300), ("SII", 250), ("L", 150), ("Red", 60), ("G", 60), ("Blue", 60)):
        b.append(_b("ZWO ASI1600MM Pro", 4656, 3520, 2.27, n, filt, xpix=3.8, focallen=430))
        b.append(_b("ZWO ASI1600MM Pro", 4656, 3520, 2.26, n // 4, filt, xpix=3.8, focallen=430))
    b.append(_b("ZWO ASI1600MM Pro", 4656, 3520, None, 120, "Ha", xpix=3.8))       # unsolved
    b.append(_b("ZWO ASI1600MM Pro", 4656, 3520, 72.0, 5, "Ha", xpix=3.8))         # solver placeholder
    # C11 EdgeHD + ASI294MM Pro (4.63 um), ~0.34"
    b.append(_b("ZWO ASI294MM Pro", 4144, 2822, 0.34, 700, "Ha", xpix=4.63))
    b.append(_b("ZWO ASI294MM Pro", 4144, 2822, 0.35, 150, "L", xpix=4.63))
    # EF200 + ASI294MM Pro unlocked 8288x5644 (2.315 um, header value as a char array), ~2.46"
    b.append(_b("ZWO ASI294MM Pro", 8288, 5644, 2.46, 300, "Ha", xpix=["2", ".", "3", "1", "5"]))
    b.append(_b("ZWO ASI294MM Pro", 5644, 8288, 2.47, 40, "OIII", xpix="2.315"))  # portrait
    # Sigma 105 + EOS R7 (OSC, no XPIXSZ -> seed 3.2 um), ~6.5"
    b.append(_b("Canon Canon EOS R7", 6960, 4640, 6.5, 500, None, focallen=105))
    b.append(_b("Canon EOS R7", 6960, 4640, 6.49, 300, None, focallen=105))
    b.append(_b("Canon EOS R7", 6960, 4640, None, 200, None, focallen=105))
    # EOS R8 spellings, never solved
    b.append(_b("EOS R8", 6000, 4000, None, 30, None))
    b.append(_b("Canon [EOS R8]", 6000, 4000, None, 25, None))
    b.append(_b("Canon Canon EOS R8", 6000, 4000, None, 20, None))
    # Junk
    b.append(_b(None, 400, 504, 2.11, 21800, None))
    b.append(_b("", 400, 504, 2.11, 50, None))
    b.append(_b("notAvailable", 4000, 3000, None, 20, None))
    b.append(_b("samsung SM-S908B", 4000, 3000, None, 12, None))
    b.append(_b("ZWO ASI1600MM Pro", 187, 125, 2.27, 30, "Ha"))                    # thumbnails
    return b


TELESCOPIUS_OPTICS = [
    {"id": 11, "name": "William Optics Zenithstar 73", "focal_length_mm": 346.0},
    {"id": 12, "name": "Celestron C11 EdgeHD", "focal_length_mm": 2800.0},
    {"id": 13, "name": "Canon EF 200mm f/2.8L", "focal_length_mm": 200.0},
    {"id": 14, "name": "Sigma 105mm f/1.4 Art", "focal_length_mm": 105.0},
]


def test_normalize_camera_name():
    assert normalize_camera_name("Canon Canon EOS R7") == "canon eos r7"
    assert normalize_camera_name("Canon EOS R7") == "canon eos r7"
    assert normalize_camera_name("EOS R8") == "canon eos r8"
    assert normalize_camera_name("Canon [EOS R8]") == "canon eos r8"
    assert normalize_camera_name("Canon Canon EOS R8") == "canon eos r8"
    assert normalize_camera_name("ZWO ASI1600MM-Pro") == "zwo asi1600mm pro"
    assert normalize_camera_name("ASI294MM Pro") == "zwo asi294mm pro"
    assert normalize_camera_name(["Z", "W", "O", " ", "A", "S", "I", "2", "9", "4"]) == "zwo asi294"
    assert normalize_camera_name("   ") is None
    assert normalize_camera_name(None) is None


def test_junk_camera_names():
    assert is_junk_camera("notavailable")
    assert is_junk_camera(normalize_camera_name("samsung SM-S908B"))
    assert is_junk_camera(normalize_camera_name("SM-S908B"))
    assert is_junk_camera(None)
    assert not is_junk_camera("canon eos r7")


def test_library_reproduces_four_rigs_with_telescopius_optics():
    p = propose(library_buckets(), [], {"optics": TELESCOPIUS_OPTICS})
    rigs = {(r["optic_existing_id"], r["camera_proposal_id"]): r for r in p["rigs"]}
    assert len(p["rigs"]) == 4, [r["name"] for r in p["rigs"]]

    zs73 = rigs[(11, "cam:zwo asi1600mm pro:4656x3520")]
    assert zs73["measured_scale_arcsec"] == pytest.approx(2.27, abs=0.01)
    assert zs73["modifier_factor"] == 1.0
    assert zs73["filter_bands"] == ["L", "R", "G", "B", "Ha", "OIII", "SII"]
    assert zs73["name"] == "William Optics Zenithstar 73 + ZWO ASI1600MM Pro"

    c11 = rigs[(12, "cam:zwo asi294mm pro:4144x2822")]
    assert c11["measured_scale_arcsec"] == pytest.approx(0.34, abs=0.005)
    assert c11["predicted_focal_mm"] == pytest.approx(2809, abs=5)

    ef200 = rigs[(13, "cam:zwo asi294mm pro:8288x5644")]
    assert ef200["measured_scale_arcsec"] == pytest.approx(2.46, abs=0.01)
    assert ef200["image_count"] == 340

    sigma = rigs[(14, "cam:canon eos r7:6960x4640")]
    assert sigma["measured_scale_arcsec"] == pytest.approx(6.5, abs=0.02)
    assert sigma["filter_bands"] == []
    assert sigma["image_count"] == 800
    assert p["optics"] == []   # every rig matched an imported optic


def test_cameras_are_normalised_grouped_and_junk_dropped():
    p = propose(library_buckets(), [], {})
    cams = {c["proposal_id"]: c for c in p["cameras"]}
    assert set(cams) == {
        "cam:zwo asi1600mm pro:4656x3520",
        "cam:zwo asi294mm pro:4144x2822",
        "cam:zwo asi294mm pro:8288x5644",
        "cam:canon eos r7:6960x4640",
        "cam:canon eos r8:6000x4000",
    }
    r7 = cams["cam:canon eos r7:6960x4640"]
    assert r7["name"] == "Canon EOS R7" and r7["image_count"] == 1000
    assert r7["pixel_size_um"] == 3.2 and r7["is_color"] is True and r7["maker"] == "Canon"
    assert r7["match_patterns"] == ["canon eos r7"]
    assert cams["cam:canon eos r8:6000x4000"]["image_count"] == 75

    asi1600 = cams["cam:zwo asi1600mm pro:4656x3520"]
    assert asi1600["pixel_size_um"] == 3.8 and asi1600["is_color"] is False
    unlocked = cams["cam:zwo asi294mm pro:8288x5644"]
    assert unlocked["pixel_size_um"] == pytest.approx(2.315)
    assert unlocked["name"] == "ZWO ASI294MM Pro (unlocked)"
    assert cams["cam:zwo asi294mm pro:4144x2822"]["name"] == "ZWO ASI294MM Pro"


def test_without_telescopius_optics_are_proposed_and_shared():
    p = propose(library_buckets(), [], {})
    names = [o["name"] for o in p["optics"]]   # sorted by focal length
    assert names == ["~102 mm (detected)", "~194 mm (detected)", "~345 mm (detected)", "~2809 mm (detected)"]
    lens = next(o for o in p["optics"] if o["name"].startswith("~102"))
    assert lens["kind"] == "LENS"
    for rig in p["rigs"]:
        assert rig["optic_existing_id"] is None and rig["optic_proposal_id"].startswith("opt:")


def test_reducer_is_detected_against_native_focal_length():
    optics = [{"id": 21, "name": "Zenithstar 73", "focal_length_mm": 430.0}]
    p = propose([b for b in library_buckets() if b["camera_name"] == "ZWO ASI1600MM Pro"], [], {"optics": optics})
    assert len(p["rigs"]) == 1
    assert p["rigs"][0]["optic_existing_id"] == 21
    assert p["rigs"][0]["modifier_factor"] == 0.8


def test_existing_rows_are_flagged():
    existing = {
        "cameras": [{"id": 5, "name": "ZWO ASI1600MM Pro", "match_patterns": ["asi1600mm"],
                     "sensor_width_px": 4656, "sensor_height_px": 3520, "pixel_size_um": 3.8}],
        "optics": TELESCOPIUS_OPTICS,
        "rigs": [{"id": 9, "camera_id": 5, "binning": 1, "declared_scale": 2.265}],
        "filters": [{"id": 3, "name": "Ha 7nm", "band": "Ha", "match_patterns": ["ha"]}],
    }
    p = propose(library_buckets(), [], existing)
    cam = next(c for c in p["cameras"] if c["proposal_id"].startswith("cam:zwo asi1600mm"))
    assert cam["exists"] and cam["existing_id"] == 5
    rig = next(r for r in p["rigs"] if r["camera_existing_id"] == 5)
    assert rig["exists"] and rig["existing_id"] == 9
    ha = next(f for f in p["filters"] if f["band"] == "Ha")
    assert ha["exists"] and ha["existing_id"] == 3
    unlocked = next(c for c in p["cameras"] if c["proposal_id"].endswith("8288x5644"))
    assert not unlocked["exists"]


def test_filters_proposed_with_raw_spellings():
    p = propose(library_buckets(), [], {})
    by_band = {f["band"]: f for f in p["filters"]}
    assert list(by_band) == ["L", "R", "G", "B", "Ha", "OIII", "SII"]
    assert by_band["R"]["match_patterns"] == ["red"]
    assert by_band["R"]["proposal_id"] == "flt:r"
    assert "None" not in by_band


def test_sites_cluster_and_threshold():
    sites = [
        {"lat": 51.48, "lon": -0.01, "site_name": "Backyard", "count": 900, "last_used": LAST},
        {"lat": 51.49, "lon": 0.00, "site_name": None, "count": 300, "last_used": datetime(2026, 9, 1)},
        {"lat": 51.60, "lon": 0.20, "site_name": None, "count": 250, "last_used": LAST},   # ~20 km away
        {"lat": 40.0, "lon": -3.0, "site_name": None, "count": 150, "last_used": LAST},    # too few
    ]
    out = propose_sites(sites, [])
    assert [s["image_count"] for s in out] == [1200, 250]
    assert out[0]["name"] == "Backyard" and out[1]["name"] == "Site 2"
    assert out[0]["latitude"] == pytest.approx(51.4825, abs=1e-3)
    assert out[0]["last_used"] == "2026-09-01T00:00:00"
    assert out[0]["proposal_id"] == "site:51.48:-0.01"

    flagged = propose_sites(sites, [{"id": 4, "latitude": 51.5, "longitude": 0.0}])
    assert flagged[0]["exists"] and flagged[0]["existing_id"] == 4
    assert not flagged[1]["exists"]


def test_proposal_ids_are_stable():
    a = propose(library_buckets(), [], {"optics": TELESCOPIUS_OPTICS}, generated_at=LAST)
    b = propose(list(reversed(library_buckets())), [], {"optics": TELESCOPIUS_OPTICS}, generated_at=LAST)
    assert sorted(r["proposal_id"] for r in a["rigs"]) == sorted(r["proposal_id"] for r in b["rigs"])
    assert a["lookback_months"] == 36


def test_plan_apply_rig_implies_camera_optic_filters():
    p = propose(library_buckets(), [{"lat": 51.48, "lon": 0.0, "site_name": None, "count": 500, "last_used": LAST}], {})
    zs73 = next(r for r in p["rigs"] if r["camera_proposal_id"].startswith("cam:zwo asi1600mm"))
    site = p["sites"][0]
    plan = plan_apply(p, [
        {"proposal_id": zs73["proposal_id"], "name": "ZS73 + ASI1600", "modifier_factor": 0.8},
        {"proposal_id": site["proposal_id"], "timezone": "Europe/London"},
        {"proposal_id": "rig:nope"},
    ], "UTC")
    assert plan["unknown"] == ["rig:nope"]
    assert [c["proposal_id"] for c in plan["cameras"]] == ["cam:zwo asi1600mm pro:4656x3520"]
    assert len(plan["optics"]) == 1
    assert sorted(f["band"] for f in plan["filters"]) == ["B", "G", "Ha", "L", "OIII", "R", "SII"]
    rig = plan["rigs"][0]
    assert rig["name"] == "ZS73 + ASI1600" and rig["modifier_factor"] == 0.8
    assert rig["camera_ref"] == "new:cam:zwo asi1600mm pro:4656x3520"
    assert rig["optic_ref"].startswith("new:opt:")
    assert len(rig["filter_refs"]) == 7
    assert plan["sites"][0]["timezone"] == "Europe/London"


def test_plan_apply_uses_existing_and_optic_override():
    existing = {"cameras": [{"id": 5, "name": "ZWO ASI1600MM Pro", "match_patterns": ["zwo asi1600mm pro"],
                             "sensor_width_px": 4656, "sensor_height_px": 3520}]}
    p = propose(library_buckets(), [], existing)
    zs73 = next(r for r in p["rigs"] if r["camera_existing_id"] == 5)
    plan = plan_apply(p, [{"proposal_id": zs73["proposal_id"], "optic_id": 42}], "UTC")
    assert plan["cameras"] == [] and plan["optics"] == []
    assert plan["rigs"][0]["camera_ref"] == "existing:5"
    assert plan["rigs"][0]["optic_ref"] == "existing:42"

    site_plan = plan_apply({"sites": [{"proposal_id": "site:1", "name": "S", "latitude": 1.0, "longitude": 2.0,
                                       "exists": False, "existing_id": None}]},
                           [{"proposal_id": "site:1"}], "Europe/Paris")
    assert site_plan["sites"][0]["timezone"] == "Europe/Paris"
