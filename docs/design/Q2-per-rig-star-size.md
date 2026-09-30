# Q2 — Per-rig median star size chart (FITS page)

Status: **Proposed** · Written: 2026-09-30 · Base commit: `40d5e58` (`main`) · Size: S–M · Depends on: Q1 (star quality metrics)

## 1. Problem

The FITS page shows Star Quality (Q1) for **one rig at a time** (`QualityStatsSection.jsx`, rig dropdown). There is no way to compare star sizes across rigs. The last card of the general charts grid, Pixel Scale Distribution, also sits alone in the left column, so the right cell is empty before the Star Quality section.

## 2. Goals / Non-goals

**Goals**
1. A chart of **median FWHM or HFR per rig**, so star sizes can be compared across all rigs at a glance.
2. It fills the empty cell beside Pixel Scale Distribution.
3. It works in arcsec, so rigs with different pixel scales are comparable.

**Non-goals**
- No change to the per-rig Star Quality cards.
- No new metrics; this uses the stored `images.fwhm_px` and `images.hfr_px`.

## 3. Design

### 3.1 Backend
Add `by_rig` to the `/quality/stats` response (`backend/app/api/quality.py::quality_stats`, `backend/app/services/quality_stats.py`). It is **independent of the selected `rig_id`**.

```
by_rig: [{ rig_id, rig_name, n, units: "ARCSEC"|"PX",
           fwhm_median, hfr_median }]
```

- One SQL, `GROUP BY images.rig_id`, using `percentile_cont(0.5)` over `fwhm_px` and `hfr_px`. Same filter as the rest of the module: `LIGHT_SUBS_SQL` and `star_metrics_status='OK'`. Rows with no rig are excluded.
- Arcsec conversion uses `SCALE_SQL` (per sub) where a plate scale exists. A rig reports `ARCSEC` when at least half its subs have a scale (same rule as `use_arcsec_for`); otherwise `PX`.
- Rigs with fewer than `MIN_BIN` measured subs are dropped.
- Names come from `_rig_names`.
- Pattern reference: `_rig_delivered` (`backend/app/api/equipment.py:298`).

### 3.2 Frontend
In `frontend/src/pages/FitsStats.jsx`, add a `chart-card` immediately after Pixel Scale Distribution:
- Data: `fetchQualityStats` (`api/client.js`) via React Query; reads `by_rig`.
- Recharts `BarChart`, one bar per rig, sorted ascending by the selected metric (smallest stars first). Style constants match `QualityStatsSection.jsx` (`TOOLTIP_STYLE`, `AXIS`, `BAR`).
- A small **FWHM / HFR** toggle in the card header. Tooltip shows median, unit and sub count.
- Rigs reported in px (no plate scale) are drawn dimmed and flagged in the tooltip, and a footnote explains they are not directly comparable.
- Empty state when fewer than two rigs have data ("Need at least two rigs with measured subs").
- Trim `.chart-card` `min-height: 400px` (`FitsStats.css`) if it adds dead space under the 300px chart.

## 4. Files
- `backend/app/api/quality.py`, `backend/app/services/quality_stats.py` (+ schema if typed)
- `backend/tests/` — new test for `by_rig` (medians, ARCSEC/PX fallback, `MIN_BIN` drop, rig-independent)
- `frontend/src/pages/FitsStats.jsx`, `FitsStats.css`

## 5. Release
Backend change: bump `VERSION`, then `docker compose build backend && docker compose up -d backend`.

## 6. Acceptance
- `/quality/stats` returns `by_rig` regardless of the `rig_id` param.
- The chart appears in the right-hand cell beside Pixel Scale Distribution and toggles between FWHM and HFR.
- Bars are in arcsec where scale is known; px rigs are visibly marked.
- `pytest backend/tests/ -k quality`, `npm run lint`, `npm run build` pass.
