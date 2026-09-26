"""
Tests for app.services.targets (F2): normalize_designation, AliasIndex,
resolve_target, and the (target, raw filter) row folding used by the
targets list aggregation.

Pure-function tests only - no DB session required, per docs/design/F2-target-integration.md §5.
"""

from collections import namedtuple

import pytest

from app.services.targets import (
    normalize_designation,
    strip_panel_suffix,
    AliasIndex,
    MatchInfo,
    resolve_target,
)


# ---------------------------------------------------------------------------
# normalize_designation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("M 31", "M31"),
    ("Messier 31", "M31"),
    ("NGC 0224", "NGC224"),
    ("ngc224", "NGC224"),
    ("IC 434", "IC434"),
    ("C 14", "C14"),
    ("Caldwell 14", "C14"),
    ("Sh2-155", "SH2155"),
])
def test_normalize_designation(raw, expected):
    assert normalize_designation(raw) == expected


def test_normalize_designation_empty():
    assert normalize_designation("") == ""
    assert normalize_designation(None) == ""


# ---------------------------------------------------------------------------
# AliasIndex
# ---------------------------------------------------------------------------

def _build_small_index():
    index = AliasIndex()
    index.add_alias("M31", "M31")
    index.add_alias("NGC224", "M31")
    index.add_alias("Andromeda Galaxy", "M31")
    index.add_alias("NGC7000", "NGC7000")
    index.add_alias("North America Nebula", "NGC7000")
    return index


def test_alias_index_resolve_m31_variants():
    index = _build_small_index()
    assert index.resolve("NGC224") == "M31"
    assert index.resolve("Andromeda Galaxy") == "M31"
    assert index.resolve("M31 Panel 3") == "M31"
    assert index.resolve("M31_P2") == "M31"
    assert index.resolve("M31-mosaic-3") == "M31"
    assert index.resolve("NGC7000 [2]") == "NGC7000"


def test_alias_index_resolve_ngc7000():
    index = _build_small_index()
    assert index.resolve("North America Nebula") == "NGC7000"


def test_alias_index_resolve_unknown():
    index = _build_small_index()
    assert index.resolve("Some Unknown Object") is None
    assert index.resolve(None) is None
    assert index.resolve("") is None


def test_alias_index_header_separator_variants():
    index = AliasIndex()
    index.add_alias("M42", "M42")
    assert index.resolve("M42 (Orion)") == "M42"
    assert index.resolve("M42 - Orion Nebula") == "M42"


def test_strip_panel_suffix():
    assert strip_panel_suffix("M31 Panel 2") == "M31"
    assert strip_panel_suffix("M31_P2") == "M31"
    assert strip_panel_suffix("M31-mosaic-3") == "M31"
    assert strip_panel_suffix("NGC7000 [2]") == "NGC7000"
    assert strip_panel_suffix("M31") == "M31"


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------

def _index():
    index = AliasIndex()
    index.add_alias("M31", "M31")
    index.add_alias("NGC224", "M31")
    index.add_alias("Andromeda Galaxy", "M31")
    index.add_alias("M110", "M110")
    return index


def test_resolve_target_manual_is_preserved():
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name="Anything",
        matches=[],
        field_radius=1.0,
        current_source="MANUAL",
        current_key="OBJ:CUSTOM",
        alias_index=_index(),
    )
    assert (key, source) == ("OBJ:CUSTOM", "MANUAL")


def test_resolve_target_header_beats_match():
    # Header resolves to M31, but the closest catalog match is M110 - header wins.
    matches = [
        MatchInfo("MESSIER", "M110", 0.05, True, 8.9),
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name="Andromeda Galaxy",
        matches=matches,
        field_radius=1.0,
        current_source=None,
        alias_index=_index(),
    )
    assert (key, source) == ("M31", "HEADER")


def test_resolve_target_match_picks_central_object_not_bright_offcenter():
    # M110 is closer to center (small separation) but M31 is much brighter;
    # spec says pick smallest separation among central candidates, not brightest.
    matches = [
        MatchInfo("MESSIER", "M31", 0.30, True, 3.4),   # bright, but far from center
        MatchInfo("MESSIER", "M110", 0.02, True, 8.9),  # dim, but central
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=1.0,  # threshold = 0.5
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == ("M110", "MATCH")


def test_resolve_target_match_radius_guard_rejects_edge_objects():
    # Only match is well outside 0.5 * field_radius -> no MATCH, falls through.
    matches = [
        MatchInfo("MESSIER", "M31", 0.9, True, 3.4),
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=1.0,  # threshold = 0.5
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == (None, "NONE")  # P0: resolved light, no target


def test_resolve_target_match_accepts_moderately_offset_object():
    # 0.4 * radius was rejected under the old 0.35 cutoff; mosaic panels and
    # offset framing land here, so it now resolves.
    matches = [
        MatchInfo("IC", "IC5068", 0.4, True, None),
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=1.0,  # threshold = 0.5
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == ("IC5068", "MATCH")


def test_resolve_target_match_ignores_named_star():
    matches = [
        MatchInfo("NAMED_STAR", "Polaris", 0.01, True, 2.0),
        MatchInfo("MESSIER", "M31", 0.1, True, 3.4),
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=1.0,
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == ("M31", "MATCH")


def test_resolve_target_non_light_returns_none():
    key, source = resolve_target(
        frame_type="DARK",
        object_name="Andromeda Galaxy",
        matches=[],
        field_radius=1.0,
        current_source=None,
        alias_index=_index(),
    )
    assert (key, source) == (None, None)


def test_resolve_target_panel_suffix_produces_obj_key():
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name="Sadr Region Panel 2",
        matches=[],
        field_radius=1.0,
        current_source=None,
        alias_index=AliasIndex(),  # empty - nothing resolves via header
    )
    assert (key, source) == ("OBJ:SADRREGION", "HEADER_RAW")


def test_resolve_target_unassigned_when_nothing_matches():
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=[],
        field_radius=None,
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == (None, "NONE")


def test_resolve_target_match_without_field_radius_is_skipped():
    matches = [MatchInfo("MESSIER", "M31", 0.01, True, 3.4)]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=None,
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == (None, "NONE")


# ---------------------------------------------------------------------------
# Folder-scoped targets list (path prefix helpers, F-targets-folder-nav)
#
# Pure-function tests only, per the module docstring above - these exercise
# the string/SQL-fragment builders in app.api.targets directly rather than
# hitting a real Postgres session.
# ---------------------------------------------------------------------------

from app.api.targets import (
    _normalize_path_prefix,
    _path_clause,
    _path_like_sql,
    _path_like_params,
    _cache_key_for_path,
    _escape_like,
)


def test_normalize_path_prefix_adds_trailing_separator():
    assert _normalize_path_prefix("/data/2025") == "/data/2025/"
    assert _normalize_path_prefix("/data/2025/") == "/data/2025/"


def test_normalize_path_prefix_windows_separator():
    assert _normalize_path_prefix("C:\\data\\2025") == "C:\\data\\2025\\"
    assert _normalize_path_prefix("C:\\data\\2025\\") == "C:\\data\\2025\\"


def test_path_clause_none_when_no_path():
    assert _path_clause(None) is None
    assert _path_clause("") is None


def test_path_clause_does_not_match_sibling_with_shared_prefix():
    # /data/2025 must not match /data/2025-backup/foo.fits - this is the same
    # separator-boundary bug FolderTree.isPathParent guards against on the frontend.
    clause = _path_clause("/data/2025")
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "/data/2025/%%" in compiled or "/data/2025/" in compiled
    assert "/data/2025-backup" not in compiled


def test_path_like_sql_and_params_escape_wildcards():
    # A folder literally named "100%_done" must not act as a SQL wildcard.
    sql = _path_like_sql("/data/100%_done")
    params = _path_like_params("/data/100%_done")
    assert sql is not None
    assert params["path_prefix"] == "/data/100\\%\\_done/%"


def test_path_like_sql_none_when_no_path():
    assert _path_like_sql(None) is None
    assert _path_like_params(None) == {}


def test_escape_like_escapes_percent_underscore_backslash():
    assert _escape_like("100%_done\\x") == "100\\%\\_done\\\\x"


def test_cache_key_for_path_differs_per_path_and_is_stable():
    root_key = _cache_key_for_path(None)
    key_a = _cache_key_for_path("/data/2025")
    key_b = _cache_key_for_path("/data/2026")
    key_a_again = _cache_key_for_path("/data/2025")

    assert root_key == "cache:targets:list"
    assert key_a != root_key
    assert key_a != key_b
    assert key_a == key_a_again
    assert key_a.startswith("cache:targets:list:")


# ---------------------------------------------------------------------------
# P0 §3.1: MATCH canonicalisation, NONE sentinel, Sh2 cross-IDs
# ---------------------------------------------------------------------------

from app.services.targets import (
    build_alias_index,
    sh2_cross_id_details,
    sh2_cross_ids,
)

MessierRow = namedtuple("MessierRow", "designation messier_number ngc_designation common_name")
NGCRow = namedtuple(
    "NGCRow",
    "designation messier_designation ic_designation common_name object_type ra_degrees dec_degrees major_axis_arcmin",
)
CaldwellRow = namedtuple("CaldwellRow", "designation source_designation aliases common_name")
Sh2Row = namedtuple(
    "Sh2Row", "designation source_designation common_name ra_degrees dec_degrees major_axis_arcmin"
)


def _ngc(designation, object_type, ra, dec, size, messier=None):
    return NGCRow(designation, messier, None, None, object_type, ra, dec, size)


# Positions are approximate catalog values; only relative geometry matters.
IC1396 = _ngc("IC1396", "Cl+N", 324.7, 57.5, 14.0)
NGC7000 = _ngc("NGC7000", "HII", 314.7, 44.3, 120.0)
NGC7635 = _ngc("NGC7635", "EmN", 350.2, 61.2, 15.0)
NGC3031 = _ngc("NGC3031", "G", 148.9, 69.07, 26.9, messier="M081")
NGC224 = _ngc("NGC224", "G", 10.68, 41.27, 190.0, messier="M031")

SH2_131 = Sh2Row("Sh2-131", "Sh 2-131", None, 324.8, 57.6, 170.0)
SH2_117 = Sh2Row("Sh2-117", "Sh 2-117", None, 314.5, 44.2, 240.0)
SH2_162 = Sh2Row("Sh2-162", "Sh 2-162", None, 350.1, 61.2, 43.0)


def _catalog_index():
    return build_alias_index(
        messier_rows=[
            MessierRow("M81", 81, "NGC3031", "Bode's Galaxy"),
            MessierRow("M31", 31, "NGC224", "Andromeda Galaxy"),
        ],
        ngc_rows=[NGC3031, NGC224, IC1396, NGC7000, NGC7635],
        caldwell_rows=[
            CaldwellRow("C11", "NGC7635", None, "Bubble Nebula"),
            CaldwellRow("C20", "NGC7000", None, "North America Nebula"),
        ],
        sh2_rows=[SH2_131, SH2_117, SH2_162],
    )


def _match(designation, catalog_type="NGC"):
    return resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=[MatchInfo(catalog_type, designation, 0.05, True, None)],
        field_radius=1.0,
        current_source=None,
        alias_index=_catalog_index(),
    )


def test_match_ngc3031_canonicalises_to_messier():
    assert _match("NGC3031") == ("M81", "MATCH")
    assert _match("NGC 224") == ("M31", "MATCH")


def test_match_caldwell_canonicalises_through_source_designation():
    assert _match("C11", "CALDWELL") == ("NGC7635", "MATCH")


def test_match_sh2_canonicalises_to_cross_identified_ngc():
    assert _match("Sh2-131", "SH2") == ("IC1396", "MATCH")
    assert _match("Sh2-117", "SH2") == ("NGC7000", "MATCH")


def test_match_unknown_designation_falls_back_to_normalized():
    assert _match("NGC 9999") == ("NGC9999", "MATCH")


def test_unresolved_light_gets_none_sentinel():
    key, source = resolve_target(
        frame_type="LIGHT", object_name=None, matches=[], field_radius=None,
        current_source=None, alias_index=_catalog_index(),
    )
    assert (key, source) == (None, "NONE")


def test_non_light_still_clears_to_none_none():
    key, source = resolve_target(
        frame_type="FLAT", object_name="M81", matches=[], field_radius=None,
        current_source="NONE", alias_index=_catalog_index(),
    )
    assert (key, source) == (None, None)


def test_manual_preserved_even_when_key_is_none():
    key, source = resolve_target(
        frame_type="LIGHT", object_name="M81", matches=[], field_radius=None,
        current_source="MANUAL", current_key=None, alias_index=_catalog_index(),
    )
    assert (key, source) == (None, "MANUAL")


def test_header_sh2_name_resolves_to_ngc():
    key, source = resolve_target(
        frame_type="LIGHT", object_name="Sh2-131", matches=[], field_radius=None,
        current_source=None, alias_index=_catalog_index(),
    )
    assert (key, source) == ("IC1396", "HEADER")


def test_sh2_cross_id_cluster_inside_big_hii_region_merges():
    # IC1396 is a 14' Cl+N inside the 170' Sh2-131: size ratio ~0.08 but Cl+N is exempt.
    details = sh2_cross_id_details([SH2_131], [IC1396], overrides={})
    assert [(d["sh2"], d["ngc"], d["via"]) for d in details] == [("Sh2-131", "IC1396", "rule")]


def test_sh2_cross_id_similar_size_nebula_merges():
    assert sh2_cross_ids([SH2_117], [NGC7000], overrides={}) == {"Sh2-117": "NGC7000"}
    assert sh2_cross_ids([SH2_162], [NGC7635], overrides={}) == {"Sh2-162": "NGC7635"}


def test_sh2_cross_id_rejects_galaxy_nearby():
    sh2 = Sh2Row("Sh2-999", None, None, 100.0, 20.0, 20.0)
    galaxy = _ngc("NGC9990", "G", 100.1, 20.1, 20.0)  # ~0.14 deg away, same size
    assert sh2_cross_ids([sh2], [galaxy], overrides={}) == {}


def test_sh2_cross_id_rejects_small_non_cluster_nebula():
    # An emission knot 1/10 the size of the Sh2 region is not the same object.
    sh2 = Sh2Row("Sh2-998", None, None, 100.0, 20.0, 100.0)
    knot = _ngc("NGC9991", "EmN", 100.05, 20.0, 10.0)
    assert sh2_cross_ids([sh2], [knot], overrides={}) == {}


def test_sh2_cross_id_rejects_too_far():
    sh2 = Sh2Row("Sh2-997", None, None, 100.0, 20.0, 20.0)
    neb = _ngc("NGC9992", "HII", 100.0, 20.4, 20.0)  # 0.4 deg > max(0.25, 0.25*20') = 0.25
    assert sh2_cross_ids([sh2], [neb], overrides={}) == {}


def test_sh2_cross_id_picks_closest_qualifying_row():
    sh2 = Sh2Row("Sh2-996", None, None, 100.0, 20.0, 30.0)
    far = _ngc("NGC9993", "HII", 100.0, 20.2, 30.0)
    near = _ngc("NGC9994", "Neb", 100.0, 20.05, 30.0)
    assert sh2_cross_ids([sh2], [far, near], overrides={}) == {"Sh2-996": "NGC9994"}


def test_sh2_cross_id_overrides_force_and_suppress():
    sh2 = Sh2Row("Sh2-995", None, None, 100.0, 20.0, 30.0)
    neb = _ngc("NGC9995", "HII", 100.0, 20.05, 30.0)
    other = _ngc("NGC9996", "G", 150.0, -10.0, 5.0)
    assert sh2_cross_ids([sh2], [neb, other], overrides={"Sh2-995": None}) == {}
    details = sh2_cross_id_details([sh2], [neb, other], overrides={"Sh2 995": "NGC 9996"})
    assert [(d["sh2"], d["ngc"], d["via"]) for d in details] == [("Sh2-995", "NGC9996", "override")]


def test_sh2_cross_id_prefers_messier_canonical():
    # Sh2-25 ~ NGC6523 (= M8): the Sh2 row lands on the Messier key.
    m8 = _ngc("NGC6523", "Cl+N", 270.9, -24.38, 45.0, messier="M008")
    sh2_25 = Sh2Row("Sh2-25", "Sh 2-25", None, 270.95, -24.35, 50.0)
    index = build_alias_index(
        messier_rows=[MessierRow("M8", 8, "NGC6523", "Lagoon Nebula")],
        ngc_rows=[m8],
        sh2_rows=[sh2_25],
    )
    assert index.resolve("Sh2-25") == "M8"
    assert index.sh2_cross_ids[0]["ngc"] == "NGC6523"


def test_alias_index_reports_applied_cross_ids():
    index = _catalog_index()
    pairs = {(d["sh2"], d["ngc"]) for d in index.sh2_cross_ids}
    assert ("Sh2-131", "IC1396") in pairs
    assert ("Sh2-117", "NGC7000") in pairs
    assert ("Sh2-162", "NGC7635") in pairs
