# F1 — Frame-Type Classification (Light / Dark / Flat / Bias)

Status: **Proposed** · Size: S · Value: 5 · Read first: [README.md](README.md) (shared contracts, migration rule)

## 1. Problem

AstroCat has no idea whether a file is a light frame or a calibration frame. Astrophotographers
routinely store darks, flats, bias/offset and dark-flat frames alongside lights, often in the same
tree. Today:

- `Image.subtype` (`SUB_FRAME`, `INTEGRATION_MASTER`, `INTEGRATION_DEPRECATED`, `PLANETARY`) describes
  the *processing stage*, not the *frame type*. A master dark and a master light are both
  `INTEGRATION_MASTER`.
- Nothing reads the FITS `IMAGETYP` keyword, even though virtually every capture program writes it
  (it is available in `raw_header`).
- Consequences:
  - Integration totals are inflated. Dashboard `/api/stats/overview`, `/api/stats/by-month`,
    `/api/stats/top-objects`, Catalogs "cumulative exposure" and `/api/stats/fits` all sum
    `exposure_time_seconds` over darks and flats too.
  - Bulk plate solving (`app/tasks/bulk.py` around lines 50 and 179) submits darks, flats and bias
    frames to Astrometry.net. These always fail and waste the submission quota.
  - The user cannot find "all my darks at -10°C, gain 100, 300 s".
- Features F2, F7 and F16 (and the future calibration matching) need a reliable "lights only"
  predicate.

## 2. Goals / Non-goals

**Goals**
1. Persist a `frame_type` for every image, derived automatically at index time.
2. Priority order: FITS/XISF header → file name → directory names → default `LIGHT`.
3. Manual override per image and in bulk, which survives rescans.
4. Backfill existing rows **without re-reading files** (use `raw_header` + `file_path` from the DB).
5. Exclude non-light frames from integration statistics and from bulk plate-solve submission.
6. Filter, badge and edit frame type in the UI.

**Non-goals**
- Calibration matching (lights → darks/flats). That is a separate future feature, which this unblocks.
- Detecting frame type from pixel statistics (e.g. DSLR lens-cap darks with no metadata).
- Changing the meaning of `subtype`.

## 3. Design

### 3.1 Data model

Add to `backend/app/models/image.py` (exact contract, see README §2.1):

```python
class FrameType(str, enum.Enum):
    """Acquisition frame type (orthogonal to ImageSubtype, which is processing stage)."""
    LIGHT = "LIGHT"
    DARK = "DARK"
    FLAT = "FLAT"
    BIAS = "BIAS"           # includes "Offset" (Sony/ZWO/PixInsight terminology)
    DARK_FLAT = "DARK_FLAT" # a.k.a. flat-dark

# in Image, in a block commented "# Frame type (F1)" above "# Timestamps":
frame_type = Column(Enum(FrameType, name="frametype"), nullable=False,
                    default=FrameType.LIGHT, server_default="LIGHT", index=True)
frame_type_source = Column(String(20), nullable=True)  # HEADER | FILENAME | PATH | DEFAULT | MANUAL
```

`frame_type_source IS NULL` means "never classified", which is what the backfill uses as its
resume marker. `MANUAL` means the user set it, and the indexer must never overwrite it (same idea as
`rating_manually_edited`).

Add a composite index for the common stats query: `Index('ix_images_frame_subtype', 'frame_type', 'subtype')`.

### 3.2 Migration `f1a7c3e90001` (down_revision `a8f9e0d1c2b3`)

Must be defensive (fresh DBs already have everything from `create_all`):

```python
def upgrade():
    conn = op.get_bind()
    op.execute("""
      DO $$ BEGIN
        CREATE TYPE frametype AS ENUM ('LIGHT','DARK','FLAT','BIAS','DARK_FLAT');
      EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    cols = {c['name'] for c in sa.inspect(conn).get_columns('images')}
    if 'frame_type' not in cols:
        op.add_column('images', sa.Column('frame_type',
            postgresql.ENUM(name='frametype', create_type=False),
            nullable=False, server_default='LIGHT'))
    if 'frame_type_source' not in cols:
        op.add_column('images', sa.Column('frame_type_source', sa.String(20), nullable=True))
    idx = {i['name'] for i in sa.inspect(conn).get_indexes('images')}
    if 'ix_images_frame_type' not in idx:
        op.create_index('ix_images_frame_type', 'images', ['frame_type'])
    if 'ix_images_frame_subtype' not in idx:
        op.create_index('ix_images_frame_subtype', 'images', ['frame_type', 'subtype'])
```

`downgrade()` drops the indexes, the columns, and then `DROP TYPE IF EXISTS frametype`, each guarded.

The migration does **not** classify rows. It is fast (`ADD COLUMN ... DEFAULT` is metadata-only on
PG ≥ 11). Classification is done by the backfill (§3.6).

### 3.3 Classifier — `backend/app/services/frame_type.py` (new, pure, no DB)

```python
def classify_frame_type(raw_header: dict | None, file_path: str) -> tuple[FrameType, str]:
    """Return (frame_type, source). Never raises."""
```

**Step 1: header.** Look for keys in this order: `IMAGETYP`, `FRAMETYP`, `FRAME`, `IMAGETYPE`.
Header keys may be any case in XISF-converted dicts, so do a case-insensitive key lookup. Normalize
the value by lowercasing it, replacing `_ - .` with spaces, collapsing whitespace, and stripping the
words `frame`, `field`, `master`, `calibrated`. Then map:

| Normalized value contains (checked in this order) | FrameType |
|---|---|
| `dark flat`, `flat dark`, `darkflat`, `flatdark` | `DARK_FLAT` |
| `bias`, `offset`, `zero` | `BIAS` |
| `dark` | `DARK` |
| `flat` | `FLAT` |
| `light`, `object`, `science` | `LIGHT` |

Real values this must handle (put them all in tests):
N.I.N.A. `LIGHT`/`DARK`/`FLAT`/`BIAS`/`DARKFLAT`; SGP/MaxIm `Light Frame`/`Dark Frame`/`Flat Field`/
`Flat Frame`/`Bias Frame`; INDI/Ekos/KStars `Light Frame`/`Dark Frame`/`Flat Frame`/`Bias Frame`;
ASIAIR/ASIStudio `Light`/`Dark`/`Flat`/`Bias`; PixInsight WBPP masters `Master Dark`/`Master Flat`/
`Master Bias`/`Master Light`; APT `Light`/`Dark`/`Flat`/`Bias`; Siril `Offset`.
An unrecognized value (e.g. `Tricolor`, `Focus`) falls through to steps 2–4 and is logged at DEBUG.

If the value contained `master`, also return a hint `is_master=True`. The classifier returns a small
dataclass `FrameTypeResult(frame_type, source, is_master_hint)`. The indexer uses the hint only when
**creating** a new row: it sets `subtype=INTEGRATION_MASTER` for new rows only, and never changes the
subtype of an existing row.

**Step 2: file name** (source `FILENAME`). Tokenize the stem on `[\s_\-.()\[\]]+` and lowercase it.
If any token is exactly one of the keyword forms below, classify:

- DARK_FLAT: `darkflat`, `darkflats`, `flatdark`, `flatdarks`, `df`; also the two-token sequences
  `dark flat` and `flat dark`.
- BIAS: `bias`, `biases`, `offset`, `offsets`
- DARK: `dark`, `darks`
- FLAT: `flat`, `flats`
- LIGHT: `light`, `lights`

Check DARK_FLAT first. If tokens of more than one type appear, the first token in the stem wins,
except that DARK_FLAT always wins over DARK or FLAT alone. For example, N.I.N.A.'s default pattern
`2026-03-14_M31_FLAT_Ha_1.2s_0001.fits` is FLAT and `DARK_300.00s_-10C_G100_0003.fits` is DARK.

**Step 3: directory names** (source `PATH`). Walk the parent directories from nearest to farthest,
but stop at the configured mount root (the matching entry of `settings.image_paths_list`, and never
above it). Tokenize each segment the same way. The **first token** of the segment must be a keyword (or
`master` followed by a keyword), and every remaining token must be "non-word-like". That means each
remaining token matches `^[-+]?\d` (temperatures, exposures, dates, gains such as `-10c`, `300s`,
`2026`, `g100`) or is a known filter name (`l`, `r`, `g`, `b`, `ha`, `oiii`, `sii`, `lum`, `red`,
`green`, `blue`). This prevents false positives:

| Segment | Result |
|---|---|
| `Darks`, `DARK`, `darks_-10C_300s`, `Master Darks`, `Flats Ha`, `Bias`, `Offsets`, `FlatDarks` | classified |
| `Dark Sky Site`, `Darkside`, `Flatiron Park`, `Light Pollution Tests`, `Lights Out Nebula` | **not** classified |

`LIGHT`/`Lights` directories classify as LIGHT with source `PATH`. This matters when a parent folder
elsewhere says `Darks`, because the nearest directory wins.

**Step 4: default** is `(LIGHT, "DEFAULT")`.

The header is authoritative over the path. If the header says `LIGHT` but the path says `Darks`, the
result is LIGHT with source HEADER.

### 3.4 Indexer integration — `backend/app/tasks/indexer.py::_process_image_impl`

1. After extraction and `sanitize_metadata`, compute
   `ft = classify_frame_type(metadata.get("raw_header"), file_path)`. This is cheap and needs no file IO.
2. Thumbnail STF: flats and bias frames look fine with the STF stretch, so leave `is_subframe` logic unchanged.
3. **Existing row:** `if image.frame_type_source != "MANUAL": image.frame_type, image.frame_type_source = ft.frame_type, ft.source`.
4. **New row:** set `frame_type`/`frame_type_source`. If `ft.is_master_hint`, set
   `subtype=ImageSubtype.INTEGRATION_MASTER`.
5. **Catalog matching:** skip `SyncCatalogMatcher` when `frame_type != LIGHT`. Flats can
   accidentally carry a copied WCS, which would produce bogus matches. When a row changes to
   non-LIGHT, delete its existing `ImageCatalogMatch` rows (only those with `match_source != 'MANUAL'`).
6. For the minimal-record path (extraction failed, `metadata = {}`), still run the classifier with
   `raw_header=None`. Path-based classification works without a header.

### 3.5 Plate-solve exclusion — `backend/app/tasks/bulk.py`

In both selection queries (around lines 50 and 179), add `Image.frame_type == FrameType.LIGHT`. In
the single-image rescan endpoint `POST /api/images/{id}/rescan`, return `409` with message
`"Calibration frames (DARK) are not plate-solved"` for non-light frames, unless `?force=true`.

### 3.6 Backfill — `backend/app/scripts/backfill_frame_types.py` (new)

- Uses sync `SessionLocal`. Selects `id, file_path, raw_header, frame_type_source` in keyset-paginated
  batches of 1000 (`WHERE id > :last_id ORDER BY id`), by default only rows where
  `frame_type_source IS NULL`. `--all` reclassifies everything except `MANUAL`. `--dry-run` prints a
  summary table: counts per (old → new, source).
- Applies the same catalog-match cleanup as §3.4 step 5 to rows that become non-LIGHT.
- Commits per batch and prints progress. It is idempotent and resumable.
- Also wrap it as the Celery task `app.tasks.indexer.backfill_frame_types` (routed to the `indexer`
  queue automatically, since it lives in `app.tasks.indexer`), and add an Admin page button,
  "Reclassify frame types", next to the existing bulk-metadata actions.
- **Auto-run:** because this is a read-only derivation from DB data, add
  `python -m app.scripts.backfill_frame_types` to the backend startup chain after `seed_named_stars`.
  It is a no-op once every row has a source. Update `CLAUDE.md` "On startup" and
  `docs/development/UTILITY_SCRIPTS.md`.

Expected speed: about 100k rows per minute (no file IO).

### 3.7 Shared helper — `backend/app/utils/frame_filters.py` (new)

```python
def light_subs_clause():
    """Frames that count toward integration: LIGHT sub-frames."""
    return and_(Image.frame_type == FrameType.LIGHT, Image.subtype == ImageSubtype.SUB_FRAME)

def lights_clause():
    return Image.frame_type == FrameType.LIGHT
```

### 3.8 Statistics changes (lights-only by default)

| Endpoint / place | Change |
|---|---|
| `GET /api/stats/overview` | `total_exposure`/integration: use `light_subs_clause()`. Add `calibration_counts: {DARK: n, FLAT: n, BIAS: n, DARK_FLAT: n}` to the response. |
| `GET /api/stats/by-month`, `/top-objects` | Filter with `lights_clause()`. |
| `GET /api/catalogs/*` `cumulative_exposure_seconds` / `image_count` | Filter the stats subquery with `lights_clause()` (4 places in `app/api/catalogs.py`). |
| `GET /api/stats/fits` | Add an optional `frame_type` query param, defaulting to `LIGHT`. Also accept `ALL`. |
| `GET /api/stats/by-subtype` | Unchanged. Add `GET /api/stats/by-frame-type` returning `[{frame_type, count, total_exposure_hours}]`. |

Remember to bump or clear the Redis stats cache keys (`cache:stats:*`) after the backfill runs. The
backfill script should `DEL` the `cache:stats:*` keys at the end.

### 3.9 API

- `schemas/image.py`: add `frame_type: FrameType = FrameType.LIGHT` and
  `frame_type_source: Optional[str] = None` to `ImageBase`. Add `frame_type: Optional[FrameType] = None`
  to `UpdateImageRequest`.
- `PUT /api/images/{id}`: when `frame_type` is provided, set it and set `frame_type_source="MANUAL"`.
  Apply the catalog-match cleanup from §3.4 step 5 when it changes to non-LIGHT, or run the matcher
  when it changes to LIGHT and the image is solved.
- `_build_image_query`: add a `frame_type: Optional[str] = None` kwarg (at the end, per README §4)
  that accepts one value or a comma list (`DARK,FLAT`). Add the matching `Query` param to the 4
  endpoints that re-declare filters.
- New `PUT /api/images/bulk/frame-type?new_frame_type=DARK&<same filters as bulk/subtype>`. Model it
  on `bulk_update_subtype` (images.py ~line 869). It sets `frame_type_source="MANUAL"` and returns
  `{"updated": n}`.
- CSV export: add a `frame_type` column.

### 3.10 Frontend

- `api/client.js`: `bulkUpdateFrameType(newFrameType, searchParams)`. `fetchImages` passes `frame_type`
  through automatically if it is in the params object.
- `pages/Search.jsx`: add a "Frame type" `<select>` (All / Lights / Darks / Flats / Bias / Dark flats)
  next to the subtype filter (~line 446), wired into URL search params like `subtype`.
  **The default is Lights** (owner decision, 2026-09-25):
  - With no `frame_type` URL param, Search sends `frame_type=LIGHT`. Choosing "All" writes
    `frame_type=ALL` to the URL and sends no filter. The backend treats `ALL` as "no filter", and its
    own default stays unfiltered, so API clients and CSV export are unchanged unless they pass a value.
  - The Lights default is a normal filter state: it appears as a `FilterChips` chip ("Lights only",
    removable, which switches to All), and "Clear filters" resets it to Lights, not All.
  - Bulk actions (subtype, metadata, frame type) act on the **current** filters, so the default
    applies to them too. The bulk confirm dialog must state the frame-type scope. This matters for the
    "Set frame type…" bulk action, since re-labelling mislabelled darks requires choosing All or Darks first.
  - Links into Search that target calibration frames (e.g. the Dashboard "Calibration library" tile)
    must pass `frame_type` explicitly (`/search?frame_type=DARK`).
  - Existing inbound links (Catalogs, Dashboard, ImageDetail) intentionally pick up the Lights
    default. No change is needed.

  Add a bulk action "Set frame type…" next to the existing bulk subtype change (~line 845).
- `components/images/ImageCard.jsx`: for non-LIGHT frames, show a badge (`DARK`, `FLAT`, `BIAS`,
  `D-FLAT`) with a new `badge-calib` class, in a neutral grey/blue CSS variable.
- `pages/ImageDetail.jsx`: add a "Frame type" selector beside the subtype selector (~line 684), with
  a small muted "(auto: header)" / "(manual)" hint from `frame_type_source`.
- `pages/Dashboard.jsx`: add a small "Calibration library" tile showing the counts from
  `overview.calibration_counts`.
- `pages/FitsStats.jsx`: add a frame-type filter dropdown, defaulting to Lights.

## 4. Files touched

New: `app/services/frame_type.py`, `app/utils/frame_filters.py`, `app/scripts/backfill_frame_types.py`,
`alembic/versions/2026_09_25_1000-f1a7c3e90001_add_frame_type.py`, `tests/test_frame_type.py`,
`docs/features/FRAME_TYPES.md`.
Modified: `models/image.py`, `schemas/image.py`, `tasks/indexer.py`, `tasks/bulk.py`, `api/images.py`,
`api/stats.py`, `api/catalogs.py`, `api/fits_stats.py`, the startup `command:` chain in
`docker-compose-dev.yml` and `docker-compose-example.yml` (plus the owner's local gitignored
`docker-compose.yml`, which must be mentioned in the PR description), `rebuild_and_seed.ps1`, `README.md`, frontend `Search.jsx`, `ImageCard.jsx/.css`, `ImageDetail.jsx`, `Dashboard.jsx`,
`FitsStats.jsx`, `client.js`, `CLAUDE.md`, `docs/core/DATABASE_SCHEMA.md`,
`docs/development/UTILITY_SCRIPTS.md`.

## 5. Testing

Unit tests (`tests/test_frame_type.py`, no DB):
- Every header value in §3.3, parametrized.
- Header beats path, file name beats directory, nearest directory beats farther.
- The false-positive directory cases in §3.3 all resolve to `(LIGHT, DEFAULT)`.
- Case-insensitive header keys (`imagetyp`), non-string header values (int/None), and `raw_header=None`.
- Master hint (`Master Flat` → FLAT + `is_master_hint`).

Integration (mocked session, style of `test_indexer_resilience.py`):
- A `MANUAL` row is not overwritten on re-index.
- Catalog matches are removed when a solved image becomes DARK.
- The `bulk.py` selection excludes non-LIGHT frames.

Manual: run the backfill with `--dry-run` on the real library and review the summary, then open
Dashboard, Catalogs and FITS Analytics and confirm the integration totals dropped by the calibration
share.

## 6. Acceptance criteria

1. After deploying and restarting, every row has non-null `frame_type_source`.
2. A N.I.N.A. dark (`IMAGETYP='DARK'`) is DARK/HEADER, a DSLR RAW in `/…/Darks/` is DARK/PATH, and a
   file in `/…/Dark Sky Site/M31/` is LIGHT/DEFAULT.
3. Setting frame type on the detail page persists across `POST /images/{id}/rescan` and a folder rescan.
4. Dashboard integration hours and Catalogs cumulative exposure exclude calibration frames.
5. Bulk plate solve submits zero non-light frames.
6. `alembic upgrade head` succeeds on both a fresh DB and an existing DB (run twice: idempotent).

## 7. Decisions (resolved with the owner, 2026-09-25)

- **Search defaults to Lights only.** See §3.10 for the URL, chip, and bulk-action rules.
- **PLANETARY images are excluded from integration totals.** `light_subs_clause()` requires
  `subtype == SUB_FRAME`, so they are excluded automatically. F2 shows them separately.
