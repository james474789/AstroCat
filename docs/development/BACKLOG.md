# Backlog

Self-contained tasks for a future agent or developer. Each entry says why it exists, what to
change, and how to know it's done. Remove an entry when it ships.

---

## Remove the legacy Nova and PixInsight annotation overlays (backend)

Added: 2026-10-07

**Why.** The dynamic AstroCat overlay (A1, `docs/design/20261007-A1-dynamic-sky-overlay.md`) is
now the only annotation shown. Image Detail's button became a simple on/off toggle for it, and
the Nova (astrometry.net annotated JPEG) and PixInsight (`{name}_Annotated.{ext}` sidecar) modes
were removed **from the UI only**. Their backend plumbing is now dead code.

**Remove**
- `backend/app/api/images.py`:
  - `GET /{image_id}/annotated`, `GET /{image_id}/pixinsight-annotation` and
    `POST /{image_id}/fetch_annotation`;
  - the `has_annotated_image` / `has_pixinsight_annotation` lookups in `get_image` and the
    second `has_annotated_image` lookup further down.
- `backend/app/schemas/image.py`: the `has_annotated_image` / `has_pixinsight_annotation` fields
  on `ImageDetail`.
- `backend/app/tasks/astrometry.py`: the "Download Annotated Image" step after a solve.
- `backend/app/services/astrometry_service.py`: `download_annotated_image` (including its
  human-check bypass) and `get_tags` if still unused.
- `backend/app/tasks/indexer.py`:
  - the `pixinsight_annotation_path` lookup, its backfill on rescan, and the assignment on
    create/update.
  - **Keep** the `stem.endswith("_Annotated")` skips. Sidecars must stay out of the catalog,
    otherwise they get indexed as near-duplicate images.
- `backend/app/models/image.py`: the `pixinsight_annotation_path` column. Drop it with a new
  Alembic migration (the column was added by `2026_02_16_1455-52a1b3c4d5e6`).
- `frontend/src/api/client.js`: `fetchAnnotation` (unused since the UI change).
- `scripts/api_performance_test.py`: the `/annotated` probe.
- Docs that describe the old modes: `docs/features/astrometry_process.md`, the
  "Objects in field" / overlay docs in `docs/features/`, and the README if it mentions them.

**Leave alone**
- Already-downloaded `annotated_*.jpg` files in the thumbnail cache. The owner chose to leave
  them on disk, so don't add a cleanup data migration.
- `_Annotated` sidecar files on disk.

**Done when**
- The repo has no references to these endpoints or fields:
  `grep -rniE "annotated_image|pixinsight_annotation|fetch_annotation|/annotated"` over
  `backend/` and `frontend/src` only finds the indexer's `_Annotated` skip.
- `alembic upgrade head` drops the column, and `alembic downgrade -1` restores it.
- The backend tests pass and the frontend builds.
- `VERSION` is bumped, and the backend and frontend containers are rebuilt and restarted.

---

## Verify header-WCS overlay accuracy and drop the ⚠ where it's proven

Added: 2026-10-07

**Why.** The AstroCat overlay draws images without a stored astrometry.net solution from the WCS
embedded in the file (`SOURCE_HEADER` in `backend/app/services/sky_overlay.py`). That covers
19,414 FIT, 290 FITS and 4 XISF images as of 2026-10-07. These always show the ⚠
"approximate" warning because their row order has never been measured. PixInsight XISF in
particular may be bottom-up, the same trap the ASTAP sidecars fell into (mirrored in Y). The
check script has nothing to compare against: no image has both a stored solution and a header
WCS.

**Do**
1. Queue astrometry.net solves (the normal rescan path) for a sample of header-WCS images that
   cover each capture tool and format: about 5–10 FIT and FITS from different rigs, and all 4 XISF.
2. Run `docker compose exec backend python -m app.scripts.verify_sky_overlay --sample 50`.
   Section 2 reports the HEADER-vs-SOLVER residual per format, and the residual after mirroring Y.
3. Act on each format:
   - **Matches directly** (median a few px): keep `WARN_NO_SIP` only when there is no SIP, and
     drop `WARN_HEADER` for that format.
   - **Matches only when mirrored:** set `flip_y` for that format or writer in
     `resolve_frame` (the `SkyFrame.flip_y` mechanism already exists for sidecars), add a test,
     and keep the warning until it re-verifies.
   - **Matches neither:** leave the warning and record what was found.
4. Extend section 2 of the script to compare SIDECAR frames against SOLVER too. Sidecars were
   verified with a one-off probe (ASTAP sidecars median 1.97 px once mirrored, others 2.1 px over
   69 frames), so this is cheap. It also covers ASTAP's flat-format `.ini`, which is assumed
   bottom-up and warns "row order not yet verified". No such image exists yet, so re-check
   whenever one does.

**Done when** each header-WCS format has a measured residual recorded in
`docs/design/20261007-A1-dynamic-sky-overlay.md`, and the warning text matches what was measured.

---

## Store the sidecar WCS for the 119 sidecar-solved images that still lack it

Added: 2026-10-07

**Why.** Data migration `0015_backfill_sidecar_wcs_cards` only visits rows whose `raw_header`
has a `SIDECAR` block. That left 119 rows with `plate_solve_source = 'SIDECAR'` but no stored
WCS (115 CR2, 2 JPG, 2 TIF), so the overlay is unavailable for them. Likely causes:
- the rows predate `raw_header["SIDECAR"]`;
- the sidecar is a `.wcs`/`.new` FITS file (`SidecarParser._parse_fits_wcs`), which returns a
  different shape with no `wcs` key;
- the `.ini` has gone.

**Do**
- Find out which cause applies, starting with
  `SELECT id, file_path, raw_header ? 'SIDECAR' FROM images WHERE plate_solve_source='SIDECAR' AND NOT COALESCE(raw_header->'SIDECAR' ? 'wcs', false)`.
- Make `_parse_fits_wcs` return its cards in the same `wcs` / `wcs_row_order` shape.
  FITS-convention WCS from a `.wcs` file needs the same row-order verification as above.
- Add a **new** data migration that covers these rows. Never re-run or edit `0015`.

**Done when** the query above returns only rows with no usable sidecar on disk.

---

## Use the exact plate solution everywhere, not the rebuilt TAN

Added: 2026-10-07

**Why.** The overlay projects through the stored solution (`SkyFrame` in
`backend/app/services/sky_overlay.py`), verified to 0.01 px against astrometry.net. Several other
places still rebuild a TAN from centre, scale, rotation and parity with a sign found by trial
(`docs/features/overlay_logic.md`). That approach ignores the downsampled solve grid,
distortion and the ASTAP row flip:
- the crosshair RA/Dec readout on Image Detail and in the full-res viewer
  (`frontend/src/utils/wcs.js` `pixelToSky`);
- the legacy `pixel_x/pixel_y` for catalog matches in `GET /api/images/{id}`
  (`backend/app/api/images.py`, "calculate pixel coordinates for matches"). It is probably
  unused by the frontend now, so remove it if so;
- `CatalogMatcher._construct_wcs` / `_is_in_image_bounds` in `backend/app/services/matching.py`,
  which decides which objects are "in field";
- the images-in-field footprints (`backend/app/services/field_overlaps.py`). The current image
  could use its `SkyFrame`, with the rebuilt TAN kept only for candidates that have nothing better.

**Do**
- Crosshair: either return the frame's linear WCS (and SIP terms) with the image detail and
  evaluate it client-side, or add a small endpoint. Keep `flip_y` and the grid rescale in one
  place: mirror `SkyFrame.to_native` / `from_native` exactly and test both sides against the
  same fixtures.
- Matching and footprints: call `sky_overlay.resolve_frame(image)` and fall back to the rebuilt
  TAN only when it returns None.
- Ship the re-match of existing images as a data migration (see CLAUDE.md, "Shipping a One-off
  Data Repair").

**Done when** the crosshair RA/Dec at a catalog marker reads that object's catalog position, and
the "Objects in field" list agrees with the overlay for SOLVER and SIDECAR images.

---

## Backfill the missing WCS file for 3 solver-solved images

Added: 2026-10-07

**Why.** 3 images with `plate_solve_source = 'SOLVER'` have no `wcs_header`: the post-solve
`/wcs_file/{job}` download failed. Without it they get no overlay. There is a gitignored
`backend/scripts/backfill_wcs_headers.py` that may already do most of this.

**Do** Add a data migration that re-fetches `/wcs_file/{astrometry_job_id}` for SOLVER rows with
no `wcs_header`, using `AstrometryService.get_wcs_file` and the provider's base URL. It should
be idempotent and skip rows whose job is gone.

**Done when** those rows have `wcs_header`, or are logged as unrecoverable.

---

## Investigate image 331's sidecar

Added: 2026-10-07

**Why.** Image 331 has an astrometry.net solve and a sidecar `.ini` WCS. The two disagree by
about 3,400 px, and mirroring either axis doesn't fix it, which suggests the sidecar belongs to
a different crop or rotation of the frame. Its overlay uses the solver solution, so it is
unaffected today, but the same stale sidecar on an image *without* a solver solution would draw
wrong markers.

**Do** Compare the `.ini` with the file's dimensions and history. If stale sidecars turn out to be
a pattern, add a sanity check when building `SOURCE_SIDECAR` frames: CRPIX inside the frame, and
the scale consistent with `pixel_scale_arcsec`.
