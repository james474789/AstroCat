# Handover: Target Recommendations. P0 done; R0 → R4 remaining

Written: 2026-09-26 · Author: Claude (orchestrating session) · For: the agent or developer picking up R0 next

`main` HEAD: **`878ca7f`** (local; **not pushed**). Live stack: backend **`20260926.14`**, healthy.
Alembic head: **`d7f9b3e41007`**. Data migrations applied: `0001`–`0007`.

## 0. Read in this order

1. **This file**: current state, what changed from the plans, and the traps.
2. [target-recommendations-research.md](target-recommendations-research.md): why the feature exists,
   competitor survey, engine design (§4), phasing (§7), the Equipment page rationale (§8a),
   Telescopius API findings (§8b), prototype lessons (§9).
3. [P0-R0-equipment-sites.md](P0-R0-equipment-sites.md): **the spec for the next step (R0 = §4)**.
   §3 (P0) is done; see §2 below for where the shipped code differs from it.
4. [README.md](README.md) §3–§5 (defensive migrations, merge hotspots, conventions), and
   [HANDOVER.md](HANDOVER.md) (the F1/F2 handover: deploy notes, Windows quirks, agent pattern).
   Both still apply.
5. `CLAUDE.md`: "Shipping a One-off Data Repair" (data migration registry) and "Deploying Changes".

## 1. Roadmap and status

| Step | Scope | Status | Estimate (tokens) |
|---|---|---|---|
| Research + prototypes | Survey, live-data analysis, local and Telescopius-hybrid prototypes | ✅ done | — |
| **P0** | Canonical target keys + Sh2 cross-IDs + `NONE` sentinel, capture-time provenance, site persistence | ✅ **deployed & verified** | actual: ~250k agent + ~150k orchestration |
| **R0** | Equipment & Sites: spec §4 | ⏭ **next** | 0.65–1.05M incl. deploy |
| R1 | Local recommendation engine + replay test on past nights + Tonight page | not started | 0.8–1.1M |
| R2 | Affinity/novelty/revisit lanes, inferred goals, feedback, Dashboard tile, optional Telescopius enrichment | not started | 0.5–0.7M |
| R3 | Season planner, opt-in weather, `.hrz`/Target Scheduler export | not started | 0.6–0.9M |
| R4 | Optional LLM nightly briefing | not started | 0.2–0.4M |
| F7 / F16 | Sessions / mosaics (older design docs) | not started | F7 should build on R0's `sites` table |

**Agreed direction (owner, 2026-09-26):** calculate each night locally in AstroCat. Telescopius is
an *optional* enrichment and equipment-import source, never a dependency. Reasons: the published
image can't require a paid Telescopius key; the replay test needs local maths; and Telescopius's
per-night numbers turned out weak (research §8b).

## 2. What P0 shipped (and how it differs from the spec)

Commits on `main`:
- `57d1714` targets
- `90f9174` indexer fix
- `d0c070b` capture time
- `b056389` sites
- `431bd46` data migrations
- `84cddc5` docs
- merge `8606b59`
- `47fba76` Barnard's Loop fix (merge `e7d4023`)
- VERSION bumps `4b89fc2` and `878ca7f`

**Code map:**
- `services/targets.py`:
  - MATCH resolves through the alias index.
  - Unresolved lights → `(None, 'NONE')`; `NONE_SOURCE`.
  - `sh2_cross_id_details()` / `sh2_cross_ids()`, with `SH2_CROSS_ID_*` constants and
    `SH2_CROSS_ID_OVERRIDES`.
  - `build_alias_index` records applied pairs on `index.sh2_cross_ids`.
- `scripts/recanonicalize_targets.py`: `recanonicalize_targets()`, `mark_unresolved_lights_none()`,
  and **`reresolve_target_keys(keys)`**. Use the last one to undo a bad cross-ID merge: MATCH/HEADER
  rows are re-derived from matches and header text.
- `utils/capture_time.py`, `scripts/backfill_capture_time.py`, and columns
  `images.capture_date_utc` (indexed) and `images.capture_time_source`.
- `utils/header_values.py` (`unchar`, `parse_sexagesimal`, `parse_exif_gps`, …),
  `scripts/backfill_sites.py`. Extractors now emit `site_lat`/`site_long`/`site_name`.
- `tasks/indexer.py`: one P0 hook after the new/existing branch sets capture-time and site columns.
- `services/data_migrations.py`:
  - `0004_canonicalize_target_keys`
  - `0005_capture_time_provenance`
  - `0006_image_site_coordinates`
  - `0007_split_barnards_loop`

**Differences from the spec (the spec text was not updated; trust this list):**
1. **An extra source value, `capture_time_source = 'OTHER'`** (UTC stays NULL), for dates taken from
   FITS `DATE` or EXIF `Image DateTime`. Order of precedence: `FILE_MTIME` if the indexer used the
   mtime fallback, then `GPS_UTC`, then `EXIF_OFFSET`, then `EXIF_LOCAL`.
2. **Site parsing reads more headers** than the spec: `OBSGEO-B/L`, `LAT-OBS/LONG-OBS`, and Pillow
   `PIL:GPSInfo`. `SITELONG` is treated as east-positive, with no correction for writers that use
   west-positive.
3. **Before the NONE sentinel pass, 0004 runs `backfill_targets(process_all=False)`**, so
   never-resolved lights go through the new resolver.
4. **Sh2 overrides:** five forced pairs (117→NGC7000, 125→IC5146, 131→IC1396, 162→NGC7635,
   190→IC1805) plus **`Sh2-276: None`**.
5. **Separation cap, `SH2_CROSS_ID_MAX_SEP_DEG = 1.0`** (not in the spec). Without it, Barnard's
   Loop (Sh2-276, 600′) matched NGC1981 1.97° away. 0007 re-split 22 frames. The largest genuine
   pair is Sh2-220/NGC1499 at 0.58°.
6. **A live bug fixed along the way (`90f9174`):** a function-local `from app.models.image import
   ImageSubtype` in `_process_image_impl` made every **new-file insert** raise `UnboundLocalError`
   after F1. It wasn't triggered in practice, since only 3 images have been added since 25 Sep.
   **No end-to-end check of a new file has happened yet.** Do one when convenient (the owner has
   to drop a file in, or use a scratch DB).

**Verified live numbers after P0** (use them as regression anchors):
- **Targets:** M81 1,391 · M31 1,731 · M33 1,169 · M51 2,354 · NGC7635 648 · IC1396 622 ·
  NGC7000 315 · SH2276 22 · NGC1981 181. There are 504 distinct light targets (561 before P0). No
  light has `target_source IS NULL`; 52,689 have `NONE`.
- **`capture_time_source` (all images):** FITS_UTC 46,007 · FILE_MTIME 23,331 · EXIF_LOCAL 16,512 ·
  GPS_UTC 7,040 · OTHER 774 · EXIF_OFFSET 69. One FITS row has `capture_date` about 1 h off from
  `DATE-OBS`; `capture_date_utc` follows `DATE-OBS`, which is correct.
- **Sites:** 31,551 lights have `site_latitude/longitude`. The clusters are ~56°N (the owner's two
  home/nearby sites, ~24.3k + ~0.5k frames) and a few smaller remote clusters (R0 detection proposes them and the owner decides).
- **0004 summary:** 45 Sh2 pairs (40 rule, 5 override), 73 keys remapped, 4,065 rows. Review it with
  `SELECT result::json->'sh2_cross_ids' FROM data_migrations WHERE id='0004_canonicalize_target_keys';`

## 3. Next step: R0 (spec §4), and how to run it

Execution pattern (proven twice). One orchestrator, and agents in isolated git worktrees that
**must not touch Docker, the live DB, or `main`, and must not push**:

1. **B1 backend** (Opus): spec §4.1–4.8 + tests.
   - Alembic `e8a0c4f51008`, `down_revision = 'd7f9b3e41007'` (the current head).
   - Add `tzdata` to requirements.
2. **B2 frontend** (Sonnet): spec §4.9 against the §4.6 API contract. It can run in parallel with B1.
3. **Orchestrator:**
   - Trial-merge B1 then B2 into a throwaway branch.
   - Full `pytest`, plus `npm run build` and `npm run lint`.
   - Back up the DB, merge to `main`, bump VERSION, and rebuild **backend and frontend**.
   - Browser walkthrough (the owner logs in).
   - Verify spec §7 criteria 5–9.

R0 facts that are already known, so don't re-derive them:

**Rigs that detection must reproduce:**

| Optic | Camera | Solved scale | Predicted |
|---|---|---|---|
| Zenithstar 73, 346 mm | ASI1600MM Pro | 2.27″ | 2.27″ |
| C11 EdgeHD, 2800 mm | ASI294MM Pro | 0.34″ | 0.34″ |
| EF200 | ASI294MM Pro, unlocked 8288×5644, 2.315 µm | 2.46″ | 2.39″ |
| Sigma 105 | EOS R7 (OSC) | 6.5″ | 6.3″ |

Recent use (last 2 years):
- R7: 2026-08
- ASI1600: 2026-03
- C11 + ASI294: 2026-02
- EF200 + ASI294: 2025-01

**Camera-name mess detection must normalise:**
- `Canon Canon EOS R7` / `Canon EOS R7`
- `EOS R8` / `Canon [EOS R8]` / `Canon Canon EOS R8`
- blank names (21.8k rows at 400×504 px, 2.11″)
- `notAvailable`
- a phone (`samsung SM-S908B`)

Headers say `TELESCOP = "EQMod Mount"`, so ignore that field for optic identity.

**Telescopius `/equipment/user`** (response verified; the spec documents no schema):
- `telescopes[]`: `label`, `aperture`, `focal_length_min/max`, `f_number`
- `cameras[]`: `label`, `custom_sensor_width/height_mm`, `sensor_width/height_px`, `is_color` (null
  for every camera), `is_cooled`
- `filters[]`: `label`, `filter_type_id`
- `mounts[]`: `model_name`
- also `accessories`, `software`, `eyepieces`, `binoculars`, `lenscams`

There are **no rig combinations and no reducers**, and pixel size is only derivable from mm/px
(~2% error). The owner's list is partly stale (it has a CEM60 mount, but headers say EQMod) and
incomplete (no R7, EF 24-105 or LRGB filters). API access is for Patreon patrons only.

**Telescopius key:**
- Env only: `TELESCOPIUS_API_KEY`, read via `config.Settings`.
- Never stored in the DB or Redis, never returned by the API, never logged.
- A key was pasted in the research chat. **The owner has been asked to regenerate it**, so don't
  reuse anything found in transcripts; ask the owner to put the new key in `.env` themselves.

**Timezones:** `EXIF_LOCAL` / `FITS_LOCAL` rows get `capture_date_utc` only once a site with a
timezone is assigned (spec §4.5 "Timezone fill"). The owner's DSLR clocks appear to run on UTC/GMT
(EXIF matched GPS time within seconds in winter samples). Some R7 frames carry
`OffsetTimeOriginal +02:00`. Handle both; don't assume.

## 4. After R0: R1 engine notes (from the prototypes)

Prototypes are in [prototypes/](prototypes/). They're throwaway reference code, not production:
- `rec_local_prototype.py`: local ephemeris (astropy for the Sun and Moon, numpy for 14k objects),
  learned horizon, per-filter Moon Lorentzian, framing, project/urgency/momentum/affinity scoring,
  lanes.
- `rec_telescopius_prototype.py`: Telescopius candidates re-ranked with history. It includes the
  duplicate-nebula merge, soft Moon penalty, and local hours-above-floor.

Both ran inside the backend container (`docker cp` + `docker exec python`) against the live DB,
read-only.

Lessons that the R1 spec must encode (research §9, §8b):
- **The Moon is a soft penalty, per filter class** (Ha/SII vs. OIII vs. broadband). On the full
  Moon of 2026-09-26, NGC7000 at 59.2° missed the Telescopius Ha/SII limit (59.3°) by 0.1° and was
  dropped by a hard filter, despite being the active project.
- **Recency ("momentum") is the strongest intent signal.** A 1-night attempt isn't a project
  (require ≥2 nights, ≥2 h, or recent activity). Diminishing returns: `sqrt((t+Δ)/t) − 1`.
- **Nebula type is unreliable in OpenNGC data** (`Neb`, `Cl+N`, and `HII` for the Pleiades' Maia).
  Treat a nebula as emission only if it's confirmed (HII/SNR type, or an Sh2 cross-ID); keep a
  curated reflection override list; otherwise treat it as broadband.
- **Size:** take the largest across merged aliases (IC1396 is 14′ as a cluster vs. 170′ as Sh2-131).
- **The learned horizon must be capped** at floor + 10° (absence of data ≠ an obstruction).
  Prototype values: floor 32.3°, E and SW 24°.
- **Latitude ~56°N means no astronomical darkness from about May to mid-August.** Fall back to
  nautical darkness and say so. The owner still images in that season (94 nights May–Jul).
- **Replay test:** ~790 historical imaging nights. For each, run the engine as of that date and
  measure hit-rate@k. Keep the novelty/revisit lanes out of the metric.
- **The "which rig is mounted" problem** is solved by R0's `is_mounted`. Default to it, and offer
  "best rig per target".

## 5. Environment and process traps

- **Tests on the host** need dummy env vars, or pydantic Settings fails at collection:
  ```bash
  cd backend && SECRET_KEY="test-only-0123456789abcdef0123456789abcdef" DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/test" python -m pytest tests/ -q -p no:cacheprovider
  ```
  Baseline on `878ca7f`: **354 passed, 1 failed**. The failure is pre-existing:
  `test_reindex_backfill.py::test_reindex_all_runs_incremental_backfills_after_scan` tries to reach
  a real Postgres. It fails on `main` too, so it isn't a regression.
- **Deploy:**
  - `docker compose build backend && docker compose up -d backend`.
  - The container runs `initialize_db` (which runs `alembic upgrade head`) on start.
  - The Celery worker then runs pending data migrations in the background. Watch them with:
    ```bash
    MSYS_NO_PATHCONV=1 docker exec astrocat-db-1 psql -U AstroCat -d AstroCat -At -c "select id, status, duration_seconds from data_migrations order by id"
    ```
  - Health check: `curl http://localhost:6089/api/health` (backend host port **6089**; frontend
    **6090**).
- **Back up before any migration that rewrites data:**
  ```bash
  MSYS_NO_PATHCONV=1 docker exec astrocat-db-1 pg_dump -U AstroCat -d AstroCat -Fc -f /tmp/x.dump
  ```
  Then `docker cp` it into `library/backups/` (the db-backup container's volume). The pre-P0 dump is
  `library/backups/pre_p0_20260926.dump`.
- **VERSION format** is `YYYYMMDD.NN`. The counter was already at `.12` when P0 started, so read
  the file; don't assume.
- **Git:**
  - Branch before committing (never commit straight to `main`); merge with `--no-ff`.
  - End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - **Nothing has been pushed**; the owner decides when.
- **Privacy:** the repo and Docker image are public. Don't put the owner's precise site coordinates
  in code, tests or docs. Use generic fixtures (e.g. 51.48, −0.0). "~56°N" is the agreed level of
  detail.
- **Raw-header quirk:** some JSONB string values are arrays of single characters. Always
  `header_values.unchar()` before parsing.
- **Windows / git-bash:** prefix `docker exec` commands that contain absolute container paths with
  `MSYS_NO_PATHCONV=1`.

## 6. Open items (not blockers)

| Item | Notes |
|---|---|
| Sh2-277 → IC434 | Sh2-277 is strictly the Flame (NGC2024) area. It was left merged because the owner frames the two together. Suppress it via `SH2_CROSS_ID_OVERRIDES` + `reresolve_target_keys(["IC434"])` in a new data migration if the owner asks. |
| New-file indexing | The fix is deployed but untested end to end (§2 item 6). |
| Remote site cluster | 6.5k frames; ask the owner when R0 detection proposes it. |
| UI label | `ImageDetail.jsx` shows "(auto: none)" next to "Unassigned" for `NONE` rows. Cosmetic; fix during B2. |
| 0001 summary | Contains a Counter with tuple keys that `json.dumps(default=str)` can't serialise. Pre-existing and untouched. |
| Targets "nights" | Still `date(capture_date - 12h)`. Switch to `capture_date_utc` + site longitude in R1. |
| Spec text drift | `P0-R0-equipment-sites.md` §3 doesn't mention the `OTHER` source, the separation cap or 0007; see §2 above. Its §8 estimates are superseded by §1 here. |
| Telescopius key | Regenerate (owner); set in `.env` only when R0's import is deployed. |
