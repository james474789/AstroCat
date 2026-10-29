from sqlalchemy.dialects import postgresql

from app.api.catalogs import ngc_designation_number


def test_ngc_sort_key_reads_digits_from_designation():
    sql = str(ngc_designation_number().compile(dialect=postgresql.dialect()))
    assert "regexp_replace" in sql and "ngc_catalog.designation" in sql
    assert "NULLIF" in sql.upper()
