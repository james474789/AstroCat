# Frame Type Classification (F1)

AstroCat classifies every indexed image as one of five acquisition frame types so
that calibration frames (darks, flats, bias, dark-flats) never inflate integration
statistics or get submitted for plate solving.

## Frame types

| Type | Meaning |
|------|---------|
| `LIGHT` | A science exposure of the target. The default. |
| `DARK` | A dark calibration frame. |
| `FLAT` | A flat-field calibration frame. |
| `BIAS` | A bias/offset frame ("Offset" in Sony/ZWO/PixInsight terminology). |
| `DARK_FLAT` | A flat-dark (dark frame taken at the flat's exposure settings). |

This is stored on `Image.frame_type` (enum, default `LIGHT`) and is orthogonal to
`Image.subtype`, which describes the *processing stage* (sub-frame vs. integration
master) rather than the acquisition type. A master dark and a master light are both
`INTEGRATION_MASTER` subtype, but different frame types.

`Image.frame_type_source` records how the value was derived: `HEADER`, `FILENAME`,
`PATH`, `DEFAULT`, or `MANUAL`. `NULL` means the row has never been classified --
this is the resume marker the backfill script uses. `MANUAL` rows (user-edited via
the API or UI) are never overwritten by the indexer or backfill.

## Classification cascade

Implemented in `backend/app/services/frame_type.py::classify_frame_type()`, a pure
function with no database access. Priority order:

1. **FITS/XISF header.** Checks `IMAGETYP`, `FRAMETYP`, `FRAME`, `IMAGETYPE` (in that
   order, case-insensitive keys). The value is normalized (lowercased, separators
   collapsed to spaces, `frame`/`field`/`master`/`calibrated` stripped) and matched
   against known vocabulary covering N.I.N.A., SGP/MaxIm, INDI/Ekos/KStars, ASIAIR,
   PixInsight WBPP masters, APT, and Siril. If the value contains "master", the
   result also carries an `is_master_hint`, which the indexer uses to set
   `subtype=INTEGRATION_MASTER` on newly-created rows only.
2. **File name.** Tokenizes the file stem and looks for `dark`/`darks`, `flat`/
   `flats`, `bias`/`biases`/`offset`/`offsets`, `light`/`lights`, and the various
   dark-flat spellings (`darkflat`, `flatdark`, `df`, or the adjacent two-token
   sequence). Dark-flat always wins over dark or flat alone; otherwise the first
   matching token in the stem wins.
3. **Directory names.** Walks parent directories from nearest to farthest, never
   above the configured mount root (`settings.image_paths_list`). A directory only
   classifies when its first token is a keyword (optionally `master` + keyword) and
   every remaining token looks like a qualifier (a leading digit -- temperature,
   exposure, date, gain -- or a known filter name). This is what lets `Darks`,
   `Master Flats`, or `darks_-10C_300s` classify while `Dark Sky Site`, `Darkside`,
   `Flatiron Park`, and `Light Pollution Tests` do not.
4. **Default.** `LIGHT` / `DEFAULT` if nothing else matched.

The header is authoritative over the path: if the header says `LIGHT` but the file
sits in a `Darks/` folder, the result is `LIGHT`/`HEADER`.

## Where it's applied

- **Indexer** (`app/tasks/indexer.py::_process_image_impl`): classifies right after
  metadata extraction. Existing rows keep their classification if
  `frame_type_source == "MANUAL"`. New rows get the master-hint subtype upgrade.
  Catalog matching is skipped for non-LIGHT frames, and any non-manual catalog
  matches are deleted when a row is (or becomes) non-LIGHT -- flats/darks can carry
  a copied WCS that would otherwise produce bogus matches.
- **Bulk plate solving** (`app/tasks/bulk.py`): both the bulk match and bulk
  astrometry-submission mount-point queries require `frame_type == LIGHT`.
- **Single-image rescan** (`POST /api/images/{id}/rescan`): returns `409` for
  non-light frames unless `?force=true`.
- **Statistics** (`app/api/stats.py`, `app/api/catalogs.py`,
  `app/api/fits_stats.py`): integration totals, monthly activity, top objects, and
  catalog cumulative exposure all exclude calibration frames by default via
  `app/utils/frame_filters.py`'s `light_subs_clause()` (LIGHT + SUB_FRAME) and
  `lights_clause()` (LIGHT, any subtype). `GET /api/stats/overview` also returns
  `calibration_counts`, and `GET /api/stats/by-frame-type` gives per-type
  count/exposure. `GET /api/stats/fits/` accepts `frame_type` (default `LIGHT`, or
  `ALL`).
- **Search UI**: defaults to Lights only (see below).

## Search UI: the Lights default

The Search page treats "Lights only" as its default filter state, not "no filter":

- With no `frame_type` URL parameter, Search sends `frame_type=LIGHT` to the API.
  Choosing "All Frame Types" writes `frame_type=ALL` to the URL and sends no
  filter -- the backend's own default (no `frame_type` param at all, e.g. from a
  direct API call) stays unfiltered.
- The Lights default is a normal, removable filter: it shows as a "Lights only"
  chip. Removing that chip switches to All; removing an explicit type (Darks, etc.)
  reverts to the Lights default. "Clear filters" resets to Lights, not All.
- Bulk actions (subtype, metadata sync, CSV export, "Set frame type...") act on the
  current filters, so the Lights default applies to them too -- relabeling
  mislabelled darks requires switching the Frame Type filter to All or Darks first.
  The bulk "Set frame type..." confirm dialog states the current scope.
- Links into Search that target calibration frames (e.g. the Dashboard "Calibration
  Library" tile) pass `frame_type` explicitly (`/search?frame_type=DARK`).

## Manual overrides

Setting frame type via `PUT /api/images/{id}` (`frame_type` field) or
`PUT /api/images/bulk/frame-type` always sets `frame_type_source=MANUAL`, which
protects it from being overwritten by the indexer or backfill. Changing to
non-LIGHT deletes non-manual catalog matches; changing a solved image back to LIGHT
re-runs catalog matching.

## Backfill

`backend/app/scripts/backfill_frame_types.py` classifies existing rows using only
the `raw_header`/`file_path` already stored in the database -- no file IO, so it
runs at roughly 100k rows/minute. It is keyset-paginated in batches of 1000, resumes
via `frame_type_source IS NULL`, supports `--all` (reclassify everything except
`MANUAL`) and `--dry-run` (print a transition summary without writing), and clears
the `cache:stats:*` Redis keys when it makes changes. It runs automatically on
every backend startup (see the `command:` chain in `docker-compose-dev.yml`/
`docker-compose-example.yml`, right after `seed_named_stars`) and is a no-op once
every row has a source. It is also available as the Celery task
`app.tasks.indexer.backfill_frame_types` and an Admin page "Reclassify frame types"
button (`POST /api/indexer/reclassify-frame-types`).

## Non-goals

- Calibration matching (associating lights with the darks/flats/bias frames that
  calibrate them) is a separate future feature this unblocks.
- Detecting frame type from pixel statistics (e.g. a DSLR dark with no metadata and
  a generic filename) is out of scope; such frames fall through to `LIGHT`/`DEFAULT`.
- `Image.subtype` semantics are unchanged.
