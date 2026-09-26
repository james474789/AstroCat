# R1: Local Recommendation Engine, Replay Test and Tonight Page

Status: **Proposed** · Written: 2026-09-26 · Base: `main` @ `97798a5` · Alembic head: `e8a0c4f51008`
· Data migrations applied: `0001`–`0007` · VERSION `20260926.15`

Read first:
- [HANDOVER-recommendations.md](HANDOVER-recommendations.md): current state and traps.
- [target-recommendations-research.md](target-recommendations-research.md): §4 engine, §5 backtest,
  §9 prototype lessons.
- [prototypes/rec_local_prototype.py](prototypes/rec_local_prototype.py): the maths this spec
  productionises.
- [README.md](README.md) §3–§5: conventions.

Depends on: P0 and R0 (merged and deployed). Blocks: R2 (affinity, novelty, feedback), R3 (season
planner).

## 1. Problem

AstroCat now knows the user's targets (canonical keys), their per-filter history, their rigs, sites
and learned horizon. It still can't answer the question the user asks before every clear night:
**"What should I image tonight, with the rig that's mounted?"** Generic planners ignore the user's
history, their real horizon, and the ~56°N summer, when there's no astronomical darkness. The
research prototype showed that a local engine using AstroCat's own data gives better answers
(research §9). R1 turns it into a tested service, adds a replay harness that measures it against
the user's own past nights, and adds a **Tonight** page.

## 2. Goals / non-goals

**Goals**
1. **A pure, unit-tested engine** (`services/recommend/`). Given a site, a date, rig(s) and history,
   it returns ranked (target, rig) picks. Each pick has explainable reasons and belongs to a
   **lane**.
2. **A replay harness** that runs the engine "as of" every historical imaging night, reports
   hit-rate@k against baselines, and supports weight tuning.
3. **`GET /api/recommendations`**, plus a per-target "why / why not" endpoint, cached, with a daily
   Celery beat pre-compute.
4. **A Tonight page:** a hero pick with a verdict, lanes of cards with altitude sparklines, and
   site, rig and date selectors.
5. **Data fixes the engine needs** (§3): UTC for local-clock rows that have no site, and one shared
   "observing night" definition that Targets also uses.

**Non-goals (later phases)**
- Affinity, novelty and "revisit with better gear" lanes, palette completeness, feedback actions,
  and a Dashboard tile: **R2**.
- Season planner, weather, `.hrz` / Target Scheduler export: **R3**.
- LLM briefing: **R4**.
- Mosaic planning. Too-big targets are counted and listed, not planned (F16).
- Telescopius as a candidate source. The engine is local-only (owner decision, 2026-09-26).

## 3. Data prerequisites (R1.0, in the same branch)

### 3.1 One definition of "observing night": `utils/observing_night.py` (new, pure)

```python
def night_of(utc: datetime | None, local: datetime | None, lon_deg: float | None) -> date | None
    # Local *solar* date of the preceding noon. With utc and lon: (utc + lon/15 h - 12 h).date().
    # With utc only: lon defaults to 0. Without utc: (local - 12 h).date(), today's behaviour.
def night_bounds_utc(night: date, lon_deg: float) -> tuple[datetime, datetime]
    # [local solar noon of `night`, next local solar noon) in UTC.
```

- The engine, the replay test and the Targets API all use it.
- `api/targets.py` changes **only** the `nights` expressions (the list count and `nights_detail`). They
  become SQL that mirrors `night_of`:
  `date(coalesce(capture_date_utc + make_interval(secs => coalesce(s.longitude,0)*240), capture_date) - interval '12 hours')`,
  with a `LEFT JOIN sites s ON s.id = images.site_id`.
- At ~−3° longitude this moves a night boundary by about 12 minutes, so the counts barely change.
  The acceptance check (§11 item 5) confirms that.

### 3.2 UTC for local-clock rows with no site

Live, **all 16,512 `EXIF_LOCAL` rows** still have `capture_date_utc IS NULL`. None of them has
coordinates, a site or a rig, so R0's timezone fill (rightly) skipped them. The DSLR history would
therefore drop out of the engine's momentum and replay.

- **Extend `tasks/equipment.py::_fill_capture_utc`:** a `FITS_LOCAL`/`EXIF_LOCAL` row with
  `site_id IS NULL` uses, in order:
  1. its camera's inferred clock mode (`equipment:clock_modes`; `UTC` → UTC = local);
  2. otherwise, the **default site's** timezone.
- Record which one was used in a new nullable column, `images.capture_utc_basis`
  (`SITE_TZ | DEFAULT_SITE_TZ | CAMERA_UTC`), so the source stays honest.
  `capture_time_source` is **not** changed.
- **Alembic `f9b1d5a62009`** (`down_revision = 'e8a0c4f51008'`, defensive): adds
  `capture_utc_basis`. R0 rows filled via their own site are back-filled as `SITE_TZ` by the data
  migration below, not by Alembic.
- **Data migration `0008_fill_utc_default_site`:** runs `run_assignment("all")` once, then
  returns its summary (counts per basis). It's idempotent. If no default site exists yet, it records
  `{"skipped": "no default site"}` and succeeds. Assignment re-runs whenever a site is accepted
  later.

### 3.3 Owner actions (not code; they improve replay coverage)

- **Older site.** 13.3k lights from 2019–2020 sit at an older site and aren't in any
  site. Detection with "include older" will propose it. **The remote cluster** (6.5k frames) is
  the owner's call.
- **Mounted rig.** Mark the current rig as mounted. The engine falls back to "all active rigs"
  when none is mounted (§4.6).

The replay test works without either: nights without a site use the default site. Both clusters are
~56°N, so the ephemeris difference is negligible.

## 4. Engine: `backend/app/services/recommend/` (new package, pure core)

Nothing in the core imports SQLAlchemy. A thin `loader.py` builds the inputs from the DB (sync
session, for Celery and scripts) and caches them.

```
recommend/
  __init__.py      # recommend(inputs, params) -> Result (public entry point)
  candidates.py    # catalog pool, canonical keys, kind, size
  ephemeris.py     # night grid, sun/moon, alt/az (vectorised)
  context.py       # NightContext: darkness tier, windows, horizon, moon
  history.py       # TargetHistory per canonical key, inferred goals
  scoring.py       # feasibility + components + weights
  lanes.py         # lane assignment, diversity, hero verdict, reasons
  loader.py        # DB -> inputs (not pure); Redis caching
  replay.py        # pure replay loop + metrics
```

### 4.1 Candidate pool: `candidates.py`

`build_candidates(messier, caldwell, ngc, sh2, alias_index) -> list[Candidate]`

```python
@dataclass(frozen=True)
class Candidate:
    key: str            # canonical target_key: exactly what images.target_key uses
    name: str           # common name, else designation
    ra_deg: float; dec_deg: float
    size_arcmin: float | None   # MAX major axis across merged aliases (IC1396: 170', not 14')
    kind: str           # EMISSION | PN | REFLECTION | GALAXY | CLUSTER | OTHER
    catalog: str        # M | C | NGC | IC | SH2 (the catalog the key comes from)
    magnitude: float | None
    aliases: frozenset[str]
```

- **Keys come from `services/targets.build_alias_index`.** It already applies Messier-first
  canonicalisation and the Sh2 cross-IDs (including overrides and the 1.0° separation cap). So a
  candidate key **is** a target key, and history joins directly, with none of the prototype's
  re-matching.
- **Size = max over aliases.** Aliases are grouped by canonical key, and each catalog row's
  major axis is folded in. Unknown sizes stay `None`.
- **Kind** (prototype lesson §9.1):
  - `EMISSION` only if **confirmed**: the NGC type is `HII`/`SNR`/emission, **or** a merged Sh2
    alias exists, and it's not in `REFLECTION_OVERRIDES`.
  - `PN` for planetary nebulae.
  - `REFLECTION` if in `REFLECTION_OVERRIDES` or of reflection type.
  - Ambiguous `Neb`/`Cl+N` without Sh2 confirmation → `REFLECTION`, which is treated as broadband
    (the conservative choice).
  - `REFLECTION_OVERRIDES` is a small curated `frozenset` in `candidates.py`, starting with the
    prototype's list (M45, M78, NGC7023, NGC1333, IC2118, NGC1977, …). Keys are canonicalised.
- **Imageability gate** for NGC/IC rows (from the prototype): `mag ≤ 11.5` or `size ≥ 8′`, or
  (`EMISSION` and `size ≥ 3′`). Sh2 regions without a cross-ID are included if `size ≥ 10′`.
  **Any key the user has imaged is always included**, whatever the gate says.
- About 1.5k candidates are expected. The pool is cached in-process and rebuilt when the alias
  index cache is stale.

### 4.2 Ephemeris: `ephemeris.py`

- **Night grid:** `night_bounds_utc(night, lon)` split into **5-minute steps** (288 steps).
- **Sun altitude:** reuse `utils/horizon.sun_altitude` (numpy).
- **Moon:** astropy `get_body("moon", ...)`, already a dependency. It gives RA/Dec/alt and
  **illuminated fraction** per step, plus the **phase age** (days since new, from the Sun–Moon
  elongation, waxing vs waning taken from the sign). It's computed only at 15-minute resolution and
  interpolated, so each night costs about 0.1 s.
- **Targets:** vectorised alt/az for N candidates × T steps with `utils/horizon.alt_az` (LST and
  spherical trig). It returns `alt`, `az` (N×T float32) and the Moon separation (N×T).
- It's a pure function of (candidate RA/Dec arrays, lat, lon, night), and the result is cacheable
  by `(site_id, night)`.

### 4.3 Night context: `context.py`

```python
@dataclass
class NightContext:
    night: date; site: SiteInfo
    tier: str               # ASTRO | NAUTICAL | BRIGHT | NONE
    dark_mask: np.ndarray   # T bools for the chosen tier
    dark_start_utc, dark_end_utc: datetime | None
    horizon: list[[az, alt]]; horizon_source: str   # SAVED | LEARNED | DEFAULT
    floor_deg: float
    moon_illum: float; moon_age_days: float; moon_up_dark_frac: float
```

- **Darkness tier** (prototype lesson: ~56°N has no astronomical darkness from about May to
  mid-August, and at midsummer not even nautical):
  - `ASTRO` if Sun < −18° for ≥ 1 h;
  - else `NAUTICAL` if Sun < −12° for ≥ 1 h;
  - else `BRIGHT` if Sun < −9° for ≥ 1 h. **Narrowband only**, and flagged loudly;
  - else `NONE`, which returns an empty result with a reason.

  The tier and its thresholds are in the response. The UI says "No astronomical darkness tonight;
  using nautical twilight".
- **Horizon:**
  1. the site's saved `horizon` (`SAVED`);
  2. else the learned profile from R0's `utils/horizon.learn_horizon` (capped at floor + 10°,
     `LEARNED`);
  3. else a flat 30° (`DEFAULT`).

  The effective limit at azimuth `az` is `max(horizon(az), floor_deg)`, where `floor_deg` = the
  learned floor (default 30°). The learned profile is cached per site for 24 h in Redis. It's the
  same computation as `GET /api/sites/{id}/horizon/learned`, so share it.

### 4.4 History: `history.py`

`build_history(rows, as_of: date | None) -> dict[key, TargetHistory]`

- `rows` are light subs with `target_key`, `target_source <> 'NONE'`, `exposure_time_seconds > 0`,
  filter, night (`night_of`), `rig_id` and `pixel_scale_arcsec`.
- **`as_of` truncates** to nights strictly before it. That's what makes replay honest.

```python
@dataclass
class TargetHistory:
    seconds_by_class: Counter   # BB | HA | OIII | SII | OSC  (from normalize_filter)
    seconds_by_filter: Counter  # raw normalize_filter buckets, for reasons
    nights: set[date]; first_night, last_night: date
    rig_ids: set[int]; has_master: bool
```

**Filter classes** map `normalize_filter` buckets to moon-rule classes: L/R/G/B/None → `BB`,
Ha → `HA`, SII → `SII`, OIII/Hb → `OIII`, Duo → `HA`+`OIII` (split 50/50), and OSC colour cameras
with no filter → `OSC`. `BB` and `OSC` share the broadband moon rule.

**Inferred goal** (the R1 simple version; R2 adds palette completeness):
1. A `target_goals` row (sum of the target's rows, or its `ANY` row) wins. `goal_source = "SET"`.
2. Else, the median total hours of targets with `has_master` and ≥ 2 nights **of the same kind**
   (needs ≥ 3 samples), else across all kinds. `goal_source = "INFERRED"`.
3. Else, 10 h. `goal_source = "DEFAULT"`.

Computed from history as of the evaluated night.

### 4.5 Scoring: `scoring.py`

Rigs come in as `RigSpec(id, name, scale_arcsec, fov_w_deg, fov_h_deg, classes: set[str], is_color)`.
- `classes` are the rig's filter classes, from `rig_filters`.
- A rig with no filters and an OSC camera → `{OSC}`.
- A rig with no filters and a mono camera → the classes seen on its assigned images, else `{BB}`.
- `scale` is the declared scale, else the measured scale. A rig without a scale or FOV is skipped,
  and the response says why.

**Per (candidate, rig) pair:**

1. **Usable mask:** `U = dark_mask ∧ alt > limit(az)`. `usable_h = ΣU·Δt`.
2. **Moon factor per class**, soft (prototype lesson: NGC7000 missed a hard cut by 0.1°):
   - `required = D / (1 + ((0.5 − age/29.53) / (W/29.53))²)` (the Lorentzian avoidance distance),
     with defaults `BB: D=120, W=14`, `HA/SII: D=40, W=10`, `OIII: D=70, W=10`.
   - `f(t) = 1` if the Moon is below the horizon, else `sigmoid((sep − required) / 5°)`, so
     0.1° short gives ≈ 0.5, not 0.
   - `moon_ok_h[class] = Σ U·f·Δt`.
   - The rules live in `MOON_RULES` and are tunable via replay.
3. **Mode:** the classes the pair can use are rig classes ∩ target-useful classes:
   - `EMISSION` → HA, SII, OIII, BB/OSC;
   - `PN` → OIII, HA, BB/OSC;
   - others → BB/OSC only.

   The `BRIGHT` tier drops BB/OSC. `mode` = the best class by `moon_ok_h`, and `avail_h` = that value.
4. **Framing:**
   - `ratio = size / min(fov_w, fov_h)` (arcmin / arcmin);
   - `px = size·60 / scale`;
   - `fit = exp(−ln(ratio/0.5)² / (2·0.7²))`;
   - `px < 40` → too small; `ratio > 3` → too big;
   - an unknown size → `fit = 0.4`, `ratio = None`.
5. **Hard feasibility**, where the first failure is recorded as `excluded_reason`:
   - `usable_h < min_usable_h` (default 1.0) → `BELOW_HORIZON`;
   - too small → `TOO_SMALL`;
   - too big → `TOO_BIG`;
   - `avail_h < 0.5` → `MOON`;
   - no class usable in the tier → `TIER`.
6. **Components** (each 0–1):

| Component | Definition |
|---|---|
| `observability` | `min(Σ U·f_mode·w(alt)·Δt / 5 h, 1)`, where `w = sin(alt)` (≈ 1/airmass) |
| `framing` | `fit` |
| `project` | "Committed" means ≥ 2 nights, **or** ≥ 2 h, **or** momentum > 0 (a 1-night test isn't a project). If committed: `min(1, (sqrt((have + Δ)/have) − 1) / 0.5) × (1 if have < goal else 0.4)`, where `Δ = 0.7 × avail_h` and `have` = hours in the classes usable tonight (falling back to all hours). Otherwise 0. |
| `momentum` | `exp(−days_since_last / 45)` if last imaged ≤ 180 days ago, else 0 (prototype lesson: the strongest intent signal) |
| `urgency` | Seasonal. Compute `usable_h` for the same site on nights +7, +14, … +84 (coarse 30-min grid). `weeks_left` = the first week with `usable_h < min_usable_h`. `urgency = (1 − weeks_left/12)` if tonight's `usable_h ≥ 0.8 × max(future)`, else 0. |
| `prior` | Catalog familiarity: M 1.0, C 0.9, Sh2 0.6, NGC/IC 0.55 |

   `score = Σ wᵢ·cᵢ`. The defaults come from the prototype: `observability 0.25, framing 0.20,
   project 0.20, momentum 0.15, urgency 0.15, prior 0.10`. They live in a frozen `Weights`
   dataclass, and replay tuning (§5) may change them. **A pair that's already at ≥ 1.5 × goal gets
   `project = 0`.**
7. **Best rig per target** (when several rigs are evaluated): the pair with the highest score. The
   runner-up rigs are listed as `alternatives` (rig name + score).

### 4.6 Rig selection

- **`rig=mounted` (the default):** the mounted rig. **If none is mounted, use `rig=all`,** and the
  response says `rig_mode="ALL_FALLBACK"`.
- **`rig=all`:** every `is_active` rig, with the best rig per target.
- **`rig=<id>`:** that rig only.

### 4.7 Lanes and hero: `lanes.py`

Each target appears in **exactly one** lane. These are checked in priority order:

| Lane id | Title | Rule |
|---|---|---|
| `active` | Active projects | momentum ≥ 0.37 (≤ 45 days) and committed |
| `continue` | Continue a project | committed, `project > 0.15`, have < goal |
| `last_chance` | Last chance this season | `urgency ≥ 0.5` |
| `moon_proof` | Moon-proof tonight | the Moon is up for > 50% of dark and illum > 0.5, and mode ∈ {HA, SII, OIII} |
| `other` | Other good options | the rest, by score |

- **Diversity:** within a lane, a pick whose centre is within 10° of **two** higher-ranked picks
  in that lane is pushed below the next pick that isn't. That stops five Cygnus nebulae in a row.
  Each lane shows at most `per_lane` items (default 6), and `other` shows 10.
- **Hero** = the highest score across lanes. Its **verdict** is:
  - `GO` if `avail_h ≥ 3` and tier ∈ {ASTRO, NAUTICAL};
  - `MARGINAL` if `avail_h ≥ 1.5`, or the tier is BRIGHT;
  - `DONT_BOTHER` otherwise, or if there are no feasible picks.

  The verdict carries its reasons.
- **Reasons** are structured chips `{code, text}`, for example:
  - `MOON_CLEAR`: "5.8 h clear of Moon (Ha)"
  - `FRAMING`: "fills 46% of ASI294 + 200 mm"
  - `HAVE`: "6.4 h over 11 nights; goal ≈ 15 h (inferred)"
  - `ACTIVE`: "last imaged 32 days ago"
  - `WINDOW`: "window closes in ~5 wk"
  - `TIER`: "nautical darkness only"
  - `MOON_MARGINAL`: "Moon 59° away; OIII limited"

### 4.8 Public entry point

```python
recommend(inputs: EngineInputs, params: Params) -> Result
# EngineInputs: candidates, history, site, horizon, rigs, goals, night (date), now (datetime|None)
# Params: rig_mode, weights, moon_rules, min_usable_h, per_lane, include_excluded (bool)
# Result: context, hero, lanes, excluded_counts, ranked (full list of feasible picks, for replay)
```

**Performance budget:** one night × 1.5k candidates × 4 rigs in under 1.5 s in the container
(ephemeris is shared across rigs). Seasonal urgency uses a cached coarse grid per `(site, night)`.

## 5. Replay test: `recommend/replay.py` (pure) + `scripts/replay_recommendations.py`

**Nights:** every distinct night (via `night_of`) with ≥ 30 min of resolved light subs.

- Live, that's **351 nights**, not the ~790 in older notes. That figure counted all nights,
  including `NONE` lights.
- 244 of those nights have UTC, and 104 have a site (before §3.2 and §3.3).
- `--since` / `--until` filter the nights.

**For each night N:**
- `history = build_history(rows, as_of=N)` (strictly earlier nights only).
- **Site:** the majority `site_id` of N's subs, else the default site.
- **Rig:**
  - the majority `rig_id` of N's subs → `rig=<id>`;
  - else `rig=all`, over the rigs whose assigned images span N ± 365 days;
  - else all active rigs.
- **Actual** = the set of target keys with ≥ 30 min on N.
- Run `recommend(...)` and keep `ranked`.

**Metrics** (JSON report + printed table):
- **`hit@k`** (k = 1, 3, 5, 10): any actual key in the top k of `ranked`.
- **`MRR`**: the mean reciprocal rank of the best-ranked actual key.
- **`feasible_recall`**: the share of actual (night, key) pairs that pass hard feasibility. **This
  checks that the filters aren't too strict.** Every miss is listed with its `excluded_reason`.
- All of the above broken down by darkness tier, Moon illumination (< 0.25, 0.25–0.75, > 0.75),
  year, and whether the rig was known.

**Baselines** (same nights, same candidates, same feasibility):
- `recency`: feasible targets by most recent last night.
- `altitude`: by `usable_h`.
- `random`: seeded, averaged over 20 runs.

**Tuning:** `--grid` runs a coarse grid over `Weights` (and optionally the `MOON_RULES` D values ±25%)
and prints the top 5 settings by `hit@5`, with MRR as the tie-break. **Nothing is auto-applied.** The
orchestrator proposes new defaults to the owner, with the report.

**Honesty rules** (research §5):
- There are no novelty or revisit lanes in R1, so all lanes count.
- Replay ignores `target_goals` created after N. The table has `created_at`; use it.
- The candidate pool and catalog are static, which is acceptable.

The script runs inside the container (`docker exec astrocat-backend-1 python -m
app.scripts.replay_recommendations [--grid] [--out /app/logs/replay.json]`). It must take under 10 min
for all nights without `--grid`.

## 6. Loader, cache and task

- **`recommend/loader.py`** (sync session) loads:
  - the candidate inputs (catalog rows + alias index);
  - history rows, in one query, with `night` computed in SQL as in §3.1;
  - sites, rigs (with classes) and goals.
- **Redis caches:**
  - `recs:inputs:v<hist_version>` (history and pool, 1 h). `hist_version` = `max(images.updated_at)`
    of light subs + the `target_goals` count.
  - `recs:result:<site>:<rig_mode>:<night>:<hist_version>` (6 h).
- **Invalidation:** equipment/site writes and `assign_equipment` completion delete `recs:*`. Reuse
  the helper that invalidates `cache:targets:*`.
- **Celery beat:** `app.tasks.recommend.precompute_tonight` runs daily at 12:00 UTC. It computes
  tonight for the default site with `rig=mounted`, and warms the cache. Route it to the default
  `celery` queue and add it to the `worker.py` include and `beat_schedule`.
- **The API runs the engine in a threadpool** (`run_in_threadpool`) with a sync session. A cold
  request must finish in under 3 s.

## 7. API: `backend/app/api/recommendations.py` (new router, `/api/recommendations`)

All endpoints need an authenticated user; there are no writes in R1.

`GET /api/recommendations?date=YYYY-MM-DD&site_id=&rig=mounted|all|<id>&per_lane=6`

The date defaults to tonight's night at the default site, and `site_id` defaults to the default site.

```jsonc
{
  "generated_at": "…", "cached": true,
  "context": {
    "night": "2026-09-26", "site": {"id": 1, "name": "…", "timezone": "Europe/London"},
    "tier": "ASTRO", "tier_note": null,
    "dark_start_utc": "…", "dark_end_utc": "…",
    "moon": {"illumination": 0.998, "age_days": 14.6, "up_fraction": 1.0},
    "horizon_source": "LEARNED", "floor_deg": 32.3,
    "rig_mode": "MOUNTED" | "ALL" | "ALL_FALLBACK" | "SINGLE",
    "rigs": [{"id": 3, "name": "…", "classes": ["HA","OIII","SII","BB"]}],
    "weights": {…}
  },
  "hero": Pick | null,
  "verdict": {"level": "GO" | "MARGINAL" | "DONT_BOTHER", "reasons": [Reason]},
  "lanes": [{"id": "active", "title": "Active projects", "items": [Pick]}],
  "excluded_counts": {"BELOW_HORIZON": 0, "TOO_SMALL": 0, "TOO_BIG": 0, "MOON": 0, "TIER": 0},
  "skipped_rigs": [{"id": 7, "name": "…", "reason": "no pixel scale"}]
}
```

```jsonc
Pick = {
  "target_key": "NGC7000", "name": "North America Nebula", "kind": "EMISSION",
  "ra_deg": 314.7, "dec_deg": 44.3, "size_arcmin": 120,
  "rig": {"id": 3, "name": "…"}, "alternatives": [{"rig_id": 1, "rig_name": "…", "score": 0.51}],
  "mode": "HA", "score": 0.742,
  "components": {"observability": 0.9, "framing": 0.8, "project": 0.6, "momentum": 0.49, "urgency": 0.0, "prior": 0.9},
  "usable_hours": 7.1, "available_hours": 5.8, "best_time_utc": "…", "max_alt_deg": 84,
  "moon_sep_min_deg": 59, "fill_ratio": 0.46,
  "have_hours": 6.4, "have_by_filter": {"Ha": 3.1, "OIII": 2.2, "SII": 1.1},
  "nights": 11, "last_imaged": "2026-08-25", "goal_hours": 15.0, "goal_source": "INFERRED",
  "reasons": [{"code": "MOON_CLEAR", "text": "5.8 h clear of Moon (Ha)"}],
  "curve": {"t_utc": ["…"], "alt": [..], "moon_alt": [..], "limit": [..], "dark": [..]}   // 15-min samples
}
```

`GET /api/recommendations/target/{key}?date=&site_id=&rig=` explains one target: the Pick
(if feasible) or `{"excluded_reason": …, "details": {…}}` for **each** rig. This backs
"Why isn't X on the list?" and helps with debugging.

`GET /api/recommendations/replay/latest` returns the last replay report JSON (admin only), if one
was saved to `library/logs/replay_latest.json`.

## 8. Frontend: Tonight page

- **Route and nav:** `/tonight` (`pages/Tonight.jsx` + `Tonight.css`). The nav item "Tonight" goes
  **before** Targets, with a Moon icon. Use the inline-SVG approach from `TelescopeIcon.jsx` if the
  pinned lucide doesn't have one; `Moon` exists in 0.292.
- **Client:** a `// Recommendations (R1)` section appended at the end of `api/client.js`.
- **Header controls:**
  - a date picker (default: tonight), with prev/next night buttons;
  - a site select (sites; default site first);
  - a rig select ("Mounted: <name>" | "All rigs (best per target)" | each rig).
- **Context strip:**
  - the dark window in local site time **and** UTC;
  - a tier badge, with a warning style for NAUTICAL/BRIGHT and the `tier_note`;
  - Moon % lit, and hours up during dark;
  - the horizon source ("learned horizon" links to Equipment > Sites);
  - `ALL_FALLBACK` shows "No rig marked as mounted: showing the best rig per target. Set one in
    Equipment."
- **Hero card:** the verdict pill (GO green / MARGINAL amber / DON'T BOTHER grey), the target name
  (linking to `/targets/<key>`), the rig and mode, the reasons, and a larger altitude chart.
- **Lanes:** a titled section per non-empty lane, as a card grid. Each card shows:
  - name + key + kind;
  - the rig (plus "also: …" alternatives);
  - mode, and a small score bar;
  - the top 3 reasons as chips;
  - a **sparkline** (recharts): target altitude (solid), Moon altitude (dashed, muted) and the
    limit line, with the dark window shaded;
  - the have/goal progress bar;
  - "Open target".
- **"Why not…?"** is a search box. It resolves a name to a key (via the existing search/targets
  lookup) and calls the per-target endpoint. It shows each rig's verdict and exclusion reason.
- **Empty and error states:**
  - no sites → a link to Equipment;
  - no rigs → a link to Equipment;
  - tier `NONE` → "Too bright tonight (Sun never below −9°)";
  - no feasible picks → the excluded counts.
- **Local time:** use the site's `timezone`, via `Intl.DateTimeFormat` with `timeZone`.

## 9. Files

**New:**
- `backend/app/utils/observing_night.py`
- `backend/app/services/recommend/{__init__,candidates,ephemeris,context,history,scoring,lanes,loader,replay}.py`
- `backend/app/tasks/recommend.py`, `backend/app/api/recommendations.py`,
  `backend/app/schemas/recommendations.py`
- `backend/app/scripts/replay_recommendations.py`
- `backend/alembic/versions/2026_09_27_1200-f9b1d5a62009_capture_utc_basis.py`
- Tests: `test_observing_night.py`, `test_recommend_candidates.py`, `test_recommend_ephemeris.py`,
  `test_recommend_scoring.py`, `test_recommend_lanes.py`, `test_recommend_replay.py`,
  `test_recommendations_api.py`
- `frontend/src/pages/Tonight.jsx` + `.css`
- `docs/features/RECOMMENDATIONS.md`

**Modified:**
- `models/image.py` (`capture_utc_basis`), `tasks/equipment.py` (§3.2),
  `services/data_migrations.py` (`0008`)
- `api/targets.py` (nights expressions only), `main.py`, `worker.py` (include + beat)
- `App.jsx`, `Layout.jsx`, `client.js`
- `docs/core/DATABASE_SCHEMA.md`, `docs/features/TARGETS.md` (night definition)

## 10. Tests (from `backend/`, same env-var pattern as the handover §5)

- **`test_observing_night.py`:**
  - UTC 01:30 at lon −3 → the previous date;
  - UTC 13:00 → the same date;
  - lon +150 shifts the boundary;
  - `utc=None` falls back to local − 12 h.
- **`test_recommend_candidates.py`:**
  - IC1396 size = 170 via the Sh2-131 alias;
  - M81 key from NGC3031 rows;
  - `Neb` without Sh2 → REFLECTION;
  - `HII` Maia (in overrides) → REFLECTION;
  - Sh2-only regions < 10′ excluded unless imaged;
  - any imaged key is always present.
- **`test_recommend_ephemeris.py`:**
  - Sun altitude at a known time/place within 0.5° of astropy;
  - Polaris altitude ≈ latitude;
  - Moon illumination on 2026-09-26 > 0.99;
  - at 56°N on 2026-06-21, the tier is BRIGHT or NONE (never ASTRO);
  - at 56°N on 2026-12-21, the tier is ASTRO with > 12 h.
- **`test_recommend_scoring.py`:**
  - soft Moon: sep = required − 0.1° gives f ≈ 0.5;
  - an EMISSION target on an HA-capable rig at full Moon stays feasible, and BB doesn't;
  - an OSC rig at full Moon has no BB picks near the Moon;
  - framing: too small, too big and unknown size;
  - project: a 1-night 20-min test gives 0, and ≥ 2 nights gives > 0;
  - ≥ 1.5 × goal gives project 0;
  - momentum decay;
  - the BRIGHT tier drops BB.
- **`test_recommend_lanes.py`:**
  - lane priority and one lane per target;
  - diversity reorder;
  - verdict thresholds;
  - reason texts.
- **`test_recommend_replay.py`:**
  - the as-of truncation never leaks night N's own data;
  - metrics on a 3-night synthetic fixture (hit@k, MRR, feasible_recall);
  - baselines are deterministic with a seed.
- **`test_recommendations_api.py`:**
  - the response shape matches §7;
  - `rig=mounted` with none mounted gives `ALL_FALLBACK`;
  - an unknown key gives 404 on the target endpoint;
  - a cache hit sets `cached: true`.
- **§3.2:**
  - a no-site `EXIF_LOCAL` row with a UTC-clock camera gets UTC = local, basis `CAMERA_UTC`;
  - otherwise it gets the default site's tz, basis `DEFAULT_SITE_TZ`;
  - no default site means untouched;
  - `0008` is idempotent.
- **Frontend:** `npm run build` and `npm run lint`, with no new lint problems (baseline 36).

## 11. Acceptance criteria (verified live after deploy)

1. **UTC coverage:** `0008` is `applied`, and ≥ 95% of `EXIF_LOCAL`/`FITS_LOCAL` light subs have
   `capture_date_utc`, each with a `capture_utc_basis`.
2. **Replay report** for all nights:
   - `feasible_recall ≥ 0.90`, with every miss explained;
   - the engine beats **every** baseline on hit@5 **and** MRR;
   - the results are reviewed with the owner before any weight changes. The report goes in
     `docs/design/R1-replay-results.md`, with numbers only and no coordinates.
3. **2026-09-26 regression (full Moon):**
   - NGC7000 is in the top 3 overall, in mode HA or SII, with an `ACTIVE` reason;
   - no BB pick has a Moon separation under 60°;
   - with the R7 (OSC) rig only, the result is empty or `DONT_BOTHER`.
4. **A June night** (2026-06-21): the tier is BRIGHT or NAUTICAL with a visible note, and the list is
   non-empty and narrowband-only when BRIGHT.
5. **Targets nights:** after the `night_of` switch, the total nights across all targets changes by
   ≤ 2%, and no target's `nights` changes by more than 1.
6. **Performance:** a cached response in under 200 ms and a cold one in under 3 s. The beat task
   warms tonight.
7. **Tonight page:** it works with no mounted rig (the fallback banner), with a mounted rig, and
   for "Why not NGC7000?" on a night where it's excluded.

## 12. Execution plan

Same pattern as R0 (HANDOVER-recommendations §3):
- agents in isolated worktrees;
- no Docker, live DB or `main` access, and no pushes;
- the orchestrator trial-merges, tests, backs up, deploys and verifies.

| Step | Who | Scope | Estimate |
|---|---|---|---|
| B1 | agent (Opus), `feat/r1-engine-backend` | §3.1–3.2, §4–7, §9–10 backend | 450–600k |
| B2 | agent (Sonnet), `feat/r1-tonight-frontend` | §8 against the §7 contract (mocked), in parallel with B1 | 200–300k |
| Deploy | orchestrator | Trial merge, tests/build/lint, DB backup, merge, VERSION bump, rebuild both, watch `0008`, check §11 1, 5–7 | 80–120k |
| Replay | orchestrator | Run replay + `--grid` in the container, write `R1-replay-results.md`, agree weights with the owner, apply them in a small follow-up commit, check §11 2–4 | 60–120k |
| **Total** | | | **~0.8–1.15M** |

**Merge hotspots:**
- `client.js`, `App.jsx` and `Layout.jsx` (appends only);
- `worker.py` (include and beat);
- `tasks/equipment.py` (only the `_fill_capture_utc` branch);
- `api/targets.py` (nights expressions only).

## 13. Decisions (defaults chosen; change before B1 starts if needed)

1. **No weather in R1.** GO/MARGINAL is about the sky geometry, not clouds (R3).
2. **No mounted rig means "all rigs, best per target"**, with a banner. The page doesn't block.
3. **Soft Moon penalty** (a sigmoid with 5° width) rather than a hard cut, for every filter class.
4. **Narrowband-only below nautical** (the BRIGHT tier, Sun < −9°), so the summer still gets
   suggestions.
5. **Inferred goals are in R1** (the simple median, per kind). Palette completeness waits for R2.
6. **The replay test doesn't auto-tune.** Weight changes are proposed to the owner with the report.
7. **The `night_of` switch also applies to Targets**, so both pages agree on what a "night" is.

## 14. B1 implementation notes (backend, `feat/r1-engine-backend`)

Where the shipped backend differs from, or adds to, the text above. The §7 shapes are kept;
every change to them is additive.

- **Exclusion order:** `TIER` (no usable class) is checked before `MOON`. With no usable
  class the available hours are always 0, so in the §4.5 order `MOON` would hide `TIER`.
- **Replay majorities are strict:** a night's rig (or site) is its majority only with > 50% of
  the night's seconds; otherwise the ±365-day / default fallbacks apply.
- **`prior`** uses the most familiar catalog among a candidate's aliases (NGC7000 is also C20,
  so 0.9), not only the catalog the key comes from. `Candidate.catalog` still names the key's
  catalog.
- **`project`:** the marginal-gain term uses the hours in the classes usable tonight (else
  all hours); the goal comparisons (`< goal`, `>= 1.5 x goal`) use total hours.
- **Inferred goal:** the all-kinds median also needs >= 3 samples, else the 10 h default.
- **`MOON_MARGINAL`** names the usable class the Moon cuts least among those below half the
  usable time (e.g. OIII next to a fine Ha).
- **Seasonal urgency:** `weeks_left` is 1-based (the first failing week, +7 d = 1); future
  nights use their own darkness tier on a 30-minute grid, without the Moon.
- **Default date:** from 06:00 local solar time the default is the coming night; before that,
  the current night (so a mid-session request stays on tonight).
- **History rows** are pre-aggregated in SQL per (key, night, filter, camera colour, rig,
  site) with the best valid pixel scale; the pool is cached in process (rebuilt with the alias
  index or when the imaged keys change), not in `recs:inputs`.
- **Result cache key** includes `per_lane`: `recs:result:<site>:<rig>:<night>:<per_lane>:<hist_version>`.
  `hist_version` also includes the light-sub count and the latest goal update.
- **Learned horizon** moved to `services/site_horizon.py` (shared SQL, computation and Redis key
  with the Sites API); the key's TTL is now 24 h for both.
- **Additive response fields:** `context.tier_thresholds`, `context.dark_hours`,
  `context.moon_rules`; per pick `lane`, `catalog`, `magnitude`. Timestamps are ISO UTC with a
  `Z` suffix. The pick curve covers sunset to sunrise (Sun < 0°) in 15-minute samples.
- **Verdict reason codes** beyond the pick chips: `NO_PICKS`, `NO_RIGS`, `SHORT`.
- **Target endpoint:** the key may be a name or alias (resolved through the alias index); the
  body adds `rig_mode` and `skipped_rigs`. 404 when the key isn't a candidate.
- **Errors:** no site, unknown `site_id` or unknown rig id → 404; a malformed `rig` → 400.
- **Replay report** (the orchestrator's contract): `generated_at, nights, params, metrics,
  baselines, breakdown, misses`, plus `actual_pairs`, `pool_coverage`, `miss_reasons`,
  `beats_baselines`, `seed`, `since/until`, `runtime_seconds` and `grid_top` with `--grid`.
  `feasible_recall` is over actual pairs whose key is in the candidate pool; targets outside
  the pool (e.g. `OBJ:` keys) are listed as `NOT_IN_POOL` misses and measured by
  `pool_coverage`. The learned horizon used for replay is today's (static, like the catalog).
- **New-file UTC:** the indexer hook also applies the default-site rule, so new site-less
  local-clock files get UTC immediately.


### 14.1 Tuning round (`feat/r1-tuning`, after the first live replay)

The first live replay (272 nights, default weights) gave hit@5 0.268 and MRR 0.179, below the
recency baseline (0.493 / 0.407), and `feasible_recall` 0.667. The misses were: MOON 56,
TOO_SMALL 43, BELOW_HORIZON 35, TIER 6, TOO_BIG 2 and NOT_IN_POOL 51. The changes below
supersede §4.5–§4.6 and §5 where they differ. The default weights and Moon rules are
unchanged; new weights are chosen with the owner from a re-run.

- **Hard filters are generous.** They drop only clear-cut cases. The full soft rules still
  drive `available_hours`, `observability` and the score.
  - **MOON:** excluded only when the pair has < 0.5 h (on the relaxed horizon) clear of
    **0.5 × the required distance**, with the same 5° sigmoid (`Params.moon_hard_fraction`).
    Broadband is routinely imaged 25–80° from the Moon.
  - **BELOW_HORIZON:** hard limit = `max(15°, limit(az) − 10°)`; `min_usable_h` defaults to
    **0.5 h** above it. `usable_hours`, `observability` and the Moon-clear hours use the real
    limit, so a low target (Orion from ~56°N culminates at 28–30°) survives but scores low.
    A `LOW` reason chip says "below your usual horizon (max N°)". Seasonal urgency keeps the
    real limit and 1 h.
  - **TIER:** only `NONE` removes classes. In `BRIGHT`, broadband/OSC is allowed with
    `observability × 0.3` (`Params.bright_broadband_factor`), and the TIER chip says
    "bright twilight: broadband penalised". `TIER` as an exclusion now only means the rig has
    no filter class useful for the target (or the tier is NONE).
  - **TOO_SMALL** below 15 px; **TOO_BIG** above 4 × the FOV short side.
  - Consequence for §11 item 3: near a full Moon, broadband picks can now appear. They have
    ~0 `available_hours` and rank low. The check becomes "every BB pick < 60° from the Moon
    has `available_hours` < 0.5 and ranks below the narrowband hero".
- **Momentum:** `exp(−days/tau)` with `Params.momentum_tau_days` (default 45). It is zero
  beyond 3 × tau (was: zero beyond 180 days), so with the default it is zero beyond 135 days.
  "Committed" (momentum > 0) follows the same cutoff.
- **`recency_rank`** is a new component: `1 / (1 + rank)`, where the rank is the number of
  targets imaged more recently (ties share a rank). Its weight defaults to 0. It appears in
  `components`, an additive change to the §7 shape.
- **Pool fixes:**
  - Imaged keys whose OpenNGC row is typed `*` or `Other` (IC1318, IC5067, IC155, IC3584,
    NGC1990) were dropped by the NGC type skip. They are now candidates. An OTHER kind imaged
    ≥ 50% in narrowband becomes EMISSION.
  - `Dup` rows (IC11, IC395, NGC2244, …) fold into the candidate within 0.25° (the pool's
    `dup_map`). An imaged Dup with nothing nearby becomes its own candidate.
- **Stray history keys** (history mapping only; `images` is not modified): a key that isn't
  in the pool folds into a pool key when it, or it with a trailing filter word (`LUM`, `HA`,
  `L`, …) or one trailing panel digit removed, resolves through the alias index or the Dup
  map. Examples: `OBJ:NGC78222` → NGC7822, `OBJ:IC13961` → IC1396, `OBJ:M81LUM` → M81,
  `OBJ:NGC22441` → NGC2239. The folding applies to the live engine too.
- **Replay evaluation set:**
  - Each actual (key, night) pair is HOME (majority `site_latitude` within 1.5° of a
    configured site), REMOTE, or UNKNOWN_SITE (no latitude).
  - **Headline metrics, baselines and breakdowns use HOME + UNKNOWN_SITE**; `nights` counts
    nights with at least one headline pair. `remote` holds the same metrics for REMOTE pairs.
  - The breakdown adds `site_class` and `source` (HEADER = HEADER/HEADER_RAW/MANUAL vs
    MATCH). `pair_counts` gives the pair counts.
  - Every miss carries `site_class`, `source`, and `details` for the rig that got furthest
    through the hard filters (rig, mode, usable and hard-usable hours, available and
    hard-available hours, Moon separation and required separation, max altitude, fill
    ratio, target px).
  - `folded_keys` records the fold map and counts.
- **Tuning grid** (`--grid`):
  - The space is `replay.TUNING_SPACE`: momentum {0.3, 0.45, 0.6}, tau {10, 20, 45},
    recency_rank {0, 0.2}, project {0, 0.1}, observability {0.125, 0.25}, framing {0.1, 0.2},
    urgency {0.075}, prior {0.05, 0.1}. That is 288 combinations; `--grid-max N` takes a
    seeded sample.
  - `--grid-moon` adds a first stage over broadband/OSC D ∈ {60, 90, 120} at the default
    weights. The best rule set is then used for the weight grid.
  - Each row carries the recency baseline on the same headline pairs.
  - The grid reuses the main run's per-night state. On a synthetic 272-night fixture that is
    about 1 s per combination; expect about 3× that live.
  - `--moon-bb D[,W]` sets the broadband/OSC rule for a single run.
