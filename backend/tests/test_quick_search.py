"""Quick search must treat % and _ literally and ignore spacing in object names."""
from sqlalchemy.dialects import postgresql

from app.api.images import _build_image_query


def _sql(search):
    stmt = _build_image_query(search=search)
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_wildcards_are_escaped():
    sql = _sql("%")
    assert "ESCAPE" in sql.upper()


def test_spaces_ignored_for_object_name():
    sql = _sql("M 31")
    assert "replace(images.object_name, ' ', '')" in sql
    assert "M31" in sql
