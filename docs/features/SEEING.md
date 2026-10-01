# Planetary seeing forecast

A **Planetary seeing** panel on the Tonight page (below the context strip, above the hero pick) answers two
questions: is tonight worth setting up for high-resolution planetary or lunar imaging, and if so, which body
and between what hours. Design: [S1](../design/20261001-S1-planetary-seeing-forecast.md). This page describes the
shipped behaviour (phases 1 and 2).

## What it combines

- **Windy-equivalent models**: free Open-Meteo data from the global models Windy shows (ECMWF IFS, ICON, GFS)
  plus, when the site lies inside one, a high-resolution regional model that becomes the primary. No key.
- **meteoblue astronomy seeing**: optional. Only used when `METEOBLUE_API_KEY` is set. Without it the rating is
  still produced and the panel says "meteoblue: not configured".
- **Planet positions**: computed locally with astropy (Jupiter, Saturn, Mars, Venus, Uranus, Neptune, the Moon)
  at 15-minute steps, with the site's horizon profile applied (never below a 15° floor).

## Scoring

Everything is in `backend/app/services/seeing/scoring.py` (pure, constants only, `SCORING_VERSION`).

- Hourly **atmosphere score** (0–1): weighted mean of surface wind (incl. gusts), meteoblue seeing index,
  surface stability (temperature + humidity, small bonus for a shallow boundary layer), mid/high cloud, jet
  level wind, and 850 hPa-to-surface shear. Missing factors drop out and the weights renormalise, so having no
  meteoblue key does not lower the score. The meteoblue arc-second value and an experimental Richardson-number
  seeing estimate are displayed with zero weight.
- Models are blended (primary 0.6, mean of the others 0.4). **Confidence** falls with model disagreement and
  with lead time (≤24 h full, then 0.85 / 0.7 / 0.5).
- **Per-planet score** = atmosphere × altitude factor × clear-sky fraction. Altitude is steep (0.25 at 20°,
  0.6 at 30°, 1.0 from 50°). Hours with less than 30% clear sky are gated out ("Cloud"). Imaging counts while
  the Sun is below −6°; Venus is also allowed in daylight (with a note).
- **Best window**: the contiguous span with the highest mean score, at least 45 minutes (a shorter span of at
  least 15 minutes is kept only if nothing longer exists) and within half of the peak score.
- **Grade** (VVP to VG) from the best window's score; **verdict** `GO`, `MAYBE`, `NO_GO` or `CLOUD`. GO needs
  grade A or better and the go checklist to pass for at least an hour of the window.
- **Go checklist**: surface wind ≤ 1.5 m/s; ≤ 3 °C with RH ≥ 85 % (or boundary layer ≤ 300 m); mid+high
  cloud ≤ 20 %; meteoblue index ≥ 4 (shown as n/a without a key); planet at least 35° at its best.
- **Warning chips**: dew risk, gusty, low (dispersion), jet overhead, models disagree.

## Data handling and privacy

- Coordinates are read only from the `sites` table at runtime. Requests are made server-side, never by the
  browser, and coordinates are **rounded** first (`SEEING_COORD_DECIMALS`, default 2).
- Provider responses are stripped of `latitude`, `longitude`, `elevation` and any lat/lon-named key before they
  are cached. Cache keys, cached values and API responses refer to the site by id only.
- `httpx`/`httpcore` logging is held at WARNING (they log full URLs at INFO) and every error message is passed
  through a URL redactor. API keys are never stored, returned or logged.

## Caching and refresh

| Redis key | Content | Freshness |
|---|---|---|
| `seeing:raw:{site}:open_meteo` | stripped provider response | 3 h (kept 24 h for stale fallback) |
| `seeing:raw:{site}:meteoblue` | same | `METEOBLUE_MIN_INTERVAL_H` (default 6 h) |
| `seeing:mbfail:{site}` | last meteoblue error | 1 h (no re-fetch while set: protects credits) |
| `seeing:view:{site}:{night}:{version}` | computed payload | 30 min (5 min if stale or a source errored) |
| `seeing:viewed:{site}` | set by the API on read | 7 days |

The Celery task `app.tasks.seeing.refresh_forecasts` runs every 3 hours at minute 10 on the default queue. It
refreshes the default site and any site viewed in the last 7 days, and warms tonight's view. If a fetch fails
the last cached data is served with `stale: true` and its `fetched_at`; with no data at all the panel shows
"Forecast unavailable" plus the source errors. A failed source never breaks the rest of the Tonight page.

## API

`GET /api/seeing/forecast?site_id=&date=` (logged-in users). `site_id` defaults to the default site and `date` to
tonight; dates more than 7 days ahead return `{"available": false, "reason": "out_of_range"}`. The body has the
night, site id/name/timezone, scoring version, `fetched_at`, `stale`, source status, `overall` (grade, score,
verdict, confidence, best body, checklist), per-body windows with altitude/score tracks and warnings, and the
hourly strip with factor values/scores and per-model scores. It contains no coordinates.

## Configuration

Environment only (never stored in the database):

| Variable | Default | Meaning |
|---|---|---|
| `METEOBLUE_API_KEY` | empty | enables the meteoblue source |
| `METEOBLUE_MIN_INTERVAL_H` | 6 | minimum hours between meteoblue fetches per site |
| `SEEING_COORD_DECIMALS` | 2 | rounding applied to coordinates before any external request |
| `SEEING_ENABLED` | true | turns the whole feature off (the panel hides itself) |

## Known limitations

- The meteoblue package name and response keys are **unconfirmed** (they could not be checked without a key).
  They are isolated in `sources/meteoblue.py` (`parse_meteoblue()` and the `MB_*` constants) and marked TODO.
- Scoring weights and grade cut points come from a small personal log and are starting points. Calibration
  against stored forecast snapshots is a later phase.
- Models without pressure-level data take jet and shear levels from ECMWF.
