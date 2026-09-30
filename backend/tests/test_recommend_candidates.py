"""R1 §4.1: the candidate pool."""

from types import SimpleNamespace as NS

from app.services.recommend.candidates import (
    KIND_CLUSTER, KIND_EMISSION, KIND_GALAXY, KIND_PN, KIND_REFLECTION, CandidatePool, build_candidates, parse_size,
    row_kind,
)
from app.services.targets import build_alias_index


def messier(n, ngc, ra, dec, otype, size, name=None, mag=None):
    return NS(messier_number=n, designation=f"M{n}", ngc_designation=ngc, ra_degrees=ra, dec_degrees=dec,
              object_type=otype, angular_size_arcmin=size, apparent_magnitude=mag, common_name=name)


def ngc(des, otype, size, ra=0.0, dec=0.0, mag=None, m=None, ic=None, name=None):
    return NS(designation=des, object_type=otype, major_axis_arcmin=size, ra_degrees=ra, dec_degrees=dec,
              apparent_magnitude=mag, messier_designation=m, ic_designation=ic, common_name=name)


def caldwell(des, src, ra, dec, otype, size, name=None):
    return NS(designation=des, source_designation=src, aliases=None, common_name=name, ra_degrees=ra,
              dec_degrees=dec, object_type=otype, apparent_magnitude=None, major_axis_arcmin=size)


def sh2(des, ra, dec, size, name=None):
    return NS(designation=des, source_designation=des.replace("Sh2-", "Sh 2-"), common_name=name, ra_degrees=ra,
              dec_degrees=dec, major_axis_arcmin=size, apparent_magnitude=None, object_type="SHARPLESS")


MESSIER = [
    messier(81, "NGC 3031", 148.89, 69.07, "Spiral Galaxy", "26.9 × 14.1", "Bode's Galaxy", 6.9),
    messier(45, None, 56.75, 24.12, "Open Cluster", "110", "Pleiades", 1.6),
    messier(57, "NGC 6720", 283.4, 33.03, "Planetary Nebula", "1.4", "Ring Nebula", 8.8),
]
NGC = [
    ngc("NGC3031", "G", 21.63, 148.89, 69.07, 6.9, m="M081", name="Bode's Galaxy"),
    ngc("IC1396", "Cl+N", 14.0, 324.74, 57.49, 3.5),
    ngc("NGC7000", "HII", 120.0, 314.68, 44.52, name="North America Nebula"),
    ngc("NGC1999", "Neb", 10.0, 84.3, -6.7),
    ngc("NGC1432", "HII", 60.0, 56.57, 24.37, name="Maia Nebula"),
    ngc("NGC9999", "G", 1.0, 10.0, 10.0, 14.2),          # faint + tiny: gated out
    ngc("NGC9998", "Dup", 30.0, 11.0, 11.0, 5.0),        # duplicate: skipped
    ngc("NGC9997", "*", 1.0, 12.0, 12.0, 5.0),           # a star: skipped
    ngc("NGC6888", "HII", 5.0, 303.0, 38.35, 12.0),      # emission >= 3': kept
    ngc("NGC869", "OCl", 30.0, 34.75, 57.13, 4.3),
]
CALDWELL = [caldwell("C20", "NGC7000", 314.68, 44.52, "HII", 120.0, "North America Nebula")]
SH2 = [
    sh2("Sh2-131", 324.5, 57.5, 170.0),
    sh2("Sh2-200", 45.0, 62.0, 5.0),      # small, no cross-ID
    sh2("Sh2-240", 85.0, 28.0, 180.0, "Spaghetti Nebula"),
]


def build(imaged=()):
    index = build_alias_index(MESSIER, NGC, CALDWELL, SH2)
    cands = build_candidates(MESSIER, CALDWELL, NGC, SH2, index, imaged)
    return {c.key: c for c in cands}


def test_ic1396_size_from_sh2_alias():
    c = build()["IC1396"]
    assert c.size_arcmin == 170.0
    assert c.kind == KIND_EMISSION
    assert "SH2131" in c.aliases


def test_m81_key_from_ngc3031_rows():
    pool = build()
    assert "M81" in pool and "NGC3031" not in pool
    m81 = pool["M81"]
    assert "NGC3031" in m81.aliases and m81.kind == KIND_GALAXY
    assert m81.size_arcmin == 26.9 and m81.name == "Bode's Galaxy" and m81.catalog == "M" and m81.prior == 1.0


def test_neb_without_sh2_is_reflection():
    assert build()["NGC1999"].kind == KIND_REFLECTION


def test_hii_maia_in_overrides_is_reflection():
    assert build()["NGC1432"].kind == KIND_REFLECTION
    assert build()["M45"].kind == KIND_REFLECTION


def test_caldwell_merges_into_ngc_key_and_raises_prior():
    pool = build()
    assert "C20" not in pool
    c = pool["NGC7000"]
    assert "C20" in c.aliases and c.prior == 0.9 and c.catalog == "NGC" and c.kind == KIND_EMISSION


def test_small_sh2_only_regions_gated_unless_imaged():
    assert "SH2200" not in build()
    assert "SH2240" in build() and build()["SH2240"].kind == KIND_EMISSION and build()["SH2240"].prior == 0.6
    assert "SH2200" in build(imaged={"SH2200"})


def test_imageability_gate_and_imaged_keys():
    pool = build()
    assert "NGC9999" not in pool
    assert "NGC9998" not in pool and "NGC9997" not in pool
    assert "NGC6888" in pool          # emission >= 3'
    assert pool["NGC869"].kind == KIND_CLUSTER
    assert "NGC9999" in build(imaged={"NGC9999"})


def test_pn_and_pool_arrays():
    pool = build()
    assert pool["M57"].kind == KIND_PN
    cp = CandidatePool(list(pool.values()))
    assert len(cp) == len(pool) and cp.get("M57").key == "M57"
    assert cp.ra.shape == (len(pool),)


def test_row_kind_and_size_parsing():
    assert row_kind("GCl") == KIND_CLUSTER and row_kind("Globular Cluster") == KIND_CLUSTER
    assert row_kind("GPair") == KIND_GALAXY and row_kind("Spiral Galaxy") == KIND_GALAXY
    assert row_kind("SNR") == "EMISSION" and row_kind("Supernova Remnant") == "EMISSION"
    assert row_kind("RfN") == KIND_REFLECTION and row_kind("Reflection Nebula") == KIND_REFLECTION
    assert parse_size("70 × 50") == 70.0 and parse_size(None) is None and parse_size(12) == 12.0


# --- tuning round: imaged odd-typed rows, Dup folding, stray key folding ----------------

from app.services.recommend.candidates import build_pool_parts, fold_key, fold_map  # noqa: E402

EXTRA_NGC = [
    ngc("NGC281", "HII", 35.0, 13.2, 56.62),
    ngc("IC0011", "Dup", None, 13.25, 56.63),          # duplicate of NGC281
    ngc("NGC7822", "HII", 20.0, 0.87, 67.2),
    ngc("NGC2239", "OCl", 24.0, 97.98, 4.94),
    ngc("NGC2244", "Dup", None, 97.99, 4.95),          # duplicate of NGC2239
    ngc("IC1318", "*", None, 305.56, 40.26, 2.2),       # typed as the star gamma Cyg
    ngc("IC5067", "Other", None, 311.95, 44.37),
    ngc("IC0395", "Dup", None, 72.4, 0.25),             # nothing near it in this fixture
]


def build_extra(imaged=(), hint=()):
    rows = NGC + EXTRA_NGC
    index = build_alias_index(MESSIER, rows, CALDWELL, SH2)
    cands, dup_map = build_pool_parts(MESSIER, CALDWELL, rows, SH2, index, imaged, hint)
    return {c.key: c for c in cands}, dup_map, index


def test_imaged_star_and_other_typed_rows_are_candidates():
    pool, _, _ = build_extra()
    assert "IC1318" not in pool and "IC5067" not in pool
    pool, _, _ = build_extra(imaged={"IC1318", "IC5067"}, hint={"IC1318"})
    assert pool["IC1318"].kind == KIND_EMISSION           # imaged mostly in narrowband
    assert pool["IC5067"].kind == "OTHER"


def test_dup_rows_fold_into_their_object():
    pool, dup_map, _ = build_extra(imaged={"IC11", "IC395"})
    assert dup_map["IC11"] == "NGC281" and "IC11" not in pool
    assert dup_map["NGC2244"] == "NGC2239"
    assert "IC395" in pool                                 # imaged, nothing to fold into


def test_fold_key_panels_filters_and_dups():
    pool, dup_map, index = build_extra()
    idx = {k: i for i, k in enumerate(pool)}
    resolve = index.resolve
    assert fold_key("OBJ:NGC78222", idx, resolve, dup_map) == "NGC7822"
    assert fold_key("OBJ:IC13961", idx, resolve, dup_map) == "IC1396"
    assert fold_key("OBJ:M81LUM", idx, resolve, dup_map) == "M81"
    assert fold_key("OBJ:NGC22441", idx, resolve, dup_map) == "NGC2239"
    assert fold_key("IC11", idx, resolve, dup_map) == "NGC281"
    assert fold_key("OBJ:COMETC2023A3", idx, resolve, dup_map) is None
    assert fold_key("M81", idx, resolve, dup_map) is None      # already a pool key
    assert fold_map(["OBJ:IC13962", "M81", "OBJ:XYZ"], idx, resolve, dup_map) == {"OBJ:IC13962": "IC1396"}


def test_build_pool_data_remaps_history():
    from datetime import date as d_

    from app.services.recommend.history import GoalRow, HistoryRow
    from app.services.recommend.loader import HistoryInputs, build_pool_data

    rows = [HistoryRow("OBJ:IC13961", d_(2025, 9, 1), "Ha", 3600.0),
            HistoryRow("IC1396", d_(2025, 9, 2), "Ha", 3600.0),
            HistoryRow("IC11", d_(2025, 9, 3), "Ha", 1800.0),
            HistoryRow("IC1318", d_(2025, 9, 4), "Ha", 7200.0)]
    hist = HistoryInputs(version="v", rows=rows, masters={"OBJ:IC13961": d_(2025, 9, 1)},
                         goal_rows=[GoalRow("OBJ:IC13961", "ANY", 3600.0, None)])
    data = build_pool_data(MESSIER, CALDWELL, NGC + EXTRA_NGC, SH2,
                           build_alias_index(MESSIER, NGC + EXTRA_NGC, CALDWELL, SH2), hist)
    assert data.key_map == {"OBJ:IC13961": "IC1396", "IC11": "NGC281"}
    assert [r.key for r in data.rows] == ["IC1396", "IC1396", "NGC281", "IC1318"]
    assert data.masters == {"IC1396": d_(2025, 9, 1)} and data.goal_rows[0].key == "IC1396"
    assert data.pool.get("IC1318").kind == KIND_EMISSION
    stats = data.fold_stats(rows)
    assert stats["keys"] == 2 and stats["rows"] == 2 and stats["hours"] == 1.5


# --- R1b: curated wide-field candidates ------------------------------------------------

def test_wide_field_regions_join_the_pool_and_fold_no_history():
    from app.services.recommend.candidates import KINDS, WF_CATALOG
    from app.services.recommend.widefield import WF_PREFIX, WIDE_FIELDS

    keys = [w.key for w in WIDE_FIELDS]
    assert len(keys) == len(set(keys)) >= 20 and all(k.startswith(WF_PREFIX) for k in keys)
    assert all(w.kind in KINDS for w in WIDE_FIELDS)
    assert all(-90 <= w.dec_deg <= 90 and 0 <= w.ra_deg < 360 and w.size_arcmin >= 60 for w in WIDE_FIELDS)
    pool, dup_map, index = build_extra(imaged={"NGC281"})
    for w in WIDE_FIELDS:
        c = pool[w.key]
        assert c.catalog == WF_CATALOG == "WF" and c.prior == 0.7 and c.aliases == frozenset()
        assert c.members == w.members and c.size_arcmin == w.size_arcmin
    # No Dup row folds into a WF region, and stray history keys never resolve to one.
    assert not any(v.startswith("WF_") for v in dup_map.values())
    idx = {k: i for i, k in enumerate(pool)}
    assert fold_key("OBJ:NGC78222", idx, index.resolve, dup_map) == "NGC7822"
    assert not any(v.startswith("WF_") for v in fold_map(["OBJ:NGC78222", "IC11", "OBJ:M81LUM"], idx, index.resolve, dup_map).values())
