# Target Recommendations — Research & Design Proposal

Status: **Research / proposal** · Written: 2026-09-26 · Base: `main` @ `698473c`

## 1. The idea in one paragraph

Every planner (Telescopius, SkySafari, Stellarium, Nova, Clear Night Coach…) answers
*"what is up tonight?"*. AstroCat can answer *"what should **I** image tonight, with **my**
gear, from **my** site, given what I have **already** shot?"*. Its edge is not better
ephemeris math — that is commodity — it is the ~94k indexed frames that say where this user
really images from, how low they really go, which rigs and filters they own, which projects
are half-finished, and which months they actually get out. The recommender should turn that
history into an engine that ranks targets and explains each pick.

## 2. What existing tools do (and where they stop)

| Tool | How it recommends | Personalisation | Gap AstroCat can fill |
|------|-------------------|-----------------|------------------------|
| **Telescopius** "What's in the sky tonight" | Hard filters: ≥30° altitude for ≥1 h of astro night, size limits per object type, Moon-Avoidance Lorentzian. Then a **randomised** selection. | Gear-aware FOV previews and manual filters only. The moderator says the list isn't personalised. | No history, no project state, no ranking. |
| **SkyTools 4** Nightly Observing List Generator | Physical **contrast / SNR model** per object, gear, sky and moon. Quality bands (Best/Fair/Poor) and a best-time window. | Gear + conditions. | Very good physics, but it doesn't know what you've captured. |
| **N.I.N.A. Target Scheduler** | Filters (horizon + offset, meridian window, flip-unsafe zone, moon avoidance, twilight per filter). Then weighted rules: Percent Complete 50%, Priority 50%, Setting Soonest 50%, Target Switch Penalty 67%, Meridian Window 75%, Mosaic Completion, Smart Exposure Order. | Projects and goals the user **enters by hand**. It only schedules targets you already chose. | Doesn't propose *new* targets. Goals are manual. |
| **Astro Imaging Planner** (gshau) | Per-target data by filter/instrument, status (Pending/Active/Acquired/Closed), weather modal. | Reads acquired data. | Tracks progress but doesn't rank or recommend. |
| **Nova DSO Tracker** | Altitude/dark-minutes sort, horizon mask, moon separation, optional **LLM "Ask Nova"** ranking. | Object list + journal. | Ranking is delegated to an LLM, not a model grounded in data. |
| **Clear Night Coach** | Scores targets on framing, mount limits, field rotation, moon and weather. Commits to **one "hero pick"** with GO/MARGINAL/DON'T-BOTHER and a reason. | Horizon (.hrz), rig. "Rests" captured targets for 30–90 days. | Uses history only to *avoid* repeats, not to continue projects. |

Takeaways worth borrowing:
1. **Two stages: filter, then score** (Target Scheduler, Telescopius). Hard constraints
   remove the impossible. A weighted score ranks what's left.
2. **Moon-Avoidance Lorentzian** (BAIT → ACP → NINA/Telescopius) is the de-facto standard:
   `required_sep = D / (1 + ((0.5 − age/29.5) / (W/29.5))²)`. D is the separation at full
   moon, W is the half-width in days, and it is tuned **per filter** (narrowband tolerates
   far more).
3. **Explain every pick** (Clear Night Coach, SkyTools quality bands). Recommendations
   without a "why" aren't trusted.
4. **Percent-complete and setting-soonest** are proven scheduling signals. AstroCat can
   compute both automatically instead of asking the user.

## 3. What AstroCat already knows (measured on the live DB, 2026-09-26)

| Signal | Where it lives | State |
|--------|----------------|-------|
| **Site** | `SITELAT/SITELONG` in `raw_header` (24,240 lights), EXIF GPS (~6.5k DSLR frames) | ⚠ `images.site_latitude/longitude` are **never populated** (0 rows). F7 §5 plans to persist them. Two ~56°N clusters in the data, probably home and a second site. |
| **Actual altitude habits** | `CENTALT` (21.9k), `AIRMASS` (22k), `CENTAZ` (8.6k). Az/alt can be computed for **every** plate-solved or `OBJCTRA` frame from RA/Dec + time + site. | 1st/5th/25th/50th percentile altitude = **25° / 32° / 47° / 58°**. The user rarely goes below ~30°. Telescopius's fixed 30° happens to be close, but this cutoff is *learned*. |
| **Rigs** | camera + pixel-scale clustering in `utils/rig_optics.py`, `NAXIS1/2`, `XPIXSZ`, `FOCALLEN` | Current rigs include ASI294MM Pro (~5.1″/px), ASI1600MM, EOS R7/R8. FOV = width × height × scale. |
| **Filters owned per rig** | `filter_name` + `normalize_filter()` | L/R/G/B and Ha/OIII/SII (mono NB), and OSC DSLR. |
| **Project state** | `target_key` + per-filter integration (`/api/targets`), `target_goals` | 500+ targets. **378 have only 1–2 nights**, a handful are 10+ night projects (M51 39 h over 11 years, M31, IC1396…). **0 goals set**, so goals must be inferred, not required. |
| **Seasonality** | nights per month from `capture_date` | Nov 129 · Oct 94 · Apr 86 · … · Jun 33 · Jul 13. |
| **Session habits** | capture hour histogram | Most frames are 19:00–03:00, which gives a practical window narrower than full astro night. |
| **Conditions logged** | `HUMIDITY`, `WINDSPD`, `DEWPOINT`, `PRESSURE` (~13k NINA frames) | Could later tie outcomes to conditions. |
| **Frame quality** | `HFR/FWHM` in only 114 frames | Not usable yet. Would need AstroCat-computed star metrics. |
| **Catalog pool** | Messier 110, NGC 13,969 (12,005 with size, 10,227 with surface brightness), Caldwell 109, Sh2 313 | Good enough for a candidate pool. Sh2 is important for a narrowband imager. |

### The latitude matters more than anything generic tools model
At ~56°N the Sun never gets below −18° from roughly **early May to mid-August**, and around
the solstice it doesn't get below −12°. A generic "astro night" planner returns **nothing**
for three months. The history shows the user still images then (May 48 / Jun 33 / Jul 13
nights). A tailored engine degrades gracefully: it uses nautical darkness, favours bright
and narrowband targets, and says so.

## 4. Proposed engine

### 4.1 Stage A — context (per night, cached)
- **Site**: default from Settings, or the dominant `SITELAT/SITELONG` cluster, selectable.
- **Dark window**: Sun altitude curve → astro / nautical intervals, intersected with the
  user's **habitual session window** (learned from capture hours, overridable).
- **Moon**: altitude, illumination and position over the night (astropy `get_body`, which is
  already a dependency; F7 uses the same maths).
- **Horizon**: user-supplied (import N.I.N.A. `.hrz`) **or learned**. For each azimuth bin,
  take a low percentile of altitudes actually imaged. That is an "at least this is clear"
  envelope. The user can raise or lower it. Absence of data ≠ obstruction, so unlearned bins
  fall back to the global learned floor (~30°).
- **Rigs**: active rigs (seen in the last N months) with FOV and filter set.
- **Weather (optional, later)**: cloud cover by hour from Open-Meteo (free, no key). This is
  the only external call, and it should be opt-in.

### 4.2 Stage B — feasibility filters (hard)
Drop a (target, rig) pair if any of these hold:
- usable time above `max(horizon(az), learned_min_alt)` during the dark window < `min_minutes`
  (default 60, learned from the typical per-target session length);
- the object is far too small for the rig (major axis < ~20 px at the rig scale) or too big
  (> ~3× FOV — send it to the *mosaic* lane instead, linking to F16);
- every filter class the rig has fails moon avoidance (Lorentzian, per filter class:
  broadband D≈120°/W≈14 d, narrowband D≈40°/W≈7 d, as starting defaults; make them tunable);
- the user dismissed or snoozed it.

Pure numpy for alt/az (LST + spherical trig) over ~14.5k objects × ~100 time steps ≈ 1.5M
evaluations. That takes milliseconds, and astroplan isn't needed. Precompute nightly with
Celery beat, cache in Redis, and re-rank per request.

### 4.3 Stage C — score (soft, explainable)
Each component is normalised to 0–1 and emits a human-readable reason chip.

| Component | Definition | Why it's tailored |
|-----------|-----------|-------------------|
| **Observability** | Airmass-weighted usable hours tonight: `∫ w(alt) dt`, with `w = 1/airmass²` clipped | Uses *their* horizon and floor, not 30° |
| **Sky quality** | Moon penalty per filter class (Lorentzian margin, or later the Krisciunas–Schaefer sky brightness) plus twilight class | Narrowband rigs score well on moonlit nights |
| **Framing fit** | Object size / FOV ratio: best at ~0.3–0.7, penalised at the extremes | Uses measured rig scale, not a typed-in scope |
| **Project value** | Marginal SNR gain from tonight's likely hours: `sqrt((t+Δ)/t) − 1`. Boosted when an inferred goal is close or a palette is incomplete (Ha+OIII but no SII; L but thin RGB) | Only possible with per-filter history |
| **Seasonal urgency** | Share of remaining usable nights this season, from the historical clear-night rate per month × nights left before the target drops below the horizon for the year | "Last good month for IC1396 until next autumn" |
| **Affinity** | Similarity to what the user images: object-type/catalog/size/constellation profile weighted by hours (content-based filtering) | Recommends Sh2 nebulae to a Sh2 imager, not planetary nebulae |
| **Novelty / revisit** | New objects near favourite regions, *or* old targets shot with a much coarser/older rig ("M33: 2015 DSLR at 11″/px → ASI294 at 5″/px") | Uses rig history over time |
| **Feedback** | Learned from dismiss/pin/"imaged it" actions | Closes the loop |

`score = Σ wᵢ·cᵢ`, starting from hand-tuned weights. Then diversify the final list (MMR:
don't return five Cygnus nebulae), and group it into **lanes** so it reads like advice
rather than a table:

1. **Continue a project**: partial targets, highest marginal value tonight
2. **Last chance this season**: urgency-driven
3. **Moon-proof tonight**: narrowband picks when the moon is up
4. **New for you**: affinity + novelty
5. **Revisit with better gear**
6. **Needs a mosaic**: too big for any rig (ties into F16)

Plus one **hero pick** at the top with a GO / MARGINAL / DON'T-BOTHER verdict and its reasons.
Clear Night Coach showed that a single committed pick is what users actually act on.

### 4.4 Inferred goals (so the user doesn't have to set any)
With 0 `target_goals` rows, "percent complete" has to be inferred:
- the default goal per object class is the user's own median final integration for that class
  (e.g. emission nebula NB 15 h, galaxy LRGB 10 h), taken from targets that have masters (F2
  already tracks master presence);
- palette completeness: expected filter mix per rig type;
- an explicit `target_goals` row always wins.

## 5. How to know it's good: backtesting on the user's own history
AstroCat has ~790 imaging nights with known choices. For each historical night, run the
recommender *as of that date* (history truncated to before it, site/rig from that night) and
measure **hit-rate@k**: did the target actually imaged appear in the top k? That gives an
offline metric to tune weights before any UI exists. Caveat: it rewards imitating past habits,
so hold novelty/revisit lanes out of the metric and judge them by feedback instead.

## 6. Fit with the codebase

- **Pure core** (per `docs/design/README.md` §5): `backend/app/services/recommend/`, split into
  `ephemeris.py` (vectorised alt/az, sun/moon, dark windows), `horizon.py`, `profile.py` (site,
  rigs, habits, affinity from history), `scoring.py`, `lanes.py`. No DB in the core, so it can
  be unit-tested like `test_targets.py`.
- **New tables** (one migration after F7's): `observing_sites` (or extend F7's settings default
  site), `site_horizons` (az-bin → alt), `recommendation_feedback` (target_key, action, until).
- **Task**: nightly Celery beat job precomputes the context + feasibility for tonight and the
  next ~30 nights. Results go to Redis (`cache:recs:<site>:<date>`).
- **API**: `GET /api/recommendations?date=&site=&rig=&lane=`,
  `GET /api/recommendations/season?months=3`, `POST /api/recommendations/feedback`,
  `GET/PUT /api/sites/{id}/horizon` (with `.hrz` import).
- **Frontend**: a "Tonight" page plus a Dashboard tile. Cards show an altitude sparkline with
  the moon, dark-window shading and the horizon; a FOV-over-object framing sketch; reason chips;
  and actions (Pin / Snooze / Not interested / Open target). Uses recharts and lucide per
  conventions.
- **Optional LLM briefing** later: feed the *deterministic* ranked output + reasons to Claude
  for a one-paragraph nightly plan. The ranking must never depend on it.

### Prerequisites and dependencies
1. **F7** must land first, or at least its site-persistence slice: the recommender needs
   `images.site_latitude/longitude` populated and a default site in Settings. F7's
   moon-illumination code should be shared, not duplicated.
2. **Timestamp hygiene**: the hour histogram has thousands of 07:00–17:00 light frames, which
   points to local-time EXIF stored as UTC (or the reverse). Learned horizons and session
   windows need correct UTC. Prefer `DATE-OBS`, use EXIF `OffsetTimeOriginal` where present,
   and exclude frames whose computed Sun altitude is > −6°.
3. F2 (done) provides targets, per-filter integration, masters and goals.
4. F16 is optional; it only feeds the mosaic lane.

## 7. Suggested phasing
| Phase | Scope | Value |
|-------|-------|-------|
| **P0** | Data foundations: canonical target keys, `NONE` sentinel, capture-time provenance + UTC, site persistence. Spec: [P0-R0-equipment-sites.md](P0-R0-equipment-sites.md) | Correct inputs; also fixes Targets totals today |
| **R0** | Equipment & Sites page (§8a); spec as above: cameras/optics/filters/rigs/sites, detected from history and user-confirmed, `images.rig_id` | Declared gear the recommender (and Targets/Stats) can rely on |
| **R1** | Site + dark window + learned floor/horizon + moon Lorentzian + framing fit + "Continue a project" and "Last chance" lanes, with reasons. Backtest harness. | Already beats every generic tool for this user |
| **R2** | Affinity/novelty/revisit lanes, inferred goals, palette completeness, feedback table, Dashboard tile | Personal discovery |
| **R3** | Season planner (allocate expected clear nights across projects over the next 3 months), weather forecast opt-in, `.hrz` import/export to N.I.N.A. | Planning, not just tonight |
| **R4** | Optional LLM nightly briefing, export to N.I.N.A. Target Scheduler | Convenience |

## 8. Open questions for the user
1. Sites: are the two ~56°N clusters home + a dark site, and should the recommender
   plan for both?
2. Which rigs are "active" now? Is the DSLR data historical only?
3. Is an outbound weather call (Open-Meteo) acceptable, or should it stay fully offline?
4. Should seasonal urgency plan across *all* nights, or only nights the user historically
   images (weekday patterns)?

## 8a. Equipment & Sites page (prerequisite "R0")

The prototype's rigs were hand-typed from a query, and it couldn't tell which rig is mounted.
Recommendations need declared equipment, so an **Equipment** page comes before R1.

**Principle: detect, then confirm.** Header data alone isn't enough (`TELESCOP` is often
"EQMod Mount", and DSLR raws have no pixel size). Manual entry alone throws away what the
library already knows. So the page proposes rigs mined from history and the user confirms,
names and corrects them. After that, every value is declared and cross-checked against
plate solves.

**Model** (new tables, one migration):
- `cameras`: name, sensor width/height (px), pixel size (µm), mono/OSC (Bayer pattern),
  `match_patterns` (header `INSTRUME`/EXIF model strings that identify it), optional QE/read
  noise. A small seed list of common sensors is pre-filled; it replaces
  `rig_optics.KNOWN_PIXEL_SIZES_UM`.
- `optics`: name, kind (OTA/lens), aperture (mm), focal length (mm), plus optional reducers /
  flatteners / Barlows as named multipliers.
- `filters`: name, normalised bucket (`normalize_filter`), bandwidth (nm), `match_patterns`.
- `rigs`: camera + optic (+ reducer) + default binning + filter set + `is_active` +
  `is_mounted` ("current rig" for tonight) + optional mount limits (meridian, zenith).
- `sites`: name, lat/lon/elevation, Bortle/SQM, horizon (`.hrz` import or the learned profile
  from §4.1), default flag. This absorbs F7's "default site in Settings".
- `images.rig_id` (nullable): assigned by matching camera pattern + solved scale against the
  declared rigs, within tolerance. It reuses the clustering in `rig_optics.py`.

**Computed, never entered:** pixel scale = 206.265 × pixel µm × bin / FL; FOV = px × scale;
f-ratio; sampling against seeing (use the user's typical seeing, default 2.5″) with
over/under-sampling warnings; relative speed between rigs (∝ 1/f²).

**Cross-check:** compare the declared scale with the median plate-solved scale of the rig's
images. A mismatch over ~5% suggests a wrong focal length, a forgotten reducer or wrong binning.
This is the same check that makes derived focal lengths in "By Filter & Rig" trustworthy.

**What it unlocks beyond recommendations:**
- TargetDetail "By Filter & Rig" shows rig names instead of camera + scale clusters.
- Search and Stats can filter by rig.
- The DSLR pixel-size gap closes.
- Sites feed F7 sessions.

**Recommender use:**
- The rig picker defaults to the `is_mounted` rig, with a "best rig per target" view as an
  alternative.
- The rig's filter set drives NB/BB mode and per-filter moon rules.
- FOV drives framing and the mosaic lane.
- Sampling drives the small-target penalty.
- Mount limits become feasibility filters.

## 8b. Alternative: external candidate source (hybrid)

Of the tools surveyed, only **Telescopius** has a usable public API (v2.2, `Authorization: Key …`,
at most 2 keys per user; terms, pricing and rate limits are not published in the spec). The rest:
- AstroBin: read-only image API, useful only as a popularity signal.
- N.I.N.A. Target Scheduler: a local SQLite database, so an *output* target rather than a source.
- SkyTools: desktop app, no API.
- Nova: self-hosted app, not a data source.
- Clear Night Coach: no public API found.

`GET /targets/search` covers most of the "sky" half of §4:
- Location/timezone and session hours (`hour_min/max` accept `astronomical_sunset`,
  `nautical_sunset`, …).
- `min_alt` + `min_alt_minutes`, and `az_quadrants` as a coarse horizon.
- `moon_dist_min` with **per-filter Lorentzian presets** (`narrowband`, `ha_s2`, `o3`, `lrgb`).
- `size_min/max` (derivable from rig FOV), `subr`, `mag`, `cat`, `types`.
- `order=imaging_time|popularity`, `ephemeris=yearly`.
- Up to 120 results per page.

Each result has **cross-IDs** (`NGC 7000`, `SH 2-117`, `C 20`), which map straight to
`target_key` via `normalize_designation`. It also has fine-grained types (`eneb`, `rneb`, `h2r`…),
which fixes the reflection/emission bug in §9. Plus sizes, and per-window `imaging_time_hours` with
moon illumination and distance. `/weather/forecast` and `/equipment/user` exist too. API keys are for **Patreon
patrons/sponsors only** (forum, Feb 2025).

**`/equipment/user` (verified with the user's key, 2026-09-26).** The spec documents no response
schema. The real response has lists: `telescopes`, `cameras`, `filters`, `mounts`, `accessories`,
`software`, `eyepieces`, `binoculars`, `lenscams`. The useful fields are:
- telescopes: `aperture`, `focal_length_min/max`, `f_number`, `label`
- cameras: `custom_sensor_width/height_mm`, `sensor_width/height_px`, `is_color`, `is_cooled`, `label`
- filters: `filter_type_id`, `label`
- mounts: `mount_type_id`, `model_name`

What's missing: there are **no rigs/combinations, no reducers** (`accessories` is empty), **no pixel
size** (it can only be derived from mm/px, which gives ~2% error: the ASI1600 comes out at 3.72 µm
instead of 3.8), and `is_color` is null for every camera. The user's list is also partly stale
(includes old scopes) and incomplete: it lacks the EOS R7, the EF 24-105 and the LRGB filters.

Paired with solved pixel scales, it still names detected rigs correctly:
- ASI1600MM @ 2.27″/px → Zenithstar 73 (346 mm), predicted 2.27
- ASI294MM @ 0.34″ → C11 EdgeHD (2800 mm), predicted 0.34
- ASI294MM unlocked 8288 px @ 2.46″ → EF200, predicted 2.39
- EOS R7 @ 6.5″ → Sigma 105, predicted 6.3

**Hybrid run, night of 2026-09-26 (~23 API calls).** Nine calls pulled the top 360 targets by
popularity per Moon preset (`ha_s2`, `o3`, `lrgb`). Thirteen more were name lookups for the
user's own narrowband projects. What the live API showed:
- Results come back under `page_results`, not `objects`. `major_axis` is in **arcsec**.
  Popularity order needs `order=popularity&order_asc=1`; `order_asc=0` returns obscure Zwicky
  clusters first.
- `imaging_time_hours` is **just the dark window** (7.83 h for everything, including objects that
  peak after dawn). It ignores `min_alt`, so hours above the floor must be computed locally. That's
  trivial from transit time + dec.
- Implied preset thresholds tonight (full Moon), measured as the smallest distance that passed:
  `ha_s2` ≈ 59.3°, `o3` ≈ 88°, `lrgb` ≈ 115°. Distance is measured **at transit only**.
- **A hard external filter drops the active project.** NGC 7000 sits at 59.2° and is excluded,
  while the adjacent Pelican (60.4°) passes. The same happens to the Bubble, IC5068, NGC281, the
  Cocoon and others. The hybrid must (a) add the user's projects back via name lookups and
  (b) treat the Moon as a soft penalty relative to the preset threshold.
- **Duplicate entries** of one nebula: IC1396 / Sh2-131 / LBN 455, IC1805 / LDN 1373,
  IC410 / Sh2-236. The duplicates split the history (`SH2131` holds 1.9 h separate from `IC1396`).
  Nebula-sized objects typed as their **embedded cluster** get the cluster's size (Heart 13′,
  Soul 24′). Merge by overlapping centre and take the largest size.
- Type lists are multi-valued. Use the **primary (first) type**: the Iris lists `eneb` among its
  types but is primarily `rneb`.
- Unnamed LBN/LDN entries crowd "new for you" unless a popularity/catalog prior is applied.

**Design:** "Import from Telescopius" seeds the component lists (optics, cameras, filters, mount).
AstroCat then proposes rigs by matching each detected camera + solved-scale cluster to the optic
whose focal length predicts that scale within ~5%. The user confirms and fills the gaps (reducers,
mono/OSC, missing gear). The AstroCat rig table stays the source of truth.

**Hybrid design:** Telescopius supplies candidates and sky facts. AstroCat does the re-ranking:
project value, momentum, affinity, revisit, rig framing, lanes and reasons. That's the part
nobody else can do. It sits behind a `CandidateSource` interface so a local ephemeris source can
be added later.

Trade-offs:
- It needs a key and the internet, and sends the site location to Telescopius.
- There's a Cloudflare-blocking precedent for server IPs (forum, Apr 2026).
- Moon distance is **at transit only**, not across the night.
- The horizon is limited to min-alt + quadrants.
- Backtesting ~790 past nights would take hundreds of paged calls, so it needs aggressive caching
  or a cut-down backtest.

Effort: R1 falls from ~0.9–1.2M to **~0.5–0.7M** tokens, and R0 can shrink to a "lite" rig
table (~0.3–0.45M).

## 9. Prototype run (night of 2026-09-26, full Moon)

A throwaway prototype of the R1 logic was run against the live DB inside the backend container.
Learned inputs: a site from the header cluster, and a floor of 32° (5th percentile of `CENTALT`).
The learned horizon in 30° azimuth bins came out as 42/42/42/24/40/39/39/32/24/32/42/42, with
the lowest bins at east (90–120°) and south-west (240–270°). Four rigs, 1,483 deduplicated
candidates. Astronomical dark was 20:15–04:00 UTC, and the Moon was 99.8% lit and up the whole time.

- Only narrowband was feasible: 126 targets. The OSC R7, the most recently used rig, had **0**
  feasible targets, which is itself a useful verdict.
- The top pick was **C20 North America Nebula** ("active project": last shot 32 days ago, 6.4 h
  over 11 nights) on the ASI294MM + 200 mm, with 6.8 h clear of the Moon. The Moon was 59° away:
  enough for Ha/SII (D=40), not enough for OIII (D=70). **Per-filter moon rules matter.**
- Next came under-goal projects such as C19 Cocoon, IC63, C34 West Veil, NGC281 and IC1805.
  The "new for you" picks were mostly Sharpless HII regions in Cas/Cep (Sh2-199, -124, -154,
  -155), which matches the user's affinity (41% of hours on emission nebulae).

What the first pass got wrong, and what the real design must handle:
1. **Nebula type is unreliable in the NGC data.** Generic `Neb` / `Cl+N` types, and even `HII`
   for the Pleiades' Maia Nebula, made reflection nebulae look moon-proof. Fix: treat a nebula as
   emission only if it's confirmed (HII/SNR type, or a matching Sharpless region), keep a small
   curated reflection-nebula override list, and otherwise assume broadband.
2. **Sizes need cross-catalog merging.** NGC gives IC1396 as 14′ (the core cluster); Sh2-131 gives
   170′. Take the largest size of the merged aliases. Also merge history across aliases:
   M81/NGC3031 and C11/NGC7635 are separate `target_key`s today.
3. **Recency ("momentum") is the strongest signal of what the user wants.** Without it, a target
   shot last month ranked below one-night tests from 2016.
4. **A single 1-night attempt isn't a project.** Require ≥2 nights, ≥2 h, or recent activity
   before the diminishing-returns term applies. Otherwise tiny histories dominate.
5. **"Revisit with better gear" needs a real definition.** Finer pixel scale alone flagged
   everything for the C11 (0.34″/px is oversampled for typical seeing). Define it as: old and short
   data, and little or no narrowband when narrowband is now possible. Penalise heavy oversampling.
6. **Learned horizon must be capped.** "Never imaged low in the north" came out as a 60° wall.
   Cap learned bins at floor + 10° until the user confirms or imports a real `.hrz`.
7. **The engine doesn't know which rig is mounted.** It must ask ("which rig tonight?") or rank
   per rig. The prototype reports the best picks per rig.

## Sources
- Telescopius "What's in the sky tonight" parameters: https://forum.telescopius.com/t/what-are-the-parameters-for-whats-in-the-sky-tonight/855
- N.I.N.A. Target Scheduler, planning/scoring engine: https://tcpalmer.github.io/nina-scheduler/concepts/planning-engine.html
- Target Scheduler exposure templates (moon avoidance): https://tcpalmer.github.io/nina-scheduler/target-management/exposure-templates.html
- ACP Scheduler constraints (Moon-Avoidance Lorentzian): http://solo.dc3.com/ar/RefDocs/HelpFiles/ACPScheduler81Help/Constraints.htm
- BAIT request files (origin of the Lorentzian): https://w.astro.berkeley.edu/bait/baitman/request.html
- SkyTools Nightly Planner: https://skyhound.com/nightly_planner.html
- Astro Imaging Planner: https://hub.docker.com/r/gshau/astroimaging-planner
- Nova DSO Tracker: https://hub.docker.com/r/mrantonsg/nova-dso-tracker
- Clear Night Coach: https://clearnightcoach.com/
