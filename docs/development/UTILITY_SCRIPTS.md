# AstroCat Utility & Maintenance Scripts

Scripts are located in `backend/app/scripts/` and run inside the backend container:

```bash
docker exec AstroCat-backend python -m app.scripts.<script_name>
```

---

## Classification & Maintenance

| Script | Description |
|--------|-------------|
| `update_classifications` | Sets images in `/data/mount2/` to `INTEGRATION_MASTER` subtype. |
| `update_planetary` | Sets images in `/data/mount3/Planetary` to `PLANETARY` subtype. |
| `normalize_existing` | Normalizes object designations (e.g. "M 42" → "M42") across catalogs and matches. |
| `backfill_dimensions` | Populates missing `width_pixels`/`height_pixels` for images. |
| `fix_thumbnail_collisions` | Detects and resolves filename collisions in the thumbnail cache. |
| `backfill_frame_types` | (F1) Classifies `frame_type`/`frame_type_source` (Light/Dark/Flat/Bias/Dark-Flat) from stored `raw_header`/`file_path` -- no file IO. Resumable (only rows with `frame_type_source IS NULL` by default); `--all` reclassifies everything except `MANUAL` rows; `--dry-run` prints a summary without writing. Runs automatically on every backend startup (no-op once every row is classified). Also available as the Celery task `app.tasks.indexer.backfill_frame_types` and an Admin page "Reclassify frame types" button. |

## Catalog Management

| Script | Description |
|--------|-------------|
| `seed_named_stars` | Seeds the `named_star_catalog` table from `NamedStars.csv`. Truncates and re-inserts. |
| `rematch_all` | Re-runs catalog matching for all plate-solved images. |
| `rematch_catalogs` | Re-runs catalog matching with updated catalog data. |
| `rematch_debug` | Debug version of catalog rematching for troubleshooting individual images. |

## Troubleshooting & Diagnostics

| Script | Description |
|--------|-------------|
| `check_missing` | Compares files on disk against the database and reports unindexed files. |
| `check_db` | Health check: verifies tables exist and provides row counts. |
| `verify_count` | Quick summary of image counts, solved vs. unsolved, and format breakdown. |
| `debug_extractor` | Tests metadata extraction on a single file for debugging. |
| `monitor_progress` | Monitors the progress of a running bulk operation in real time. |

## Administration

| Script | Description |
|--------|-------------|
| `create_admin` | Creates an admin user account. |
| `initialize_db` | Runs database initialization (migrations + catalog seeding). |
| `reprocess_unsolved` | Re-queues unsolved images for astrometry processing. |

## Targets (F2)

| Script | Description |
|--------|-------------|
| `backfill_targets` | Resolves `target_key`/`target_source` for LIGHT subs that don't have one yet, in resumable keyset batches of 1000. Never overwrites a `MANUAL` target. Pass `--all` to re-resolve every non-MANUAL row (e.g. after a catalog reseed or alias index change). Clears `cache:targets:*` in Redis when done. Safe to run any time the indexer/backfill chain runs, after `backfill_frame_types` (F1) if present. |

See `docs/features/TARGETS.md` for the full target resolution design.

## Data foundations (P0)

These run automatically once, in the background, as data migrations `0004`-`0006`
(Admin > Data Maintenance shows their status and summary). Each is idempotent; run by
hand only for troubleshooting.

| Script | Description |
|--------|-------------|
| `recanonicalize_targets` | Re-keys non-MANUAL LIGHT rows to canonical target keys through the current alias index (`NGC3031` -> `M81`, `C11` -> `NGC7635`, Sh2 regions -> their NGC/IC nebula; resolvable `OBJ:` keys become `HEADER`), one bulk `UPDATE` per old key. Merges `target_goals` (keeps the larger goal per filter group) and clears `cache:targets:*`. Returns `{remapped_keys, rows_updated, goals_merged, remaps, sh2_cross_ids}`. A second run is a no-op. Data migration `0004_canonicalize_target_keys` also runs the `NONE` sentinel pass afterwards. |
| `backfill_capture_time` | (`backfill_capture_time()`) Fills `capture_date_utc`/`capture_time_source` from stored `raw_header` only (no file IO), batches of 1000. Treats `capture_date == file_last_modified` with no header date as `FILE_MTIME`. Resumable (rows with `capture_time_source IS NULL`); `--all` recomputes every row. Data migration `0005_capture_time_provenance`. |
| `backfill_sites` | (`backfill_image_sites()`) Fills `site_latitude`/`site_longitude`/`site_name` from stored `raw_header` only: `SITELAT`/`SITELONG` (decimal or sexagesimal), `OBSGEO-B`/`OBSGEO-L`, EXIF GPS; name from `SITENAME`/`OBSERVAT`. (0, 0) and out-of-range values are treated as missing. Batches of 1000; by default only rows with no site whose header has a site key; `--all` recomputes. Data migration `0006_image_site_coordinates`. |

## Equipment & sites (R0)

There is no script or data migration for R0: rigs and sites are created by the user
(Equipment page, detect-then-confirm), and assignment runs as the Celery task
`app.tasks.equipment.assign_equipment` (queued automatically, debounced, after any
camera/optic/rig/site change, or via `POST /api/equipment/assign?scope=unassigned|all`).
For troubleshooting inside the container: `python -c "from app.tasks.equipment import
run_assignment; print(run_assignment('all'))"` runs it synchronously and prints the summary
(`rig_changed`, `site_changed`, `utc_filled`, per-reason counts, `clock_modes`, `measured`).
See `docs/features/EQUIPMENT.md`.

## Recommendations (R1)

Data migration `0008_fill_utc_default_site` runs once, in the background: it re-runs
equipment assignment (`run_assignment('all')`) so local-clock frames without a site get
`capture_date_utc` from the camera's UTC clock or the default site's timezone, and records
`capture_utc_basis`. With no default site yet it records `{"skipped": "no default site"}`.

| Script | Description |
|--------|-------------|
| `replay_recommendations` | Read-only. Runs the recommendation engine "as of" every historical imaging night (>= 30 min of resolved light subs; history and goals strictly before the night) and reports hit@1/3/5/10, MRR and `feasible_recall` against recency / altitude / random baselines, on the headline set (HOME + UNKNOWN_SITE pairs; REMOTE pairs reported separately), with breakdowns by tier, Moon, year, known rig, site class and target source, and every miss with its reason and numbers. `--since/--until YYYY-MM-DD` filter nights; `--moon-bb D[,W]` sets the broadband/OSC Moon rule; `--grid` runs the 288-combination tuning grid (`--grid-max N` samples) and prints the top 5 by hit@5 with the recency baseline (nothing is applied); `--grid-moon` first picks the broadband/OSC Moon D from 60/90/120; `--out PATH` writes the JSON report there as well as to `<log_dir>/replay_latest.json` (served by `GET /api/recommendations/replay/latest`). Run inside the backend container: `python -m app.scripts.replay_recommendations --out /app/logs/replay.json`. |

See `docs/features/RECOMMENDATIONS.md`.
