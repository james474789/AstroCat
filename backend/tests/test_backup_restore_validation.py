import pytest

from app.api.admin import prepare_restore_sql


def test_empty_backup_is_refused():
    with pytest.raises(ValueError):
        prepare_restore_sql(b"")
    with pytest.raises(ValueError):
        prepare_restore_sql(b"  \n")


def test_non_sql_is_refused():
    with pytest.raises(ValueError):
        prepare_restore_sql(b"hello world")


def test_dump_without_clean_resets_schema():
    out = prepare_restore_sql(b"-- PostgreSQL database dump\nCREATE TABLE t (id int);\n").decode()
    assert "pg_terminate_backend" in out
    assert "DROP SCHEMA IF EXISTS public CASCADE" in out


def test_dump_with_clean_is_not_reset():
    out = prepare_restore_sql(b"-- PostgreSQL database dump\nDROP TABLE IF EXISTS t;\nCREATE TABLE t (id int);\n").decode()
    assert "DROP SCHEMA" not in out
