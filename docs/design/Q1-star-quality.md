# Q1 — Star Quality Metrics (HFR / FWHM)

Status: **Proposed** · Written: 2026-09-27 · Base commit: `1e1bf62` (`main`) · Size: L · Depends on: F1 (`frame_type`), F2 (`target_key`), P0 (`capture_date_utc`), R0 (`rig_id`/`site_id`)
Relates to: F7 sessions. F7 §2 lists "HFR/FWHM" as a separate feature and reserves room for it in its timeline. This is that feature.

## 1. Problem

AstroCat knows *how much* data exists for a target, but not *how good* it is. Imagers want to know:

- Which rig/filter combination gives the sharpest data on a target (the Targets page "By Filter & Rig" table).
- Whether last night was a good-seeing night, when focus drifted, and when clouds or wind arrived (FWHM/HFR over a session).
- Which subs to cull before stacking.
- What seeing their site really delivers. `Site.typical_seeing_arcsec` is a hand-entered default (2.5″) that drives the rig sampling check in `app/utils/optics.py`.

No star measurement exists today. The only related code is the `SAMPLING_*` seeing/scale band in `optics.py` and its mirror in `frontend/src/api/client.js:969`.

## 2. Goals / Non-goals

**Goals**
1. Measure **HFR** (half-flux radius, px), **FWHM** (px and arcsec), **eccentricity**, **star count** and **background** for every Light sub, automatically, as part of indexing.
2. Backfill the existing library gradually without saturating the NAS.
3. Show the metrics on Targets ("By Filter & Rig", per-night overlay), Image Detail, Search, Stats, Equipment, Dashboard and Admin.
4. A **session timeline**: FWHM/HFR over the night, with star count, eccentricity, altitude and autofocus events.
5. Query-time **suspect-sub flags** relative to the sub's own night, rig and filter.

**Non-goals (v1)**
- Writing metrics back into files (FITS keywords / XMP). Ratings already sync to XMP; a later "reject subs above X" could reuse that.
- Per-star catalogs or star overlays on the viewer. v1 stores only aggregates plus a 3×3 region grid.
- Matching N.I.N.A./PixInsight numbers exactly. Algorithms differ (see §4.6). AstroCat's values are internally consistent, which is what comparisons need.
- Measuring JPG/PNG/8-bit TIFF. Stretched, compressed data gives meaningless profiles.

## 3. Current pipeline (what we hook into)

```
scan_directory ──► process_image(file_path)            queue: indexer
                     _process_image_impl
                       1 extract metadata (header only)
                       2 thumbnail (strided memmap read, NOT full-res)
                       3 upsert Image row
                       4 capture time / site (P0)
                       5 catalog match (LIGHT + solved)
                       6 assign_target_sync (F2)
                       7 assign_equipment_sync (R0)
                       commit
```

Key facts that shape the design:
- The thumbnail read is **strided** (`ThumbnailGenerator._source_stride`), so every star profile is destroyed. It cannot be reused. Star measurement needs its own full-resolution read of the frame.
- `process_image` is on the `indexer` queue, and one worker (`supervisord.conf`: `-Q indexer,thumbnails,celery`, `--concurrency=$CELERY_WORKERS`) consumes every queue.
- Astrometry and thumbnails already run as follow-on tasks. Star metrics follows the same pattern.
- One-off repairs go through `data_migrations.REGISTRY` (CLAUDE.md). Here the backfill is **ongoing and self-healing**, so it's a beat-driven sweeper instead (§5.4).

## 4. Measurement algorithm

New module: `backend/app/services/star_metrics.py`. It is a pure function over a file path plus the stored header, with no DB access, so it is unit-testable on synthetic arrays.

```python
@dataclass
class StarMetrics:
    status: str                  # OK | NO_STARS | SKIPPED | FAILED
    hfr_px: float | None         # median half-flux radius, native (binned) pixels
    fwhm_px: float | None        # median sqrt(fwhm_major*fwhm_minor), native pixels
    eccentricity: float | None   # median sqrt(1 - (minor/major)^2)
    star_count: int | None       # usable stars after rejection
    details: dict                # -> images.star_metrics JSONB (§5.1)

ALGO_VERSION = 1
def measure(path: str, raw_header: dict | None, file_format: ImageFormat) -> StarMetrics
```

### 4.1 Loading linear luminance

`load_luminance(path, header) -> (float32 2-D array, cfa_factor, saturation_adu)`

| Source | Handling |
|---|---|
| FITS/FIT mono | First HDU with ≥2 dims. Full read (`memmap=False` fallback for BZERO, same as `thumbnails.py`). |
| FITS with `BAYERPAT` (OSC, undebayered) | **Green super-pixel**: average the two green sites of each 2×2 cell. `cfa_factor = 2`. |
| FITS RGB cube (NAXIS3 = 3) | Use the green plane (or the channel mean if the data is already debayered). `cfa_factor = 1`. |
| XISF | `xisf` reader. CFA from the `BAYERPAT` keyword / `ColorFilterArray` property. Same rules as FITS. |
| CR2/CR3/ARW/NEF/DNG | `rawpy`: `raw_image_visible` minus `black_level_per_channel`, green super-pixel from `raw_pattern`. `cfa_factor = 2`. No demosaic and no white balance, so the data stays linear. |
| TIFF 16/32-bit | Treat as linear mono/RGB (green plane). 8-bit TIFF: `SKIPPED`. |
| JPG/JPEG/PNG | `SKIPPED` (reason `NONLINEAR_FORMAT`). |

`saturation_adu` is taken from the header `SATURATE`/`DATAMAX` if present, else `2^BITPIX − 1` (after BZERO), else `rawpy.white_level`, else the 99.99th percentile.

All results are converted back to **native pixels** by multiplying by `cfa_factor`. They are then directly comparable with `pixel_scale_arcsec`, which comes from the plate solve of the native (binned) frame.

Memory: a 61 MP frame as float32 is ~245 MB, and background maps add ~2×. Free arrays eagerly. `worker_max_tasks_per_child=100` already bounds leaks. For frames >80 MP, measure a centred 50% crop plus the four corner tiles instead (flag `cropped: true`).

### 4.2 Detection — `sep` (Source Extractor as a library)

```python
bkg = sep.Background(data, bw=64, bh=64, fw=3, fh=3)
sub = data - bkg                      # in place
sep.set_extract_pixstack(2_000_000)
objs = sep.extract(sub, thresh=5.0, err=bkg.globalrms, minarea=5,
                   deblend_cont=0.005, clean=True)
```

`sep` is a C extension that is fast (~0.3–1 s for 26 MP) and BSD/LGPL. Add `sep>=1.4` (the maintained sep-developers release with cp312 manylinux wheels; verify at implementation time) and `scipy` explicitly to `requirements.txt`. Fallback if `sep` wheels are a problem: `photutils` (`DAOStarFinder` + `Background2D`). It is slower but pure pip.

### 4.3 Star rejection

Keep an object only if **all** of these hold:
- `flag == 0` (not blended or truncated)
- `peak + bkg < 0.9 × saturation_adu` (saturated cores flatten profiles and inflate FWHM)
- `a ≥ 0.6 px` (rejects hot pixels and cosmic rays) and `a ≤ 25 px` (rejects galaxies and nebula knots)
- more than `max(10, 6a)` px from the frame edge
- `npix ≥ 5`

After rejection, if `n < 10`: status `NO_STARS`. `star_count` is still stored: a near-zero count on a Light is itself the cloud signal the timeline shows.

### 4.4 HFR

```python
r, f = sep.flux_radius(sub, x, y, 6.0 * a, 0.5, normflux=flux, subpix=5)
hfr_px = median(r[f == 0]) * cfa_factor
```

The details JSONB also keeps `hfr_p10` and `hfr_p90`.

### 4.5 FWHM and eccentricity — PSF fit on the best stars

Take up to **100** kept stars with SNR > 30, preferring mid-brightness stars (skip the brightest 5%, which are near saturation). For each one:
- Cut out a box of `2·ceil(3·HFR)+1` pixels.
- Fit an **elliptical Moffat with β = 4** (PixInsight SubframeSelector's default family) plus a constant, using `scipy.optimize.least_squares`. Free parameters: amplitude, x0, y0, α_x, α_y, θ, background.
- FWHM per axis = `2α·sqrt(2^(1/β) − 1)`.
- Reject fits that don't converge, or that give FWHM outside [0.7, 40] px.

Aggregates:
- `fwhm_px = median(sqrt(fwhm_maj·fwhm_min)) · cfa_factor`
- `eccentricity = median(sqrt(1 − (min/maj)²))`
- `theta_deg` = circular median. It is kept in the JSONB and shows guiding and wind direction.

If fewer than 5 fits succeed, fall back to `fwhm_px ≈ 2·hfr_px` (exact for a Gaussian) and set `fwhm_method: "HFR_X2"`.

Cost: ~2–5 ms per fit, so <0.5 s per frame.

### 4.6 Consistency with other tools (documented, not solved)

- N.I.N.A.'s HFR is a flux-weighted mean radius over its own detection. Expect AstroCat HFR to differ by a roughly constant factor, with the same trends.
- PixInsight SubframeSelector's FWHM (Moffat) should agree within ~10%.

The UI labels values as "AstroCat-measured", and the tooltip explains this.

### 4.7 Opportunistic hints from headers and filenames

`app/utils/star_metric_hints.py` reads whatever the capture software left behind. It costs no extra IO: `raw_header` is already stored, and the filename is known.
- Header keys: `HFR`, `FWHM`, `STARS`/`STARCOUNT`, `ECCENTRICITY`, `FOCPOS`/`FOCUSPOS`, `FOCTEMP`/`FOCUSTEM`, `AMBTEMP`, `AIRMASS`.
- N.I.N.A. filename tokens (`$$HFR$$`, `$$STARCOUNT$$`, `$$FWHM$$` from Hocus Focus). A tolerant regex matches e.g. `_HFR_2.31_`, `HFR2.31`, `_2.31HFR`.

Hints go in `star_metrics.hints`. Two uses:
- They are a fallback display value for `SKIPPED` formats (e.g., a JPG with an HFR in its name), with `source=HINT`.
- The focuser position and temperature feed the timeline's autofocus events (§8.3).

Measured values always win.

### 4.8 Region grid (tilt / field curvature)

Split the frame 3×3 and store the median HFR of each cell (`grid_hfr: [[..3],[..3],[..3]]`) when every cell has ≥5 stars. Image Detail renders it as a small heatmap. Corners much larger than the centre means curvature or backfocus. One side larger than the other means tilt. Adding it at this point is cheap, and it's a common question.

## 5. Pipeline integration

### 5.1 Schema (one Alembic migration, `down_revision = 'b1d3f7c84011'`)

The headline numbers go in columns on `images`, and everything else in one JSONB:

```python
# app/models/image.py — Star quality (Q1)
hfr_px              = Column(Float, nullable=True)
fwhm_px             = Column(Float, nullable=True)
eccentricity        = Column(Float, nullable=True)
star_count          = Column(Integer, nullable=True)
star_metrics_status = Column(String(12), nullable=True)   # NULL(never) | PENDING | OK | NO_STARS | SKIPPED | FAILED
star_metrics_version= Column(SmallInteger, nullable=True) # ALGO_VERSION that produced the row
star_metrics_at     = Column(DateTime, nullable=True)
star_metrics        = Column(JSONB, nullable=True)
Index('ix_images_star_status', 'star_metrics_status')     # sweeper
```

`star_metrics` JSONB shape:
```json
{"source": "MEASURED", "method": "sep+moffat4", "cfa_factor": 2, "cropped": false,
 "n_detected": 1840, "n_hfr": 1210, "n_fwhm": 100, "fwhm_method": "MOFFAT",
 "hfr_p10": 1.61, "hfr_p90": 2.40, "fwhm_major_px": 3.4, "fwhm_minor_px": 3.1, "theta_deg": 42,
 "bkg_adu": 812.4, "noise_adu": 11.2, "saturated_rejected": 37,
 "grid_hfr": [[2.1,1.9,2.2],[1.9,1.8,2.0],[2.3,2.0,2.4]],
 "hints": {"HFR": 2.31, "FOCPOS": 14820, "FOCTEMP": 7.5},
 "file_mtime": "2026-09-26T22:14:03", "attempts": 1, "error": null, "elapsed_ms": 1840}
```

Why columns and not a side table:
- Every consumer (search filters and sort, Targets aggregates, the timeline) already filters on `images`, and the whole codebase builds queries on `Image`.
- Five nullable floats are cheap. `raw_header`/`wcs_header` JSONB already set the "details in JSONB" precedent.

**Arcseconds are derived, not stored.** The plate-solve scale can arrive after measurement (astrometry.net is async), or be corrected later. One SQL helper in `app/utils/star_quality.py` does the conversion:

```python
SCALE_SQL = "coalesce(images.pixel_scale_arcsec, r.measured_scale_arcsec)"   # LEFT JOIN rigs r ON r.id = images.rig_id
FWHM_ARCSEC_SQL = f"images.fwhm_px * {SCALE_SQL}"
```

Plus a SQLAlchemy expression equivalent for the ORM paths.

### 5.2 New task and queue

`backend/app/tasks/quality.py`:

```python
@celery_app.task(bind=True, name="app.tasks.quality.measure_star_metrics",
                 autoretry_for=(OperationalError, BlockingIOError, TimeoutError),
                 retry_backoff=True, max_retries=3, soft_time_limit=600, time_limit=900)
def measure_star_metrics(self, image_id: int, force: bool = False): ...
```

1. Load the row. Skip unless eligible (§5.3) or `force`.
2. Call `measure(path, raw_header, file_format)`, which takes ~1–3 s CPU plus the file read.
3. Write the columns, `star_metrics`, `version = ALGO_VERSION`, `at = utcnow()`. Hints are merged every time.
4. Exceptions: transient IO errors re-raise so Celery retries. Anything else sets `FAILED`, `attempts += 1` and `error`, then returns. A poison file must never loop, which mirrors the minimal-record philosophy in `_process_image_impl`.
5. Invalidate the Targets Redis cache for that `target_key` (debounced: a single `SET targets:dirty 1 EX 60` that the list reader checks), because Targets shows medians.

Routing: `"app.tasks.quality.*": {"queue": "quality"}`, and add `quality` to the `-Q` list in `supervisord.conf`. A separate queue means a 30k-frame backfill never delays the indexing of new files or thumbnails. It also makes queue depth measurable (`LLEN quality`) for throttling.

### 5.3 Hook in `_process_image_impl`

After `session.commit()`:

```python
from app.services.star_metrics import needs_measurement   # pure predicate
if needs_measurement(image):
    image.star_metrics_status = "PENDING"; session.commit()
    measure_star_metrics.delay(image.id)
```

`needs_measurement(image)` is true when:
- `frame_type == LIGHT`
- `subtype in (SUB_FRAME, INTEGRATION_MASTER)` (masters are measured, but aggregates use `LIGHT_SUBS` only)
- `file_format` is not JPG/JPEG/PNG
- `settings.star_metrics_enabled` is on
- and **any** of these is true:
  - status is NULL
  - `star_metrics_version < ALGO_VERSION`
  - `star_metrics.file_mtime != image.file_last_modified` (the file was re-saved, e.g. re-calibrated in place)
  - status `FAILED` with `attempts < 3`

`reindex_all` therefore re-measures only what changed. Frame-type changes to non-LIGHT (manual reclassification) clear the metrics in the frame-type PATCH handler, so calibration frames don't pollute aggregates.

### 5.4 Backfill: beat-driven sweeper (not a data migration)

```python
"star-metrics-sweeper": {"task": "app.tasks.quality.sweep", "schedule": 600.0}
```

`sweep()`:
- Skip if `star_metrics_backfill` is disabled, or `LLEN quality > 50`.
- Otherwise select up to `star_metrics_sweep_batch` (default 200) eligible rows. Order: newest `capture_date_utc` first, so recent nights light up first. Also include `PENDING` rows older than 2 h (tasks lost to a worker crash).
- Mark them PENDING and enqueue.

200 frames / 10 min ≈ 29k/day. A 50 k library at ~50 MB/frame is ~2.5 TB of reads spread over ~2 days. Both settings are in Settings > Indexing (§7.9).

This is the same mechanism for backfill, retries, version bumps and stuck tasks. It is idempotent and needs no manual step, which satisfies the CLAUDE.md "no scripts users run by hand" rule without a `REGISTRY` entry.

### 5.5 Manual triggers

- `POST /api/images/{id}/star-metrics` (force re-measure, admin)
- `POST /api/admin/star-metrics/remeasure` with body `{scope: "failed"|"all"|"target", target_key?}`. This resets status to NULL, and the sweeper picks the rows up.

## 6. Suspect-sub flags (query-time, not stored)

Groups change as nights and rigs are reassigned, so flags are computed with window functions over `(night, rig_id, normalized filter)` instead of being stored:

```sql
percentile_cont(0.5) WITHIN GROUP (ORDER BY fwhm_px) OVER w  AS night_median_fwhm
...
flag = CASE WHEN fwhm_px    > :k_fwhm  * night_median_fwhm  THEN 'SOFT'      -- default 1.3
            WHEN star_count < :k_stars * night_median_stars THEN 'CLOUD'     -- default 0.5
            WHEN eccentricity > :ecc_max                     THEN 'TRAILED'  -- default 0.6
       END
```

Note: Postgres doesn't allow `percentile_cont` as a window function. Implement it as a CTE that groups by the window key and joins back. The thresholds are in Settings.

Colour coding across the UI is **relative to the rig's own distribution**, not absolute:
- green ≤ the rig's p25
- amber up to 1.3× the rig's median
- red above that

A 0.6″/px refractor and a 3″/px lens are never judged on the same scale.

## 7. API changes

All of this is additive. Existing response fields don't change.

### 7.1 Image list / detail (`api/images.py`, `schemas/`)
- Response fields: `hfr_px`, `fwhm_px`, `fwhm_arcsec` (derived), `eccentricity`, `star_count`, `star_metrics_status`.
- Detail only: `star_metrics` (JSONB), plus `quality_context: {night, night_median_fwhm_arcsec, night_median_hfr_px, delta_pct, flag}`.
- New list filters: `fwhm_arcsec_max`, `hfr_px_max`, `eccentricity_max`, `star_count_min`, `star_metrics_status`, `quality_flag` (`SOFT|CLOUD|TRAILED|ANY`).
- New `sort_by` values: `hfr_px`, `fwhm_px`, `eccentricity`, `star_count`.

### 7.2 Targets list (`_compute_targets_list`, cached)
Per target, over `LIGHT_SUBS`: `quality: {measured, median_fwhm_arcsec, median_hfr_px}`. The rigs-per-target query already exists, so this adds one grouped query with `percentile_cont`. The `filter` query param also gets per-filter medians inside `filters[]`.

### 7.3 Target detail — "By Filter & Rig"
**Gotcha:** `rig_stmt` groups by the raw `filter_name`/camera/scale/pixel/bin/rig, and `build_filter_rig_rows_with_rigs` (`utils/rig_optics.py:264`) then **merges buckets by summing**. Medians cannot be merged that way. So:
- Add `array_agg(images.hfr_px) FILTER (WHERE images.hfr_px IS NOT NULL)` and the same for `fwhm_arcsec` and `eccentricity` to `rig_stmt`.
- Concatenate the lists in both merge paths (`build_filter_rig_rows` and `_with_rigs`) and compute the stats in Python after merging. Pop the lists before returning.

Row additions:
```json
"quality": {"measured": 212, "median_hfr_px": 1.92, "median_fwhm_px": 3.6,
            "median_fwhm_arcsec": 2.41, "best_fwhm_arcsec": 1.98, "p90_fwhm_arcsec": 3.05,
            "median_ecc": 0.41, "median_stars": 1320}
```
`best` means the p10, which is robust to single lucky frames. Add unit tests for the merge in `tests/test_rig_optics.py`.

`nights_detail[]` gains `quality: {median_fwhm_arcsec, median_hfr_px, measured}` per night. This is a separate `GROUP BY night` query using the same `NIGHT_SQL`.

### 7.4 Session timeline — new router `api/quality.py`
```
GET /api/quality/nights?target_key=&rig_id=&site_id=&from=&to=
    -> [{night, rig_id, rig_name, subs, measured, median_fwhm_arcsec, median_hfr_px}]
       (the picker; newest first)

GET /api/quality/timeline?night=YYYY-MM-DD[&rig_id][&site_id][&target_key]
    -> {
      night, site: {id, name, lat, lon, tz},
      points: [{image_id, t_utc, filter, target_key, exposure_s,
                hfr_px, fwhm_px, fwhm_arcsec, eccentricity, star_count, bkg_adu,
                altitude_deg, airmass, focuser_pos, focuser_temp, flag}],
      events: [{t_utc, type: AUTOFOCUS|FILTER_CHANGE|TARGET_CHANGE|MERIDIAN_FLIP|GAP, detail}],
      summary: {per_filter: [{filter, n, median_fwhm_arcsec, best, worst, slope_per_hour}],
                sky_dark_utc: [start, end]}
    }
```

- Points: `LIGHT_SUBS` in `night_bounds_utc(night, lon)` for the chosen rig/site, ordered by `capture_date_utc`.
- **Altitude/airmass** is computed server-side and vectorized with the existing `utils/horizon.alt_az`. It uses the image RA/Dec, or the target's catalog RA/Dec if unsolved, plus site lat/lon. Airmass uses Kasten–Young.
- **Events** are inferred from consecutive points:
  - AUTOFOCUS: `FOCPOS` changes by more than 0.
  - FILTER_CHANGE / TARGET_CHANGE: normalized value changes.
  - MERIDIAN_FLIP: `rotation_degrees` jumps by ~180° (±10), or `PIERSIDE` changes.
  - GAP: gap > max(3×exposure, 10 min).
- `slope_per_hour` is the Theil–Sen slope of FWHM over time. It gives "focus drift" at a glance.
- Optional `normalize=zenith` returns `fwhm × airmass^-0.6`, which separates seeing changes from altitude effects.

### 7.5 Equipment (`api/equipment.py`)
- `rig_dict` gains `delivered_fwhm: {median_arcsec_90d, best_arcsec_90d, n}`.
- `site_dict` gains `measured_fwhm_arcsec_90d`. Call it "delivered FWHM", because it includes optics and guiding and is ≥ seeing.
- The rig sampling check (`rig_optics_summary(..., seeing_arcsec=...)`) uses the rig's measured median when `n ≥ 50`, else the site value as today. The response says which it used.

### 7.6 Stats (`api/fits_stats.py`)
New `quality` block:
- FWHM histogram per rig
- monthly median FWHM (seasonal seeing)
- FWHM vs altitude (binned)
- per-filter median offset vs L for each rig
- HFR vs focuser temperature (binned)

### 7.7 Dashboard
`last_night: {night, subs, median_fwhm_arcsec, suspect, rigs:[...]}`. This goes on the existing dashboard payload.

### 7.8 Admin
The data-maintenance status gains `star_metrics: {eligible, ok, no_stars, skipped, failed, pending, never, queue_depth, algo_version}`.

### 7.9 Settings (`config.py` defaults + Settings UI)
| Setting | Default |
|---|---|
| `star_metrics_enabled` | `true` |
| `star_metrics_backfill` | `true` |
| `star_metrics_sweep_batch` | `200` |
| `quality_soft_factor` | `1.3` |
| `quality_cloud_factor` | `0.5` |
| `quality_trailed_ecc` | `0.6` |
| `quality_units` | `"ARCSEC"` (alternative: `PX`); the default for the per-viewer toggle (§12.3) |

## 8. UI

Shared pieces:
- `frontend/src/utils/quality.js`: `formatFwhm(arcsec, px)` → `2.41″ (3.6 px)`, `formatHfr(px)`, `qualityColor(value, rigStats)`, and the primary-metric switch.
- `frontend/src/utils/filterColors.js`: move `FILTER_COLORS` / `filterColor` out of `TargetDetail.jsx`, because the timeline reuses them.
- `components/quality/QualityBadge.jsx`: a coloured number with a tooltip ("AstroCat-measured, n = 212 subs, median; best = p10").

### 8.1 Targets page (requested)
**Target detail → "By Filter & Rig"** gets new columns:

| Filter | Rig | Pixel Scale | Focal Length | Subs | Integration | **FWHM** | **Best** | **HFR** | **Ecc.** |
|---|---|---|---|---|---|---|---|---|---|
| Ha | RedCat 51 | 3.10″/px | 250 mm | 96 | 8h 00m | 7.9″ | 7.1″ | 1.3 px | 0.38 |
| Ha | C8 + 0.63 | 0.62″/px | 1280 mm | 40 | 3h 20m | 2.6″ | 2.2″ | 2.1 px | 0.44 |

- Subs with partial coverage show `212/240` measured in the tooltip.
- The **Filter Breakdown** table gets one "Median FWHM" column.
- The **Nights chart** becomes a `ComposedChart`: the existing stacked integration bars plus a **line of per-night median FWHM** on a right-hand axis. **Clicking a night** opens the session timeline panel (§8.2) inline under the chart, filtered to this target, with a toggle to "whole night (all targets)".
- **Targets list**: an optional sortable "FWHM" column, and "sharpest data" as a sort key.

### 8.2 Session timeline (requested): `components/quality/SessionQualityChart.jsx`
One component, mounted in three places:
- TargetDetail (on night click)
- the new **Night Report** page `/nights/:night?rig_id=`
- Image Detail, as a compact sparkline with the current sub highlighted

Layout: stacked Recharts panels sharing `syncId` so hover lines up.

```
FWHM″ ┤    ●●  ●                 ┊AF          ●
  3.0 ┤  ●    ●  ●●●   ●●●●●●●●●●┊●●●●●●●●●●●  ●●     ← points coloured by filter,
  2.5 ┤●                         ┊               ●●     rolling-median line, red ring = flagged
      ┤ ░░░░░░░░ altitude (right axis, shaded) ░░░░░░
Stars ┤▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▅▃▁ ▁▃▇▇        ← clouds show as a dip
Ecc.  ┤────────────────────────────────── ╱╲──        ← wind/guiding
      └┬──────┬──────┬──────┬──────┬──────┬──────┬──  local site time
      21:00  22:00  23:00  00:00  01:00  02:00  03:00
       ┊ = autofocus (FOCPOS change)   ▏ = filter change   ⇄ = meridian flip
```

Controls:
- metric toggle: FWHM″ / FWHM px / HFR / Eccentricity / Stars / Background (sky brightness: moon and dawn)
- "normalize to zenith"
- filter chips
- clicking a point opens Image Detail
- a per-filter summary strip underneath: median, best, worst, drift/hour

Times are shown in the site's timezone (R0 `Site.timezone`).

**Night Report page** (`pages/NightReport.jsx`, route `/nights/:night`):
- a night picker (from `/api/quality/nights`) and a rig selector (multi-rig nights since R0 allow 5 mounted rigs)
- the timeline
- a table of subs sortable by any metric, with a "select suspect" helper that links to Search pre-filtered

When F7 sessions land, the F7 Session detail embeds this component and `/nights/:night` redirects there. F7 §2 already reserves the timeline room for it.

### 8.3 Image Detail
A **"Star Quality"** card between "Exposure Data" and "Equipment" shows:
- HFR, FWHM (″ and px), eccentricity and θ, stars, background
- **Sampling**: FWHM px per pixel scale → Under / OK / Over, using the `SAMPLING_MIN/MAX` bands from `optics.py`
- **vs night median** (e.g., `+12%`, with the flag chip)
- the **3×3 HFR grid** heatmap (tilt / curvature)
- the compact session sparkline with "View night →"
- an admin "Re-measure" button
- status text for `PENDING` ("measuring…"), `NO_STARS`, `SKIPPED` (with the reason) and `FAILED` (with the error)

### 8.4 Search
- Advanced filters: FWHM max (″), HFR max (px), Eccentricity max, Stars min, and "Suspect only" (SOFT/CLOUD/TRAILED).
- Sort options for each metric.
- `ImageCard` shows a small FWHM badge when sorted or filtered by quality.

### 8.5 Other places it's useful

| Where | What | Why |
|---|---|---|
| **Equipment → Rig card** | "Delivered FWHM (90 d): 2.6″ median · 2.0″ best". The sampling badge uses the measured value. | Replaces the guessed 2.5″ in the sampling check with reality. Tells users whether they are over- or under-sampled. |
| **Equipment → Site** | "Measured 2.4″, *Use as typical seeing*" button, which PATCHes `typical_seeing_arcsec` | This feeds the rig sampling check and, later, R1 recommendation scoring. |
| **Stats page** | New "Quality" section: FWHM histogram per rig, monthly median FWHM, FWHM vs altitude, **per-filter FWHM offset vs L**, HFR vs focuser temperature | The per-filter offset shows chromatic focus shift, meaning N.I.N.A. filter offsets are needed. HFR vs temperature gives a temperature-compensation coefficient. Both are actionable. |
| **Dashboard** | "Last night" card: subs, median FWHM, suspect count, link to Night Report | The morning-after question. |
| **Admin → Data Maintenance** | Progress ("12,340 / 48,211 measured · 210 failed · queue 38"), "Re-measure failed/all" | Backfill visibility. |
| **Tonight** (later) | "Last FWHM on this rig/target" hint per pick | Low value in v1. The R1 engine could later prefer good-seeing-sensitive targets on nights forecast to be steady. Out of scope. |

## 9. Testing

**Unit tests** (`tests/test_star_metrics.py`) use synthetic frames made with numpy: Moffat/Gaussian stars at known FWHM 2–8 px, Poisson noise, a background gradient, hot pixels, saturated stars and elongated stars. Assertions:
- HFR and FWHM within ±5% of truth
- eccentricity within ±0.05
- saturated stars rejected
- a star-free frame gives `NO_STARS`
- a CFA mosaic gives `cfa_factor = 2`, and results are in native pixels
- JPG gives `SKIPPED`

**Pipeline tests:**
- `needs_measurement` truth table (version bump, mtime change, FAILED attempts, non-LIGHT)
- the sweeper respects queue depth and batch size
- the process hook sets PENDING

**API tests:**
- by-filter-rig medians survive bucket merging (two raw filter spellings → one row)
- timeline events (FOCPOS change, flip, gap)
- flag CTE thresholds
- arcsec falls back to the rig's measured scale when the image is unsolved

**Validation on real data:** run over ~50 subs that the owner also runs through PixInsight SubframeSelector (CSV export). Expect Pearson r > 0.95 for FWHM, and document the offset. Also compare against N.I.N.A. filename HFR hints where present.

**Performance:** benchmark 26 MP mono, 61 MP mono, and a 24 MP CR2 on the NAS. Target is under 3 s CPU and under 1 GB peak RSS per frame.

## 10. Rollout / phasing

| Phase | Scope | Visible result |
|---|---|---|
| **Q1a** | Dependencies (`sep`, `scipy`), migration, `star_metrics.py`, `tasks/quality.py`, `quality` queue, process hook, sweeper, settings, image API fields, Image Detail card, Admin progress | Every new Light is measured; the library backfills over ~1–2 days |
| **Q1b** | Targets list/detail aggregates, By Filter & Rig columns, nights FWHM line | The requested Targets view |
| **Q1c** | `api/quality.py` (nights, timeline, events, altitude), `SessionQualityChart`, Night Report page, TargetDetail night click-through, Image Detail sparkline | The requested session timeline |
| **Q1d** | Search filters/sort/suspect flags, Equipment delivered FWHM plus "use as seeing", Stats quality section, Dashboard card | Culling and equipment insight |

Deployment follows CLAUDE.md. Bump `VERSION`, then run `docker compose build backend && docker compose up -d backend`. The supervisord `-Q` change and the new dependencies are baked into the image. The Alembic migration runs through the existing startup path, and the sweeper starts on the first beat tick.

## 11. Risks

- **NAS IO during backfill.** This is the dominant cost. It is mitigated by the separate queue, the queue-depth gate, the batch size setting and the on/off toggle. The worst case is a slow NAS for a day or two.
- **Worker CPU and RAM contention.** One pool serves all queues, so a heavy measure task shares a slot with indexing. If this bites, run a dedicated `-Q quality --concurrency=1` process in supervisord.
- **Undersampled data** (FWHM < 1.5 px, typical for short camera lenses). Moffat fits are unstable here. Fall back to HFR×2 and show "undersampled" in Image Detail instead of a precise-looking number.
- **Wide-field lens frames with strong coma/vignetting at the edges.** The median is dominated by edge stars. The 3×3 grid makes this visible. A later option could be "centre-only metric".
- **Numbers differ from N.I.N.A./PixInsight.** This is documented (§4.6). Relative comparisons within AstroCat are the product.

## 11a. As built — Q1a-1 (2026-09-27, branch `feat/q1a-star-metrics`)

These are the places where the implementation differs from the text above. Where they disagree, this section wins.

- **CFA handling (§4.1).** The build sums all four sites of each 2×2 cell (R+G+G+B) instead of averaging the two greens. This needs no Bayer pattern and no `ROWORDER`/offset handling.
  - On a real R7 sub, full-resolution interpolated green gave FWHM 3.16 px against 3.21 px for the super-pixel sum.
- **Detection (§4.2).** Frames over 8 MP are detected on a 2×2-binned copy, and positions are then refined at full resolution with `sep.winpos`. Detection was 80% of the runtime.
  - This took a 26 MP frame from 5.5 s to about 3 s.
  - Synthetic tests confirm no bias (FWHM within 5%).
- **Measurement is unchanged.** Saturation uses the header value, else BITPIX, else the clipping plateau. HFR uses a two-pass aperture. FWHM is an elliptical Moffat with β=4 on up to 100 stars.
- **Status `HINT`.** A `SKIPPED` file (JPG/PNG/8-bit TIFF) that carries a capture-software HFR stores that value with status `HINT`. Aggregates must use `status = 'OK'` only.
- **Hints.** Also parsed: `GUIDE_RMS` (N.I.N.A. `_Guide-2.30_`). The N.I.N.A. filename `FWHM-60.77` token is arcsec on the owner's data (20.8″/px rig), so FWHM hints are kept as written and never used as pixels.
- **Frame-type edits (§5.3).** No PATCH-handler change. Non-Light rows keep their numbers but every aggregate filters on `LIGHT_SUBS`. A row that becomes Light has a NULL status, so the sweeper picks it up.
- **Dependencies.** `sep==1.4.1` and `scipy==1.13.1`. `sep` 1.4.1 requires **numpy ≥ 1.26.4**, so numpy was bumped from 1.26.3 to 1.26.4 (patch release).
- **Settings.** Env-level for now: `STAR_METRICS_ENABLED`, `STAR_METRICS_BACKFILL`, `STAR_METRICS_SWEEP_BATCH`, `STAR_METRICS_QUEUE_MAX`. Q1a-2 exposes them in the Settings UI.

**Real-data check** (read-only, owner's NAS):

| Data | Result | Time per frame |
|---|---|---|
| R7 + 105 mm, FITS | All OK, stable across the night | ~2.5 s incl. NAS read |
| ASI294MM + 23 mm, N.I.N.A. | All OK | ~3.5 s |
| 6D, CR2 | OK | 2.3–2.5 s |
| R8 + EdgeHD, CR3 | OK | 3.0 s |
| 390 MB XISF master | OK | 51 s (23 s read + 27 s for 25 k stars), 0.9 GB peak RSS |

- **HFR vs N.I.N.A.** Mono N.I.N.A. HFR is within about ±10% on most frames. N.I.N.A. HFR on a 6D CR2 is about 2.6× AstroCat's, because N.I.N.A. measures on the raw mosaic with its own definition. The PixInsight comparison planned for Q1a-3 is the arbiter.
- **Genuine optics findings.** The 3×3 grid shows a left/right HFR gradient on the R7 night (3.3 vs 2.3 px). The ASI294 30-Nov R subs have strongly elongated stars (major/minor ≈ 2), which N.I.N.A.'s single HFR number doesn't show.
- **Library size at build time.** 64.7 k eligible frames (23 k FIT, 21 k FITS, 13 k CR2, 4.4 k JPG, 1.3 k CR3, 1 k TIF, 308 XISF, …). At 200 per 10 min the backfill takes about 2–3 days.

## 11b. As built — Q1a-2 and Q1b (2026-09-27)

**Q1a-2**
- `ImageDetail.quality` block, re-measure endpoints, Admin ⭐ Star Quality section, and the ″/px toggle (sidebar and card).
- Runtime switches live in the Redis system settings: `quality_units`, `star_metrics_enabled`, `star_metrics_backfill`. The env `STAR_METRICS_*` settings win when off.

**Q1b**
- **Targets list.** Every target and filter bucket gets `quality`: median FWHM/HFR in px and arcsec. Target-level values come from exact Postgres `percentile_cont`. Filter buckets that fold several raw names use a measured-weighted median of the per-name medians.
- **Sort.** `sort=fwhm` puts the sharpest first. Unmeasured targets always sort last.
- **By Filter & Rig.** Buckets carry `quality_samples` (`[fwhm_px, hfr_px, ecc, stars, scale]` per measured sub, via `json_agg`). Both row builders in `rig_optics.py` concatenate them, and `summarize_samples` produces median, best (p10) and p90 FWHM, HFR, eccentricity and star count after the fold.
- **Nights.** Each `nights_detail[]` entry gets `quality`. Target Detail draws it as a right-axis line on the Nights chart.
- **Masters.** Masters carry `fwhm_px`/`hfr_px` and arcsec, shown under each master card.
- **ALGO_VERSION 2.** Real EdgeHD narrowband subs (0.34″/px, uncalibrated 300 s Hα/SII) gave FWHM 1.2 px against HFR 4.4 px, because fit candidates were ranked by peak and warm-pixel pairs out-peaked the soft stars. Three changes fixed it:
  - Candidates are now ranked by flux.
  - Objects whose HFR is below 0.5× the HFR of the brightest-by-flux stars are rejected as artefacts and not counted.
  - Fits narrower than 0.8× the star's own HFR are discarded.

  Those subs now measure 7.5–8.1 px, consistent with the rest of the night, and control frames are unchanged within about 5%. Rows measured by version 1 are re-measured automatically.
- **Plate-scale guard.** 466 of 6,417 header-solved images store a plate scale more than 2.5× off their rig's measured scale (e.g. 72″/px on registered `_r.fit` subs of a 2.27″/px rig). `resolve_scale` / `SCALE_SQL` use the rig scale in that case (`scale_source: RIG_OVERRIDE`), and Image Detail explains why. The root cause is a separate task.

## 11c. As built — Q1c (2026-09-27)

**API** (`api/quality.py`)
- `GET /api/quality/nights` returns nights newest first, with a per-rig breakdown.
- `GET /api/quality/timeline?night=&target_key=&rig_id=&site_id=` returns one night.
- The pure logic lives in `services/session_quality.py`.

**Altitude and airmass**
- Uses `utils/horizon.alt_az` at mid-exposure. Unsolved subs borrow their target's median pointing for that night.
- Airmass uses Kasten–Young.
- The site comes from the assigned site, else the image's header site, else the default site.

**Events**
- Autofocus: any `FOCPOS` change. N.I.N.A. filename tokens `Focus-122312` and `FTemp--2.37C` are now parsed too.
- Filter change and target change.
- Gaps longer than max(10 min, 3 × exposure).
- **Meridian flips** come from a `PIERSIDE` change, or from a *sustained* rotation change: at most ¼ of the 4 subs before on one side and at least ¾ of the 4 after on the other.
  - Real R7 headers alternate −87.6°/92.0° sub to sub, which fired 8 false flips with a naive 180° jump test.

**Flags**
- Computed per rig+filter against the night's median: SOFT (FWHM > 1.3×), CLOUD (stars < 0.5×), TRAILED (ecc > max(0.6, median + 0.15)).
- The TRAILED threshold is relative so that lenses whose stars are always elongated aren't flagged wholesale.
- Groups with fewer than 5 subs aren't flagged.

**Summary**
- Median, best and worst FWHM per rig+filter.
- Drift is the Theil–Sen slope per hour, only for runs of at least 45 minutes.
- The dark window is the full astronomical-darkness interval of that night.

**UI**
- `SessionQualityChart` is one shared time axis with small synced panels: the metric, altitude, stars and eccentricity. Each panel has one y-axis (no dual axes).
- Filters use colour **and** marker shape. Lines break across pauses longer than 20 minutes. The running median uses measured subs only.
- It appears in three places:
  - the new **Nights** page (`/nights/:night`), which has a picker, rig selector, summary cards and a sortable, flag-filterable table of subs
  - Target Detail: click a night
  - Image Detail: a compact chart with the current sub highlighted
- The Target Detail Nights chart from Q1b had FWHM on a second y-axis. It now has its own synced chart.

## 12. Decisions (owner, 2026-09-27)

1. **Header/filename hints: use them when present.** The capture software only sometimes writes HFR. The hint parser (§4.7) ships in Q1a.
   - Hint values are shown next to the measured value in Image Detail ("N.I.N.A. HFR 2.31").
   - They are the value of record when AstroCat can't measure the frame.
   - Aggregates and flags always use AstroCat-measured values, so frames with and without hints stay comparable.
2. **Backfill the entire library.** The sweeper (§5.4) covers every eligible row, newest first. Nothing is date-limited.
3. **Unit toggle arcsec ⇄ pixels, default arcsec.**
   - In arcsec mode, FWHM and HFR are both shown in ″ (`value_px × scale`). In pixel mode, both are shown in px.
   - Rows with no known scale always show px, with a "no plate scale" hint.
   - The toggle is a global header control. Each viewer's choice is stored in `localStorage`, and the default comes from the `quality_units` setting (`ARCSEC`).
   - The API returns both units, so toggling never refetches.
   - This replaces `quality_primary_metric` in §7.9.
4. **Masters are measured.**
   - They show on Image Detail and in the Target "Masters" gallery (a FWHM badge per master).
   - They are excluded from sub aggregates, flags and the timeline (`LIGHT_SUBS`).
5. **Add `sep` + `scipy`.** Approved.
