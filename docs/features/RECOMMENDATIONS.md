# Recommendations ("Tonight")

AstroCat answers "what should I image tonight, with the rig that's mounted?" with a local
engine that uses the user's own history, rigs, sites and learned horizon. Design:
[R1-recommendation-engine.md](../design/R1-recommendation-engine.md). No external service is
called; Telescopius is not a candidate source.

## Observing night

One definition everywhere (`backend/app/utils/observing_night.py`): a night is the local
*solar* date of the preceding noon, `(utc + lon/15 h - 12 h).date()`, with the site's
longitude (0 without a site). Rows without a UTC time fall back to `(capture_date - 12 h)`.
The Targets page's night counts use the same definition (`NIGHT_SQL`).

## UTC for local-clock frames

`FITS_LOCAL` / `EXIF_LOCAL` frames get `capture_date_utc` from, in order: the camera's
inferred UTC clock (`CAMERA_UTC`), the frame's own site timezone (`SITE_TZ`), or, for frames
without a site, the default site's timezone (`DEFAULT_SITE_TZ`). The choice is recorded in
`images.capture_utc_basis`. Data migration `0008_fill_utc_default_site` applies this to
existing rows once; the assignment task and the indexer hook keep it up to date.

## Engine (`backend/app/services/recommend/`)

The core is pure (no SQLAlchemy); `loader.py` reads the DB and caches.

| Module | Role |
|---|---|
| `candidates.py` | One candidate per canonical target key (Messier, Caldwell, NGC/IC, Sh2 folded through the alias index). Size = the largest across aliases. Kind: EMISSION only when confirmed (HII/SNR/emission type or a Sharpless alias), a curated reflection list, ambiguous nebulae treated as broadband. Imageability gate for NGC/IC-only and Sh2-only objects; anything the user has imaged is always kept (including rows OpenNGC types as a star or `Other`, such as IC1318). `Dup` rows fold into the object they duplicate, and stray history keys (`OBJ:NGC78222` for panel 2, `OBJ:M81LUM`) fold into the pool key for history only. |
| `ephemeris.py` | 5-minute grid from local solar noon to noon; Sun (numpy), Moon (astropy, 15-minute samples interpolated; analytic fallback), target alt/az and Moon separation (N x T). Cached per (site, night). |
| `context.py` | Darkness tier: ASTRO (Sun < -18 deg for >= 1 h), NAUTICAL (< -12), BRIGHT (< -9; broadband allowed but penalised), NONE. Horizon: saved, else learned (shared with `GET /api/sites/{id}/horizon/learned`), else flat 30 deg; the limit is `max(horizon(az), floor)`. |
| `history.py` | Per-target history from light subs (filter classes BB/HA/OIII/SII/OSC), truncated "as of" a night for replay; inferred goals (explicit goal, else per-kind median of finished targets, else 10 h). |
| `scoring.py` | Generous hard filters that drop only clear-cut cases: BELOW_HORIZON (< 0.5 h above max(15 deg, limit - 10 deg)), TOO_SMALL (< 15 px), TOO_BIG (> 4 x the FOV short side), TIER (no usable filter class, or tier NONE), MOON (< 0.5 h clear of half the required distance). Seven components: observability, framing, project, momentum (exp(-days/tau), zero beyond 3 tau), urgency, prior, recency_rank. They use the full rules: the real horizon, the Lorentzian Moon rule per filter class applied softly (5 deg sigmoid), and BRIGHT broadband x 0.3. Best rig per target when several are evaluated. |
| `lanes.py` | Lanes (active, continue, last_chance, moon_proof, other), diversity (no five Cygnus nebulae in a row), hero verdict (GO / MARGINAL / DONT_BOTHER), reason chips. |
| `replay.py` | The pure replay loop and metrics (see below). |

Default weights: observability 0.25, framing 0.20, project 0.20, momentum 0.15, urgency
0.15, prior 0.10, recency_rank 0 (momentum tau 45 days). Moon rules (D deg / W days): broadband and OSC 120/14, Ha and SII 40/10,
OIII 70/10.

Rigs: `rig=mounted` (default) uses the mounted rig, or every active rig when none is
mounted (`rig_mode = ALL_FALLBACK`); `rig=all` uses every active rig; `rig=<id>` one rig. A
rig needs a pixel scale (declared, else measured) and a sensor size, otherwise it is listed
in `skipped_rigs`. Filter classes come from the rig's filters; a rig with no filters is OSC
for a colour camera, else the classes seen on its images, else broadband.

## API (`/api/recommendations`, any logged-in user)

- `GET /api/recommendations?date=YYYY-MM-DD&site_id=&rig=mounted|all|<id>&per_lane=6`
  returns `context`, `hero`, `verdict`, `lanes`, `excluded_counts`, `skipped_rigs` (shape in
  the design doc §7). The date defaults to tonight at the site (from 06:00 local solar time
  "tonight" is the coming night); the site defaults to the default site. 404 when there is
  no site or the site/rig is unknown.
- `GET /api/recommendations/target/{key}?date=&site_id=&rig=` explains one target for every
  evaluated rig: `{target_key, name, night, results: [{rig_id, rig_name, pick, excluded_reason,
  details}]}`. The key may also be a name or alias. 404 when it isn't in the candidate pool.
- `GET /api/recommendations/replay/latest` (admin) returns `<log_dir>/replay_latest.json`,
  404 if no replay has been saved.

### Feedback, impressions and outcomes (R2a, any logged-in user, own data)

Design: `docs/design/R2a-feedback-dashboard.md`.

- `POST /api/recommendations/feedback` with `{target_key, action, nights?, reason?, note?,
  context?: {night, lane, rank, score, rig_id}}`. Actions: `PIN`/`UNPIN`, `SNOOZE` (`nights`
  1, 7 or 30: hidden for nights < viewed night + nights)/`UNSNOOZE`, `DISMISS` (optional
  `reason` `DONE`/`NOT_MY_TYPE`/`TOO_HARD`/`OTHER`, no expiry)/`UNDISMISS`, and `IMAGED`
  (logged only). The key may be a name or alias and is stored canonically. Returns the key's
  state; 400 for a bad action/nights/reason/note (> 200 chars), 404 for an unknown key. Each
  state change writes one `recommendation_events` row; a repeated action is a no-op.
- `GET /api/recommendations/feedback` lists keys with an active state (pinned, dismissed, or
  snoozed until today or later) for the Hidden-items manager.
- `GET /api/recommendations` applies the user's feedback: snoozed/dismissed targets leave the
  lanes (counted as `SNOOZED`/`DISMISSED` in `excluded_counts`), feasible pins form the
  `pinned` ("Your pins") lane first (every pin, by score, no diversity reordering), and pins
  not shown are in `pinned_unavailable` with the reason (an exclusion code, `SNOOZED`,
  `DISMISSED`, `NOT_IN_POOL` or `NO_RIGS`). The hero is the top score; a pin within 0.02 wins
  the tie. Picks carry `feedback: {pinned, snoozed_until}` and `context.feedback_counts` has
  the user's counts. The target endpoint adds a top-level `feedback` object and reports
  `SNOOZED`/`DISMISSED` (with `details.until` / `details.reason`, and
  `details.engine_excluded_reason`) for every rig of a hidden target.
- Impressions: when the night requested is tonight's default night, the hero plus the first
  `per_lane` items of each lane are written to `recommendation_impressions` after the
  response (BackgroundTasks, `ON CONFLICT DO NOTHING`). Other dates write nothing.
- `GET /api/recommendations/outcomes?days=90` (1-730): an impression is acted on when a light
  sub of that key (after the stray-key folding) has that observing night (`NIGHT_SQL`). Only
  nights up to yesterday count (later ones are `pending_nights`), and the rates use only
  nights with impressions **and** imaging (`nights_imaged`); a rate is 0.0 when nothing was
  shown. `imaged_not_shown` counts those nights on which something was imaged that the page
  never showed. Self-reported `IMAGED` events (one per key and night) are confirmed when the
  library has the key on the event night or the night before.

## Caching and pre-compute

- Redis `recs:result:v2:<site>:<rig>:<night>:<hist_version>` (6 h) holds the
  user-independent engine payload: every feasible pick (with its lane, reasons and exact
  score/hours), the exclusions, and the shared curve parts. Each request renders it with
  that user's feedback (one query) and `per_lane`, rebuilding lanes, hero and verdict with
  the same rules, so feedback writes invalidate nothing and every `per_lane` (the Dashboard
  tile uses 1) shares the pre-computed entry (`cached: true` on a hit).
  `recs:inputs:v<hist_version>` (1 h) holds the history inputs. `hist_version` changes with
  any light-sub update or goal change.
- `recs:*` is dropped on any equipment, site, mount or horizon change and after every
  equipment assignment run.
- Celery beat runs `app.tasks.recommend.precompute_tonight` daily at 12:00 UTC (default site,
  `rig=mounted`) to warm the cache.

## Replay test

`python -m app.scripts.replay_recommendations [--since] [--until] [--moon-bb D[,W]] [--grid]
[--grid-moon] [--grid-max N] [--out PATH]` (inside the backend container). For every night with >= 30 min of resolved
light subs it runs the engine with history and goals strictly before that night, the night's
majority site (else the default) and rig (else the rigs used within +/- 365 days, else all
active rigs), and checks whether the targets actually imaged (>= 30 min) were recommended.
It reports hit@1/3/5/10, MRR and `feasible_recall` (share of actual in-pool targets that
pass the hard filters; every miss is listed with its reason) against recency, altitude and
random baselines, broken down by tier, Moon, year and known rig.

Each actual (target, night) pair is HOME (its majority site latitude is within 1.5 deg of a
configured site), REMOTE, or UNKNOWN_SITE. The headline numbers use HOME + UNKNOWN_SITE, and
REMOTE is reported separately, because remote-telescope nights are scored against the home sky.
Metrics are also split by target source (header-named vs plate-solve MATCH). Every miss lists
the rig, mode, Moon separation and required separation, max altitude, usable hours and target
size in pixels.

`--grid` runs the tuning grid (288 weight/tau combinations; `--grid-max` samples fewer)
and prints the top 5 with the recency baseline on the same pairs. `--grid-moon` first picks the
broadband/OSC Moon D from 60/90/120. Nothing is applied automatically.
