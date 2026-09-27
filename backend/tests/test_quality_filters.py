"""Q1d star quality search filters (docs/design/Q1-star-quality.md §6, §7.1)."""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.image import Image
from app.services.quality_filters import QualityFilters


def _sql(qf: QualityFilters) -> str:
    stmt = qf.apply(select(Image.id))
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))


def _qf(**kw):
    base = dict(fwhm_min=None, fwhm_max=None, hfr_max=None, quality_units="ARCSEC", eccentricity_max=None,
                star_count_min=None, star_metrics_status=None, quality_flag=None)
    base.update(kw)
    return QualityFilters(**base)


def test_no_filters_leave_query_alone():
    assert "star_metrics" not in _sql(QualityFilters.none())


def test_size_filters_imply_measured_and_use_scale_in_arcsec():
    sql = _sql(_qf(fwhm_max=3.0))
    assert "images.star_metrics_status =" in sql
    assert "measured_scale_arcsec" in sql          # rig-scale guard in the arcsec expression


def test_px_units_compare_raw_pixels():
    sql = _sql(_qf(fwhm_max=3.0, quality_units="px"))
    assert "measured_scale_arcsec" not in sql and "images.fwhm_px <=" in sql


def test_status_list_with_never():
    qf = _qf(star_metrics_status="failed,never,bogus")
    assert qf.statuses == ["FAILED", "NEVER"]
    sql = _sql(qf)
    assert "IS NULL" in sql and "star_metrics_status IN" in sql


def test_quality_flag_any_expands_and_uses_suspect_subquery():
    qf = _qf(quality_flag="ANY")
    assert qf.flags == ["SOFT", "CLOUD", "TRAILED"]
    sql = _sql(qf)
    assert "percentile_cont" in sql and "greatest(" in sql


def test_specific_flags_only():
    assert _qf(quality_flag="cloud, soft ,nope").flags == ["CLOUD", "SOFT"]
