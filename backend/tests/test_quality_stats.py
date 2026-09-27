"""Q1d library-wide star quality statistics (docs/design/Q1-star-quality.md §7.6)."""

from datetime import datetime, timedelta

import pytest

from app.services.quality_stats import (
    build_stats, by_altitude, filter_offsets, histogram, hfr_vs_temperature, monthly, use_arcsec_for,
)


def _r(i, fwhm=3.0, filt="L", scale=1.0, alt=50.0, temp=None, hfr=None, t=None):
    return {"fwhm_px": fwhm, "hfr_px": hfr if hfr is not None else fwhm * 0.6, "scale": scale, "filter": filt,
            "alt_deg": alt, "foc_temp": temp, "t": t or datetime(2026, 1, 1) + timedelta(days=i)}


def test_units_follow_scale_coverage():
    assert use_arcsec_for([_r(0), _r(1), _r(2, scale=None)]) is True
    assert use_arcsec_for([_r(0, scale=None), _r(1, scale=None), _r(2)]) is False
    assert build_stats([_r(i) for i in range(10)], prefer_arcsec=False)["units"] == "PX"


def test_filter_offsets_against_L():
    rows = [_r(i, fwhm=3.0) for i in range(6)] + [_r(i, fwhm=3.6, filt="B") for i in range(6)] + [_r(0, fwhm=9, filt="R")]
    out = filter_offsets(rows, use_arcsec=True)
    assert [o["filter"] for o in out] == ["L", "B"]          # R has < 5 subs
    assert out[0]["reference"] is True and out[1]["offset_pct"] == 20.0


def test_filter_offsets_without_L_use_most_common():
    rows = [_r(i, fwhm=2.0, filt="Ha") for i in range(8)] + [_r(i, fwhm=2.5, filt="OIII") for i in range(5)]
    out = {o["filter"]: o for o in filter_offsets(rows, True)}
    assert out["Ha"]["reference"] and out["OIII"]["offset_pct"] == 25.0


def test_monthly_and_altitude_bins():
    rows = [_r(i, fwhm=3.0 + (i % 2) * 0.2, alt=25 + (i % 3) * 20) for i in range(60)]
    months = monthly(rows, True)
    assert months[0]["month"] == "2026-01" and months[0]["n"] == 31
    alts = by_altitude(rows, True)
    assert [a["alt_from"] for a in alts] == [20, 40, 60]


def test_histogram_has_all_counts():
    rows = [_r(i, fwhm=2 + i * 0.01) for i in range(100)]
    h = histogram(rows, True, bins=10)
    assert len(h) == 10 and sum(b["count"] for b in h) == 100


def test_hfr_temperature_slope():
    # HFR rises 0.05 px for every degC the focuser cools: a negative slope.
    rows = [_r(i, temp=10 - i * 0.2, hfr=2.0 + i * 0.01) for i in range(50)]
    out = hfr_vs_temperature(rows)
    assert out["slope_px_per_degc"] == pytest.approx(-0.05, abs=0.001)
    assert out["n"] == 50 and out["bins"]


def test_hfr_temperature_needs_a_range():
    rows = [_r(i, temp=5.0 + (i % 2) * 0.5) for i in range(40)]
    assert hfr_vs_temperature(rows)["slope_px_per_degc"] is None
