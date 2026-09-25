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
        field_radius=1.0,  # threshold = 0.35
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == ("M110", "MATCH")


def test_resolve_target_match_radius_guard_rejects_edge_objects():
    # Only match is well outside 0.35 * field_radius -> no MATCH, falls through.
    matches = [
        MatchInfo("MESSIER", "M31", 0.9, True, 3.4),
    ]
    key, source = resolve_target(
        frame_type="LIGHT",
        object_name=None,
        matches=matches,
        field_radius=1.0,  # threshold = 0.35
        current_source=None,
        alias_index=AliasIndex(),
    )
    assert (key, source) == (None, None)


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
    assert (key, source) == (None, None)


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
    assert (key, source) == (None, None)
