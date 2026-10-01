# F16 — Mosaic Detection & Coverage Map

Status: **Proposed** · Size: M–L · Value: 3 · Read first: [README.md](README.md) · Depends on: F1 schema (`frame_type`)

## 1. Problem

Imagers shoot **mosaics** (multiple overlapping panels) for targets larger than their field of view:
M31 with a long focal length, the Veil complex, Cygnus, Orion, the Heart & Soul. A mosaic is often
captured over months, and the recurring questions are:
- Which panels do I have, and how much integration does each one have, per filter?
- Which panel is behind? (e.g. "Panel 3 has 2 h of OIII, the others have 6 h".)
- Are there gaps in coverage?

AstroCat knows each solved image's center, radius, rotation and pixel scale, but has no notion of
panels or mosaics. Also:
- `images.field_boundary` (a PostGIS `Geography(POLYGON)`) exists in the model but is **never
  populated**. See `app/api/search.py` ~line 38 ("we need to ensure field_boundary is populated").
- The full-SIP WCS resolution logic (stored `wcs_header` → `raw_header` WCS → a WCS built from DB
  columns) lives **inline** in `GET /api/images/{id}` (`app/api/images.py` ~lines 506–620). Near-copies
  of it live in `CatalogMatcher._construct_wcs` and `SyncCatalogMatcher`.

## 2. Goals / Non-goals

**Goals**
1. Compute and persist each solved light frame's **sky footprint** polygon (`field_boundary`).
2. Group light frames into **pointings (panels)**: same rig, the same framing within tolerance.
3. Detect **mosaics**: connected groups of ≥ 2 panels of the same rig that partially overlap.
4. Persist mosaics and panels so the user can **confirm, reject, rename, and edit** them. Those
   decisions survive re-detection.
5. A **Mosaics** page and a **Mosaic detail coverage map**: panels drawn to scale on the sky, shaded
   by integration (total or per filter), with thumbnails, per-panel stats, "behind" highlighting, and
   gap display.

**Non-goals**
- Planning new panels or exporting to N.I.N.A. Framing Assistant (a possible follow-up; the data model allows it).
- Stitching or rendering a full-resolution mosaic.
- Using footprints for general spatial search. `search.py` could move to `ST_Intersects` once
  footprints are populated, but that is a separate follow-up and is listed in §8.

## 3. Concepts

| Term | Definition |
|---|---|
| **Rig key** | `(camera_key, round(pixel_scale_arcsec, 1 decimal ±5%))`. Pixel scale identifies the optical train better than `telescope_name`, which is often missing or inconsistent, and it handles reducers. Bucket the pixel scale with a tolerance of 5%: sort the distinct scales per camera and start a new bucket when a scale exceeds the bucket's first value × 1.05. |
| **Pointing / panel** | A cluster of light frames with the same rig whose centers are within `pointing_tol = 0.10 × min(field_width, field_height)` of each other. Meridian flips (180° rotation) stay in the same pointing because rotation is ignored for clustering. Dithers (a few pixels) are well inside the tolerance. |
| **Mosaic** | A connected component (≥ 2 panels) of the graph whose edges join panels of the **same rig** that *partially overlap*: `0.02 ≤ overlap_area / min(area_a, area_b) ≤ 0.60`. More than 60% means a reframe/revisit of the same target, not a mosaic panel. Less than 2% means they only touch. |

Masters: masters (`INTEGRATION_MASTER`) also get footprints and are assigned to the panel whose
center they fall within (same rig tolerance). They are shown as the panel's thumbnail, but they don't
count toward integration.

## 4. Design

### 4.1 WCS and footprint service — `backend/app/services/footprint.py` (new)

```python
def build_wcs(image) -> astropy.wcs.WCS | None
    # Same priority cascade as images.py detail endpoint:
    #   1) wcs_header (full SIP from Astrometry.net) overlaid on raw_header
    #   2) raw_header if it contains CRVAL1 and (CD1_1 or CDELT1) and is celestial
    #   3) constructed TAN WCS from ra/dec center, pixel_scale, rotation, parity (copy the
    #      CD-matrix construction from CatalogMatcher._construct_wcs exactly, incl. parity from
    #      raw_header['astrometry_parity'])
    # Needs width_pixels/height_pixels; returns None otherwise.

def compute_footprint(image) -> list[tuple[float, float]] | None
    # Sample the image border: 4 corners + 3 points along each edge (16 points), in pixel coords
    # (0.5 .. w+0.5 etc.), convert with wcs.all_pix2world(..., 0) → (ra, dec) degrees.
    # Return closed ring, counter-clockwise on the sky as seen by PostGIS (see ring orientation note).

def footprint_wkt(ring) -> str
    # 'POLYGON((lon lat, ...))' with lon = ra if ra <= 180 else ra - 360
```

Geometry notes (put them in the module docstring):
- The sky is treated as the PostGIS geography sphere, with `lon = RA` and `lat = Dec`. Edges of a TAN
  (gnomonic) projection map to **great circles**, which is exactly how geography interprets polygon
  edges. So a corner-only polygon is exact for a pure TAN field. The 16-point ring captures SIP
  distortion.
- The existing `center_location` points are written as `ST_MakePoint(ra, dec)` with `ra` in
  0..360. Normalize footprints to lon −180..180 and **write a test** that
  `ST_Intersects(field_boundary, center_location)` holds for fields centred at RA 0.2°, 179.9°, 180.1°
  and 359.8°, and for a field containing the celestial pole (Dec 89.5°). If PostGIS rejects 0..360 in
  one of them, normalize `center_location` the same way in this feature. Grep for `ST_MakePoint` to
  find all writers.
- Area and overlap: use `ST_Area(geog)` (m²) and `ST_Area(ST_Intersection(a, b))`. Only **ratios** are
  used, so the Earth-spheroid units cancel out, up to a negligible ellipsoid distortion. For display
  in deg², divide by `(111319.49)²`, which is approximate and fine for UI.
- Reject degenerate footprints: fewer than 4 unique points, NaN, or a field wider than 60°. Store NULL
  and log DEBUG. All-sky and fisheye DSLR shots will hit this, which is intended.

Wrappers:
- `set_footprint_sync(session, image)` sets `image.field_boundary = func.ST_GeogFromText(wkt)` (or
  None). It also sets `image.footprint_version = 1` (see §4.2).
- `set_footprint_async(db, image)` does the same for `astrometry.py`.

Call sites (README §4 ordering):
- `tasks/indexer.py::_process_image_impl`: after the WCS columns are set, when `is_plate_solved` and
  `frame_type == LIGHT`. Otherwise set `field_boundary = None`.
- `tasks/astrometry.py`: where the solve result is written (~line 304, after
  `image.wcs_header = wcs_header`).

Do **not** refactor `api/images.py`'s inline cascade in this PR, because it is a merge hotspot. Leave
`# TODO(F16): use services.footprint.build_wcs` next to it. A follow-up PR can dedupe.

### 4.2 Data model

New `backend/app/models/mosaic.py`:

```python
class Mosaic(Base):
    __tablename__ = "mosaics"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=True)           # user-editable; auto default from targets
    auto_name = Column(String(200), nullable=True)      # derived, e.g. "M31 · 4 panels"
    status = Column(String(12), nullable=False, default="DETECTED")  # DETECTED | CONFIRMED | REJECTED
    source = Column(String(8), nullable=False, default="AUTO")       # AUTO | MANUAL
    camera_key = Column(String(100)); camera_name = Column(String(100))
    pixel_scale_arcsec = Column(Float)
    ra_center_degrees = Column(Float); dec_center_degrees = Column(Float)
    coverage = Column(Geography('MULTIPOLYGON', srid=4326), nullable=True)  # union of panel footprints
    coverage_area_deg2 = Column(Float); hull_area_deg2 = Column(Float); gap_fraction = Column(Float)
    panel_count = Column(Integer, default=0)
    light_count = Column(Integer, default=0); light_seconds = Column(Float, default=0)
    first_capture = Column(DateTime); last_capture = Column(DateTime)
    object_names = Column(JSONB)                        # distinct header object_name values (panel suffixes stripped)
    notes = Column(Text)
    detected_at = Column(DateTime); created_at / updated_at
    panels = relationship("MosaicPanel", back_populates="mosaic", cascade="all, delete-orphan",
                          order_by="MosaicPanel.panel_index")

class MosaicPanel(Base):
    __tablename__ = "mosaic_panels"
    id = Column(Integer, primary_key=True)
    mosaic_id = Column(Integer, ForeignKey("mosaics.id", ondelete="CASCADE"), nullable=False, index=True)
    panel_index = Column(Integer, nullable=False)       # 1-based, reading order (see §4.4 step 6)
    label = Column(String(50))                          # user-editable, default "P{index}"
    ra_center_degrees = Column(Float); dec_center_degrees = Column(Float)
    rotation_degrees = Column(Float)                    # median of member rotations (mod 180 aware)
    footprint = Column(Geography('POLYGON', srid=4326)) # footprint of the representative frame
    light_count = Column(Integer, default=0); light_seconds = Column(Float, default=0)
    by_filter = Column(JSONB)                           # [{"filter": "Ha", "subs": n, "seconds": s}]
    cover_image_id = Column(Integer, ForeignKey("images.id", ondelete="SET NULL"))
    first_capture = Column(DateTime); last_capture = Column(DateTime)
```

Add to `Image` (block `# Mosaic (F16)`):

```python
mosaic_panel_id = Column(Integer, ForeignKey("mosaic_panels.id", ondelete="SET NULL"), nullable=True, index=True)
footprint_version = Column(Integer, nullable=True)   # NULL = footprint never computed
```

Migration `f16da61c0004` (see README §3 for `down_revision`) creates both tables defensively, adds
the two image columns, the FK and index, and a **GiST index on `images.field_boundary`** if one is
missing (`ix_images_field_boundary_gist`). Check `sa.inspect(conn).get_indexes('images')` and also
`pg_indexes`, because GeoAlchemy2 may have auto-created `idx_images_field_boundary` via `create_all`.

Import both models in `app/models/__init__.py`.

### 4.3 Footprint backfill — `backend/app/scripts/backfill_footprints.py`

Keyset batches of 500 over `is_plate_solved AND frame_type='LIGHT' AND footprint_version IS NULL`.
It builds footprints from DB columns and JSONB only (no file IO) and bulk-updates using
`ST_GeogFromText`. It is expected to be CPU-bound on astropy WCS: about 1–3 k images/s. Add it to the
startup chain after the F1 backfill. It is a no-op when complete. `--all` recomputes everything.

### 4.4 Detection algorithm — `backend/app/services/mosaics.py` (new)

The pure core has no DB access and runs on plain dataclasses, which makes it unit-testable. It uses
numpy only. **Do not add scipy.** The data sizes below don't need it.

```python
@dataclass
class Frame:  id, rig_key, ra, dec, width_deg, height_deg, rotation, capture, exposure,
              filter_name, object_name, is_master, footprint_ring  # ring as [(ra,dec)...]

def detect(frames: list[Frame]) -> list[DetectedMosaic]
```

1. **Partition by rig key** (§3).
2. **Pointing clustering** within each rig: greedy grid-hash clustering on unit vectors.
   - Convert (ra, dec) to 3-D unit vectors. Hash into cells of size `pointing_tol` using
     `(floor(dec/tol), floor(ra*cos(dec)/tol))`. Near the poles (|dec| > 80°), use a single band keyed
     only by the dec cell.
   - Process frames sorted by capture time. Assign each frame to the nearest existing cluster centroid
     within `pointing_tol`, checking the 9 neighbouring cells, or else start a new cluster. Update the
     centroid incrementally (vector mean, renormalized).
   - Complexity is about O(n). For 100 k frames, it takes well under a few seconds in Python and numpy.
3. **Panel footprint**: for each pointing, pick a representative frame. That is the frame whose
   center is closest to the centroid, preferring frames with an SIP `wcs_header` when available.
   Its ring becomes the panel footprint.
4. **Overlap graph**: for each rig, compare pointing pairs only when
   `angular_distance(centers) < (diag_a + diag_b) / 2`, which is a cheap prefilter (pointing counts
   are small, hundreds to low thousands per rig). Compute the overlap fraction in PostGIS or with a
   pure-Python spherical polygon clip. **Recommendation:** do the heavy step in SQL. Insert the
   candidate pairs' rings into a temp table and run one query:
   `SELECT a, b, ST_Area(ST_Intersection(fa, fb)) / LEAST(ST_Area(fa), ST_Area(fb))`. The pure core
   then takes an injected `overlap_fn(ring_a, ring_b) -> float`. Tests pass a planar approximation
   (Shapely is not installed, so use a simple Sutherland–Hodgman clip on gnomonic-projected
   coordinates as the test helper, and also as a fallback).
5. **Components**: union-find over edges with `0.02 ≤ f ≤ 0.60`. Keep components with ≥ 2 panels.
   Extra confidence signals (stored in a `score`, not required): stripped `object_name` is equal
   across panels, the panels were captured within 365 days, and headers contain `Panel`/`P\d` suffixes.
   Also detect **explicit mosaics** from header names: if frames of one rig have object names that
   differ only by a panel suffix (use F2's regex from `F2-target-integration.md` §3.1 step 4; copy it
   into this module if F2 has not merged), union those pointings even if their overlap is < 2%.
   N.I.N.A. mosaics with no overlap margin still form a mosaic this way.
6. **Panel ordering**: project panel centers gnomonically around the mosaic center, with +y = north
   and +x = east flipped for sky view (east left). Sort by rows (y descending, rows grouped within half
   a panel height), then x ascending. Assign `panel_index` 1..n.
7. **Masters**: after panels exist, assign each light master to the panel whose center is within
   `pointing_tol` (same rig). Otherwise leave it unassigned.
8. **Gaps**: in SQL, `coverage = ST_Multi(ST_Union(panel footprints))` and `hull = ST_ConvexHull(coverage)`.
   `gap_fraction = 1 - area(coverage)/area(hull)`. Holes inside the hull are shown on the map (§4.7).
   Geography has no `ST_ConvexHull`, so cast to geometry in a local projection or run it on
   `geometry` with lon/lat. The mosaic spans at most a few degrees, so the planar hull on lon/lat is an
   acceptable approximation. Avoid RA-wrap problems by shifting longitudes relative to the mosaic
   center before hulling.

### 4.5 Persistence and reconciliation — `backend/app/tasks/mosaics.py` (new Celery module)

`app.tasks.mosaics.detect_mosaics` (routed to the `indexer` queue; run nightly at 13:00 UTC via beat,
plus on demand from Admin):

1. Load frames: solved, `frame_type == LIGHT`, `field_boundary IS NOT NULL` (all subtypes; masters are
   flagged). Stream the columns only.
2. Run `detect()`.
3. **Reconcile with existing mosaics**, preserving user decisions:
   - For each detected mosaic D, find the existing mosaic E (same rig) with the highest Jaccard index
     over *member image ids*. A match needs J ≥ 0.3.
   - If E is `REJECTED`, keep E rejected, update its membership silently, and never show it in the
     default list.
   - If E is `CONFIRMED` or `DETECTED`, update E's panels in place. Match panels by center distance
     < `pointing_tol` so user `label`s are kept. Add new panels and remove panels that have vanished,
     but never remove panels from a `source='MANUAL'` mosaic.
   - Unmatched D → a new `DETECTED` mosaic.
   - An existing `AUTO`+`DETECTED` mosaic with no match → delete it. `CONFIRMED` ones with no match are
     kept, with a `stale` flag in the API derived from `detected_at` older than the last run.
4. Set `images.mosaic_panel_id` for members in bulk (clear it first for images of affected mosaics).
5. Recompute aggregates (`panel.by_filter`, counts, seconds, first/last, `cover_image_id`, mosaic
   totals, coverage, `gap_fraction`, `auto_name`). Integration counts only `light_subs_clause()`
   (F1). `auto_name` = the most common stripped `object_name` across members, else the closest catalog
   designation to the mosaic center (via `ImageCatalogMatch` of members), plus `· N panels`.
6. Record run stats in Redis: `mosaics:last_run` = `{started, finished, frames, pointings, mosaics}`.
   Also set a `mosaics:running` flag with a TTL for the Admin UI.

Incremental triggering is not needed. Nightly plus manual is enough, since mosaics change slowly.

`worker.py`:
- Append `"app.tasks.mosaics"` to `include`.
- Add the route `"app.tasks.mosaics.*": {"queue": "indexer"}`.
- Add the beat entry `mosaics-detect` (crontab 13:00 UTC).
- Also add `app.tasks.mosaics.backfill_footprints`, a Celery wrapper of §4.3 for the Admin button.

### 4.6 API — `backend/app/api/mosaics.py` (new router, `/api/mosaics`)

| Method & path | Purpose |
|---|---|
| `GET /api/mosaics?status=DETECTED,CONFIRMED&search=&camera=&sort=last\|panels\|integration&page=&page_size=` | Paginated `MosaicSummary` (no geometry except the center; include the `cover_image_id` of the panel with most integration). |
| `GET /api/mosaics/{id}` | `MosaicDetail`: summary + `panels: [{id, panel_index, label, center, rotation, ring: [[ra,dec],...], light_count, light_seconds, by_filter, cover_image_id, first/last, behind: bool}]` + `coverage_rings` (outer rings and holes as `[[ra,dec]]` lists, from `ST_AsGeoJSON(coverage)`, converting lon back to RA 0..360) + `gap_fraction`, `stale`. `behind` = the panel's seconds < 70% of the median panel's seconds (for the selected filter; compute client-side too). |
| `PATCH /api/mosaics/{id}` | `{name?, status?, notes?}` (status: CONFIRMED or REJECTED). |
| `PATCH /api/mosaics/{id}/panels/{panel_id}` | `{label?}`. |
| `POST /api/mosaics/{id}/panels/{panel_id}/detach` | Removes a wrong panel: sets it to its own single-panel "orphan" (deleted) and sets `source='MANUAL'` on the mosaic so re-detection won't re-add it. |
| `POST /api/mosaics/merge` | `{mosaic_ids: [a, b]}` → merges into `a`, `source='MANUAL'`, `status='CONFIRMED'`. |
| `POST /api/mosaics/detect` | Enqueues detection (admin), 202. `GET /api/mosaics/detect/status` → the last-run stats plus the running flag. |
| `POST /api/mosaics/footprints/backfill` | Enqueues the footprint backfill (admin), 202. |
| `_build_image_query` | New kwargs `mosaic_id: Optional[int]` (join via `mosaic_panels`) and `mosaic_panel_id: Optional[int]`, plus `Query` params on the 4 filter endpoints. |

`schemas/mosaic.py` holds the Pydantic models. `ImageDetail` gets
`mosaic_panel: Optional[{mosaic_id, panel_index, label, mosaic_name}]`, loaded with one small query in
`get_image`.

### 4.7 Frontend

- **Nav**: "Mosaics" after Sessions (icon `LayoutGrid`).
- **`/mosaics` → `pages/Mosaics.jsx` + `.css`**: status tabs (Detected / Confirmed / Rejected), and
  cards with a **mini coverage map** (the same SVG component at small size), name, rig, panel count,
  total hours, last capture, gap %, and a "behind" warning count. Detected cards have quick "Confirm"
  and "Not a mosaic" buttons.
- **`/mosaics/:id` → `pages/MosaicDetail.jsx` + `.css`**
  - A **coverage map component** `components/mosaics/CoverageMap.jsx`:
    - Projection: gnomonic (TAN) around the mosaic center. Put the pure JS `skyToTan(ra, dec, ra0, dec0)
      → (ξ, η)` in `src/utils/wcs.js` (append; don't modify existing functions). Display with north up
      and east left (the astronomical convention), plus a toggle to flip east/west for "as captured"
      orientation.
    - SVG `viewBox` fitted to the panels' bounds with 5% padding. It scales responsively.
    - Each panel is drawn as a `<polygon>` from its ring. The fill opacity or color ramp shows
      integration for the selected metric. There is a filter selector: `All`, or each normalized
      filter (use F2's `normalize_filter` client mirror if present, otherwise raw names).
    - The panel's cover thumbnail, drawn as `<image>` clipped to the polygon, with an affine transform
      from the image's pixel corners to the projected ring corners (3-point affine from corners 0, 1,
      3). A toggle switches between "thumbnails" and "heatmap".
    - Labels (`P3 · 2.1h`), plus a red dashed outline and a ⚠ for `behind` panels.
    - Coverage holes (from `coverage_rings` holes) are drawn hatched, and the gap % is shown in the legend.
    - An RA/Dec graticule (lines every 1° or 0.5° depending on the span, drawn as projected
      polylines), with a small compass (N/E arrows).
    - Hovering a panel shows a tooltip with per-filter hours. Clicking selects it and scrolls the
      panel table.
  - Panel table: index, editable label, center (formatted RA/Dec via the existing `formatRA`/`formatDec`
    in `client.js`), rotation, hours per filter columns, subs, last captured, and actions (view subs →
    `/search?mosaic_panel_id=`, detach).
  - Header: editable name, status buttons (Confirm / Reject), rig, totals, notes (autosave), and a
    "stale" badge.
- **ImageDetail.jsx**: when `mosaic_panel` is present, show a "Mosaic" row: `M31 mosaic · P3` linking to
  the detail page.
- **Admin.jsx**: a "Mosaics" card with Detect now, Backfill footprints, and the last-run stats.
- **client.js**: `fetchMosaics`, `fetchMosaic`, `updateMosaic`, `updateMosaicPanel`, `detachMosaicPanel`,
  `mergeMosaics`, `triggerMosaicDetect`, `fetchMosaicDetectStatus`, `triggerFootprintBackfill`.

## 5. Files touched

New: `app/services/footprint.py`, `app/services/mosaics.py`, `app/models/mosaic.py`, `app/tasks/mosaics.py`,
`app/api/mosaics.py`, `app/schemas/mosaic.py`, `app/scripts/backfill_footprints.py`,
`alembic/versions/…-f16da61c0004_add_mosaics.py`, `tests/test_footprint.py`, `tests/test_mosaic_detect.py`,
`frontend/src/pages/Mosaics.jsx/.css`, `MosaicDetail.jsx/.css`, `components/mosaics/CoverageMap.jsx/.css`,
`docs/features/MOSAICS.md`.
Modified: `models/image.py`, `models/__init__.py`, `schemas/image.py`, `tasks/indexer.py`,
`tasks/astrometry.py`, `api/images.py` (filters, detail field, TODO comment), `worker.py`, `main.py`,
startup chain (compose templates, `rebuild_and_seed.ps1`, README), frontend `App.jsx`, `Layout.jsx`,
`ImageDetail.jsx`, `Admin.jsx`, `Search.jsx`/`FilterChips.jsx` (`mosaic_id`, `mosaic_panel_id`
params), `utils/wcs.js` (append `skyToTan`), `client.js`, `CLAUDE.md` (beat jobs), `docs/core/DATABASE_SCHEMA.md`,
`docs/development/UTILITY_SCRIPTS.md`.

## 6. Testing

Unit (pure, no DB):
- `compute_footprint`:
  - A synthetic TAN WCS at (RA 10.68, Dec 41.27), 3°×2°, rotation 30° gives corners within 1″ of the
    analytic values.
  - A field at RA 359.9 gives a ring with longitudes on both sides of 0 after normalization.
  - Dec 89.5 does not raise.
  - NaN, oversize and missing-dimension inputs give None.
- Pointing clustering:
  - 200 dithered frames plus a meridian flip (rotation +180) form 1 pointing.
  - Two framings 30% apart form 2 pointings.
  - The same framing on two rigs forms 2 pointings.
- Detection:
  - A 2×2 grid with 15% overlap forms 1 mosaic with 4 panels, ordered P1 top-left (north-east) → P4.
  - Two revisits with 85% overlap are **not** a mosaic.
  - Two panels with 0% overlap but `OBJECT` values `M31 Panel 1`/`M31 Panel 2` are a mosaic (the
    explicit rule).
  - A mosaic straddling RA 0.
- Reconciliation:
  - A CONFIRMED mosaic keeps its id, name and panel labels after re-detection with one extra panel.
  - A REJECTED mosaic stays rejected.
  - A MANUAL mosaic never loses panels.

Integration (PostGIS test DB, if one is available in the dev stack): the `ST_Intersects(field_boundary,
center_location)` wrap/pole test from §4.1, and the SQL overlap fraction of two known rings is within
1% of the planar test helper.

Manual: on the real library, run the footprint backfill and detection, then review the Detected list.
Confirm that at least one known mosaic appears with the right panel count, and that routine
single-panel targets with many revisits do **not** appear.

## 7. Acceptance criteria

1. `field_boundary` is populated for all solved light frames with dimensions. New solves (indexer and
   Astrometry.net) populate it automatically.
2. Known mosaics in the library are detected with the correct panel count, and their per-panel hours
   match Search `mosaic_panel_id=` sums.
3. A behind panel is visibly flagged on the coverage map and in the table.
4. User confirm, reject, rename, label, detach and merge actions survive nightly re-detection.
5. Detection over the full library completes in < 2 minutes. Log the run stats.
6. The coverage map renders correctly (north up, east left) for a mosaic near RA 0 and for one at
   Dec > 60°, and is usable at 375 px.

## 8. Follow-ups (out of scope)

- Switch `search.py` coordinate search and `_build_image_query` spatial search to
  `ST_Intersects(field_boundary, point)`, with a fallback to the center distance when it is NULL.
- Dedupe the WCS cascade in `api/images.py` and `matching.py` onto `services/footprint.build_wcs`.
- Mosaic planning: suggest the next panel centers and export to N.I.N.A. Sequencer/Framing Assistant.
