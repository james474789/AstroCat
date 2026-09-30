# Equipment & Sites (R0)

Status: **Backend shipped** (branch `feat/r0-equipment-backend`). Design:
`P0-R0-equipment-sites.md` §4.

## What it is

AstroCat knows **which rig** took each frame and **where** it was taken:

- **Cameras, optics, filters and rigs.** A rig is camera + optic, plus an optional
  reducer/Barlow (`modifier_factor`: 0.8 for a 0.8x reducer, 2.0 for a 2x Barlow),
  binning and a filter set. Up to five rigs can be marked **mounted** at once (several
  mounts imaging concurrently). The R1 recommender defaults to the mounted rigs and, with
  more than one, plans a separate target for each.
  **Target size.** Each rig also has an editable target-size window (Equipment > rig form >
  "Target size", entered in degrees, stored as `rigs.min_target_arcmin` / `max_target_arcmin`).
  Tonight only suggests targets inside it. Leave a box empty for the default: 22% of the field's
  short side up to 80% of its long side (the form shows the default as the placeholder and the
  rig row shows the effective window). The API returns the stored values plus
  `target_window_arcmin` (the effective `[min, max]`); a bound must be positive, at most 3600 and
  the minimum must be below the maximum (400 otherwise); `null` on a PUT clears it.
  Each rig card's "N subs" count and its **View images** button open Search with
  `?rig_id=<id>&frame_type=LIGHT`: all light frames on the rig, subs and masters (R0c).
  The count itself still counts subs only. Anyone can use these links.
- **Sites** have coordinates, an IANA timezone, sky quality (Bortle/SQM), typical
  seeing and a horizon profile. One site can be the **default**.
- **Images** get `rig_id` / `rig_source` (`AUTO` | `MANUAL`) and `site_id`.

Header telescope names are unreliable (`TELESCOP = "EQMod Mount"`), so optics are never
identified from `TELESCOP`. Rigs are identified from the camera plus the plate-solved scale.

## Detect, then confirm

`GET /api/equipment/detect` proposes cameras, optics, filters, rigs and sites from light
sub-frames captured in the last 36 months (`?include_older=true` for all history).
Nothing is created until the user accepts (`POST /api/equipment/detect/apply`). There is
**no data migration** that creates rigs. Proposals are cached for 10 minutes and the cache
is dropped on any equipment change.

- **Cameras.** Names are normalised to a matching key: lowercase, brackets and dashes
  removed, a repeated maker collapsed, and an implied maker added. So `Canon Canon EOS R7`
  and `Canon EOS R7` become `canon eos r7`, and `EOS R8`, `Canon [EOS R8]` and
  `Canon Canon EOS R8` become `canon eos r8`.
  - Cameras are grouped by key plus sensor size (either orientation; binned frames fold into
    the unbinned size). Another sensor mode of the same camera (the ASI294MM *unlocked*
    8288x5644 at 2.315 µm) is a separate camera row.
  - Blank names, frames under 320 px (thumbnails) and junk names are dropped. Junk names
    (`notAvailable`, phone models) are dropped only when they have fewer than 50 subs.
  - Pixel size comes from the median header `XPIXSZ` (÷ binning), else from the seed table
    (`SEED_SENSORS` in `utils/rig_optics.py`).
  - `is_color` is true when `BAYERPAT` is present, for DSLR makers, or for "MC" models;
    "MM" models are mono.
- **Rigs.** For each camera, valid solved scales are clustered (5%). A cluster needs at
  least 20 subs.
  - The predicted focal length is `206.265 × pixel_µm × binning / scale`.
  - It's matched to an existing optic (for example one imported from Telescopius) whose
    focal length × 1.0 / 0.8 / 0.7 is within 5%. A match at 0.8 or 0.7 sets the proposal's
    `modifier_factor`. If no optic matches, a new optic is proposed as `~345 mm (detected)`.
  - Filter bands are the `normalize_filter` buckets seen in that cluster.
- **Filters.** There is one proposal per filter band, with the raw spellings as
  `match_patterns`.
- **Sites.** Per-image coordinates are clustered within 5 km. A cluster needs at least 200
  frames. Proposals within 10 km of an existing site are marked `exists`.
  - Accepted sites take the browser's timezone unless it is overridden.
  - The first accepted site becomes the default if no default exists yet.

Accepting a rig also creates its camera, optic and filters if they don't exist yet.

## Telescopius import (optional)

`POST /api/equipment/import/telescopius` imports telescopes/lenses, cameras and filters
from `GET https://api.telescopius.com/v2.2/equipment/user`. Mounts are returned only as
suggestions for `mount_name`.

**Key handling.** The key is read from the `TELESCOPIUS_API_KEY` environment variable
(`Settings.telescopius_api_key`). It is never stored, never returned by any endpoint, and
never logged. Without it, `GET /api/equipment` reports `telescopius_available: false`, the
UI hides the button, and the import endpoint returns 400. Upstream failures return 502
with a message that never contains the key.

**Mapping.**
- A telescope entry is imported as a `LENS` when its focal length is 600 mm or less and its
  brand (or the first word of its label) is a lens maker; otherwise it's a `TELESCOPE`.
- Zoom lenses are imported at `focal_length_max`, with a note.
- Camera pixel size is derived from the sensor size in mm ÷ px (about 2% error, noted on
  the row). A pixel size measured from headers on a detected camera is never overwritten.
- Telescopius has no rigs or reducers, and `is_color` is always null there.

**Idempotence.** `external_ref = "telescopius:<id>"` means a re-import updates rows and
never duplicates them. `MANUAL` values are kept. An unchanged row is counted as `skipped`.

## Assignment

`app/services/equipment_assignment.py` holds the pure rules. Two things apply them:

- the Celery task `app.tasks.equipment.assign_equipment(scope)`;
- the indexer hook `assign_equipment_sync` (runs after target resolution; rigs/sites cached
  for 60 s; it never raises, so a failure can't break indexing).

The rules:

- **Rig.** The image's camera name must contain one of the camera's `match_patterns`, after
  the same name normalisation. The image dimensions must match the sensor (or the sensor ÷
  binning).
  - With a valid solved scale, the image goes to the rig whose declared scale (or measured
    scale, if the pixel size is unknown) is within 7%, nearest first.
  - Without one, the image is assigned if `FOCALLEN` (or the EXIF focal length) is within
    5% of exactly one rig's effective focal length. Failing that, it's assigned if exactly
    one *active* rig uses that camera.
  - Otherwise the rig stays NULL. AstroCat never guesses between two rigs.
  - `rig_source = 'MANUAL'` rows are never touched.
- **Site.** The image goes to the nearest site within 10 km of its coordinates.
  - An image with no coordinates gets the default site only if its rig's images that do
    have coordinates are at least 90% at the default site.
- **Timezone fill.** `FITS_LOCAL` and `EXIF_LOCAL` rows get a `capture_date_utc` once they
  have a site: the local time (`DATE-LOC` or `DateTimeOriginal`) is converted from the
  site's timezone with `zoneinfo`, so DST is handled.
  - Rows whose source is already UTC, or that carry an `OffsetTimeOriginal`, are never
    touched.
  - Some DSLR clocks run on UTC. For each camera, the task compares `DateTimeOriginal` with
    the GPS time on `GPS_UTC` frames, on dates when the site's zone differs from UTC. With at
    least 3 votes and an 80% majority for "UTC", that camera's local times are taken as UTC.
    The result is cached in Redis (`equipment:clock_modes`) for the indexer hook.
    Inconclusive evidence (winter-only samples, for example) falls back to the site
    timezone.

**The task** runs in keyset batches of 1000.
- `scope=unassigned` covers rows without a rig or without a site. `scope=all` recomputes
  every row.
- It also runs the default-site and timezone passes, and refreshes
  `rigs.measured_scale_arcsec` / `measured_count` (the median valid solved scale of the
  rig's assigned light subs).
- It clears `cache:targets:*`.

**Queueing.** Any create, update or delete of a camera, optic, rig or site (and accepting
proposals) queues a **debounced** `scope=all` run.
- There is at most one pending task (Redis key `equipment:assign:pending`); a pending
  `unassigned` run is widened to `all`.
- A run lock (`equipment:assign:lock`) means two runs never overlap.
- `POST /api/equipment/assign?scope=unassigned|all` queues a run by hand.

**Manual override.** `PUT /api/images/{id}` with `"rig_id": <int>` sets a `MANUAL` rig.
With `"rig_id": null` it clears the rig and its source. If the field is absent, the rig is
left alone.

### Unassigned images (R0b)

Auto-assignment never guesses, so some frames keep `rig_id IS NULL`. Masters are hit
hardest: cropped or drizzled stacks never match the sensor dimensions. You can find these
frames and allocate them by hand, in bulk. Design: `R0b-rig-allocation.md`.

- **Scope.** Only light frames with subtype `SUB_FRAME` or `INTEGRATION_MASTER`. Planetary,
  deprecated and calibration frames are never listed or allocated.
- **Manual.** Allocations are written as `rig_source = 'MANUAL'`, so the batch task and the
  indexer hook never overwrite them (not even `scope=all`).
- **Equipment → Rigs → "Unassigned images".** Rig-less frames are bucketed by what
  identifies a rig physically (`app/services/rig_allocation.py`):
  - **camera**: the normalised camera name;
  - **binning, derived** against the camera's native (largest) sensor from the configured
    cameras or the seed table: frame dimensions first, then XPIXSZ. Header binning is only
    a fallback, because drivers label the same sensor mode differently;
  - **calculated focal length**: `206.265 × effective pixel ÷ solved scale`, where the
    effective pixel is XPIXSZ or native pixel × binning. Unsolved frames use `FOCALLEN` /
    the EXIF focal length. Focal lengths cluster at 5%. Frames with no focal evidence form
    one "unknown" bucket per camera and binning.

  Subs and masters share a bucket; cropped masters keep the rig's focal length. Drizzled
  masters calculate to a multiple of it, so they land in their own bucket. Each bucket
  shows its focal range, sub/master counts, frame sizes, filters, exposure, dates and why
  auto-assignment failed.
  - **Suggestion.** A bucket pre-selects a rig when `assign_rig` matches exactly, or when
    one rig on the same camera has the same effective pixel size (i.e. binning) and an
    effective focal length within 5%. Otherwise nothing is pre-selected.
  - **Bulk assigning.** An admin ticks buckets. Picking a rig ticks that bucket, and
    "Select all with a rig" ticks every bucket that has one. **Assign selected** applies
    each bucket's own rig in one request. The server re-derives each bucket's members from
    its key, so ids never come from the client. Buckets that changed since the page loaded
    are skipped and reported (409 if all of them did). Anyone can view the panel.
  - **View images (R0c).** A bucket's count and its **View images** link open Search with
    `?rig_bucket=<key>`. The server resolves the key to the bucket's current members with
    the same grouping code as the panel, so Search shows exactly that bucket, unsolved
    frames included. Bulk actions there (Assign Rig…, Set Frame Type…, CSV export, …) apply
    to exactly those images. A key that no longer exists (e.g. the bucket was just
    assigned) matches nothing, and Search says the bucket no longer exists.
    Design: `R0c-equipment-click-through.md`.
- **Search.** The Rig filter offers Any / Unassigned / each rig. **Assign Rig…** allocates
  a rig to every light sub and master in the current results; other frames are reported
  as skipped. "Clear rig" resets them to auto-assignment.
- **Counts.** `rig.image_count` and `rigs.measured_scale_arcsec` still count light subs
  only, so manually allocated masters don't change them. Manually allocated subs do feed
  the measured scale on the next task run.

## Optics (computed, not stored)

`app/utils/optics.py` computes these for each rig in the API:

- `scale` = `206.265 × pixel_µm × binning / (focal × modifier)`
- `fov_deg`
- `focal_ratio`
- `effective_focal_mm`
- `sampling`: seeing ÷ scale. 1–3 px per FWHM is `ok`, below that is `under`, above is
  `over`. It uses the default site's typical seeing, or 2.5″.
- `scale_check`: declared vs measured scale. `delta_pct` is relative to the measured
  value, and the verdict is `ok` within 5%.

## Horizon

- `GET /api/sites/{id}/horizon/learned` learns a profile from the site's light subs.
  - Each frame's altitude and azimuth come from `CENTALT`/`CENTAZ`, or are computed from
    RA/Dec (the plate solve, else `OBJCTRA`/`OBJCTDEC`) plus `capture_date_utc`. Frames
    taken with the Sun above −6° are dropped.
  - The floor is the 5th percentile of all altitudes. Each 15° bin with at least 50 samples
    takes its own 5th percentile, clipped to [15°, floor + 10°]; other bins use the floor.
  - The result is cached for 1 hour.
- `PUT /api/sites/{id}/horizon` saves a profile. `POST .../horizon/import` (a multipart
  `.hrz` file) and `GET .../horizon/export` round-trip the N.I.N.A. format: one `az alt`
  pair per line, `#` comments, sorted, with azimuth wrapped into [0, 360).

## Targets integration

On `GET /api/targets/{key}`, `by_filter_rig` rows gain `rig_id` / `rig_name`.
- Subs with an assigned rig are grouped per (filter, rig) and labelled with the rig's name,
  camera and effective focal length.
- Other subs fall back to the camera + scale clustering.
- `rig_optics.known_pixel_size` looks up the cameras table before the seed table.

## API summary

See the R0 API contract in the design doc. Reads need a logged-in user; writes need admin.

| Endpoint | Purpose |
|---|---|
| `GET /api/equipment` | cameras, optics, filters, rigs (with computed optics), sites, `telescopius_available`, `max_mounted_rigs` |
| `POST/PUT/DELETE /api/equipment/{cameras,optics,filters,rigs}[/{id}]` | CRUD. Deleting a camera or optic that a rig uses returns 409. |
| `POST/DELETE /api/equipment/rigs/{id}/mount` | Mount / unmount. Other mounted rigs stay mounted; a sixth mount returns 409. |
| `GET /api/equipment/detect`, `POST /api/equipment/detect/apply` | Proposals / accept |
| `POST /api/equipment/import/telescopius` | Optional import |
| `POST /api/equipment/assign?scope=` | Queue assignment |
| `GET /api/equipment/unassigned` | Rig-less light subs and masters bucketed by camera, derived binning and calculated focal length: `{total, groups}` (R0b) |
| `POST /api/equipment/unassigned/assign` | Admin. `{items: [{key, rig_id}]}`: allocate buckets as `MANUAL`. Returns `{updated_count, results, stale_keys}`; 409 if every bucket changed. |
| `PUT /api/images/bulk/rig?new_rig_id=<id or none>&<search filters>` | Allocate a rig to the light subs and masters in the results; others are counted as skipped |
| `GET/POST/PUT/DELETE /api/sites[/{id}]` | Sites (`is_default` exclusive) |
| `GET /api/sites/{id}/horizon/learned`, `PUT .../horizon`, `POST .../horizon/import`, `GET .../horizon/export` | Horizon |

Image list, search and bulk endpoints accept `?rig_id=` (an id, or `none` for images with no rig), `?rig_bucket=` (an Unassigned bucket key; R0c) and `?site_id=`. `ImageDetail`
gains `rig_id`, `rig_name`, `rig_source` and `site_id`. `site_name` is the assigned site's
name when there is one, else the header value.
