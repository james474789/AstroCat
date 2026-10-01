# S1: High-resolution planetary imaging forecast (Tonight page)

Status: **Proposed** · Written: 2026-10-01 · Base: `main` (alembic head `d3f5b9e06013`)

## 0. Privacy rule (read first)

**No location data goes in the repository.** That covers source, tests, fixtures, docs, commit messages,
logs and cache keys. The only place a site's coordinates live is the `sites` table, which the user fills in
from the Equipment page. Everything in this feature reads coordinates **at runtime, from the DB, by
`site_id`**. §8 lists the concrete guards. This document gives no site name, coordinates or region, and
neither may anything written from it.

## 1. Goal

Add a **Planetary seeing** panel to the Tonight page that answers two questions:

1. Is tonight worth setting up for high-resolution planetary (and lunar) imaging?
2. If so, which planet, and between what hours?

It combines:
- **Windy-equivalent model data** from Open-Meteo: the ECMWF/ICON/GFS/regional models that Windy displays
- **meteoblue astronomy seeing**, when an API key is configured
- each planet's **altitude** from our own ephemeris

These go into one hourly score and a per-planet best window. The weights come from the user's own seeing log
(§2), not from generic rules of thumb.

User decisions (2026-10-01):
- Windy numbers come from **Open-Meteo using the same models**. Windy's own API costs €990/yr, has no ECMWF,
  and its trial key returns deliberately scrambled data. The UI labels this source "Windy-equivalent models".
- **meteoblue is an optional source**, used only when `METEOBLUE_API_KEY` is set. Without it, the rating is
  still produced and the UI says that meteoblue is missing.
- **Secrets go in `.env` / docker env** (like `astrometry_api_key` and `telescopius_api_key`). They are never
  stored in the DB, returned by the API or logged.
- **Scope**: an hourly score through the night and a best window for each planet.

## 2. What "good" looks like: evidence from the user's log

The user's Planetary Seeing Log covers 108 Jupiter/Saturn capture sessions from 2024 to 2026. Each has a
self-graded seeing code and star ratings on the finished images. 51 sessions also have forecast screenshots
from meteoblue and Windy. Seeing grades are scored VVP 0.5 · VP 1 · P 2 · A 3 · G 4 · VG 5. Hit rate is the
share of sessions that produced at least one rated image.

| Factor (forecast, averaged over capture hours) | Best band | Hit rate in best band | Worst band |
|---|---|---|---|
| Surface wind (Windy, 10 m) | **≤ 1 m/s** | 50% (1.5 rated/session, avg grade 3.2) | ≥ 3 m/s: 10% |
| Ground temperature | **≤ 1 °C** | 48% | > 5 °C: 0% |
| Relative humidity | **≥ 90%** | 50% | < 80%: 7% |
| High cloud | **0%** | 34% | > 20%: 14% |
| meteoblue seeing index 1 | **≥ 4** | 47% | < 4: 20% |
| Upper wind 250/300 hPa | **< 20 m/s** | 40% | > 35 m/s: 25% (weak signal) |
| meteoblue jet stream | **< 20 m/s** | 43% | 20–35 m/s: 19% (weak, noisy) |
| meteoblue arcsec | **no useful signal** | < 1.3″ nights had the *lowest* avg grade | — |

There were also two qualitative findings:
- **Planet altitude dominates.** All twelve 4★/5★ images are of Jupiter, imaged later and higher. Saturn,
  imaged around 17:00–19:30 and low in the sky, produced nothing rated even on nights graded G.
- **Cold, damp, calm and settled air** (typically under high pressure) is the pattern behind the temperature
  and humidity bands. Part of it is seasonal, since most good nights fall in Nov–Jan. It is better treated as a
  *stable surface layer* signal than as "cold is good" in itself.

The user's go/no-go rule from the log is: **surface wind ≤ 1 m/s, ground near or below freezing with high
humidity, no mid or high cloud, meteoblue index 1 ≥ 4, and the planet well up. The arc-second forecast is
background only.**

### 2.1 General seeing physics (supports the weights)

- Planetary seeing is set by the integrated turbulence (Cn²) along the line of sight. The ground layer
  (the first few hundred metres, local heat and wind) and jet-level shear (around 200–300 hPa) are the two
  big contributors.
- A calm, radiatively cooled night lets the surface layer go stable and laminar. This is the "≤ 1 m/s, cold,
  humid" signature. Its risks are dew and fog, which are why high RH also triggers a dew warning (§5.4).
- **Wind shear** matters more than jet *speed*. meteoblue notes poor seeing both above 35 m/s and below
  5 m/s. Our model data has winds at several pressure levels, so we can compute shear (§5.2, experimental).
- Airmass grows quickly below 30° altitude (2.0 at 30°, 2.9 at 20°). Turbulence and atmospheric dispersion
  both scale with it, which is why altitude multiplies the score rather than adding to it.
- Thin high cloud usually marks a passing jet or front. That makes it a *seeing* signal as well as a
  transparency one.

## 3. Data sources

All requests are server-side (Celery and the backend). The browser never calls a weather provider, so the
browser never sends coordinates to a third party.

### 3.1 Open-Meteo: "Windy-equivalent models" (always on, free, no key)

`GET https://api.open-meteo.com/v1/forecast` with `latitude`, `longitude` (rounded, see §8),
`timezone=UTC`, `wind_speed_unit=ms`, `forecast_days=7`, `models=<list>`, and hourly variables:

```
wind_speed_10m, wind_gusts_10m, temperature_2m, relative_humidity_2m, dew_point_2m,
cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high, surface_pressure,
boundary_layer_height,
wind_speed_200hPa, wind_speed_250hPa, wind_speed_300hPa, wind_speed_500hPa, wind_speed_700hPa, wind_speed_850hPa,
wind_direction_200hPa, wind_direction_250hPa, wind_direction_300hPa, wind_direction_500hPa,
wind_direction_700hPa, wind_direction_850hPa,
temperature_250hPa, temperature_300hPa, temperature_500hPa, temperature_700hPa, temperature_850hPa
```

- **Models.** Use the global models Windy shows: `ecmwf_ifs025`, `icon_seamless` and `gfs_seamless`. Also use
  the best high-resolution regional model that covers the site. It is picked at runtime from a static list
  of model bounding boxes in `services/seeing/models.py` (for example `ukmo_seamless`, `icon_d2`,
  `meteofrance_seamless`, `gfs_hrrr`), tested with the site's coordinates in memory. The list holds model
  coverage areas only, never sites. The regional model, when present, is the **primary** model. ECMWF is
  primary otherwise.
- Some variables are missing from some models (for example `boundary_layer_height`). Missing means `null`, and
  that factor drops out for that model (weights renormalize, see §5.3).
- **Strip `latitude`, `longitude` and `elevation`** from the response before it is cached or stored. Open-Meteo
  echoes them back.
- One call returns all models. Budget: about 8 calls per site per day, far below the free limits.
  Non-commercial use is within Open-Meteo's terms.
- Check the exact variable names against the Open-Meteo docs when implementing. Unknown names cause a 400
  error, so `fetch_open_meteo` has a test that loads a recorded, sanitized response.

### 3.2 meteoblue astronomy seeing (optional, `METEOBLUE_API_KEY`)

- meteoblue packages API (`https://my.meteoblue.com/packages/<package>`) with the astronomy seeing package,
  hourly, 7 days. Fields of interest are seeing arcsec, **seeing index 1**, seeing index 2, jet stream speed,
  bad-layer bottom/top/gradient, and low/mid/high cloud.
- The **exact package name and JSON keys are confirmed at implementation time** from the key's package list
  (the public docs are rendered in JavaScript and weren't readable during design). The response mapping lives
  in one function, `parse_meteoblue()`, with a recorded and sanitized fixture.
- Credits: the free trial gives one year of credits, then about €400/yr. Fetch at most every 6 h per site
  (configurable with `METEOBLUE_MIN_INTERVAL_H`), and only for sites that have been viewed in the last 7 days
  or are the default site.
- **Never scrape meteoblue.com.** This feature only reads the API.
- Errors (bad key, out of credits) are recorded in source status and shown in the UI. They never fail the
  panel.

### 3.3 Not used (and why)

- **Windy Point Forecast API**: €990/yr, no ECMWF, and the trial data is scrambled. It is not built. A
  `sources/windy.py` adapter could be added later behind `WINDY_API_KEY` without changing the scoring
  contract (§5).
- **7Timer!** ASTRO seeing index: free, but coarse (GFS) and adds little over §3.1. Optional future source.

### 3.4 Planets (local)

The bodies are Jupiter, Saturn, Mars, Venus, Uranus, Neptune and the Moon. Use astropy `get_body` at 15-min
steps, the same approach as the Moon in `services/recommend/ephemeris.py`. Convert to topocentric alt/az with
`utils/horizon.alt_az` and apply the site horizon via `context.horizon_limit`. For each body, also compute the
apparent diameter (from distance), the illuminated fraction for Venus, Mars and the Moon, and the
elongation from the Sun.

Imaging is allowed while **Sun < −6°**, which is wider than the deep-sky darkness tiers because planets are
routinely imaged in twilight. Venus is allowed in daylight, with an "imaging in daylight" note.

## 4. Architecture

```
backend/app/services/seeing/
  __init__.py      build_forecast(site, night) -> payload (pure orchestration)
  sources/
    open_meteo.py  fetch_open_meteo(lat, lon, models) -> raw (no coords kept); parse -> HourlyFrame per model
    meteoblue.py   fetch_meteoblue(lat, lon, key) -> raw; parse_meteoblue -> HourlyFrame
  models.py        regional-model coverage boxes; pick_models(lat, lon)  (no sites)
  planets.py       planet_tracks(lat, lon, night, horizon) -> {body: alt[], az[], diam, illum}
  scoring.py       PURE: factor curves, combine(), planet_windows(), overall() — all constants here
  privacy.py       round_coords(), redact_url(), strip_location(raw)
backend/app/tasks/seeing.py       refresh_forecasts (beat, every 3 h), refresh_site(site_id)
backend/app/api/seeing.py         GET /api/seeing/forecast
backend/app/models/seeing.py      SeeingForecastSnapshot (see §6)
frontend/src/components/tonight/PlanetarySeeingPanel.jsx (+ .css)
```

- **The coordinates flow in one direction only**: `Site` row → `SiteSpec` (in memory) → `round_coords` → HTTP
  query. They are never put in a return value, cache key, DB column, log line or exception message.
- **Cache**: Redis `seeing:raw:{site_id}:{source}` stores the raw (stripped) response for 3 h (Open-Meteo) or
  6 h (meteoblue). `seeing:view:{site_id}:{night}:{version}` stores the computed payload for 30 min.
  `version` = `SCORING_VERSION` (a constant bumped whenever weights change).
- **Beat**: `app.tasks.seeing.refresh_forecasts` runs at minute 10 every 3 h on the `celery` queue. It refreshes
  the default site, plus any site whose `seeing:viewed:{site_id}` key has been set within 7 days. The API sets
  that key on read.
- **Graceful degradation**: if a fetch fails, serve the last cached raw response with `stale: true` and its
  `fetched_at`. With no data at all, the panel shows "Forecast unavailable" and the source errors.

## 5. Scoring (`services/seeing/scoring.py`, pure, unit-tested)

All thresholds and weights are module constants. Each comment cites its §2 row so later calibration (§9) has
a clear trail.

### 5.1 Hourly factor curves (each returns 0–1, or `None` when the input is missing)

Curves are piecewise-linear between the knots below. The knots come straight from the §2 bands.

| Factor key | Input | Knots (input → score) | Weight |
|---|---|---|---|
| `surface_wind` | 10 m wind, m/s (max of wind and 0.5 × gust) | 0→1.0, 1→1.0, 3→0.35, 5→0.1, 8→0 | **0.25** |
| `seeing_index` | meteoblue index 1 (1–5) | 2→0.15, 3→0.35, 4→0.85, 5→1.0 | **0.20** (0 when no key) |
| `stability` | mean of: temperature (≤1 °C→1, 5→0.4, 10→0.1) and RH (≥90→1, 80→0.55, 70→0.2); +0.1 bonus if BLH ≤ 300 m, capped at 1 | — | **0.15** |
| `upper_cloud` | max(mid, high) cloud % | 0→1, 20→0.55, 50→0.2, 80→0 | **0.15** |
| `jet` | max wind 200/250/300 hPa (or meteoblue jet stream when present) | ≤15→1, 20→0.85, 35→0.55, 50→0.3, 70→0.1 | **0.15** |
| `ground_shear` | \|wind 850 hPa − wind 10 m\| m/s | ≤5→1, 10→0.6, 20→0.2 | **0.10** |
| `arcsec` | meteoblue seeing ″ | shown only | **0** (§2: no signal) |
| `computed_seeing` | experimental Richardson/Cn² estimate from the level winds and temperatures (§5.2) | shown only | **0** until calibrated |

**Clear-sky gate** (multiplies, not weighted): `clear = 1 − max(low, total)/100`, clamped. A planet hour with
`clear < 0.3` is shown as **Cloud**, whatever its seeing.

### 5.2 Experimental computed seeing

For each pair of adjacent pressure levels (850/700/500/300/250/200), compute the bulk Richardson number from
the potential-temperature and wind-vector differences. Flag a layer as turbulent when Ri < 0.25, with Cn²
weight ∝ shear² × layer thickness, and integrate to an r₀ / FWHM estimate. It is displayed as "model ″
(experimental)" with weight 0 until §9 shows it predicts the user's grades. Keep it in its own function,
`computed_seeing(levels)`.

### 5.3 Combining models and factors

1. For each model, compute the hourly atmosphere score `A_m(t)` = Σ wᵢ·fᵢ / Σ wᵢ over the factors that aren't
   `None`. Weights renormalize, so a missing meteoblue key doesn't lower the score.
2. `A(t)` = primary model × 0.6 + mean of the other models × 0.4.
   **Confidence** = 1 − (spread of `A_m(t)` across models / 0.5), clamped to 0–1, then reduced by lead time:
   × 1.0 for ≤ 24 h, 0.85 for ≤ 48 h, 0.7 for ≤ 72 h, then 0.5.
3. When meteoblue is present, `seeing_index` and `jet` (meteoblue jet stream) are applied to every model
   (meteoblue is single-source).

### 5.4 Per-planet windows

- Altitude factor (multiplies): `alt_f(h)` = 0 below `max(horizon(az), 15°)`, 0.25 at 20°, 0.6 at 30°, 0.85 at
  40°, 1.0 at ≥ 50°. Steeper than airmass alone, following the Jupiter-versus-Saturn finding.
- `P_body(t) = A(t) × alt_f(alt_body(t)) × clear(t)`, at 15-min resolution (hourly weather interpolated).
- **Best window**: the contiguous span with the highest mean `P` that is at least 45 min long (configurable)
  and where `P ≥ 0.5 × peak`. Report start/end (UTC + site tz), the peak time and altitude, and the mean
  score.
- Bodies are listed when their best window has a score above 0 or they are up while the Sun is below −6°. They
  are sorted by window score. Mercury, and any body with a window under 15 min, are left out.
- **Warnings** (chips):
  - "Dew risk": RH ≥ 95% or (T − dew point) ≤ 1 °C
  - "Gusty": gusts ≥ 6 m/s
  - "Low: dispersion": peak alt < 30°, so suggest an ADC
  - "Jet overhead": jet > 35 m/s
  - "Models disagree": confidence < 0.5

### 5.5 Overall rating

- `overall` = the best body's window score, mapped onto the user's own scale:
  `< 0.15` VVP · `< 0.3` VP · `< 0.45` P · `< 0.6` A · `< 0.75` G · `≥ 0.75` VG. The cut points start as
  constants and are refit in §9.
- **Verdict**: `GO` when grade ≥ A **and** the §2 go checklist passes for at least 1 h of the best window.
  `MAYBE` when grade ≥ P or the checklist passes. `NO_GO` otherwise. `CLOUD` when the gate kills every window.
- **Go checklist** (each item shown as a check or cross, evaluated over the best window):
  - surface wind ≤ 1.5 m/s (the log says ≤ 1 m/s; allow some forecast slack)
  - temperature ≤ 3 °C and RH ≥ 85% (or BLH ≤ 300 m)
  - mid+high cloud ≤ 20%
  - meteoblue index 1 ≥ 4 (shown as "n/a" with no key)
  - planet altitude ≥ 35%

## 6. Storage: forecast snapshots (for calibration)

New table `seeing_forecast_snapshots` (migration after `d3f5b9e06013`, defensive `sa.inspect`, model imported
in `models/__init__.py`):

| column | type | note |
|---|---|---|
| id | int PK | |
| site_id | FK sites.id, index | **no coordinates** |
| night | date, index | observing night (local noon to noon) |
| fetched_at | timestamp | |
| scoring_version | int | |
| hourly | JSONB | the factor inputs and scores per hour, for the night only (location-stripped) |
| summary | JSONB | overall, verdict, per-body windows |

Unique on (site_id, night, scoring_version, date_trunc('hour', fetched_at)). `refresh_forecasts` writes one
row per site per run for tonight. That is about 8 small rows per night, with 400-day retention (pruned by the
same task). This keeps forecasts from **before** the session, which is exactly what the log's screenshots
were.

## 7. API and UI

### 7.1 `GET /api/seeing/forecast?site_id=&date=`

Logged-in user. `site_id` defaults to the default site, `date` to tonight. The response has **no coordinates**:

```json
{
  "night": "2026-10-01", "site": {"id": 1, "name": "<site name>", "timezone": "<IANA>"},
  "scoring_version": 1, "fetched_at": "…Z", "stale": false,
  "sources": [{"id": "open_meteo", "label": "Windy-equivalent models", "models": ["…"], "primary": "…",
               "status": "ok"},
              {"id": "meteoblue", "status": "no_key" | "ok" | "error", "message": null}],
  "overall": {"grade": "A", "score": 0.52, "verdict": "MAYBE", "confidence": 0.71,
              "best_body": "jupiter", "checklist": [{"key": "surface_wind", "pass": true, "value": 0.8}]},
  "bodies": [{"body": "jupiter", "diameter_arcsec": 44.1, "illum": null,
              "window": {"start_utc": "…", "end_utc": "…", "peak_utc": "…", "peak_alt": 52.0, "score": 0.52},
              "warnings": ["dew_risk"], "track": [{"t": "…", "alt": 31.2, "score": 0.41}]}],
  "hourly": [{"t": "…Z", "score": 0.48, "confidence": 0.8, "clear": 0.9,
              "factors": {"surface_wind": {"value": 0.8, "score": 1.0}, "jet": {"value": 28, "score": 0.65}},
              "per_model": {"ecmwf_ifs025": 0.5}}]
}
```

`date` beyond 7 days returns `{"available": false}`. Days 4–7 are returned with their reduced confidence.

### 7.2 Tonight page

Add `PlanetarySeeingPanel` as a collapsible section **below the `ContextStrip` and above the HeroCard**. It
fetches through its own `useQuery(['seeingForecast', siteId, date])` with a 10-min `staleTime`, so it doesn't
slow the deep-sky picks. It follows the existing date and site controls.

- **Header**: grade badge (VVP…VG, colored), verdict pill (GO / MAYBE / NO GO / CLOUD), confidence, and "best:
  Jupiter 22:10–00:40".
- **Planet rows**: name, diameter, window (local and UTC, like `formatLocalRange`), peak altitude, a small
  altitude/score sparkline (reusing the `AltitudeSparkline` style), and warning chips.
- **Hourly strip**: a heat row for the overall score, plus expandable rows for each factor (wind, jet,
  mid/high cloud, temperature/RH, meteoblue index 1, arcsec, computed ″). Hovering a cell shows the raw value
  and the per-model scores.
- **Go checklist**: the §5.5 items as checks and crosses.
- **Source chips**: "Windy-equivalent models: ECMWF, ICON, GFS, +regional · updated 40 min ago", and
  "meteoblue: not configured / ok / error". A stale-data notice appears when relevant.
- The panel remembers its collapsed state (localStorage, try/catch). When there's no site, it is hidden and
  the existing "No observing sites yet" state covers it.

`frontend/src/api/client.js` gets a `getSeeingForecast(siteId, date)` section appended at the end.

## 8. Privacy guards (concrete)

1. **Coordinates** are read only from the `sites` table at runtime. No constants, defaults, examples or test
   values that resemble the user's site. Tests use obviously synthetic coordinates (`lat=0.0, lon=0.0`, or
   a regional-model box test that uses each box's centre computed from the box itself).
2. **Precision**: `round_coords()` rounds to 2 decimals (~1 km, finer than any model grid) before any
   external request (configurable as `SEEING_COORD_DECIMALS`, default 2).
3. **Logging**: set the `httpx` and `httpcore` loggers to WARNING in the seeing module, because httpx logs
   full request URLs, query string included, at INFO. Every exception message is passed through
   `redact_url()` (drops the query string) before it is logged or stored in source status. Keys are never
   logged.
4. **Responses and storage**: `strip_location()` removes `latitude`, `longitude`, `elevation` and any
   `*lat*`/`*lon*` keys from raw provider JSON before caching. Cache keys, DB rows and API responses refer to
   `site_id` only.
5. **Fixtures**: recorded provider responses in `backend/tests/fixtures/seeing/` must pass through
   `strip_location()`, and their timestamps are shifted to a fixed fake date. A test asserts that no fixture
   contains a `lat`/`lon` key.
6. **Commit guard (local, untracked config)**: `scripts/check_location_leak.py` reads the site
   coordinates and names from the running DB (or a gitignored `.location-guard` file). It scans the staged
   diff for those coordinates at ≥ 2 decimals, in either sign or order, and for the site names, and fails the
   commit if it finds any. It is installed as an opt-in pre-commit hook (`scripts/install_hooks.ps1`). The
   script contains no location; it only reads it locally.
7. **Docs**: this file and `docs/features/SEEING.md` describe the mechanism generically and never cite the
   site.

## 9. Calibration (phase 3)

The log's thresholds come from about 50 nights, so they are strong hints, not fitted weights. Once
AstroCat stores snapshots (§6):

- `backend/app/scripts/calibrate_seeing.py` joins snapshots to the user's graded nights. Grades come from
  session folder names (the `YYYY_MM_DD - <GRADE>` codes such as AG and PVP, averaged as in the log), via
  the indexed image paths for planetary sessions. Folder names are read at runtime and nothing is
  committed.
- It reports the hit rate by factor band (the same tables as the log) and fits the cut points in §5.5 by
  maximizing rank correlation with the grade. It **prints suggested constants**, and a human edits
  `scoring.py` and bumps `SCORING_VERSION`. There is no automatic re-weighting.
- It also evaluates `computed_seeing` and `arcsec`, and promotes either to a non-zero weight only if the
  evidence supports it.

## 10. Config (`backend/app/config.py`, env only)

```python
# Planetary seeing forecast (S1). Env only: never stored, returned or logged.
meteoblue_api_key: Optional[str] = None
meteoblue_min_interval_h: int = 6
seeing_coord_decimals: int = 2
seeing_enabled: bool = True
```

Add the variable **names** (empty values) to `docker-compose-example.yml` and the docker env pass-through.

## 11. Phases and acceptance

| Phase | Scope | Accept when |
|---|---|---|
| **1** | Open-Meteo source, planets, scoring, API, Tonight panel, privacy guards 1–5 | The panel shows grade, verdict, planet windows and hourly strip for the default site. No coordinates in API responses, Redis or logs (verified by grepping the log dir and `redis-cli --scan` + GET for the site's lat/lon). |
| **2** | meteoblue source behind key, source chips, credit throttle | With a key, index 1/jet/arcsec appear and enter the score. With none, `status: no_key` and the score is unchanged in shape. |
| **3** | Snapshots table + retention, `calibrate_seeing.py`, commit guard 6 | The script reproduces the log's band tables from stored snapshots. The hook blocks a staged file containing the site's coordinates. |

**Tests** (`backend/tests/test_seeing_*.py`, pure, no network):
- factor curves at each knot
- weight renormalization when factors are missing
- the multi-model spread and confidence calculation
- window finding (contiguous, min length, horizon)
- grade/verdict mapping, including the go checklist
- `strip_location` and `redact_url`
- `pick_models` with synthetic points inside and outside each box
- the parsers against sanitized fixtures
- a "no coordinates in payload" test: build a payload for a site with sentinel coordinates such as
  `12.3456`/`-65.4321`, then assert that neither appears anywhere in the JSON

**Deploy**: bump `VERSION`, then rebuild both backend and frontend (see CLAUDE.md).

## 12. Open items for implementation

- Confirm the meteoblue seeing package name and keys against a live key (§3.2).
- Confirm that Open-Meteo pressure-level variables exist for the chosen regional model. If they're missing,
  take the jet and shear from ECMWF for every model.
- Decide whether the Moon appears in the planet list by default (proposed: yes, for lunar hi-res, sorted with
  the others).
