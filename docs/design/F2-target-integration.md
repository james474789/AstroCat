# F2 — Target Integration Dashboard ("Targets")

Status: **Proposed** · Size: S–M · Value: 4–5 · Read first: [README.md](README.md) · Depends on: F1 schema (`frame_type`)

## 1. Problem

The question an astrophotographer asks most often is *"how much data do I have on this target, per
filter?"* For example: "M31: L 6h, Ha 12h 30m, OIII 8h. Do I need another night of OIII?"

What exists today:
- The Catalogs page (`app/api/catalogs.py`) shows `image_count` and `cumulative_exposure_seconds` per
  catalog object. However, it counts **every image whose field contains the object**. A wide-field
  shot of Cygnus credits dozens of NGC objects, a frame of M31 also credits M32, M110 and NGC 206, and
  masters and (until F1) calibration frames are included too. There is **no per-filter breakdown**.
- `/api/stats/top-objects` has the same attribution problem, and its `name` field is empty.
- Targets that are not in a seeded catalog (Sh2-155, vdB 152, comets, "Cygnus Wall") are invisible
  unless you search the header `object_name`.

## 2. Goals / Non-goals

**Goals**
1. Give each light frame one **primary target** (canonical key plus display name), persisted and
   editable.
2. A **Targets** page listing every target with total integration, sub count, per-filter breakdown,
   nights, date span, rigs, and whether a master exists.
3. A **Target detail** page with per-filter × per-rig integration, a timeline of nights, masters, and
   links into Search.
4. Normalized filter names (`Ha`, `H-alpha`, `HA 7nm`, `Halpha` → `Ha`).
5. Optional per-target, per-filter **integration goals**, with a progress display.

**Non-goals**
- Mosaic grouping (F16) and sessions (F7). F2 counts nights with a simple SQL expression (README §2.2).
- Planning or visibility (a future planner feature).
- Changing the Catalogs page semantics. It keeps "object appears in field" counting, but gets a link
  to the Target page when a target with that key exists.

## 3. Design

### 3.1 What is a "target"?

A target is identified by `target_key`, a canonical, space-free string:
- For catalog objects, the **canonical catalog designation**. Priority is Messier > NGC/IC > Caldwell
  > named star, so M31, NGC224 and "Andromeda Galaxy" all become `M31`, and C14 becomes `NGC869` (the
  Caldwell source designation).
- For non-catalog objects, `OBJ:` plus the normalized header object name, e.g. `OBJ:SH2-155`,
  `OBJ:CYGNUSWALL`.

Display name: for catalog targets, `designation — common_name` (e.g. `M31 — Andromeda Galaxy`). For
`OBJ:` targets, the most frequent original `object_name` spelling.

**Resolution order** (first hit wins). The result is stored with its `target_source`:

1. `MANUAL`: a user override is never touched by automation.
2. `HEADER`: the header `object_name` resolves to a catalog object through the alias index (§3.3).
3. `MATCH`: the image is plate-solved and has catalog matches. Pick the match with the smallest
   `angular_separation_degrees` among `is_in_field` matches, excluding `NAMED_STAR`, and only if that
   separation ≤ `0.35 × field_radius_degrees` (i.e. the object is reasonably central). Ties go to
   catalog priority, then brighter `apparent_magnitude`. If nothing qualifies, fall through.
4. `HEADER_RAW`: `object_name` is present but unresolved, so the key becomes `OBJ:<normalized>`.
   Strip mosaic/panel suffixes before normalizing: `M31 Panel 2`, `M31_P2`, `M31-mosaic-3`,
   `NGC7000 [2]` → `M31` / `NGC7000`. Use the regex
   `(?i)[\s_\-]*(panel|pane|p|tile|mosaic|mos)[\s_\-]*\d+$|\s*\[\d+\]$`.
5. Otherwise `target_key = NULL`, shown as "Unassigned".

Header resolution (2) beats matching (3) because the user's capture software named the target
deliberately. Matching is the fallback for DSLR/JPG files without an `OBJECT` header.

Exclusions: rows with `frame_type != LIGHT` get `target_key = NULL`, and `target_source` stays NULL
unless it is `MANUAL`. `PLANETARY` images get a target like any other image but are excluded from
integration sums (see §3.5).

### 3.2 Data model

Add to `Image` (block commented `# Target (F2)`):

```python
target_key = Column(String(64), nullable=True, index=True)
target_source = Column(String(20), nullable=True)  # MANUAL | HEADER | MATCH | HEADER_RAW
```

New table `target_goals` (optional goal tracking):

```python
class TargetGoal(Base):
    __tablename__ = "target_goals"
    id = Column(Integer, primary_key=True)
    target_key = Column(String(64), nullable=False, index=True)
    filter_group = Column(String(20), nullable=False)   # normalized filter (see §3.4), or "ANY"
    goal_seconds = Column(Float, nullable=False)
    created_at / updated_at
    __table_args__ = (UniqueConstraint('target_key', 'filter_group', name='uq_target_goal'),)
```

`TargetGoal` lives in a new `app/models/target.py` and is imported in `app/models/__init__.py`.

Migration `f2b8d4fa0002` (see README §3 for its `down_revision`): defensively add the two columns,
the index `ix_images_target_key`, a composite index `ix_images_target_frame (target_key, frame_type)`,
and create `target_goals` if it is missing.

### 3.3 Alias index — `backend/app/services/targets.py` (new)

`TargetResolver` builds an in-memory dict once per process and refreshes it lazily every 1 h. The
catalogs are static seeds, so this is safe. It uses a **sync** load (`SessionLocal`) in Celery and an
**async** load in API routes. The resolving logic itself is pure:

```python
def normalize_designation(s: str) -> str
    # upper, strip spaces/underscores/hyphens between prefix and number, strip leading zeros in the number:
    # "M 31"→"M31", "NGC 0224"→"NGC224", "ngc224"→"NGC224", "IC 434"→"IC434", "Sh2-155"→"SH2155",
    # "Caldwell 14"/"C 14"→"C14", "Messier 31"→"M31"

class AliasIndex:  # pure data + lookup
    def resolve(self, text: str) -> str | None   # -> canonical target_key or None
```

Index entries (all keys normalized):
- Messier: `designation`, `ngc_designation`, `common_name` → `M<n>`.
- NGC/IC: `designation` → itself, unless `messier_designation` is set (then → `M<n>`). Include
  `ic_designation` and `common_name` (OpenNGC common names can be comma-separated lists, so split them).
- Caldwell: `designation` and `aliases` → canonical of `source_designation` if that resolves,
  otherwise the Caldwell designation.
- Named stars: `designation` and `common_name` → star designation. Used only for header resolution.

**Verify the stored formats against the DB before coding.** For example, NGC designations are stored
space-free and may be zero-padded (`NGC0224`, from the OpenNGC CSV in `app/data/seed.py`). That is why
`normalize_designation` strips leading zeros on both sides.

Pure function used by all callers:

```python
def resolve_target(*, frame_type, object_name, matches, field_radius, current_source, alias_index)
    -> tuple[str | None, str | None]   # (target_key, target_source)
```

`matches` is a list of simple tuples `(catalog_type, designation, separation_deg, is_in_field,
magnitude)`. The pure function has no DB access. Sync and async wrappers (`assign_target_sync(session,
image)` and `assign_target_async(db, image)`) load the matches and call it.

Header-name matching should also try the text with panel suffixes stripped, and the text before
` - `/`(` (e.g. `M42 (Orion)` → `M42`).

### 3.4 Filter normalization — `backend/app/utils/filter_names.py` (new)

```python
def normalize_filter(name: str | None) -> str
```

Case-insensitive, after stripping bandwidth/brand noise (`\d+(\.\d+)?\s*nm`, `astrodon`, `chroma`,
`antlia`, `baader`, `optolong`, `zwo`, `3nm`, `ultra`, `pro`):

| Output | Inputs (examples) |
|---|---|
| `L` | L, Lum, Luminance, Clear, UV/IR, UVIR, L-Pro (broadband light-pollution filters count as `L`) |
| `R` / `G` / `B` | R, Red / G, Green / B, Blue |
| `Ha` | Ha, H-a, H-alpha, Halpha, HA, H_Alpha, Hα, bare `H` (single-letter filter wheel labels) |
| `OIII` | OIII, O3, O-III, Oiii, bare `O` |
| `SII` | SII, S2, S-II, bare `S` |
| `Hb` | Hb, H-beta |
| `Duo` | L-eNhance, L-eXtreme, L-Ultimate, ALP-T, NBZ, Triad, Quad, Duo, Dual, Tri-band, CLS |
| `None` | empty / null / `NoFilter` / `OSC`, i.e. an OSC or DSLR with no filter wheel. Display it as **"OSC / No filter"**. |
| `Other:<orig>` | anything unrecognized. Keep it visible rather than hiding data. |

Display order: `L, R, G, B, Ha, OIII, SII, Hb, Duo, None, Other:*`. Colors (CSS variables to add in
`Targets.css`): L `#d0d4dc`, R `#e05050`, G `#50c070`, B `#5080e0`, Ha `#c8283c`, OIII `#2f80ed`,
SII `#a83246`, Hb `#3cc8ff`, Duo `#b060c0`, None `#a0a0a0`, Other `#707070`. Narrowband hues follow
the common bicolor/SHO convention: Ha and SII (both deep-red emission lines) render as two distinct
reds, OIII (the blue channel in the Hubble palette) renders as blue. `L` is the only neutral/grey
bucket among real filters; `Other`/`None` stay grey as the "unclassified" fallback.

Filter normalization is done **at query time in Python** over grouped rows (group by the raw
`filter_name` in SQL, then fold in Python). The number of distinct raw filter names is small. Do not
add a column for it.

### 3.5 Aggregation queries

Common predicate: `light_subs_clause()` from F1 (`frame_type == LIGHT AND subtype == SUB_FRAME`), plus
`target_key IS NOT NULL`.

**List** (one query, then fold filters in Python):

```sql
SELECT target_key, filter_name,
       count(*) AS subs, sum(exposure_time_seconds) AS secs,
       min(capture_date) AS first, max(capture_date) AS last
FROM images WHERE <predicate> GROUP BY target_key, filter_name;
```

Then separate small queries keyed by target:
- nights: `count(distinct date(capture_date - interval '12 hours'))`
- rigs: `array_agg(distinct camera_name)`, `array_agg(distinct telescope_name)`
- masters: `count(*) WHERE subtype = INTEGRATION_MASTER AND frame_type = LIGHT`
- cover image: the id of the highest-`rating` master, else the most recent master, else the most
  recent light with a thumbnail (`DISTINCT ON (target_key) ... ORDER BY target_key, <priority>`)

Add display metadata by bulk-looking-up catalog rows for keys that are catalog designations
(`common_name`, `object_type`, `constellation`, `apparent_magnitude`).

Cache the full folded list in Redis under `cache:targets:list` (TTL 120 s). Sorting, searching and
paging happen in Python on the cached list. There are at most a few thousand targets, which is
cheap. Invalidate the cache from the manual-assign endpoints.

### 3.6 Pipeline integration

- `tasks/indexer.py::_process_image_impl`: after catalog matching and before `session.commit()`, call
  `assign_target_sync(session, image)` (README §4 ordering). It skips when `target_source == 'MANUAL'`.
- `tasks/astrometry.py`: after a successful solve has written the WCS and run the async matcher, call
  `assign_target_async(db, image)`. Find the spot where `image.wcs_header = wcs_header` is set
  (~line 304) and follow the matching call there.
- `scripts/rematch_catalogs.py` / `rematch_all.py`: call the sync assigner after re-matching.
- Backfill `backend/app/scripts/backfill_targets.py`: keyset batches of 1000 over lights where
  `target_source IS NULL` (or all non-MANUAL rows with `--all`). It loads matches for the batch in one
  query, resolves with the pure function, bulk-updates, and deletes `cache:targets:*` at the end.
  No file IO. Add it to the startup chain after F1's backfill, since it is a no-op when complete, and
  document it in UTILITY_SCRIPTS.

### 3.7 API — `backend/app/api/targets.py` (new router, `/api/targets`)

| Method & path | Purpose |
|---|---|
| `GET /api/targets?search=&sort=integration\|name\|last\|subs&order=desc&min_hours=&filter=Ha&has_master=&catalog=MESSIER\|NGC\|IC\|CALDWELL\|OTHER&page=&page_size=` | Paginated list (schema below). |
| `GET /api/targets/unassigned/summary` | `{count, total_seconds}` for lights with a NULL target, with a link to Search to fix them. |
| `GET /api/targets/{target_key}` | Detail: everything in the list row, plus `by_filter_rig: [{filter, camera, telescope, subs, seconds}]`, `nights: [{night: date, filters: {Ha: secs, ...}}]`, `masters: [ImageList]`, `goals: [{filter_group, goal_seconds, have_seconds}]`, `catalog: {...}` (or null). |
| `PUT /api/targets/{target_key}/goals` | Body `[{filter_group, goal_seconds}]`. Replaces the goal set. `goal_seconds <= 0` deletes. |
| `PUT /api/images/{id}` (existing) | Accept `target_key: Optional[str]`. An empty string clears it. Resolve free text through `AliasIndex` so that typing "m 31" yields `M31`, otherwise `OBJ:<normalized>`. Sets `target_source='MANUAL'`. |
| `PUT /api/images/bulk/target?target=<text>&<same filters as bulk/subtype>` | Bulk manual assign. Needed to fix mis-attributed batches. |
| `_build_image_query` | New kwarg `target_key` (exact match), plus `Query` params on the 4 filter endpoints. `target_key=__none__` means `IS NULL`. |

List row schema (`schemas/target.py`):

```python
class FilterIntegration(BaseModel):
    filter: str               # normalized
    raw_names: list[str]
    subs: int
    seconds: float
    goal_seconds: float | None = None

class TargetSummary(BaseModel):
    target_key: str
    display_name: str
    catalog_type: str | None          # MESSIER/NGC/IC/CALDWELL/NAMED_STAR/None
    object_type: str | None
    constellation: str | None
    total_seconds: float
    total_subs: int
    nights: int
    first_capture: datetime | None
    last_capture: datetime | None
    filters: list[FilterIntegration]  # in display order
    cameras: list[str]
    telescopes: list[str]
    master_count: int
    cover_image_id: int | None
```

Also update `GET /api/stats/top-objects` to read from the targets list (top 10 by `total_seconds`)
and fill `name`. Keep the response shape unchanged for Dashboard compatibility.

Register the router in `main.py` with `dependencies=[Depends(get_current_user)]`.

### 3.8 Frontend

- **Nav** (`Layout.jsx`): add "Targets" after "Catalogs" (icon `Crosshair` from lucide-react).
- **Route** `/targets` → `pages/Targets.jsx` + `Targets.css`.
  - Toolbar: search box, sort select, catalog chips, "has master" toggle, and a min-hours number input.
  - Rows (a card list or table; prefer a dense table on desktop that collapses to cards below 768 px):
    thumbnail (`/api/images/{cover_image_id}/thumbnail`), display name + constellation/type, **one
    horizontal stacked bar** of per-filter seconds (colors from §3.4, tooltip showing `Ha 12h 30m ·
    143 subs`), total hours, nights, last-captured date, and rig chips.
  - If goals exist, draw each filter segment against a faint goal outline, with the % complete in the
    tooltip.
  - Header tile with totals, and an "Unassigned lights: N (Xh)" link to
    `/search?target_key=__none__&frame_type=LIGHT`.
- **Route** `/targets/:targetKey` → `pages/TargetDetail.jsx`. `targetKey` is URL-encoded because of
  `OBJ:` keys.
  - Hero: the cover image, catalog facts (type, magnitude, size, constellation), and totals.
  - Filter table: filter × rig matrix (subs / hours), plus editable goals (inline number inputs in
    hours, saved with `PUT .../goals`).
  - Nights chart (recharts `BarChart`, stacked by filter, x = night).
  - Masters gallery (reuse `ImageCard`).
  - Buttons: "View all subs" → `/search?target_key=<key>&frame_type=LIGHT`, and "Catalog entry" →
    `/catalogs/<type>/<designation>` if it is a catalog target.
- **ImageDetail.jsx**: add a "Target" row: the display name linking to `/targets/<key>`, a
  `(auto: header|match)` hint, and a small edit affordance (a text input using the PUT above).
- **Catalogs.jsx**: on each object card with `image_count > 0`, add a link "Target page →" when the
  designation equals a `target_key`. Build the set of keys from one call to a lightweight
  `GET /api/targets?page_size=10000&fields=keys` variant, or have the list endpoint accept
  `keys_only=true`.
- **Search.jsx**: honor the `target_key` URL param (pass-through to `fetchImages`) and show it as a
  filter chip via `FilterChips.jsx`.
- **client.js**: add `fetchTargets(params)`, `fetchTarget(key)`, `updateTargetGoals(key, goals)`,
  `bulkAssignTarget(text, searchParams)`, and a `formatHours(seconds)` helper (`12h 30m`).

## 4. Files touched

New: `app/models/target.py`, `app/services/targets.py`, `app/utils/filter_names.py`,
`app/api/targets.py`, `app/schemas/target.py`, `app/scripts/backfill_targets.py`,
`alembic/versions/…-f2b8d4fa0002_add_targets.py`, `tests/test_targets.py`, `tests/test_filter_names.py`,
`frontend/src/pages/Targets.jsx/.css`, `TargetDetail.jsx/.css`, `docs/features/TARGETS.md`.
Modified: `models/image.py`, `models/__init__.py`, `schemas/image.py`, `api/images.py`, `api/stats.py`,
`tasks/indexer.py`, `tasks/astrometry.py`, `scripts/rematch_catalogs.py`, `scripts/rematch_all.py`,
`main.py`, startup chain (compose templates, `rebuild_and_seed.ps1`, README), frontend `App.jsx`,
`Layout.jsx`, `ImageDetail.jsx`, `Catalogs.jsx`, `Search.jsx`, `FilterChips.jsx`, `client.js`,
`docs/core/DATABASE_SCHEMA.md`, `docs/development/UTILITY_SCRIPTS.md`.

## 5. Testing

Unit (pure):
- `normalize_designation`: `M 31`, `Messier 31`, `NGC 0224`, `ngc224`, `IC 434`, `C 14`, `Caldwell 14`, `Sh2-155`.
- `AliasIndex.resolve` with a small hand-built index: M31 via `NGC224`, `Andromeda Galaxy`, and
  `M31 Panel 3`; NGC7000 via `North America Nebula`; unknown → None.
- `resolve_target`:
  - MANUAL is preserved.
  - HEADER beats MATCH.
  - MATCH picks the central object, not a bright off-center one.
  - The `0.35 × radius` guard rejects edge objects.
  - NAMED_STAR is ignored for MATCH.
  - Non-LIGHT → None.
  - Panel suffix stripping produces `OBJ:` keys.
- `normalize_filter`: every row of the §3.4 table, including `Ha 7nm`, `Astrodon Ha 3nm`, `L-eXtreme`,
  `None`, `""`, `"Lum"`.
- The folding of `(target, raw filter)` rows into a `TargetSummary`, with goals.

Integration (mocked session or test DB if available): after the backfill, an image of M31 with
header `OBJECT='Andromeda'` gets `M31/HEADER`; a solved JPG with no header gets `MATCH`.

Manual: compare the Targets page totals for a well-known target against a manual sum in Search
(`object_name=M31`, lights, subs).

## 6. Acceptance criteria

1. Every LIGHT sub has either a `target_key` or appears in "Unassigned" (backfill complete).
2. For M31 imaged with a mono camera, the Targets row shows separate L/R/G/B/Ha bars whose sum equals
   the Search result's total exposure for `target_key=M31&frame_type=LIGHT&subtype=SUB_FRAME`.
3. A wide-field Cygnus image with `OBJECT='Sadr Region'` does **not** credit NGC 6914 or other edge
   objects. It becomes `OBJ:SADRREGION`, unless the header text resolves via the alias index.
4. A manual re-assign of an image (or a bulk re-assign) survives rescans and re-solves.
5. Goals persist and render as progress on both pages.
6. Pages are usable at 375 px width.

## 7. Decisions (resolved with the owner, 2026-09-25)

- **Integration comes from subs only.** A master's header-derived integration (e.g. PixInsight
  `NCOMBINE × EXPTIME`) is **not** credited, even when its subs are missing. Show `master_count` and
  list the masters.
- **PLANETARY images are excluded from integration.** Show them as a separate count on the target
  (add `planetary_count` to `TargetSummary`).
- Should a mosaic's panels roll up into one target? With panel-suffix stripping they already do (all
  become `M31`). F16 gives the per-panel view.
