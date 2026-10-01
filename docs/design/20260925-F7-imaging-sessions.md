# F7 — Imaging Sessions (Group by Observing Night)

Status: **Proposed** · Size: M · Value: 5 · Read first: [README.md](README.md) · Depends on: F1 schema (`frame_type`)

## 1. Problem

Imagers think in **nights**: "What did I shoot on 14 March? How many usable hours did I get? When did
the clouds roll in? Did I take flats that night?" AstroCat only offers a flat, filterable list of
files. An observing night also crosses midnight, so filtering by calendar date splits it in two.

Related gaps found while designing this:
- Extractors return `site_lat`/`site_long` (`fits_extractor.py` ~line 74, `xisf_extractor.py`), but
  `_process_image_impl` **never persists them**. `Image.site_latitude`, `site_longitude` and
  `site_name` are always NULL.
- `SITELAT`/`SITELONG` are often sexagesimal strings (`'+45 30 00'`, `'-071 12 30.5'`). `_parse_float`
  returns None for these.
- `capture_date` semantics differ by format. FITS/XISF `DATE-OBS` is **UTC**. EXIF `DateTimeOriginal`
  (RAW/JPG) is **camera-local time**. When both are missing, the indexer falls back to the file mtime.

## 2. Goals / Non-goals

**Goals**
1. Automatically group every image into an **imaging session**: one observing night × one camera × one site.
2. Persist sessions with derived stats: start/end, light hours, subs per target/filter, calibration
   counts, temperature range, gaps (interruptions), efficiency, and moon illumination.
3. User-editable session **notes** and **rating** that survive recomputation.
4. A **Sessions** page with a year-calendar heatmap and a session list, and a **Session detail**
   page with a timeline.
5. Persist site latitude/longitude from headers, and a default site in Settings.

**Non-goals**
- Weather/seeing data imports, N.I.N.A./ASIAIR log parsing (future).
- Frame-quality metrics (HFR/FWHM): a separate feature. Leave room in the timeline for them.
- Calibration matching (a separate feature). Sessions only *count* calibration frames.

## 3. Design

### 3.1 Observing-night definition

A session is keyed by `(night_date, camera_key, site_key)`.

**night_date** is the local civil date on which the night *began*. The boundary is local **solar
noon**, computed from longitude, so no time-zone database is needed:

```python
def observing_night(capture: datetime, basis: Literal["UTC","LOCAL"], longitude_deg: float | None) -> date:
    if basis == "LOCAL":            # EXIF: already camera-local wall-clock time
        return (capture - timedelta(hours=12)).date()
    if longitude_deg is None:       # UTC but unknown site: see fallback below
        longitude_deg = default_site_longitude_or_zero()
    local_solar = capture + timedelta(hours=longitude_deg / 15.0)   # east-positive longitude
    return (local_solar - timedelta(hours=12)).date()
```

- `basis` is `UTC` for FITS/FIT/XISF and `LOCAL` for CR2/CR3/ARW/NEF/DNG/JPG/JPEG/PNG/TIFF/TIF.
  Put this in a helper `capture_time_basis(file_format)`.
- Longitude precedence: the image's `site_longitude`, then Settings `default_site_longitude`, then 0.
  With 0, the boundary is 12:00 UTC, which splits nights for users at UTC+10 or in western Americas.
  So the Sessions page shows a **banner** prompting the user to set a default site when it is unset and
  more than 20% of UTC-basis images lack a longitude.
- **SITELONG sign convention:** N.I.N.A., SGP, Ekos and ASIAIR write east-positive. Some older
  software writes west-positive. Treat values as east-positive. Settings has a checkbox, "My capture
  software writes west-positive longitudes", which negates header longitudes when set. Store that in
  SystemSettings and apply it at parse time.

**camera_key**: `lower(strip(camera_name))`, with runs of whitespace collapsed, or `"unknown"`.
Telescopes are *not* part of the key. Swapping a lens mid-night is still the same session. Dual rigs
(two cameras) produce two sessions on the same night, and the UI groups them by night.

**site_key**: `f"{round(lat/0.05)*0.05:.2f},{round(lon/0.05)*0.05:.2f}"` (about 5 km buckets) when the
image has lat/lon, otherwise `"default"`.

All frame types are assigned to sessions. A session with zero lights is flagged
`is_calibration_only` (for example, a dark library shot on a cloudy afternoon).

Images with a mtime-fallback `capture_date` (no `DATE-OBS`/EXIF date) still get a session, because
the mtime is often right for files copied the same day. But the session shows a warning count
`fallback_date_count`. Detect this with `raw_header` lacking both `DATE-OBS` and `DATE`, and no EXIF
date. Simplest approach: have extractors add `metadata["capture_date_source"] = "HEADER"|"EXIF"`, and
have the indexer store it (new column, §3.2) with `"MTIME"` for the fallback.

### 3.2 Data model

New `backend/app/models/session.py` (imported in `models/__init__.py`):

```python
class ImagingSession(Base):
    __tablename__ = "imaging_sessions"
    id = Column(Integer, primary_key=True)
    night_date = Column(Date, nullable=False, index=True)
    camera_key = Column(String(100), nullable=False)
    site_key = Column(String(32), nullable=False, default="default")

    # --- derived (overwritten by recompute) ---
    camera_name = Column(String(100))              # most common original spelling
    telescopes = Column(JSONB)                     # ["RedCat 51", ...]
    site_latitude = Column(Float); site_longitude = Column(Float); site_name = Column(String(100))
    time_basis = Column(String(5))                 # UTC | LOCAL | MIXED
    start_time = Column(DateTime)                  # first frame start
    end_time = Column(DateTime)                    # last frame start + its exposure
    light_count = Column(Integer, default=0); light_seconds = Column(Float, default=0)
    dark_count = Column(Integer, default=0); flat_count = Column(Integer, default=0)
    bias_count = Column(Integer, default=0); dark_flat_count = Column(Integer, default=0)
    master_count = Column(Integer, default=0)
    targets = Column(JSONB)       # [{"object": "M31", "filter": "Ha", "subs": 40, "seconds": 12000, "first": iso, "last": iso}]
    filters = Column(JSONB)       # ["Ha","OIII"] raw names, in first-use order
    gaps = Column(JSONB)          # [{"start": iso, "end": iso, "minutes": 42}] light-frame gaps > threshold
    efficiency = Column(Float)    # light_seconds / (end_time - start_time), 0..1, lights only
    temp_min = Column(Float); temp_max = Column(Float)
    gains = Column(JSONB)         # distinct gain/ISO values
    moon_illumination = Column(Float)   # 0..1 at local midnight of night_date
    moon_altitude_max = Column(Float)   # optional; degrees, only when site known
    is_calibration_only = Column(Boolean, default=False)
    fallback_date_count = Column(Integer, default=0)
    recomputed_at = Column(DateTime)

    # --- user-owned (never touched by recompute) ---
    title = Column(String(200)); notes = Column(Text); rating = Column(Integer)  # 0-5

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint('night_date', 'camera_key', 'site_key', name='uq_session_key'),)
```

Add to `Image` (block `# Session (F7)`):

```python
session_id = Column(Integer, ForeignKey("imaging_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
capture_date_source = Column(String(10), nullable=True)   # HEADER | EXIF | MTIME
```

Migration `f7c9e50b0003` (see README §3 for `down_revision`) defensively creates the table, the
unique constraint, the `night_date` index, the two image columns, the FK and index
`ix_images_session_id`.

**Important:** in `models/image.py`, declare the FK using the string table name so import order is
safe. Do not add a `relationship` on `Image`, because `ImageList` serialization must not trigger lazy
loads. `ImagingSession` may have `images = relationship("Image", lazy="noload")` if needed.

### 3.3 Site persistence fix (small, required)

In `_process_image_impl`, in both the update and insert branches:

```python
image.site_latitude = metadata.get("site_lat")
image.site_longitude = metadata.get("site_long")
image.capture_date_source = metadata.get("capture_date_source") or ("MTIME" if <fell back> else None)
```

In `FITSExtractor` and `XISFExtractor`, parse lat/long with a new helper
`BaseExtractor._parse_angle(val) -> float|None`. It accepts floats, numeric strings, and sexagesimal
strings `'[+-]DD[ :]MM[ :]SS(.s)'`, and also reads `OBSGEO-B`/`OBSGEO-L` and `SITELAT`/`SITELONG`
aliases `LAT-OBS`/`LONG-OBS`. Apply the west-positive setting here. The extractor has no Redis
access, so do it in the indexer via a cached settings read. There is also `site_name` from
`OBSERVAT`/`SITENAME` if present.

EXIF GPS (`GPS GPSLatitude` etc.) is optional. Parse it if it is trivially available in
`exif_extractor.py`, otherwise skip.

### 3.4 Assignment — `backend/app/services/sessions.py` (new)

Pure functions (unit-tested): `capture_time_basis`, `observing_night`, `camera_key`, `site_key`,
`session_key_for(image_fields, settings) -> (night_date, camera_key, site_key) | None`. It returns
None when `capture_date` is NULL.

Sync DB function, used by the indexer and the backfill:

```python
def assign_session_sync(session, image, settings) -> None:
    key = session_key_for(...)
    old = image.session_id
    if key is None:
        image.session_id = None
    else:
        stmt = (pg_insert(ImagingSession).values(night_date=..., camera_key=..., site_key=...)
                .on_conflict_do_update(constraint="uq_session_key", set_={"updated_at": func.now()})
                .returning(ImagingSession.id))
        image.session_id = session.execute(stmt).scalar_one()
    mark_dirty(old, image.session_id)     # Redis SADD sessions:dirty <ids> (skip None)
```

`ON CONFLICT DO UPDATE ... RETURNING` is race-safe when many Celery workers process files from the
same night concurrently. Do not recompute aggregates inline, because that would be O(n²) during a
bulk scan.

Call sites:
- `tasks/indexer.py::_process_image_impl`, before `session.commit()` (README §4 ordering).
- `tasks/indexer.py::_scan_directory`: before the bulk `delete(Image)` of missing paths (~line 108),
  select the distinct `session_id` of the rows being deleted and `mark_dirty` them.
- `api/images.py`: `PUT /{id}`, when `frame_type` changes (F1) → `mark_dirty(image.session_id)`.

Settings are read once per task from Redis (`system_settings` key, see `app/api/settings.py`). Cache
them in a module-level variable with a 60 s TTL.

### 3.5 Recompute — `backend/app/tasks/sessions.py` (new Celery module)

- `app.tasks.sessions.recompute_dirty`: a beat job every **60 s**. It `SPOP`s up to 500 ids from
  `sessions:dirty` and calls `recompute_session(id)` for each. It is idempotent.
- `recompute_session(db, id)` (sync):
  1. Load `(id, capture_date, exposure_time_seconds, frame_type, subtype, filter_name, object_name,
     temperature_celsius, gain, iso_speed, telescope_name, camera_name, site_latitude,
     site_longitude, capture_date_source, file_format)` for the session, ordered by `capture_date`.
  2. If there are zero rows: delete the session **unless** `notes` or `title` or `rating` is set. In
     that case keep it with zeroed counts so user notes are not lost.
  3. Otherwise compute every derived column in Python (straightforward folds). Lights means
     `frame_type == LIGHT and subtype == SUB_FRAME`. Masters are counted separately.
     - `gaps`: between consecutive light frames, `gap = next.start - (prev.start + prev.exposure)`.
       Record the gap if > `max(15 min, 3 × median exposure)`.
     - `efficiency = light_seconds / max(end_time - start_time, 1s)`, clamped to 0..1.
       Null if < 2 lights.
     - `moon_illumination`: astropy. Local midnight is `night_date + 1 day 00:00` local solar →
       convert to UTC using the longitude. `sun = get_body('sun', t)` and `moon = get_body('moon', t)`,
       `elong = sun.separation(moon)`, `illum = (1 - cos(elong)) / 2`. Cache by `night_date` in a
       dict, since it is the same for all sessions that night (±negligible). `moon_altitude_max` is
       optional: sample hourly between `start_time` and `end_time` with `AltAz` when the site is known.
     - `time_basis`: `MIXED` if both bases appear. The UI then labels times as approximate.
  4. Set `recomputed_at = utcnow()`.
- `app.tasks.sessions.rebuild_all`: reassigns every image (keyset batches of 2000, same logic as the
  backfill), then recomputes all sessions and deletes empty ones that have no user data. It is
  triggered by an Admin button and **automatically** from `POST /api/settings` when
  `default_site_longitude` or the west-positive flag changes, because night boundaries move. It sets
  a Redis flag `sessions:rebuild_running` so the UI can show progress.
- `app.tasks.sessions.sweep`: a nightly beat job at 12:00 UTC that marks all sessions touched in the
  last 3 days as dirty. This is a belt-and-braces measure against missed dirty marks.

`worker.py`:
- Append `"app.tasks.sessions"` to `include`.
- Add the route `"app.tasks.sessions.*": {"queue": "indexer"}`.
- Add the beat entries `sessions-recompute-dirty` (60 s) and `sessions-sweep` (crontab 12:00).

### 3.6 Backfill — `backend/app/scripts/backfill_sessions.py`

This runs `rebuild_all` logic synchronously, with progress output. It is resumable via
`--only-missing`, which only processes images with `session_id IS NULL AND capture_date IS NOT NULL`,
and that is the default. It does **not** re-read files. Site lat/long for already-indexed images come
from `raw_header` (`SITELAT`/`SITELONG` keys) using the same `_parse_angle`, so the backfill also fills
`site_latitude`/`site_longitude` where they are NULL. Add it to the startup chain after the F1
backfill. It is fast and a no-op when complete.

### 3.7 Settings

Extend `SystemSettings` in `app/api/settings.py` with optional fields that have defaults, so existing
Redis JSON still parses:

```python
default_site_name: Optional[str] = None
default_site_latitude: Optional[float] = None   # -90..90
default_site_longitude: Optional[float] = None  # -180..180, east-positive
site_longitude_west_positive: bool = False
session_gap_minutes: int = 15
```

Validate the ranges. If the longitude or the flag changed, enqueue `rebuild_all`. Add a "Observing
site" card to the Admin settings section (`Admin.jsx`, near `systemSettings`) with name, lat and lon
inputs, a "Use my browser location" button (`navigator.geolocation`, filling in the fields for the
user to confirm), the west-positive checkbox, and the gap threshold.

### 3.8 API — `backend/app/api/sessions.py` (new router, `/api/sessions`)

| Method & path | Purpose |
|---|---|
| `GET /api/sessions?year=&start=&end=&camera=&object=&include_calibration_only=false&page=&page_size=&sort=night_desc` | Paginated `SessionSummary` list. `object` matches inside the `targets` JSONB (`targets @> '[{"object": "M31"}]'`, or an ILIKE over `jsonb_array_elements`). |
| `GET /api/sessions/calendar?year=2026` | `[{night_date, sessions: n, light_seconds, is_calibration_only}]`, one row per night with any session. Cached in Redis for 120 s. |
| `GET /api/sessions/years` | `[{year, nights, light_seconds}]` for the year selector and totals. |
| `GET /api/sessions/{id}` | `SessionDetail` = summary + `timeline: [{image_id, start, exposure, frame_type, filter, object, temp, gain}]` for **all** frames, ordered by time. Cap it at 5000 and set `truncated` if exceeded. |
| `PATCH /api/sessions/{id}` | Body `{title?, notes?, rating?}`. Only user-owned fields. |
| `POST /api/sessions/rebuild` | Enqueue `rebuild_all`, returns 202. Admin only (reuse the pattern that `admin.py` uses for admin checks). |
| `GET /api/sessions/rebuild/status` | `{running: bool}`. |
| `_build_image_query` | New kwarg `session_id: Optional[int]`, and a `Query` param on the 4 filter endpoints. |

`schemas/session.py`: `SessionSummary` contains every column except the timeline. `targets`, `gaps`
and `filters` are typed as lists of small models. Add `duration_seconds` (computed) and
`display_title = title or f"{night_date:%a %d %b %Y} · {camera_name}"`.

`ImageDetail` schema: add `session_id: Optional[int]` and `capture_date_source`.

### 3.9 Frontend

- **Nav**: "Sessions" after Targets (icon `CalendarDays`).
- **`/sessions` → `pages/Sessions.jsx` + `.css`**
  - Year selector (from `/years`) with totals: nights, light hours, sessions.
  - **Calendar heatmap**: a custom SVG GitHub-style grid (53 weeks × 7 days, month labels). Cell
    shade scales with `light_seconds` using 5 buckets from the accent CSS variable. Calibration-only
    nights get an outlined cell. Hover shows a tooltip: `Sat 14 Mar 2026 · 5h 20m · 2 sessions`.
    Clicking a cell filters the list to that night (or navigates when there is exactly one session).
    Render it in a horizontally scrollable container on mobile.
  - **Session list** (newest first). Each row shows the date and weekday, `display_title`, camera and
    telescopes, target chips (`M31 · Ha 3h, OIII 2h`), light hours, efficiency %, moon icon plus %
    (🌑…🌕 buckets or a small SVG), calibration counts (`D 30 · F 40 · B 50`), a notes indicator, and
    stars for the rating.
  - The site banner from §3.1 when applicable, linking to Admin → settings.
- **`/sessions/:id` → `pages/SessionDetail.jsx` + `.css`**
  - Header: editable title, night, site, rig, moon, rating (reuse `RatingStars`), and totals
    (light hours, span, efficiency).
  - **Timeline** (recharts `ScatterChart` or a custom SVG): x = time, one lane per target (+ a
    "Calibration" lane), and each sub drawn as a rect of width = exposure, colored by filter (use the
    F2 palette if present, otherwise hash the filter name to a hue). Gaps are shaded with a hatch and a
    label (`42 min gap`). Overlay a secondary-axis line for sensor temperature. When
    `time_basis == UTC`, show times in **local solar time** of the session longitude, with a toggle to
    show UTC.
  - Table target × filter: subs, seconds, first and last.
  - Thumbnail strip: 24 evenly sampled lights (`/search?session_id=…` for all), using `ImageCard` or
    the existing thumbnail endpoint.
  - Notes textarea that autosaves on blur, with a debounced 800 ms `PATCH`.
- **Dashboard.jsx**: a "Recent sessions" widget with the last 5 (date, title, hours, targets), linking
  to detail.
- **ImageDetail.jsx**: a "Session" row linking to `/sessions/{session_id}`, plus a small warning
  `(date from file time)` when `capture_date_source == 'MTIME'`.
- **Search.jsx / FilterChips.jsx**: honor `session_id` in the URL.
- **client.js**: `fetchSessions`, `fetchSessionCalendar`, `fetchSessionYears`, `fetchSession`,
  `updateSession`, `rebuildSessions`, `fetchSessionRebuildStatus`.

## 4. Files touched

New: `app/models/session.py`, `app/services/sessions.py`, `app/tasks/sessions.py`, `app/api/sessions.py`,
`app/schemas/session.py`, `app/scripts/backfill_sessions.py`, `alembic/versions/…-f7c9e50b0003_add_imaging_sessions.py`,
`tests/test_sessions.py`, `tests/test_parse_angle.py`, `frontend/src/pages/Sessions.jsx/.css`,
`SessionDetail.jsx/.css`, `docs/features/SESSIONS.md`.
Modified: `models/image.py`, `models/__init__.py`, `schemas/image.py`, `extractors/base.py`,
`extractors/fits_extractor.py`, `extractors/xisf_extractor.py`, `extractors/exif_extractor.py`
(`capture_date_source`), `tasks/indexer.py`, `api/images.py`, `api/settings.py`, `worker.py`,
`main.py`, startup chain (compose templates, `rebuild_and_seed.ps1`, README), frontend `App.jsx`,
`Layout.jsx`, `Admin.jsx`, `Dashboard.jsx`, `ImageDetail.jsx`, `Search.jsx`, `FilterChips.jsx`,
`client.js`, `CLAUDE.md` (beat jobs list), `docs/core/DATABASE_SCHEMA.md`, `docs/development/UTILITY_SCRIPTS.md`.

## 5. Testing

Unit (pure):
- `observing_night`:
  - UTC basis at lon −75 (US East): 2026-03-15T03:00Z → 2026-03-14. 2026-03-14T23:30Z → 2026-03-14.
  - UTC basis at lon +150 (Australia): 2026-03-14T10:00Z (20:00 local) and 2026-03-14T18:00Z (04:00
    local next day) both → 2026-03-14.
  - LOCAL basis: 2026-03-15T02:00 → 2026-03-14, and 2026-03-14T13:00 → 2026-03-14.
  - lon None → uses the default, then 0.
- `_parse_angle`: `45.5`, `"45.5"`, `"+45 30 00"`, `"-071:12:30.5"`, `"garbage"` → None.
- `site_key` bucketing and `camera_key` normalization.
- The recompute fold, with a synthetic frame list:
  - gap detection (threshold vs median exposure)
  - efficiency
  - calibration-only flag
  - `MIXED` basis
  - targets JSON grouping
- Moon illumination: a known full moon (2026-03-03) > 0.95, a known new moon (2026-03-19) < 0.05.
  Verify these dates against an ephemeris in the test comment, and use tolerance.

Concurrency: in a test DB, if available, run 20 threads calling `assign_session_sync` on the same key.
Exactly one session row should result.

Manual: pick a known night from the library and confirm that the session start/end match the first
and last sub, the hours match Search `session_id=`, and that setting the default site longitude
rebuilds correctly.

## 6. Acceptance criteria

1. After deploy, every image with a `capture_date` has a `session_id`. A night that crosses midnight
   (local) is one session, not two.
2. Two cameras on the same night give two sessions, shown together on the calendar day.
3. Notes, title and rating survive `rebuild_all` and rescans.
4. Deleting files from disk and rescanning updates the counts within about 1 minute (beat
   recompute), and fully emptied sessions without notes disappear.
5. `site_latitude`/`site_longitude` are populated for FITS/XISF files that carry SITELAT/SITELONG in
   any supported format.
6. Calendar and detail pages work at 375 px (the calendar scrolls horizontally).

## 7. Decisions and follow-ups

- **Resolved (owner, 2026-09-25): never split a night.** One session per camera per night per site.
  Long gaps are shown on the timeline as interruptions, and there is no split threshold setting.
- Follow-up, not a decision: should targets in the session summary use F2's `target_key`? **Default: raw `object_name`** now
  (keeps F7 independent). Follow-up after F2 merges: use `COALESCE(target_key, object_name)` and
  normalized filters.
