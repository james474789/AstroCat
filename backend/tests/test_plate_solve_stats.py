"""Plate-solve stats only count images that can be plate-solved."""

from sqlalchemy.dialects import postgresql

from app.models.image import FrameType, ImageSubtype
from app.utils.frame_filters import (
    PLATE_SOLVABLE_SQL,
    PLATE_SOLVABLE_SUBTYPES,
    plate_solvable_clause,
)


def test_solvable_subtypes_are_subs_and_masters_only():
    assert set(PLATE_SOLVABLE_SUBTYPES) == {ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER}
    assert ImageSubtype.PLANETARY not in PLATE_SOLVABLE_SUBTYPES
    assert ImageSubtype.ALLSKY not in PLATE_SOLVABLE_SUBTYPES
    assert ImageSubtype.AURORA not in PLATE_SOLVABLE_SUBTYPES
    assert ImageSubtype.INTEGRATION_DEPRECATED not in PLATE_SOLVABLE_SUBTYPES


def test_clause_requires_light_frames():
    sql = str(plate_solvable_clause().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "frame_type" in sql and "LIGHT" in sql
    for ft in FrameType:
        if ft != FrameType.LIGHT:
            assert ft.value not in sql.replace("DARK_FLAT", "") or ft.value == "LIGHT"
    assert "SUB_FRAME" in sql and "INTEGRATION_MASTER" in sql
    assert "PLANETARY" not in sql and "DEPRECATED" not in sql


def test_raw_sql_matches_clause():
    assert "'LIGHT'" in PLATE_SOLVABLE_SQL
    assert "'SUB_FRAME'" in PLATE_SOLVABLE_SQL and "'INTEGRATION_MASTER'" in PLATE_SOLVABLE_SQL
    assert "PLANETARY" not in PLATE_SOLVABLE_SQL and "DEPRECATED" not in PLATE_SOLVABLE_SQL
