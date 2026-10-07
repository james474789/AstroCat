# A1 — Dynamic catalog overlay ("AstroCat annotations")

Status: **Implemented (uncommitted)** · Written: 2026-10-07 · Base commit: `54482b3` (`main`)

## Goal
Image Detail's annotation toggle gains an **AstroCat** mode that draws every catalog object in the
DB (Messier, Caldwell, NGC, IC, Sharpless, named stars) inside the field, coloured by catalog, from
the image's own plate solution. The same overlay is available in the full-resolution viewer
(button or `C`). The Nova (downloaded JPEG) and PixInsight modes are kept.

Cycle: None → AstroCat → Nova → PixInsight (modes that aren't available are skipped).

## Why the previous overlay (removed in `aa1ad72`) was wrong
1. **Wrong pixel grid.** `images.wcs_header` is the astrometry.net solution of the *downsampled JPEG*
   that was uploaded (`AstrometryService.upload_file` → `load_source_image` at ~1024 px), not of the
   original frame. `GET /images/{id}` returned `world_to_pixel` on that small grid and the frontend
   divided by the original `width_pixels`, so every marker was squeezed toward the top-left by the
   downsample factor.
2. **Header pollution.** That WCS was built from `raw_header` with `wcs_header` laid over it, mixing
   the camera's CDELT/PC/SIP cards with the solver's CD/SIP.
3. **Guessed fallback.** Without a WCS it rebuilt a TAN from centre/scale/rotation/parity, with the
   rotation sign found by trial (`docs/features/overlay_logic.md`, now superseded).

## Design
**Server-side projection with astropy** (`backend/app/services/sky_overlay.py`, pure functions):
`all_world2pix` through the stored solution, SIP included.

**Coordinate contract** (single place, `SkyFrame.to_native`): astropy 0-based grid pixel
`(x0, y0)` → native continuous pixel `((x0 + 0.5)·W/gridW, (y0 + 0.5)·H/gridH)`, top-left origin.
This is the same frame as `field_overlaps` and the frontend. FITS row 1 is the top display row for
every source: the thumbnail, the full-res tiles and the solver upload all use loaders that never flip.

**Sources**
| Source | Where | Grid | Warning |
|---|---|---|---|
| SOLVER | `wcs_header` cards only (never merged with `raw_header`) | `IMAGEW×IMAGEH`; if missing, inferred from the plate scale (flagged) | none, unless the grid was inferred |
| SIDECAR | linear WCS from the plate-solve `.ini` (`raw_header["SIDECAR"]["wcs"]`, kept by `SidecarParser`, backfilled by data migration `0015`) | native frame; **mirrored in Y** for ASTAP solves, which count rows from the bottom (measured against astrometry.net on 11 frames: median 1.97 px). Other writers measured top-down (58 frames, median 2.1 px) | always ("no distortion model"); also "row order not yet verified" for ASTAP's flat-format `.ini` |
| HEADER | the file's own WCS (`raw_header`), WCS cards only | `wcs_frame()` (handles the ASIAIR `IMAGEW` grid) | always ("embedded WCS"); also "no distortion model" without SIP, and "XISF row order not yet verified" for XISF |

The warning shows as ⚠ on the annotation button and in the legend; the tooltip gives the reason.
Images with neither source can't use AstroCat mode, and there is no fallback to a guessed TAN.

**Objects**
- **Query.** One PostGIS `ST_DWithin` per catalog table, around the field's covering circle plus a
  1.6° margin, so big objects centred just outside the frame still show.
  - NGC-table rows are split into NGC/IC by prefix.
  - OpenNGC `Dup`/`NonEx` rows are excluded.
- **Kept** when the centre is in frame, or when the ellipse reaches into it.
- **Merged** (union-find) when rows share a normalised designation or cross-reference (Messier
  `ngc_designation`, NGC `messier_designation`/`ic_designation`, Caldwell/Sh2 `source_designation` +
  `aliases`) and are within 1° of each other.
  - Colour and label come from the highest-priority catalog: Messier > Caldwell > NGC > IC > Sh2 > star.
  - Geometry comes from the row with the best size data.
- **Ellipses.** The centre and both semi-axis ends (PA north through east) are projected through the
  full WCS, so rotation, parity, distortion and scale all come from the solution.
  - No PA → a dashed circle of the mean size.
  - Too small or sizeless → a point marker. Named stars → a ring with ticks.

**API:** `GET /api/images/{id}/sky-overlay` returns `{source, accuracy_warning, width, height, objects[]}`.
`ImageDetail` gains `sky_overlay_source` and `sky_overlay_warning`, so the button knows availability
without fetching the overlay.

**Frontend**
- `SkyOverlayLayer` is one SVG in the `FieldOverlayLayer` pattern: visual only, hit-tested by the host.
- Labels use greedy, collision-free screen-space placement with a spatial hash; the hovered object
  shows its full description.
- `SkyOverlayLegend` toggles catalogs; the choice is remembered per browser.
- Interaction: hovering shows details; clicking searches for the object; on touch, a first tap
  highlights and a second tap searches.
- Catalog objects take hit priority over field-overlap footprints.

## Verification
- `backend/tests/test_sky_overlay.py` (synthetic headers), covering:
  - agreement with an independent native-grid WCS to 1e-6 px;
  - SIP round trip under 0.01 px;
  - CRPIX 0/1-based handling;
  - ellipse orientation under rotation and parity;
  - merging, Dup exclusion, source selection, the ASIAIR grid and inferred IMAGEW.
- `python -m app.scripts.verify_sky_overlay --sample 30` (in the backend container, read-only):
  1. **SOLVER vs the solver's own annotations.** Compares against `/api/jobs/{id}/annotations/`
     on the upload grid. Target: median |r| < 1 grid px, with signed medians ≈ 0.
  2. **HEADER vs SOLVER, per file format.** Also reports the residual after mirroring Y. This is
     the XISF gate: if the mirrored residual is much smaller, add a per-format `flip_y` in
     `SkyFrame.to_native`/`from_native`, then drop `WARN_XISF` for verified formats.

## Follow-ups (not in A1)
- `GET /images/{id}` still computes legacy `pixel_x/pixel_y` for matches through the old path, and
  `CatalogMatcher` bounds-checks with the guessed TAN. Both could move to `SkyFrame`.
- The crosshair RA/Dec readout (`utils/wcs.js pixelToSky`) uses the simplified TAN. A
  `/sky-overlay` companion returning the WCS for client-side readout would make it exact.
- Solved images whose `wcs_header` fetch failed can't use AstroCat mode; a data migration could
  re-fetch `/wcs_file/{job}` for them.
