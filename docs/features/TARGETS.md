# Targets (F2)

Status: **Shipped** (branch `feature/f2-targets`)

## What it is

The Targets dashboard answers "how much data do I have on this target, per filter?"
Every LIGHT sub-frame is resolved to a single **primary target** (`target_key` +
`target_source` on `Image`), and the `/targets` page aggregates integration time,
sub count, per-filter breakdown, nights, date span, rigs, and master presence per
target - in contrast to the Catalogs page, which still counts "every image whose
field contains the object" (unchanged).

## Data model

- `images.target_key` (String(64), indexed): canonical target identifier, or `NULL`
  for unassigned. For catalog objects it's the canonical designation (`M31`,
  `NGC7000`, `C14`); for anything else it's `OBJ:<normalized header text>`
  (`OBJ:SH2155`, `OBJ:CYGNUSWALL`).
- `images.target_source`: `MANUAL` | `HEADER` | `MATCH` | `HEADER_RAW` | `NULL`.
- `target_goals` table: optional per-target, per-filter (or `ANY`) integration
  goals in seconds.

## Resolution order (first hit wins)

1. **MANUAL** - a user override (via the UI or bulk-assign) is never touched by
   automation again.
2. **HEADER** - the FITS/EXIF `OBJECT` header resolves through the catalog alias
   index (Messier/NGC/IC/Caldwell designations and common names, plus named
   stars). Mosaic/panel suffixes (`Panel 2`, `_P2`, `-mosaic-3`, `[2]`) and text
   after ` - `/`(` are tried too, so `M31 Panel 2` and `M42 (Orion)` resolve.
3. **MATCH** - for plate-solved images, the nearest **central** catalog match
   (excluding `NAMED_STAR`) among `is_in_field` matches, only if its separation
   is ≤ 0.35 × the field radius. Ties break on catalog priority (Messier > NGC/IC
   > Caldwell) then brighter magnitude.
4. **HEADER_RAW** - header text that didn't resolve becomes `OBJ:<normalized>`.
5. Otherwise `target_key = NULL` ("Unassigned").

Only `frame_type = LIGHT` images get a target; everything else is cleared to
`(NULL, NULL)` unless it's `MANUAL`. `PLANETARY` images get a target like any
other light but are excluded from integration sums (shown as `planetary_count`
instead). Masters are excluded from integration sums too - integration comes
from subs only, per the design decision that a master's header-derived
`NCOMBINE x EXPTIME` shouldn't be double-counted or trusted over the actual subs.

Implementation: `backend/app/services/targets.py`. `normalize_designation`,
`AliasIndex`, and `resolve_target` are pure functions with no DB access
(`backend/tests/test_targets.py`); `assign_target_sync`/`assign_target_async` are
thin session-aware wrappers used by the indexer (sync) and the astrometry task /
API routes (async) respectively, avoiding a duplicated
`CatalogMatcher`/`SyncCatalogMatcher`-style split.

## Filter normalization

`backend/app/utils/filter_names.py::normalize_filter` maps the wide variety of
raw `filter_name` strings (vendor names, bandwidths, capitalization) onto a
small canonical set: `L, R, G, B, Ha, OIII, SII, Hb, Duo, None, Other:<orig>`.
See `backend/tests/test_filter_names.py` for the full mapping table. Filter
folding happens at query time in Python (group by raw `filter_name` in SQL,
fold in Python) - there's no normalized-filter column.

## Pipeline integration

- `tasks/indexer.py::_process_image_impl` calls `assign_target_sync` after
  catalog matching and before `session.commit()`.
- `tasks/astrometry.py` calls `assign_target_async` right after the async
  catalog matcher runs for a completed solve.
- `scripts/rematch_catalogs.py` / `rematch_all.py` re-resolve the target after
  a manual re-match.
- `scripts/backfill_targets.py` backfills existing rows in resumable batches
  of 1000 (see `docs/development/UTILITY_SCRIPTS.md`).

All of the above skip images whose `target_source == 'MANUAL'`.

## API

`/api/targets` (see `backend/app/api/targets.py` and `backend/app/schemas/target.py`):

- `GET /api/targets` - paginated, filterable (search, catalog, has_master,
  min_hours, filter) and sortable (integration/name/last/subs) list. The full
  aggregation is cached in Redis under `cache:targets:list` (TTL 120s);
  search/sort/paging happen in Python over the cached list.
- `GET /api/targets/unassigned/summary` - `{count, total_seconds}` for
  unassigned lights.
- `GET /api/targets/{target_key}` - detail: filter x rig matrix, nightly
  timeline, masters, goal progress, catalog facts.
- `PUT /api/targets/{target_key}/goals` - replaces the goal set for a target.
- `PUT /api/images/{id}` - now accepts `target_key` (free text, resolved
  through the alias index; empty string clears it; always sets
  `target_source = MANUAL`).
- `PUT /api/images/bulk/target?target=<text>&...filters` - bulk manual
  (re-)assignment across a filtered set of images.
- `_build_image_query` gained a `target_key` kwarg (`__none__` selects
  unassigned lights).
- `GET /api/stats/top-objects` now reads from the targets list (correct
  per-target attribution) instead of `ImageCatalogMatch`.

## Frontend

- `/targets` (`pages/Targets.jsx`): dense list with a per-filter stacked bar
  per target, toolbar (search/sort/catalog chips/has-master/min-hours), and an
  "Unassigned lights" link into Search.
- `/targets/:targetKey` (`pages/TargetDetail.jsx`): hero with cover image and
  catalog facts, editable per-filter goals, a nightly stacked bar chart
  (recharts), and a masters gallery.
- `ImageDetail.jsx` shows the resolved target with an auto/manual hint and an
  inline edit affordance.
- `Catalogs.jsx` links to the Target page when a card's designation matches an
  existing `target_key`.
- `Search.jsx` / `FilterChips.jsx` honor and display the `target_key` URL
  param (including `__none__` for unassigned).

## Known limitations / follow-ups

- "Nights" uses the simple `count(distinct date(capture_date - interval '12
  hours'))` expression (README §2.2), not F7's `imaging_sessions` table (F7
  hadn't merged when this branch was written). A follow-up can switch to
  `count(distinct session_id)` once F7 lands.
- `light_subs_clause()` from F1's `app/utils/frame_filters` doesn't exist yet
  on this branch (F1b hadn't merged); the predicate is inlined with a
  `# TODO(F1)` comment in `backend/app/api/targets.py`.
