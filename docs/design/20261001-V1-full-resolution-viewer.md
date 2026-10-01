# V1 - Full-resolution image viewer (deep zoom)

Status: **Implemented (uncommitted; VERSION 20261001.03)** · Written: 2026-10-01 · Base commit: `7549b6c` (`main`)

Lets a user open any image at full sensor resolution in a full-screen viewer with smooth zoom, pan,
a minimap, 1:1 inspection and the existing crosshair/RA-Dec readout. It replaces "zooming a 1024 px
thumbnail up to 8×", which is what the detail page does today.

## 1. Decisions (agreed 2026-10-01)

| Topic | Decision |
|---|---|
| Where | A dedicated **full-screen viewer** at route `/images/:id/view`, opened from ImageDetail. The inline preview on ImageDetail stays as it is. |
| Stretch | **Auto-STF by default, plus server-side presets** (Linear / Auto-STF / Strong / Unlinked colour). Changing the preset re-renders, and each preset is cached separately. |
| OSC Bayer data | **Debayer to colour** using the `BAYERPAT` header. |
| Overlays | **Crosshair + pixel X/Y + RA/Dec only.** This ports what ImageDetail already does. Catalog markers, ADU readout and annotation layers are out of scope (§10). |

## 2. Current state (verified)

- `GET /api/images/{id}/thumbnail` serves a cached JPEG no larger than `THUMBNAIL_MAX_SIZE` (1024).
  `?stretched=true` makes a 1024 px STF preview on the fly.
- `ImageDetail.jsx` has a CSS-transform pinch/pan zoom (touch only, 1–8×) over that thumbnail.
  Zooming only magnifies the 1024 px JPEG.
- `ThumbnailGenerator.load_source_image` deliberately *decimates* the source (strided reads, RAW
  `half_size`). For 3-D FITS it uses only plane 0. It **never debayers** FITS/XISF CFA data.
- Library profile (live DB, ~90k images):

| Format / subtype | Count | Avg size | Largest |
|---|---|---|---|
| FIT/FITS subs | 45.8k | 4676×3502 / 2949×2109 | 12,576 px wide, 1.2 GB |
| CR2/CR3 subs | 15k | ~5200×3500 | 6188 px wide |
| PNG/TIF/JPG planetary | 26k | mostly < 1000 px (JPG avg 6582 px) | 13,659 px wide |
| TIF/XISF masters | ~400 | 4584×3289 – 6320×5744 | 11,160 px wide, **2.8 GB** |
| OSC FITS with `BAYERPAT` | **~11k** (BGGR 10.5k, RGGB 0.4k) | | |

Two conclusions follow. First, pre-rendering tiles for the whole library is not viable (it would take
hundreds of GB), so tiles are **built on demand per image and kept in a size-capped LRU cache**.
Second, a single full-size JPEG is not viable either: a 180 MP image decodes to more than 700 MB in the
browser. So the viewer uses a **tile pyramid**.

## 3. Architecture

```
ImageDetail ──"Full resolution" (F)──▶ /images/:id/view  (FullResViewer.jsx, OpenSeadragon)
                                            │ 1. GET /api/images/{id}/fullres?preset=auto
                                            ▼
                                  ┌── ready ──▶ DZI manifest URL ──▶ tiles (FileResponse, immutable cache)
 api/fullres.py ── status ────────┤
                                  └── missing ─▶ enqueue tasks.fullres.build_pyramid (queue "fullres")
                                                 ▲ client polls status every 1 s, shows thumbnail meanwhile
 services/fullres/
   render.py   full-res load → debayer → global stretch (LUT) → uint8 RGB/mono
   pyramid.py  pyvips dzsave → <cache>/<key>/image.dzi + image_files/…
   cache.py    cache key, manifest, LRU bookkeeping (Redis ZSET), eviction
```

The design is built from standard pieces:
- **Deep Zoom (DZI)** tile pyramid: 510 px tiles, 1 px overlap, JPEG Q88.
- **pyvips** (libvips) writes the pyramid. It is streaming, multi-threaded and low-memory, and makes a
  12k × 8k pyramid in a few seconds.
- **OpenSeadragon** (BSD-3) is the browser viewer. It provides wheel/pinch/drag zoom, a navigator
  minimap, fullscreen and keyboard support out of the box.

## 4. Backend

### 4.1 Rendering (`services/fullres/render.py`)

`render_full(image, preset) -> RenderResult(array uint8 HxW or HxWx3, scale, notes)`

1. **Load at full resolution**, with no stride and no `half_size`. Each format reuses the code paths
   in `ThumbnailGenerator` but skips the decimation:
   - FITS: read the primary data HDU with `memmap=True`. If BZERO/BSCALE are present, fall back to
     `memmap=False` (same `ValueError` fallback as the thumbnail code). For a 3-plane cube (NAXIS=3,
     46 images), use all 3 planes as RGB, not plane 0.
   - XISF: use the `_read_xisf_preview` memmap path with stride 1, falling back to `XISF.read`.
   - RAW (CR2/CR3/…): `rawpy.postprocess(half_size=False)`. Subs keep linear `gamma=(1,1)`, 16-bit,
     as the thumbnail does.
   - TIFF: `tifffile` level 0, memmap where possible.
   - JPEG/PNG/WebP: Pillow, no `draft()`.
2. **Orientation must be identical to `load_source_image`.** The crosshair maps viewer pixels to sky
   coordinates with `pixelToSky(..., parity)` in the same frame as the thumbnail. No new flips are
   allowed. A test (§8) checks this by comparing the downsampled full-res render against the thumbnail.
3. **Debayer** (FITS/XISF, mono 2-D data with `BAYERPAT`/`COLORTYP` in `raw_header`, not RAW, which
   rawpy already handles):
   - Apply `XBAYROFF`/`YBAYROFF` and `ROWORDER` (`BOTTOM-UP` flips the pattern's rows) to get the
     effective pattern at pixel (0,0).
   - Use OpenCV `cvtColor` with the matching `COLOR_Bayer??2RGB` code (bilinear). The OpenCV names
     are offset from the FITS names (FITS `RGGB` → `COLOR_BayerBG2RGB`), so the mapping table lives in
     one function and is tested with synthetic CFA frames (§8).
   - Debayer runs on the integer data before stretching. 16-bit stays 16-bit, so memory is 3×
     the uint16 size rather than float32.
   - A setting `FULLRES_DEBAYER=true` turns this off if it ever needs to be.
4. **Stretch globally, never per tile.** Parameters come from a strided sample (≈2 MP) of the
   whole frame, so every tile uses the same curve:

| Preset | Applies to | Curve |
|---|---|---|
| `linear` | default for non-subs (masters, JPEG/PNG, planetary) | min/max (or native 8-bit passthrough) |
| `auto` | default for `SUB_FRAME` (same rule as the thumbnail task: `apply_stf = is_subframe`) | current `apply_stf_stretch` (target_bg 0.25, shadows −1.25 MAD), linked |
| `strong` | any | STF with target_bg 0.40, shadows −2.0 MAD |
| `unlinked` | colour only (hidden for mono) | STF parameters computed per channel (neutralises colour casts in OSC subs) |

   - **Applying the curve:** for integer data (uint8/uint16, which covers nearly all subs) build a
     65,536-entry `uint8` lookup table from the curve and index into it. This is a single pass with
     no float copy of the frame. For float data (some masters, XISF), apply the curve in row blocks
     of about 256 rows so peak memory stays bounded.
   - `apply_stf_stretch` is refactored into `stf_params(sample)` plus `stf_curve(x, params)`. The
     thumbnail path keeps calling a wrapper with identical output, which is covered by the existing
     `test_stf.py`.
5. **Size guard.** Above `FULLRES_MAX_MEGAPIXELS` (default 250), downsample by an integer factor until
   under the cap. Record `scale` in the manifest and show "rendered at 1/2 resolution" in the UI. No
   current image hits this (the largest is about 185 MP), but it protects the worker.

### 4.2 Pyramid (`services/fullres/pyramid.py`)

- `pyvips.Image.new_from_memory(arr, w, h, bands, "uchar").dzsave(tmp_dir/"image", tile_size=510,
  overlap=1, suffix=".jpg[Q=88,strip]", layout="dz")`. The pyramid is written to a temp dir inside the
  cache root and then **atomically renamed** to `<key>/`, so a half-written pyramid is never served.
- **Native shortcut:** a JPEG/PNG/WebP with max dimension ≤ 4096 px, with preset `linear` and no
  orientation change, skips the pyramid. The manifest says `type: "image"` and the viewer loads the
  original through `GET /fullres/source`. This covers most of the ~26k small planetary frames for free.

### 4.3 Cache (`services/fullres/cache.py`)

- Root: new setting `FULLRES_CACHE_PATH` (default `/data/fullres`), new compose volume
  `./library/fullres:/data/fullres` (in all compose files and the example).
- **Key:** `sha1(file_path | mtime_ns | size | preset | RENDER_VERSION)[:16]`. A file edit or a
  renderer change (bump `RENDER_VERSION`) invalidates the key naturally. No DB table or migration is
  needed.
- `<key>/manifest.json`: `{image_id, preset, width, height, scale, type, tile_size, overlap, bytes,
  built_at, render_ms, notes}`.
- **LRU:** Redis ZSET `fullres:lru` (member = key, score = last access). It is touched when the
  **status/manifest** is fetched, not on every tile, which keeps tile serving to a cheap FileResponse.
  The hash `fullres:bytes` stores the size per key.
- **Eviction:** after each build, and from a beat task every 30 min (`tasks.fullres.evict`), delete the
  oldest keys until the total is ≤ `FULLRES_CACHE_MAX_GB` (default **20 GB**). A key built in the last
  10 minutes is never evicted. If Redis is flushed, the next eviction rebuilds the ZSET from the
  manifests on disk (using mtime as a proxy).
- **Build dedupe:** Redis lock `fullres:build:{key}` (SET NX, TTL 15 min). Status and progress live in
  `fullres:status:{key}` = `{state: queued|loading|debayering|stretching|tiling|ready|error, pct,
  error}`. The tiling percentage comes from the pyvips `eval` progress signal.

### 4.4 Task and queue

- `app/tasks/fullres.py::build_pyramid(image_id, preset)` routes to a **new queue `fullres`**.
- **New supervisord program** `celery_fullres` runs `-Q fullres --concurrency=${FULLRES_WORKERS:-1}`.
  Rendering a 2.8 GB master needs several GB of RAM. Keeping it on one dedicated worker means it can
  never stack 8-wide on the main pool or delay indexing and thumbnails.
- `task_time_limit` is 15 min. On failure, status becomes `error` with the message, and the lock is
  released.
- No data migration is needed. Nothing is backfilled; everything is on demand.

### 4.5 API (`api/fullres.py`, mounted under `/api/images`, same `get_current_user` cookie auth)

| Method & path | Returns |
|---|---|
| `GET /{id}/fullres?preset=` | `200 {state:"ready", manifest, dzi_url}` · `202 {state, pct}` (enqueues if not queued) · `404` source missing · `409` preset invalid for image (e.g. `unlinked` on mono). Omitting `preset` uses the image's default (§4.1.4). Response includes `presets: [...]` valid for this image. |
| `GET /{id}/fullres/{key}/image.dzi` | DZI XML (`FileResponse`). |
| `GET /{id}/fullres/{key}/image_files/{level}/{col}_{row}.jpg` | Tile. `Cache-Control: private, max-age=31536000, immutable` (content-addressed key). |
| `GET /{id}/fullres/source` | Native-shortcut original, correct `media_type`, inline (not attachment). |
| `POST /{id}/fullres/rebuild?preset=` | Admin only. Drops the key and re-enqueues. |
| `GET /api/indexer/fullres-cache` / `DELETE` | Cache stats (count, bytes, cap) / clear. Admin only. Used by the Admin page. |

Security:
- `key` must match `^[0-9a-f]{16}$`, and `level`/`col`/`row` must be integers. These are validated
  before the path is built.
- The resolved path is checked with `validate_path_safety(path, [FULLRES_CACHE_PATH])`.
- The `key` must belong to `{id}`: its manifest's `image_id` is checked, with the result cached in
  process.
- `source` validates against `image_paths_list`, as `/download` does.

### 4.6 Dependencies and Docker

- `requirements.txt`: `pyvips` (cffi binding) and `opencv-python-headless`.
- `backend/Dockerfile` (dev and prod stages): `apt-get install libvips42`. Its default build already
  covers JPEG; no TIFF/FITS loaders are needed because pyvips only receives numpy arrays.
- If adding libvips is rejected, the fallback is a pure-Pillow DZI writer (≈80 lines: crop tiles per
  level, halve with `reduce(2)`). It is slower (≈3–5× for large frames) but has no new system
  dependency. See §9 Q1.

## 5. Frontend

### 5.1 Entry points

- ImageDetail gets a **"🔍 Full resolution"** button in `image-actions` and the keyboard shortcut
  **`F`**. Both go to `/images/:id/view`, which carries the existing `searchContext`/nav state.
- New lazy route in the router: `const FullResViewer = lazy(() => import('./pages/FullResViewer'))`.
  OpenSeadragon (~200 KB min) is only downloaded when the viewer is opened.

### 5.2 `pages/FullResViewer.jsx` + `.css`

Layout: a black full-viewport stage with an auto-hiding top bar (it fades out after 2 s of no mouse
movement and comes back on movement or tap).

```
[← Back]  NGC7635_Light_300s_0042.fits   ‹ 42 / 640 ›   [Auto-STF ▾]   [Fit] [1:1]  37%   [⛶]
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                  ┌────────┐  │
│                         (OpenSeadragon canvas)                   │minimap │  │
│                                                                  └────────┘  │
│ X 2311  Y 1488 · RA 23h20m48.3s  Dec +61°12′05″                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Loading flow**
1. Call `GET /fullres`. Meanwhile, open OSD on the **existing thumbnail** (`type:"image"`), so the
   user sees the frame immediately.
2. While the response is 202, show an overlay chip "Rendering full resolution… tiling 64 %" and poll
   every 1 s, backing off to 3 s after 30 s.
3. When it is `ready`, open the DZI and **preserve the viewport**. Bounds are normalised, so the
   thumbnail and the DZI share coordinates. The swap is invisible apart from the image sharpening.
4. On `error`, show the message and "Retry". On 404, show "Source file missing" and a Back button.

**OpenSeadragon config**
- `showNavigator: true` (bottom-right) and `maxZoomPixelRatio: 4`, so the max is 400 % of native.
  That is enough to see star profiles.
- `minZoomImageRatio: 0.8`, `visibilityRatio: 0.5`, `immediateRender` off,
  `crossOriginPolicy: false` (same origin, so the cookie auth just works).
- Default controls hidden; buttons are our own (Lucide icons, matching the app style).
- `ajaxWithCredentials` is not needed (same origin).

**Controls and keyboard**

| Action | Mouse / touch | Key |
|---|---|---|
| Zoom about cursor | wheel, pinch, double-click/double-tap | `+` / `-` |
| Pan | drag | `W A S D` |
| Fit | button | `0` |
| 1:1 (100 % native pixels) | button | `1` |
| Prev / next image | ‹ › buttons, swipe at fit zoom | `←` / `→` (same as ImageDetail) |
| Fullscreen | ⛶ | `F` |
| Back to ImageDetail | ← Back | `Esc` |
| Cycle preset | dropdown | `P` |

- Zoom % is shown relative to native pixels (`viewport.viewportToImageZoom`). For a downsampled render
  it is multiplied by `scale`, so 100 % always means one sensor pixel.
- **Sticky viewport for blinking:** on prev/next, if the next image has the same `width × height`,
  keep the normalised centre and zoom. This allows like-for-like comparison of stars and tracking
  across subs. Otherwise reset to Fit.
- Prefetch: once the current image is `ready`, fire a status request for the next image so its build
  starts in the background (debounced and cancelled on navigation). This is only done when the next
  image's pixel count is ≤ 60 MP, so the worker is never flooded by quick paging through masters.
- Changing the preset reloads that preset (cached builds are instant) and keeps the viewport.

**Crosshair / readout**
- On mouse move (or tap on touch): `viewer.viewport.viewerElementToImageCoordinates(pt)` gives
  `(imgX, imgY)` in rendered pixels. Multiplying by `scale` gives sensor pixels, which are passed to
  the existing `pixelToSky(...)` with the same arguments as ImageDetail.
- Thin crosshair lines are drawn in a CSS overlay (not an OSD overlay, so they don't scale). The
  readout is pinned bottom-left rather than following the cursor, so it never hides the star being
  inspected.
- RA/Dec only appears when the image is plate-solved (same condition as now).

### 5.3 Shared code

- Extract ImageDetail's prev/next + return-to-search logic into `hooks/useImageNav.js`, used by both
  pages (a small, behaviour-preserving refactor).
- `api/client.js`: `fetchFullRes(id, preset)`, `getFullResSourceUrl(id)`.
- Admin page (thumbnail/cache area): add a "Full-res cache: 6.2 / 20 GB · 143 images [Clear]" row.

### 5.4 Dependency

- `openseadragon` (BSD-3-Clause), wrapped directly in a `useEffect`, so no React wrapper library is
  needed.

## 6. Configuration (new settings, `.env` / `config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `FULLRES_CACHE_PATH` | `/data/fullres` | Pyramid cache root (new volume `./library/fullres`) |
| `FULLRES_CACHE_MAX_GB` | `20` | LRU cap |
| `FULLRES_MAX_MEGAPIXELS` | `250` | Downsample guard |
| `FULLRES_WORKERS` | `1` | Concurrency of the `fullres` queue |
| `FULLRES_DEBAYER` | `true` | Kill switch for CFA debayering |

## 7. Performance expectations (to confirm in phase 1)

| Image | Load | Debayer + stretch | Tiles (pyvips) | Pyramid size |
|---|---|---|---|---|
| 4676×3502 16-bit mono FITS (16 MP) | <1 s | <0.5 s | ~1 s | ~8 MB |
| 6000×4000 CR2 (24 MP) | ~2 s (full demosaic) | – | ~1.5 s | ~12 MB |
| 11160×? XISF master, float, 2.8 GB | 10–30 s (disk-bound) | ~5 s | ~5 s | ~60 MB |

### Measured (2026-10-01, dev box: Docker Desktop on Windows, CIFS shares mounted inside the container)

**Load time here is bound by the share read speed, not by the code.** From inside the container the
shares read at about 14.5 MB/s (plain `dd`), while the same files copy to the Windows host at about
100 MB/s, so the limit is the Docker Desktop/WSL2 layer. The Proxmox production host does not have
that layer and should load several times faster. The **Stretch** and **Tiles** columns are compute
only and do not depend on the environment. Peak RSS is for the whole render process.

| Image | Load | Debayer + stretch | Tiles (pyvips) | Pyramid size |
|---|---|---|---|---|
| 4676×3502 16-bit mono FITS (16 MP) | <1 s | <0.5 s | ~1 s | ~8 MB |
| 6000×4000 CR2 (24 MP) | ~2 s (full demosaic) | – | ~1.5 s | ~12 MB |
| 11160×? XISF master, float, 2.8 GB | 10–30 s (disk-bound) | ~5 s | ~5 s | ~60 MB |

### Measured (2026-10-01, inside the backend container, files read from the NAS shares)

Cold reads from the shares run at roughly 5-18 MB/s (Tailscale-addressed CIFS), so **load time is
the NAS read, not compute**. Stretch and tiling are fast everywhere. Peak RSS is for the whole
render process.

| Image | Load (+debayer) | Stretch | Tiles | Total | Pyramid | Peak RSS |
|---|---|---|---|---|---|---|
| mono FITS sub 4656×3520 (16 MP, 32 MB) | 2.2-3.3 s | 0.08 s | 0.1 s | 2.4-3.5 s | 10.9 MB | 202 MB |
| BGGR OSC FITS sub 1280×960 (all-sky; the only BGGR subs in the library) | 0.2 s (debayer <0.01 s) | 0.10-0.15 s | 0.03 s | 0.3-0.4 s | 0.6 MB | 190 MB |
| RGGB OSC FITS sub 5472×3648 (20 MP, extra) | 3.0 s (debayer 0.04 s) | 0.7 s | 0.12 s | 3.9 s | 7.7 MB | 354 MB |
| CR2 3476×5208 (18 MP, 25 MB; rotated by the EXIF flip) | 2.0 s | 0.7 s | 0.11 s | 2.8 s | 5.2 MB | 414 MB |
| mono FITS 8288×5644 (47 MP) | 6.2 s | 0.15 s | 0.17 s | 6.5 s | 36 MB | 318 MB |
| RGB FITS cube 12576×8112 (102 MP, 1.2 GB; the largest FITS) | 269 s | 4.7 s | 0.4 s | 274 s | 5.8 MB | 1.7 GB |
| **XISF master 11160×10516 (117 MP, 2.8 GB; the largest XISF), `linear`** | 157 s | 5.0 s | 0.8 s | **162 s** | 5.6 MB | 3.3 GB |
| same, `auto` (repeat run; the share was slower) | 373 s | 5.6 s | 0.8 s | **379 s** | 48.6 MB | 3.3 GB |

Findings that changed the implementation:
- **Read each file once.** Reads dominated, so sources are read with ordinary reads (FITS/TIFF
  whole) and uncompressed XISF is read into RAM in **one** pass (up to 6 GB); the stretch scan and
  apply then run from memory. Block reads (two passes) remain only as the fallback above 6 GB.
  Memory-mapping measured much slower on this setup (one pass over a 390 MB XISF took 26 s mapped),
  so it is not used.
- A first build of a very large master needs about the file's size in RAM (3.3 GB for the 2.8 GB
  master). It stayed well inside the 15-minute task limit here even at 14.5 MB/s. Repeat views are
  instant.

At ~10 MB per typical sub, 20 GB holds about 2,000 viewed images. A first view takes 1–3 s for a sub
and around 30 s for the very largest masters. Repeat views are instant.

## 8. Tests (`backend/tests/test_fullres_*.py`)

- **Stretch:** for uint16 input, the LUT path output equals `apply_stf_stretch` within ±1 LSB. Block
  application of the float path equals whole-array application. `unlinked` gives per-channel medians
  ≈ target_bg.
- **Debayer:** synthetic 4×4 CFA frames with known R/G/B values for each of RGGB/BGGR/GRBG/GBRG,
  including `ROWORDER=BOTTOM-UP` and odd `X/YBAYROFF`. Each must produce the correct pure colours.
- **Orientation:** for a fixture FITS, XISF and TIFF, downsample the full-res render to thumbnail size
  and check correlation with the `load_source_image` output is > 0.98.
- **Cache key:** changes with mtime, size, preset and RENDER_VERSION, and is stable otherwise.
- **Eviction:** evicts the oldest first down to the cap, and spares builds less than 10 minutes old.
- **API:**
  - The 202 → ready flow, with the task executed eagerly.
  - A second request does not double-enqueue (lock).
  - Tile path traversal is rejected: `..`, a non-hex key, or a key belonging to another image all
    give 400/404.
  - A `409` comes back for `unlinked` on mono.
  - Native-shortcut images return `type:"image"`.
- Frontend: `npm run build` + `npm run lint`. The user does the browser checks.

## 9. Open questions / assumptions (defaults chosen; say if you disagree)

1. **libvips in the image.** It adds about 15 MB to the backend image. The fallback is the pure-Pillow
   tiler (§4.6). *Default: libvips.*
2. **Cache cap of 20 GB** on `./library/fullres` (the disk has 473 GB free). *Default: 20 GB.*
3. **Debayer algorithm: bilinear.** It is fast and fine for inspection. OpenCV's edge-aware `_EA`
   is a one-line swap if colour fringing on stars bothers you.
4. **Thumbnails stay mono for OSC subs.** Debayering thumbnails too would be a separate change with a
   data migration to regenerate about 11k thumbnails. *Not in V1.*

## 10. Out of scope (candidates for V2)

- Catalog object markers drawn from the WCS (vector overlays that stay sharp at any zoom).
- Raw ADU / pixel-value readout under the cursor.
- Nova / PixInsight annotation layers in the viewer.
- Live client-side stretch sliders (16-bit tiles + WebGL).
- Side-by-side / split compare of two images.

## 11. Phasing

| Phase | Scope | Done when |
|---|---|---|
| 1 | `services/fullres/*`, STF refactor, task + queue + supervisord program, settings, compose volume, Dockerfile deps, API, tests | Tests pass; a DZI builds in the container for one FITS sub, one OSC sub, one CR2 and the largest XISF master, with timings recorded in this doc |
| 2 | `FullResViewer` page, route, ImageDetail button + `F`, `useImageNav` refactor, readout, presets, sticky viewport, prefetch | `npm run build`/`lint` clean; frontend + backend rebuilt |
| 3 | Eviction beat task, Admin cache stats/clear, `docs/features/FULLRES_VIEWER.md`, docs index, VERSION bump | Cache stays under cap |
