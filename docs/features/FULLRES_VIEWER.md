# Full-resolution viewer (V1)

Design: [docs/design/20261001-V1-full-resolution-viewer.md](../design/20261001-V1-full-resolution-viewer.md)

Opens any image at full sensor resolution in a full-screen deep-zoom viewer: smooth zoom and pan, a
minimap, 1:1 inspection, server-side stretch presets and a crosshair with pixel X/Y and RA/Dec.

## Using it

- On an image's detail page, click **Full resolution** or press **F**. The viewer is at
  `/images/:id/view`.
- The 1024 px thumbnail shows immediately; the full-resolution tiles replace it once built, with the
  viewport preserved. A chip shows the build progress. First view of a typical sub takes a few
  seconds, a large master minutes; repeat views are instant (cached).

| Action | Mouse / touch | Key |
|---|---|---|
| Zoom about cursor | wheel, pinch, double-click/tap | `+` / `-` |
| Pan | drag | `W A S D` |
| Fit | button | `0` |
| 1:1 (100 % = native sensor pixels) | button | `1` |
| Previous / next image in the search results | ‹ › buttons, swipe at Fit | `←` / `→` |
| Fullscreen | ⛶ | `F` |
| Back to the detail page | ← Back | `Esc` |
| Cycle stretch preset | dropdown | `P` |
| Back to the search results | | `G` |

- The zoom % is relative to native pixels. If the image was larger than `FULLRES_MAX_MEGAPIXELS`
  and rendered at reduced size, the viewer says so, and 100 % still means one sensor pixel.
- **Paging keeps the view** when the next image has the same pixel size, so stars and tracking can
  be compared between subs. A different size resets to Fit.
- The next image's build is started in the background once the current one is ready (only if it is
  60 MP or smaller).
- The readout (bottom-left) shows the sensor pixel under the cursor and, for plate-solved images,
  RA/Dec. On touch, tap to read.

## Stretch presets

Every preset is rendered on the server with **one global curve fitted to a whole-frame sample**
(never per tile), and cached separately.

| Preset | Default for | Curve |
|---|---|---|
| `linear` | everything except sub-frames | min/max (8-bit JPEG/PNG pass through unchanged) |
| `auto` | `SUB_FRAME` (same rule as thumbnails) | STF, target background 0.25, shadows −1.25 MAD, linked channels |
| `strong` | – | STF, target background 0.40, shadows −2.0 MAD |
| `unlinked` | colour images only | STF fitted per channel (neutralises colour casts) |

One-shot colour (OSC) FITS/XISF data with `BAYERPAT` (and optionally `XBAYROFF`, `YBAYROFF`,
`ROWORDER`) is **debayered to colour** (bilinear, OpenCV) before stretching. Thumbnails are not
changed and stay mono for these. `FULLRES_DEBAYER=false` turns debayering off.

The render uses exactly the same orientation as the thumbnail (no flips), so pixel → sky mapping is
the same as on the detail page.

## How it works

```
ImageDetail ─► /images/:id/view (FullResViewer.jsx, OpenSeadragon)
                  │ GET /api/images/{id}/fullres?preset=
                  ▼
   200 ready ─► DZI tiles from the cache (FileResponse, immutable)
   202       ─► enqueue app.tasks.fullres.build_pyramid on the "fullres" queue; client polls
```

- `backend/app/services/fullres/render.py` – load at full resolution → debayer → global stretch
  (uint16 lookup table for integer data, 256-row blocks for float data) → uint8 mono/RGB.
- `…/pyramid.py` – libvips/pyvips `dzsave` (510 px tiles, 1 px overlap, JPEG Q88) into a temp dir
  inside the cache, renamed into place atomically.
- `…/cache.py` – key, manifest, LRU and eviction.
- `backend/app/tasks/fullres.py` – `build_pyramid` and the periodic `evict`.
- `backend/app/api/fullres.py` – the endpoints below. No database table or migration is involved.

### Reading the source files

The library lives on network shares, where memory-mapping a file measured slow
Sources are therefore read with ordinary reads:
FITS and TIFF whole, uncompressed XISF into RAM in one pass (files up to 6 GB; larger ones are read
in row blocks, which costs a second pass over the file). Expect a build to be bounded by the
share's read speed.

### Cache

- Root: `FULLRES_CACHE_PATH` (`/data/fullres`, volume `./library/fullres`).
- Key: `sha1(file_path | mtime_ns | size | preset | RENDER_VERSION | debayer | max-MP)[:16]`. Editing
  the file, changing a setting that alters pixels, or bumping `RENDER_VERSION` in
  `services/fullres/cache.py` makes a new key; old pyramids age out through the LRU.
- LRU in Redis (`fullres:lru`, touched when the status/manifest is fetched, not per tile), capped at
  `FULLRES_CACHE_MAX_GB`. Eviction runs after each build and every 30 minutes; a pyramid built in
  the last 10 minutes is never evicted. If Redis is flushed the index is rebuilt from the manifests
  on disk.
- Small JPEG/PNG/WebP (≤ 4096 px, `linear`, no EXIF rotation) skip the pyramid and are shown from the
  original file via `/fullres/source`.
- Admin → Thumbnail Cache section shows the cache size and has a **Clear** button.

### Worker

Builds run on their own queue (`fullres`) and supervisord program (`celery_fullres`,
`-Q fullres --concurrency=$FULLRES_WORKERS`), so a big master can never delay indexing or
thumbnails. The main worker's `-Q` list does not include it. Hard time limit: 15 minutes.

## API

| Method & path | Returns |
|---|---|
| `GET /api/images/{id}/fullres?preset=&retry=` | `200 {state:"ready", manifest, key, dzi_url \| source_url, presets, preset, default_preset}` · `202 {state, pct, error, …}` while building (`state:"error"` is also a 202, with `error`; pass `retry=true` to re-queue) · `404` source missing · `409` preset invalid for the image · `422` unreadable file |
| `GET /api/images/{id}/fullres/{key}/image.dzi` | DZI XML |
| `GET /api/images/{id}/fullres/{key}/image_files/{level}/{col}_{row}.jpg` | Tile (`Cache-Control: private, max-age=31536000, immutable`) |
| `GET /api/images/{id}/fullres/source` | Native-shortcut original, inline |
| `POST /api/images/{id}/fullres/rebuild?preset=` | Admin: drop the pyramid and rebuild |
| `GET` / `DELETE /api/indexer/fullres-cache` | Admin: cache stats / clear |

Tile security: the key must match `^[0-9a-f]{16}$`, `level`/`col`/`row` must be integers, the path
is checked with `validate_path_safety` against the cache root (symlinks refused), and the key's
manifest `image_id` must equal `{id}`.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `FULLRES_CACHE_PATH` | `/data/fullres` | Pyramid cache root |
| `FULLRES_CACHE_MAX_GB` | `20` | LRU cap |
| `FULLRES_MAX_MEGAPIXELS` | `250` | Larger renders are downsampled by an integer factor |
| `FULLRES_WORKERS` | `1` | Concurrency of the `fullres` queue |
| `FULLRES_DEBAYER` | `true` | Debayer CFA FITS/XISF |

## Tests

`backend/tests/test_fullres_render.py` (stretch, debayer for all patterns/offsets/row orders,
orientation against the thumbnail), `test_fullres_cache.py` (key, eviction, pyramid) and
`test_fullres_api.py` (flow, dedupe, security, native shortcut).
