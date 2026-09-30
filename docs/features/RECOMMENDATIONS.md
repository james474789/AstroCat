# Recommendations ("Tonight")

AstroCat answers "what should I image tonight, with the rig that's mounted?" with a local
engine that uses the user's own history, rigs, sites and learned horizon. Design:
`R1-recommendation-engine.md`. No external service is
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
| `widefield.py` | Curated wide-field regions (pure data, see below). |
| `candidates.py` | One candidate per canonical target key (Messier, Caldwell, NGC/IC, Sh2 folded through the alias index). Size = the largest across aliases. Kind: EMISSION only when confirmed (HII/SNR/emission type or a Sharpless alias), a curated reflection list, ambiguous nebulae treated as broadband. Imageability gate for NGC/IC-only and Sh2-only objects; anything the user has imaged is always kept (including rows OpenNGC types as a star or `Other`, such as IC1318). `Dup` rows fold into the object they duplicate, and stray history keys (`OBJ:NGC78222` for panel 2, `OBJ:M81LUM`) fold into the pool key for history only. |
| `ephemeris.py` | 5-minute grid from local solar noon to noon; Sun (numpy), Moon (astropy, 15-minute samples interpolated; analytic fallback), target alt/az and Moon separation (N x T). Cached per (site, night). |
| `context.py` | Darkness tier: ASTRO (Sun < -18 deg for >= 1 h), NAUTICAL (< -12), BRIGHT (< -9; broadband allowed but penalised), NONE. Horizon: saved, else learned (shared with `GET /api/sites/{id}/horizon/learned`), else flat 30 deg; the limit is `max(horizon(az), floor)`. |
| `history.py` | Per-target history from light subs (filter classes BB/HA/OIII/SII/OSC), truncated "as of" a night for replay; inferred goals (explicit goal, else per-kind median of finished targets, else 10 h). |
| `scoring.py` | Generous hard filters that drop only clear-cut cases: BELOW_HORIZON (< 0.5 h above max(15 deg, limit - 10 deg)), TOO_SMALL (below the rig's size window, or < 15 px), TOO_BIG (above the window), TIER (no usable filter class, or tier NONE), MOON (< 0.5 h clear of half the required distance). Seven components: observability, framing, project, momentum (exp(-days/tau), zero beyond 3 tau), urgency, prior, recency_rank. They use the full rules: the real horizon, the Lorentzian Moon rule per filter class applied softly (5 deg sigmoid), and BRIGHT broadband x 0.3. Best rig per target when several are evaluated. |
| `lanes.py` | Lanes (active, continue, last_chance, moon_proof, other), diversity (no five Cygnus nebulae in a row), hero verdict (GO / MARGINAL / DONT_BOTHER), reason chips. |
| `replay.py` | The pure replay loop and metrics (see below). |

### Per-rig size window (R1b)

Each rig has a target-size window (`rigs.min_target_arcmin` / `max_target_arcmin`, edited on the
Equipment page). A declared bound wins; otherwise the window is 22% of the FOV short side up to 80%
of the long side (3 deg - 16 deg for a 20.4 x 13.6 deg field). A target below it is `TOO_SMALL`,
above it `TOO_BIG` (this replaces the old "4 x the short side" rule); an unknown size passes; the
15 px floor stays. The size rules are skipped for a target the user has **already imaged on that rig,
or on a rig whose pixel scale is within +/-25%** (per-row solved scales of the light subs, as of
the night in replay), so North America stays on a 2.3"/px rig even when the window would drop it.
`pair_details` (the target endpoint) reports `size_window_arcmin` and `imaged_on_rig`.

### Wide-field candidates (R1b)

`widefield.py` holds ~25 hand-checked regions (Cygnus, North America + Pelican, Heart and Soul,
Cepheus, Orion, Auriga, Rosette + Cone, Andromeda + Triangulum, Virgo, Sagittarius, Rho Ophiuchi,
...), keys `WF_<SLUG>`, `catalog = "WF"`, prior 0.7, a few degrees across. EMISSION only for
HII-dominated fields. `Candidate.members` lists the object keys inside; members keep their own
history, a WF region never folds any, and no Dup row folds into one. Pin, snooze and dismiss work
on them. The size window keeps them off narrow rigs. They have no images, so the Tonight card
links the member objects instead of `/targets/WF_...`, and `pick_to_dict` adds `members` for them.
Regions above 16 deg (whole Cygnus, whole Orion, Milky Way core) only pass on a rig whose maximum
has been raised.

Default weights: observability 0.25, framing 0.20, project 0.20, momentum 0.15, urgency
0.15, prior 0.10, recency_rank 0 (momentum tau 45 days). Moon rules (D deg / W days): broadband and OSC 120/14, Ha and SII 40/10,
OIII 70/10.

Rigs: `rig=mounted` (default) uses the mounted rigs (up to five), or every active rig when none is
mounted (`rig_mode = ALL_FALLBACK`); `rig=all` uses every active rig; `rig=<id>` one rig. A
rig needs a pixel scale (declared, else measured) and a sensor size, otherwise it is listed
in `skipped_rigs`. Filter classes come from the rig's filters; a rig with no filters is OSC
for a colour camera, else the classes seen on its images, else broadband.

Several mounted rigs image at the same time, so with `rig_mode = MOUNTED` and more than one
rig the body also carries `rig_plan`: `[{rig, items}]`, one entry per mounted rig, where
`items[0]` is that rig's primary target and the rest (up to 3 in all) are backups. No target
is planned on two rigs. Every (target, rig) pair competes by that rig's score, pinned targets
first, and each rig gets a primary before any rig gets a backup, so a rig can be handed a
target that scores slightly higher elsewhere (`best_rig: false`; the pick is re-labelled for
the assigned rig, its best rig moves to `alternatives`). Dismissed and snoozed targets are
left out. `rig_plan` is `[]` in every other case. The hero and lanes are unchanged.

**Quality bar (R1b).** A (target, rig) pair is eligible for the plan only with at least 1.5 h
(`MARGINAL_MIN_HOURS`) of Moon-clear time on that rig, so a rig with no such pair gets **no items**
rather than the least-bad target (a bright-Moon night gives a broadband/OSC rig nothing). Each
`rig_plan` entry also has `verdict` (`lanes.verdict()` on the rig's primary, relabelled for that rig;
an empty rig is `DONT_BOTHER` with code `NOTHING_GOOD`), `note` (set when the rig has no items: the
main reason in one line, e.g. "Moon 78% lit and up all night: broadband/OSC needs 120°; nothing clears
it for 1.5 h", "Nothing between 3°-16° is well placed tonight") and `size_window_arcmin`. The note
comes from the per-rig `rig_summary` in the cached payload: feasible count, targets inside the size
window, exclusion counts (whole pool and inside the window), and how many feasible pairs fall short
of the bar. `context.rigs[].size_window_arcmin` carries each rig's window.

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
  user-independent engine payload (`format` 4 since R1b; older entries are ignored): every feasible pick (with its lane, reasons and exact
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
