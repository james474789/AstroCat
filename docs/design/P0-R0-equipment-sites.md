# P0 + R0: Data Foundations and Equipment & Sites

Status: **Proposed** · Written: 2026-09-26 · Base: `main` @ `698473c` · Alembic head: `c6e8a2d31006`
Read first: [README.md](README.md) (conventions, §3 migration rules, §4 merge hotspots),
[target-recommendations-research.md](target-recommendations-research.md) (why this exists).
Depends on: F1, F2 (merged). Blocks: R1 (recommendation engine), F7 (sessions use `sites`).

## 1. Problem

The target recommender needs to know **where** each frame was taken, **when** it was taken (in
UTC), **which rig** took it, and **which target** it belongs to. Measured on the live library
(93.7k images, 91.5k lights):

| Gap | Evidence |
|-----|----------|
| **The MATCH path doesn't canonicalise the target key** | `resolve_target` returns `normalize_designation(best.designation)` directly, so plate-solved frames of M81 are keyed `NGC3031` (519 subs) while header-resolved ones are `M81` (872). Same split for M31/`NGC224` (239), M33/`NGC598` (62), M51/`NGC5194` (90), and the Bubble: `C11` (245) vs `NGC7635` (400). Totals on the Targets page are wrong today. |
| **No Sh2 ↔ NGC/IC cross-identification** | Sharpless rows carry no NGC/IC alias. `SH2131` (64 subs) is IC1396, `SH2117` is NGC7000, and so on. |
| **"Processed, no match" looks the same as "never processed"** | Both have `target_source IS NULL`, so `backfill_targets` rescans ~57k rows on every run (HANDOVER §6). |
| **Timestamps have unknown provenance** | 23,316 lights have `capture_date` = file mtime (the indexer fallback). These are mostly planetary PNG/TIF and processed files, which is why 07:00–17:00 "lights" exist. DSLR EXIF times are camera-local with no zone. Only FITS `DATE-OBS` is reliably UTC. |
| **Site is never persisted** | `images.site_latitude/longitude` are 0% populated. Extractors read `SITELAT/SITELONG`, but the indexer drops them. `_parse_float` fails on sexagesimal values like `"56d0m0.000s N"`, and EXIF GPS (7k frames) is never parsed. |
| **Rigs are guessed** | Telescope headers say "EQMod Mount", and DSLR raws lack pixel size, so `utils/rig_optics.py` keeps a hard-coded `KNOWN_PIXEL_SIZES_UM` table and clusters camera + solved scale at query time. There is no rig identity, no "mounted" rig, and no FOV. |

Raw-header quirk to handle everywhere: some string values are stored as **JSON arrays of single
characters** (`["+","0","2",":","0","0"]`, and `EXIF DateTimeOriginal` likewise). Join them before
parsing.

## 2. Goals / Non-goals

**Goals**
- **P0.1** Canonical target keys everywhere. Fix MATCH, add Sh2 cross-IDs, re-key existing rows,
  and merge goals.
- **P0.2** A `NONE` sentinel for "resolved, no target".
- **P0.3** Record where each capture time came from, plus a trustworthy `capture_date_utc`.
- **P0.4** Persist per-image site coordinates from FITS (decimal and sexagesimal) and EXIF GPS.
- **R0.1** Equipment model: cameras, optics, filters, rigs (camera + optic + optional modifier +
  binning + filter set), and sites (with timezone, sky quality, horizon).
- **R0.2** **Detect, then confirm.** Propose cameras, filters, rigs and sites from library history.
  The user accepts or edits them.
- **R0.3** Optional **Telescopius import** of scopes, lenses, cameras, filters and mount. It names
  detected rigs by matching optics to solved scales.
- **R0.4** Computed optics: pixel scale, FOV, f-ratio, sampling vs seeing, and declared-vs-measured
  scale check.
- **R0.5** `images.rig_id` / `images.site_id` assignment (automatic, with manual override), an
  indexer hook, and a backfill task.
- **R0.6** Horizon per site: learned from history, imported/exported as N.I.N.A. `.hrz`, editable.
- **R0.7** Equipment page. Targets "By Filter & Rig" uses declared rig names when available.

**Non-goals**
- The recommendation engine and Tonight page (R1).
- Weather.
- F7 sessions. F7 will consume `sites` and `capture_date_utc`.
- Changing `capture_date` semantics or the Targets "nights" SQL. Switching "nights" to UTC + site
  longitude is an R1 follow-up.
- Mount modelling beyond a name and optional limits (fields exist, nothing enforces them yet).

## 3. Design — P0 (data foundations)

### 3.1 Target canonicalisation — `backend/app/services/targets.py`

1. In `resolve_target` step 3 (MATCH), return
   `alias_index.resolve(best.designation) or normalize_designation(best.designation)` instead of the
   raw normalised designation. This is a one-line fix. The existing tests still pass, and new ones
   are added (§6).
2. **Sh2 cross-IDs.** Add a pure function
   `sh2_cross_ids(sh2_rows, ngc_rows) -> dict[sh2_designation -> ngc_designation]` and call it
   inside `build_alias_index` before the Sh2 loop. The Sh2 row is then registered under the NGC/IC
   canonical key. Rule (from the prototype, where it merged correctly):
   - The NGC/IC row's `object_type` ∈ {`HII`, `EmN`, `Neb`, `Cl+N`, `SNR`, `RfN`}.
   - Centre separation < `max(0.25°, 0.25 × max(size_sh2, size_ngc))`.
   - Either `min(size)/max(size) ≥ 1/3`, **or** the NGC/IC object is `Cl+N` (IC1396 is a 14′
     cluster inside the 170′ Sh2-131).
   - Pick the closest qualifying row, and prefer Messier/Caldwell canonicals via the existing
     resolve.
   - Expected hits include Sh2-131→IC1396, Sh2-117→NGC7000, Sh2-125→IC5146, Sh2-162→NGC7635,
     and Sh2-190→IC1805.
   - Keep a short hard-coded `SH2_CROSS_ID_OVERRIDES` dict to force or suppress individual pairs.
3. **Re-keying existing rows:** new script `app/scripts/recanonicalize_targets.py::recanonicalize_targets()`.
   - Build the alias index and select distinct `(target_key, target_source)` among non-MANUAL lights.
   - For each key where `alias_index.resolve(key)` returns a different canonical key (MATCH/HEADER),
     or an `OBJ:` key now resolves (HEADER_RAW → HEADER), run one bulk `UPDATE ... WHERE target_key = :old`.
   - Merge `target_goals`: when both old and new keys have a goal for the same `filter_group`, keep
     the larger one.
   - Delete Redis `cache:targets:*`.
   - Return `{remapped_keys, rows_updated, goals_merged}`. It's idempotent, and a second run is a
     no-op.
4. **Sentinel.** After re-keying, set `target_source = 'NONE'` for lights with `target_key IS NULL`
   and `target_source IS NULL` (after P0.1 every light has been through the resolver).
   - `resolve_target` returns `(None, 'NONE')` instead of `(None, None)` for LIGHT frames that fall
     through.
   - Non-lights still clear to `(None, None)`.
   - `backfill_targets` (non-`--all`) keeps selecting only `target_source IS NULL`, so it's a true
     no-op on restart.
   - The API's unassigned filter (`target_key=__none__`) is unchanged, because it keys on
     `target_key IS NULL`.

### 3.2 Capture-time provenance — `backend/app/utils/capture_time.py` (new, pure)

New columns on `images`, placed in a commented `# Capture time / site (P0)` block above
`# Timestamps`:

```python
capture_date_utc = Column(DateTime, nullable=True, index=True)   # naive UTC; NULL when not derivable
capture_time_source = Column(String(20), nullable=True)          # see table
```

| `capture_time_source` | When | `capture_date_utc` |
|---|---|---|
| `FITS_UTC` | FITS/XISF `DATE-OBS` present | = parsed `DATE-OBS` |
| `FITS_LOCAL` | only `DATE-LOC` | `NULL` until the site's timezone is known (R0 fills it) |
| `GPS_UTC` | EXIF `GPS GPSDate` + `GPS GPSTimeStamp` | GPS date + time |
| `EXIF_OFFSET` | EXIF `DateTimeOriginal` + `OffsetTimeOriginal` | local − offset |
| `EXIF_LOCAL` | EXIF `DateTimeOriginal` only | `NULL` until the site's timezone is known (R0 fills it) |
| `FILE_MTIME` | indexer fallback used | `NULL`. **Never used for astronomy.** |

Pure API:
- `derive_capture_time(metadata: dict, raw_header: dict, used_mtime_fallback: bool) -> (utc: datetime|None, source: str)`
- helpers `unchar(v)`, `parse_exif_datetime`, `parse_offset`, `parse_gps_time`

`capture_date` keeps its current meaning (unchanged for UI and existing queries).

- **Indexer:** `_process_image_impl` sets both new columns immediately after `capture_date` is
  assigned (one call, both new and existing branches). It passes `used_mtime_fallback=True` when
  `metadata.get("capture_date")` was falsy.
- **Backfill:** `app/scripts/backfill_capture_time.py::backfill_capture_time()` computes both columns
  from `raw_header` only (no file IO), in batches of 1000. It detects the mtime fallback as
  `capture_date == file_last_modified` with no header date.

### 3.3 Site persistence — `backend/app/utils/header_values.py` (new, pure)

- `parse_sexagesimal(v) -> float|None` accepts `51.48`, `"51.48"`, `"56d0m0.000s N"`, `"+51 28 40"`,
  `"51:28:40"`, `"0d30m0.000s W"` (sign from N/S/E/W), and char-arrays.
- `parse_exif_gps(raw_header) -> (lat, lon)|None` handles `GPSLatitude` lists like
  `[56.0, 2.2314, 0.0]` plus `Ref` N/S/E/W.
- `FitsExtractor`/`XisfExtractor` use `parse_sexagesimal` instead of `_parse_float` for
  `SITELAT/SITELONG`, and also read `SITENAME` (or `OBSERVAT`) into `metadata["site_name"]`.
  `ExifExtractor` adds `site_lat/site_long` from GPS.
- The indexer persists `site_latitude/site_longitude/site_name` (both branches). Values outside
  ±90/±180, or exactly (0, 0), are treated as missing.
- **Backfill:** `app/scripts/backfill_sites.py::backfill_image_sites()` works from `raw_header`
  only, in batches of 1000.

### 3.4 P0 data migrations (append to `REGISTRY`, in this order)

| id | runs |
|----|------|
| `0004_canonicalize_target_keys` | `recanonicalize_targets()` then the `NONE` sentinel pass |
| `0005_capture_time_provenance` | `backfill_capture_time()` |
| `0006_image_site_coordinates` | `backfill_image_sites()` |

Each is idempotent, returns a JSON summary, and needs no restart or manual step (CLAUDE.md
"Shipping a One-off Data Repair"). `0005` must run before any R0 site timezone fill.

### 3.5 P0 migration

Alembic revision **`d7f9b3e41007`** (`down_revision = 'c6e8a2d31006'`) adds `capture_date_utc`
(+ index) and `capture_time_source`. It's defensive per README §3: inspect before adding, because
`create_all` may have already built them.

## 4. Design — R0 (Equipment & Sites)

### 4.1 Data model — `backend/app/models/equipment.py` (new)

```python
class Camera(Base):            # __tablename__ = "cameras"
    id, name (String 100, unique), maker (String 50, null)
    sensor_width_px, sensor_height_px (Integer, null)
    pixel_size_um (Float, null)              # unbinned
    is_color (Boolean, null)                 # OSC/DSLR True, mono False, unknown NULL
    is_cooled (Boolean, null)
    match_patterns (JSONB, default [])       # lowercase substrings of camera_name/INSTRUME
    source (String 20)                       # DETECTED | TELESCOPIUS | MANUAL | SEED
    external_ref (String 50, null)           # e.g. "telescopius:50600"
    notes (Text, null), created_at, updated_at

class Optic(Base):             # "optics"
    id, name (unique), kind (String 10: TELESCOPE | LENS)
    aperture_mm (Float, null), focal_length_mm (Float, not null)
    source, external_ref, notes, created_at, updated_at

class Filter(Base):            # "filters"
    id, name (unique), band (String 20)      # normalize_filter bucket: L,R,G,B,Ha,OIII,SII,Hb,Duo,None,Other
    bandwidth_nm (Float, null), match_patterns (JSONB), source, external_ref, created_at, updated_at

class Rig(Base):               # "rigs"
    id, name (unique)
    camera_id (FK cameras, not null), optic_id (FK optics, not null)
    modifier_name (String 50, null), modifier_factor (Float, default 1.0)   # 0.8 reducer, 2.0 Barlow
    binning (Integer, default 1)
    is_active (Boolean, default True), is_mounted (Boolean, default False)  # at most one mounted
    mount_name (String 100, null)
    measured_scale_arcsec (Float, null), measured_count (Integer, default 0) # cached by assignment task
    created_at, updated_at
rig_filters = Table("rig_filters", rig_id FK, filter_id FK, PK(rig_id, filter_id))

class Site(Base):              # "sites"
    id, name (unique), latitude, longitude (Float, not null), elevation_m (Float, null)
    timezone (String 64, not null)           # IANA, e.g. "Europe/London"
    bortle (Integer, null), sqm (Float, null), typical_seeing_arcsec (Float, default 2.5)
    is_default (Boolean, default False)      # at most one
    horizon (JSONB, null)                    # [[az_deg, alt_deg], ...] sorted by az
    horizon_source (String 10, null)         # LEARNED | IMPORTED | MANUAL
    created_at, updated_at
```

- **Images** (same commented block as P0): add `rig_id` (FK `rigs.id`, `ON DELETE SET NULL`,
  index), `rig_source` (`AUTO | MANUAL`, null), and `site_id` (FK `sites.id`, `SET NULL`, index).
- **Constraints:** a partial unique index for one mounted rig
  (`CREATE UNIQUE INDEX uq_rigs_mounted ON rigs (is_mounted) WHERE is_mounted`) and likewise for
  one default site. The API also enforces this: setting one clears the others in the same
  transaction.
- **Sensor modes:** an unlocked or different-resolution mode of the same physical camera (the
  ASI294MM at 8288×5644, 2.315 µm) is a **separate `Camera` row**, e.g. "ASI294MM Pro (unlocked)".
  That's the same approach the user took in Telescopius, and it keeps scale maths trivial.
- **Migration:** Alembic **`e8a0c4f51008`** (`down_revision = 'd7f9b3e41007'`). It's defensive and
  imports the new models in `models/__init__.py`.
- **tzdata:** add `tzdata` to `requirements.txt` so `zoneinfo` works in the slim image.

### 4.2 Optics maths — `backend/app/utils/optics.py` (new, pure)

```python
effective_focal_mm(focal_mm, modifier_factor=1.0)
pixel_scale(pixel_um, focal_mm, binning=1, modifier_factor=1.0)   # 206.265 * px * bin / (fl * mod)
fov_deg(width_px, height_px, scale_arcsec) -> (w_deg, h_deg)
focal_ratio(focal_mm, aperture_mm, modifier_factor=1.0)
sampling(scale_arcsec, seeing_arcsec) -> ("under"|"ok"|"over", seeing/scale)  # ok if 1.0 <= s/scale <= 3.0 (Nyquist-ish, 2-3 px per FWHM)
scale_check(declared, measured, tol=0.05) -> {"delta_pct", "verdict": "ok"|"mismatch"|"unknown"}
```

Rigs expose these as computed fields in the API. They are not stored, except for `measured_*`.

### 4.3 Detection — `backend/app/services/equipment_detection.py` (new)

Pure core: `propose(buckets, image_sites, existing) -> Proposals`. A thin sync wrapper runs the
aggregation SQL. The buckets come from **light sub-frames seen in the last 36 months**, with an
"include older" flag:

```sql
select camera_name, width_pixels, height_pixels, binning,
       raw_header->>'XPIXSZ' xpix, raw_header->>'BAYERPAT' bayer, raw_header->>'FOCALLEN' fl,
       pixel_scale_arcsec, filter_name, count(*), max(capture_date)
from images where frame_type='LIGHT' and subtype='SUB_FRAME' group by ...
```

Pre-aggregate in SQL (percentile for scale), then:

1. **Cameras.** Normalise `camera_name` to a key: lowercase, collapse a repeated maker
   ("Canon Canon EOS R7" → "canon eos r7"), strip brackets ("Canon [EOS R8]"). Group by key +
   sensor dims.
   - Pixel size: `XPIXSZ` median, else a `SEED_SENSORS` table (moved out of `rig_optics.py`,
     extended), else NULL.
   - `is_color`: `BAYERPAT` present, a DSLR maker, or "MC"/"C" suffixes → True; "MM" → False.
   - `match_patterns = [key]`.
   - Blank or junk names (`notAvailable`, phone models, 187×125 thumbnails) are dropped when under
     50 subs.
2. **Rigs.** Within each camera, cluster valid solved scales with the existing
   `rig_optics` clustering (5% tolerance).
   - For each cluster, `predicted_focal = 206.265 × pixel_um × bin / scale`.
   - Match it to an existing `Optic` (e.g. from the Telescopius import) whose focal length (× common
     modifiers 0.7/0.8/1.0) is within 5%. Otherwise propose a new optic named "~345 mm (detected)".
   - Filter set = `normalize_filter` buckets seen in that cluster.
   - Proposed name: "`<optic> + <camera>`".
3. **Filters.** One proposal per non-`None` bucket, with the raw spellings as `match_patterns`.
4. **Sites.** Cluster `images.site_latitude/longitude` (P0.4) at 0.05° (≈5 km) with counts. Propose
   clusters with ≥ 200 frames. Name each from the most common `site_name`, else "Site 1…".
   Timezone defaults to the browser's (sent by the UI).

Proposals already matching an existing row (same name/pattern, or FL within 5%) are marked
`exists`. Accepting is explicit (§4.6).

### 4.4 Telescopius import — `backend/app/services/telescopius.py` (new, optional)

- The key comes **only** from the env var `TELESCOPIUS_API_KEY`, read via `config.Settings` as
  `telescopius_api_key: Optional[str]`.
- It's never stored in Redis settings, never returned by any endpoint, and never logged.
- If it isn't set, the import button is hidden (`GET /api/equipment` returns
  `telescopius_available: false`).
- `fetch_equipment()` calls `GET https://api.telescopius.com/v2.2/equipment/user`
  (header `Authorization: Key …`, timeout 20 s).

Pure mapping, `map_telescopius(payload) -> ImportPlan`. Field names verified 2026-09-26:
- `telescopes[]` → `Optic(name=label, kind=LENS if focal_length_max <= 600 and custom_brand in LENS_BRANDS else TELESCOPE, aperture_mm=aperture, focal_length_mm=focal_length_max)`.
  Zoom lenses (`min != max`) are imported with `focal_length_max` and a note.
- `cameras[]` → `Camera(name=label, sensor_*_px, pixel_size_um = custom_sensor_width_mm / sensor_width_px * 1000, is_cooled)`.
  The derived pixel size is flagged as approximate (~2% error seen). A detected `XPIXSZ` wins when
  merging.
- `filters[]` → `Filter(name=label, band=normalize_filter(name))`.
- `mounts[]` → suggestions for `Rig.mount_name`.
- `external_ref = "telescopius:<id>"` makes the import idempotent: re-importing updates rows and
  never duplicates them.

Returns `{created, updated, skipped}` per type. After import, detection re-runs so rig proposals
pick up the named optics.

### 4.5 Assignment — `backend/app/services/equipment_assignment.py` (new)

Pure core: `assign_rig(image: dict, rigs: list[RigInfo]) -> (rig_id|None, reason)`.
- **Camera match:** the image `camera_name` contains one of the camera's `match_patterns`, and the
  dims match the sensor (or sensor ÷ binning).
- **With a valid solved scale** (`rig_optics.valid_pixel_scale`): pick the rig whose declared scale
  is within 7%, nearest first.
- **Without one:** assign only if exactly one *active* rig uses that camera (reason
  `single_rig_for_camera`), or if the `FOCALLEN` header is within 5% of exactly one rig's
  effective focal length.
- **Otherwise** leave it NULL. Never guess between two rigs.

`assign_site(lat, lon, sites) -> site_id|None` picks the nearest site within 10 km. Images without
coordinates get the default site **only** if they already have a rig and the rig's other images
are ≥ 90% at that site. Otherwise they stay NULL.

**Timezone fill:** when a site is assigned, `FITS_LOCAL`/`EXIF_LOCAL` rows get
`capture_date_utc = local → UTC via site.timezone`, using `zoneinfo` so DST is handled.

- **Celery task `tasks/equipment.py::assign_equipment(scope="unassigned"|"all")`** runs in batches
  of 1000. It never touches `rig_source='MANUAL'`, refreshes `rigs.measured_scale_arcsec/count`
  (median solved scale of assigned subs), and invalidates `cache:targets:*`.
  - Queued automatically (debounced: one pending task at a time, Redis lock like
    `data_migrations`) after any rig/camera/site create, update or delete, and after detection is
    accepted.
  - Route it to the existing default queue and add it to the `worker.py` `include`.
- **Indexer hook:** after `assign_target_sync`, one call to `assign_equipment_sync(session, image)`,
  which uses cached rigs/sites (60 s TTL).
- **Manual:** `PUT /api/images/{id}` accepts `rig_id` and sets `rig_source='MANUAL'`; `null`
  clears it.

### 4.6 API — `backend/app/api/equipment.py` (new router, `/api/equipment`) and `/api/sites`

All endpoints require an authenticated user, and writes require admin (same dependency as the
Admin endpoints).

| Method & path | Purpose |
|---|---|
| `GET /api/equipment` | `{cameras, optics, filters, rigs, sites, telescopius_available}`. Rigs include computed `scale`, `fov_deg`, `focal_ratio`, `sampling`, `scale_check`, `image_count`, `last_used`, `filters`. |
| `POST/PUT/DELETE /api/equipment/{cameras,optics,filters,rigs}[/{id}]` | CRUD. Delete of a camera/optic in use by a rig → 409. |
| `POST /api/equipment/rigs/{id}/mount` | Sets `is_mounted` exclusively. `DELETE` unmounts. |
| `GET /api/equipment/detect?include_older=false` | Proposals (§4.3), cached 10 min in Redis. |
| `POST /api/equipment/detect/apply` | Body: accepted proposal ids with optional edits (name, optic choice, modifier). Creates rows, then queues assignment. |
| `POST /api/equipment/import/telescopius` | Runs the §4.4 import. 400 if no key, 502 on upstream failure (message only, no key echo). |
| `POST /api/equipment/assign?scope=unassigned\|all` | Queues `assign_equipment`. Returns the task id. |
| `GET/POST/PUT/DELETE /api/sites[/{id}]` | CRUD. `is_default` is exclusive. |
| `GET /api/sites/{id}/horizon/learned` | Learned profile (§4.7), cached 1 h. |
| `PUT /api/sites/{id}/horizon` | Body `{points: [[az, alt], ...], source}`. |
| `POST /api/sites/{id}/horizon/import` | Multipart `.hrz` upload. |
| `GET /api/sites/{id}/horizon/export` | Returns `.hrz` text. |

`_build_image_query` gains `rig_id` and `site_id` kwargs, appended **at the end** per README §4,
and the 4 endpoints that re-declare filters get matching `Query` params. Pydantic schemas go in
`backend/app/schemas/equipment.py`.

### 4.7 Horizon — `backend/app/utils/horizon.py` (new, pure)

- **`learn_horizon(samples: list[(az, alt)], floor_pct=5, bin_deg=15, min_count=50, cap_above_floor=10) -> [[az, alt]]`**
  - Global floor = the `floor_pct` percentile of all altitudes.
  - Each bin with ≥ `min_count` samples → that bin's percentile, clipped to
    `[15, floor + cap_above_floor]`.
  - Other bins → the global floor.
  - Tested against the prototype: floor 32.3°, east and south-west bins at 24°.
- **Samples** come from light subs at the site that have `capture_date_utc` and coordinates:
  - `CENTALT`/`CENTAZ` headers where present;
  - otherwise computed from RA/Dec (`ra_center_degrees`, or `OBJCTRA/OBJCTDEC`) +
    `capture_date_utc` + site (vectorised numpy, as in the prototype).
  - Rows with Sun altitude > −6° at capture are excluded (this catches mis-timed frames).
- **`parse_hrz(text)` / `format_hrz(points)`:** N.I.N.A. format, one `az alt` pair per line, `#`
  comments, sorted by az, and wrapping at 360.

### 4.8 Targets integration

- **`GET /api/targets/{key}` `by_filter_rig`:** if the target's subs have `rig_id`, group by rig
  (with name, scale, FOV). Subs without a rig fall back to the current camera + scale clustering
  under their existing labels. The response shape is unchanged apart from an added optional
  `rig_id`/`rig_name` per row.
- **`rig_optics.known_pixel_size`:** takes an optional `camera_lookup` callable, used first before
  the seed table. The `targets.py` caller passes a lookup built from `cameras`.

### 4.9 Frontend

- **Route & nav:** new route `/equipment` (`pages/Equipment.jsx` + `Equipment.css`). The nav item
  "Equipment" goes after Targets, with the lucide `Telescope` icon.
- **Client:** a clearly headed `// Equipment (R0)` section at the end of `api/client.js`.
- **Tabs:** Rigs (default), Cameras, Optics, Filters, Sites.
- **Detected setups banner** (shown when proposals exist): "AstroCat found 4 setups in your
  library". It opens a review panel with one card per proposal showing:
  - the name;
  - camera + optic (a dropdown of existing optics plus "create detected");
  - an editable modifier and binning;
  - filters as chips;
  - image count, last used, measured scale;
  - checkbox to accept.
  Then "Import from Telescopius" (only if available) and "Apply".
- **Rig cards:**
  - name and "Mounted" pill (a radio-like toggle);
  - Active toggle;
  - camera · optic (· modifier);
  - scale ″/px, FOV °×°, f/ratio;
  - a sampling badge against the default site's typical seeing;
  - a **scale check badge**: "measured 2.27″ ✓", or "declared 2.46″ vs measured 2.27″ (8% off,
    reducer?)";
  - subs and last used;
  - Edit / Delete.
- **Forms** are modal dialogs matching Admin's existing patterns. Computed values preview live as
  fields change.
- **Sites tab:**
  - A form: lat/lon, timezone select (default `Intl.DateTimeFormat().resolvedOptions().timeZone`),
    Bortle/SQM, typical seeing, default toggle.
  - A **horizon chart** (recharts `LineChart`, az 0–360 on x, alt on y). It shows the learned
    profile as a dashed line and the saved profile as a solid line, with "Use learned",
    "Import .hrz" and "Export .hrz" buttons.
- **ImageDetail:** one row in Equipment: "Rig: <name> (auto|manual)" with an inline select to
  override.
- **TargetDetail:** the By Filter & Rig table shows rig names when present.

## 5. Files touched

**New:**
- `alembic/versions/2026_09_27_1000-d7f9b3e41007_capture_time_provenance.py`
- `alembic/versions/2026_09_27_1100-e8a0c4f51008_equipment_and_sites.py`
- `models/equipment.py`, `schemas/equipment.py`, `api/equipment.py`
- `services/equipment_detection.py`, `services/equipment_assignment.py`, `services/telescopius.py`
- `tasks/equipment.py`
- `utils/capture_time.py`, `utils/header_values.py`, `utils/optics.py`, `utils/horizon.py`
- `scripts/recanonicalize_targets.py`, `scripts/backfill_capture_time.py`, `scripts/backfill_sites.py`
- `frontend/src/pages/Equipment.jsx` + `.css`
- `docs/features/EQUIPMENT.md`

**Modified:**
- `models/image.py` (P0 + R0 column blocks), `models/__init__.py`
- `services/targets.py` (MATCH canonicalisation, Sh2 cross-IDs, NONE sentinel)
- `services/data_migrations.py` (0004–0006)
- `extractors/fits_extractor.py`, `xisf_extractor.py`, `exif_extractor.py` (sites)
- `tasks/indexer.py` (3 single-line hooks)
- `api/images.py` (`rig_id` update and filter), `api/targets.py`, `utils/rig_optics.py`
- `main.py` (routers), `worker.py` (include), `config.py` (`telescopius_api_key`)
- `requirements.txt` (`tzdata`), `.env.example` (`TELESCOPIUS_API_KEY=` commented)
- `App.jsx`, `Layout.jsx`, `client.js`, `ImageDetail.jsx`, `TargetDetail.jsx`
- `docs/core/DATABASE_SCHEMA.md`, `docs/features/TARGETS.md` (canonicalisation note),
  `docs/development/UTILITY_SCRIPTS.md`

## 6. Testing (pytest from `backend/`, style of `test_targets.py` / `test_indexer_resilience.py`)

- **`test_targets.py` (extended):**
  - MATCH on `NGC3031` with Messier in the index → `M81`; MATCH on `C11` → `NGC7635`.
  - An unresolved light → `(None, 'NONE')`; non-light → `(None, None)`; MANUAL preserved.
  - `sh2_cross_ids` fixtures: Sh2-131↔IC1396 (Cl+N, size ratio 0.08) **merges**; Sh2-117↔NGC7000
    merges; a galaxy within 0.2° of an Sh2 region does **not**.
- **`test_recanonicalize_targets.py`** (mocked session, like the frame-type backfill tests):
  bulk-update SQL per remapped key, goal merge keeps the max, second run is a no-op.
- **`test_capture_time.py`** — every source row in §3.2, including:
  - char-array values;
  - `OffsetTimeOriginal "+02:00"` → 2 h earlier UTC;
  - GPS `[22, 54, 37]` + `"2018:12:23"` → `2018-12-23T22:54:37`;
  - mtime fallback → `(None, 'FILE_MTIME')`.
- **`test_header_values.py`:** sexagesimal variants (`"56d0m0.000s N"` → 56.0,
  `"0d30m0.000s W"` → −0.5, `"+51 28 40"`, `"-0:00:05"`); EXIF GPS lists; (0,0) → None.
- **`test_optics.py`:**
  - C11 2800 mm + 4.63 µm → 0.341″;
  - ZS73 346 mm + 3.8 µm → 2.265″;
  - ASI294 unlocked 2.315 µm + 200 mm → 2.387″;
  - 0.8 reducer multiplies scale by 1.25;
  - sampling 0.34″ at 2.5″ seeing → `over`;
  - `scale_check` 2.46 vs 2.27 → 8.4%, `mismatch`.
- **`test_equipment_detection.py`:**
  - bucket fixtures reproducing the live library yield 4 rig proposals (ZS73/ASI1600 2.27″,
    C11/ASI294 0.34″, EF200/ASI294-unlocked 2.46″, Sigma105/R7 6.5″) when the Telescopius optics
    are present;
  - camera-name normalisation merges `Canon Canon EOS R7`/`Canon EOS R7`, and
    `EOS R8`/`Canon [EOS R8]`/`Canon Canon EOS R8`;
  - junk names under 50 subs are dropped.
- **`test_telescopius_mapping.py`:** a sanitised fixture of the real `/equipment/user` payload
  (field names from §4.4, no key, ids kept). Covers idempotent re-import and the lens-vs-telescope
  heuristic.
- **`test_equipment_assignment.py`:**
  - scale within 7% picks the right rig of two on the same camera;
  - no scale + two rigs → None;
  - no scale + one rig → assigned;
  - MANUAL untouched;
  - site within 10 km;
  - `EXIF_LOCAL` + Europe/London in July → UTC −1 h, January → 0 h.
- **`test_horizon.py`:**
  - learned profile from synthetic samples (floor, per-bin percentile, cap, min_count);
  - `.hrz` round-trip;
  - wrap at 360.
- Frontend: `npm run build` and `npm run lint` clean.

## 7. Acceptance criteria (verified against the live library after deploy)

**P0 (after data migrations 0004–0006 show `applied` in Admin > Data Maintenance)**
1. No light has `target_key` in {`NGC3031`, `NGC224`, `NGC598`, `NGC5194`, `C11`}.
   - M81 = the former M81 + NGC3031 subs (≈1,391).
   - NGC7635 includes the former `C11` subs (≈645).
   - `SH2131` rows now roll up under `IC1396`.
   - The Targets list count drops accordingly.
2. `SELECT count(*) FROM images WHERE frame_type='LIGHT' AND target_source IS NULL` = 0. A second
   `backfill_targets()` run touches 0 rows.
3. Every light has `capture_time_source`. `FILE_MTIME` ≈ 23k. For N.I.N.A./SharpCap FITS,
   `capture_date_utc = capture_date`.
4. ≥ 24k lights have `site_latitude/longitude`, including the sexagesimal-header ones. Clusters sit
   at the two known site clusters (both ~56°N).

**R0**
5. The Equipment page shows detected setups. With the Telescopius import done, the four rigs above
   are proposed with correct optic names, and accepting them creates rigs whose scale check is
   within 5% of the measured scale (ASI294 unlocked + EF200 shows ~3%).
6. After assignment, ≥ 90% of plate-solved lights from the last 36 months have `rig_id`. No image
   is assigned when two rigs on the same camera are ambiguous.
7. Accepting the proposed ~56°N site (Europe/London) fills `capture_date_utc` for
   `EXIF_LOCAL`/`FITS_LOCAL` rows at that site. The learned horizon matches the prototype's
   (floor ~32°, E and SW ~24°). The `.hrz` export re-imports identically.
8. TargetDetail "By Filter & Rig" shows rig names for NGC7000 / IC1396. ImageDetail shows the rig
   and allows an override.
9. With `TELESCOPIUS_API_KEY` unset, the page works fully and hides the import button. With it
   set, no response or log line contains the key.

## 8. Execution plan

Follows [HANDOVER.md](HANDOVER.md) §3–4 (worktrees, no live-stack access for agents, trial merge,
rebuild **both** containers, VERSION bump, browser walkthrough with the user logged in).

| Step | Who | Scope | Estimate |
|---|---|---|---|
| A | 1 agent (Opus), worktree `feat/p0-data-foundations` | §3 entirely, with tests | 250–400k |
| A-deploy | orchestrator | Trial merge, tests, rebuild, watch data migrations 0004–0006 apply, check acceptance 1–4 with SQL | 60–100k |
| B1 | agent (Opus), worktree `feat/r0-equipment-backend` | §4.1–4.8 plus tests. Branches from main **after A merges** (needs P0 columns). | 350–500k |
| B2 | agent (Sonnet), worktree `feat/r0-equipment-frontend` | §4.9, against the §4.6 contract. Can start in parallel with B1, using the §4.6 shapes as the mock. | 200–300k |
| B-deploy | orchestrator | Merge B1 then B2 (conflict hotspots: `client.js`, `ImageDetail.jsx`, `TargetDetail.jsx`, `api/images.py`), then rebuild and walkthrough: run detection, Telescopius import, apply, assign, check acceptance 5–9 | 100–150k |
| **Total** | | | **~0.95–1.45M** |

## 9. Decisions (defaults chosen; change before step A if needed)

1. **Sh2 cross-IDs are automatic** with the §3.1 rule, plus an override dict. The migration summary
   lists every pair applied so it can be reviewed in Admin.
2. **The Telescopius key is env-only** (`TELESCOPIUS_API_KEY`), never stored in the DB or Redis.
   The key shared during research should be regenerated before it goes into `.env`.
3. **Sensor modes are separate camera rows**, not a modes sub-table.
4. **`capture_date` is left untouched.** Astronomy code uses `capture_date_utc` and ignores
   `FILE_MTIME`.
5. **Detection looks back 36 months by default.** Older gear is available via "include older" and is
   never auto-proposed as active.
