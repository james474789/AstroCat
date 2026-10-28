"""
File-path LIKE filters (D2 follow-up to A1).

Postgres' default LIKE escape is a backslash, so a pattern built from a Windows path
("D:\\Astro%") silently matched nothing: rescans re-queued every file and never removed deleted
ones, and per-folder bulk tasks found no images. Every file-path LIKE now goes through
app.utils.path_security with an explicit ESCAPE '!'. Folder filters (scan, bulk) also stop
matching sibling folders ("/data/mount1" no longer matches "/data/mount10/...").
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects import postgresql

from app.models.image import Image
from app.utils.path_security import escape_like, path_startswith, path_under_root


def _pg(clause) -> tuple[str, dict]:
    compiled = clause.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.parametrize("value, escaped", [
    ("D:\\Astro\\M31", "D:\\Astro\\M31"),       # backslashes are plain characters under '!'
    ("/data/My_Photos", "/data/My!_Photos"),
    ("/data/100%", "/data/100!%"),
    ("/data/wow!", "/data/wow!!"),
])
def test_escape_like(value, escaped):
    assert escape_like(value) == escaped


@pytest.mark.parametrize("root, exact, pattern", [
    ("/data/mount1", "/data/mount1", "/data/mount1/%"),
    ("/data/mount1/", "/data/mount1", "/data/mount1/%"),
    ("D:\\Astro_1\\", "D:\\Astro_1", "D:\\Astro!_1\\%"),
    ("D:/Astro", "D:\\Astro", "D:\\Astro\\%"),
    ("/data/50%!", "/data/50%!", "/data/50!%!!/%"),
])
def test_path_under_root_sql(root, exact, pattern):
    sql, params = _pg(path_under_root(Image.file_path, root))
    assert "ESCAPE '!'" in sql
    assert sorted(params.values()) == sorted([exact, pattern])


def test_path_startswith_sql_keeps_prefix_semantics():
    sql, params = _pg(path_startswith(Image.file_path, "D:\\Astro\\M_31"))
    assert "ESCAPE '!'" in sql and "||" not in sql
    assert list(params.values()) == ["D:\\Astro\\M!_31%"]


def test_matching_on_a_real_database():
    """SQLite honours LIKE ... ESCAPE: siblings and wildcard look-alikes don't match."""
    md = MetaData()
    t = Table("images", md, Column("id", Integer, primary_key=True), Column("file_path", String))
    engine = create_engine("sqlite://")
    md.create_all(engine)
    paths = ["D:\\Astro_1", "D:\\Astro_1\\a.fits", "D:\\Astro_10\\b.fits", "D:\\AstroX1\\c.fits",
             "/data/mount1/d.fits", "/data/mount10/e.fits", "/data/50%/f.fits", "/data/50x/g.fits"]
    with engine.begin() as conn:
        conn.execute(t.insert(), [{"file_path": p} for p in paths])

        def hits(clause):
            return sorted(r[0] for r in conn.execute(select(t.c.file_path).where(clause)))

        assert hits(path_under_root(t.c.file_path, "D:\\Astro_1\\")) == ["D:\\Astro_1", "D:\\Astro_1\\a.fits"]
        assert hits(path_under_root(t.c.file_path, "/data/mount1")) == ["/data/mount1/d.fits"]
        assert hits(path_under_root(t.c.file_path, "/data/50%")) == ["/data/50%/f.fits"]
        assert hits(path_startswith(t.c.file_path, "D:\\Astro_1\\")) == ["D:\\Astro_1\\a.fits"]


def test_scan_looks_up_existing_rows_with_an_escaped_prefix(tmp_path):
    from app.tasks.indexer import _scan_directory

    root = tmp_path / "Lib_1"
    root.mkdir()
    existing = root / "m31.fits"
    existing.write_bytes(b"")
    statements = []

    def execute(stmt, *a, **k):
        statements.append(stmt)
        result = MagicMock()
        result.all.return_value = [(str(existing), "annotated")]
        return result

    session = MagicMock()
    session.execute.side_effect = execute
    session_cm = MagicMock()
    session_cm.__enter__.return_value = session

    with patch("app.tasks.indexer.SessionLocal", return_value=session_cm), \
         patch("app.tasks.indexer.redis"), \
         patch("app.tasks.indexer._touch_indexer_heartbeat"), \
         patch("app.tasks.indexer.process_image") as process_image:
        result = _scan_directory(str(root))

    sql, params = _pg(statements[0])
    assert "ESCAPE '!'" in sql
    assert params["file_path_1"] == str(root)
    assert result["files_found"] == 1 and result["files_queued"] == 0  # the stored row was found
    process_image.delay.assert_not_called()


@pytest.mark.parametrize("task_name", ["bulk_match_task", "bulk_astrometry_task",
                                       "bulk_thumbnail_task", "bulk_metadata_task"])
def test_bulk_tasks_filter_the_folder_with_an_escaped_prefix(task_name):
    from app.tasks import bulk

    filters = []
    session = MagicMock()

    def capture(*conds):
        filters.extend(conds)
        q = MagicMock()
        q.all.return_value = []
        q.filter.side_effect = capture
        return q

    session.query.return_value.filter.side_effect = capture
    session_cm = MagicMock()
    session_cm.__enter__.return_value = session

    with patch("app.tasks.bulk.SessionLocal", return_value=session_cm), patch("app.tasks.bulk.redis"):
        try:
            getattr(bulk, task_name).run("D:\\Astro_1")
        except Exception:
            pass  # only the query filter matters here

    compiled = [_pg(c) for c in filters]
    path_filters = [(sql, p) for sql, p in compiled if "file_path" in sql]
    assert path_filters, f"{task_name} built no file_path filter"
    sql, params = path_filters[0]
    assert "ESCAPE '!'" in sql
    assert sorted(params.values()) == ["D:\\Astro!_1\\%", "D:\\Astro_1"]
