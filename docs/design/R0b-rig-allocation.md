# R0b — Find & allocate images with no rig

Status: **Proposed** · Written: 2026-09-29 · Base commit: `b9edc51` (`main`) · Parent: [P0-R0-equipment-sites.md](P0-R0-equipment-sites.md), shipped behaviour in [docs/features/EQUIPMENT.md](../features/EQUIPMENT.md)

## Revision (2026-09-29, after first build)

Grouping by subtype × frame size × header binning × plate scale gave 519 groups on the live
library, and the 25% scale rule made loose suggestions. The user changed the design as
follows. This supersedes §4 and §5.2, and the parts of §6.2 and §7.1 that depend on them.

- **Buckets** are camera × **derived** binning × **calculated focal length** (5% clusters).
  Binning is relative to the camera's native sensor (dims, then XPIXSZ; header binning
  only as a fallback). Focal length is `206.265 × effective pixel ÷ solved scale`, with
  FOCALLEN / EXIF as the fallback for unsolved frames. Subs and masters share a bucket.
  Key: `{cam_key|none}|b{bin}|{focal_min:.1f}`, or `|-` for unknown focal.
  On the live data this gives 161 buckets (75 with ≥ 20 images).
- **Suggestion:** an exact `assign_rig` match, else a rig on the same camera with the same
  effective pixel size (binning) and effective focal length within 5%. Otherwise none.
  The scale-25% and camera-only fallbacks are gone.
- **Bulk apply:** `POST /api/equipment/unassigned/assign` takes
  `{items: [{key, rig_id}]}` and returns `{updated_count, results, stale_keys}`. It
  returns 409 only when every key is stale. The panel has per-bucket checkboxes, a
  "Select all with a rig" shortcut and one "Assign selected" button.

## 0. Who builds this

**Recommended:** use one `general-purpose` agent on **Opus** with `isolation: "worktree"`, and have it build backend and frontend in a single pass. Don't split the work.
- **Why that agent type:** it's the only type that can edit files and run commands. `Explore` and `Plan` are read-only.
- **Why Opus:** the work touches two layers and has subtle invariants: MANUAL precedence, grouping in the GET and POST must be identical, and `rig_id` is re-typed across 6 endpoints. Sonnet would probably manage, but a slip here silently mis-assigns data.
- **Why one agent:** the API contract below is small and the frontend depends on it directly. Two parallel agents would spend more effort coordinating than they save.
- **Suggested prompt:** "Implement docs/design/R0b-rig-allocation.md exactly. Read CLAUDE.md first. Follow §9 (build order) and §10 (acceptance). Do not bump VERSION or rebuild Docker until tests and the frontend build pass; then do both per CLAUDE.md. Commit on a feature branch; do not merge."

## 1. Problem

`assign_rig` (`backend/app/services/equipment_assignment.py:146`) never guesses. A frame whose dims or solved scale doesn't fit a rig exactly keeps `rig_id IS NULL`. This hits **masters** hardest: cropped or drizzled stacks never match the sensor dims, so the result is `no_camera_match` / `scale_mismatch`. The only way to fix these today is one image at a time in ImageDetail.

Users need to **find** those images and **allocate** them to a rig in bulk.

## 2. Decisions (confirmed with the user)

| # | Decision |
|---|----------|
| D1 | Scope is **LIGHT** frames with `subtype IN (SUB_FRAME, INTEGRATION_MASTER)` only. PLANETARY, INTEGRATION_DEPRECATED and calibration frames are out of scope. |
| D2 | Build **both** UIs: (a) an "Unassigned images" panel on Equipment → Rigs that groups similar frames, with per-group allocation; (b) Search gets a **Rig** filter (Any / Unassigned / each rig) and a bulk **Assign Rig…** action. |
| D3 | Each group **pre-selects a suggested rig** when one is plausible. The user still confirms before anything is saved. |
| D4 | Allocations are written as `rig_source='MANUAL'`. The batch task (`app/tasks/equipment.py:154`) and the indexer hook (`apply_equipment`, `equipment_assignment.py:442`) already skip MANUAL rows, so nothing overwrites them. |
| D5 | No schema change and no migration. No data migration either. |

Non-goals: changing auto-assignment rules, site allocation, and counting masters in `rig.image_count`. That count stays light-subs-only (`_rig_usage`, `api/equipment.py:283`). Say so in the docs.

## 3. Shared constants

In `backend/app/services/equipment_assignment.py` (near the other constants):

```python
ALLOCATABLE_SUBTYPES = ("SUB_FRAME", "INTEGRATION_MASTER")
```

The SQLAlchemy predicate goes in `backend/app/services/rig_allocation.py` (new, §4):

```python
def allocatable_clause():
    return (Image.frame_type == FrameType.LIGHT) & Image.subtype.in_(
        [ImageSubtype.SUB_FRAME, ImageSubtype.INTEGRATION_MASTER])
```

## 4. New pure module: `backend/app/services/rig_allocation.py`

The module takes no session and does no I/O, so it can be unit-tested without a DB (same style as `equipment_assignment.py`).

### 4.1 `suggest_rig(sample: dict, rigs: List[RigInfo]) -> Tuple[Optional[int], Optional[str]]`

`sample` has the same keys as `assign_rig`'s image dict: camera_name, width_pixels, height_pixels, binning, pixel_scale_arcsec, xpixsz, focallen, focal_length. It returns `(rig_id, basis)`, where basis is one of `"exact" | "scale" | "focal" | "camera"`, or `(None, None)`.

1. `rig_id, _ = assign_rig(sample, rigs)`. If that gives a rig, return `(rig_id, "exact")`. This can happen when a rig was created after the last run.
2. `cands = [r for r in rigs if camera_matches(sample["camera_name"], r.patterns)]`. Dims are **ignored** here. If `cands` is empty, return `(None, None)`.
3. If `valid_pixel_scale(sample["pixel_scale_arcsec"])` gives a value `s`:
   - For each candidate, `ref = r.declared_scale() or r.measured_scale_arcsec`, both at `r.binning`.
   - Pick the smallest `abs(ref - s)/ref`. If it is `<= SUGGEST_SCALE_TOLERANCE = 0.25`, return `(id, "scale")`.
4. Take `focal = _num(focallen) or _num(focal_length)`, reusing `_num` from equipment_assignment. If exactly one candidate has `effective_focal` within `SUGGEST_FOCAL_TOLERANCE = 0.10`, return `(id, "focal")`.
5. Let `active = [r for r in cands if r.is_active]`. If `len(active) == 1`, return `(active[0].id, "camera")`. Otherwise, if `len(cands) == 1`, return `(cands[0].id, "camera")`.
6. Return `(None, None)`.

### 4.2 `group_unassigned(rows: Iterable[dict], rigs: List[RigInfo]) -> List[dict]`

`rows` holds one dict per unassigned allocatable image (see the §5.1 SQL): `id, camera_name, subtype, width_pixels, height_pixels, binning, pixel_scale_arcsec, xpixsz, focallen, focal_length, filter_name, exposure_time_seconds, capture_date`.

Algorithm:
1. Partition by `(subtype, cam_key, width_pixels, height_pixels, binning)`.
   - `cam_key = normalize_camera_name(camera_name) or ""`, from `equipment_detection.py:53`.
   - `None` is a valid partition value everywhere, e.g. a master with no INSTRUME.
2. Within a partition, split the rows into `scaled` (with `_scale = valid_pixel_scale(...)` set) and `unscaled`.
   - `scaled` is clustered with `cluster_by_scale(scaled, "_scale", CLUSTER_TOLERANCE)` (`app/utils/rig_optics.py:218`, 5%).
   - `unscaled` becomes one group, if it is non-empty.
3. Each group outputs:

```json
{
  "key": "INTEGRATION_MASTER|zwo asi2600mm pro|6248x4176|1|1.2210",
  "subtype": "INTEGRATION_MASTER",
  "camera_name": "ZWO ASI2600MM Pro",
  "width_pixels": 6248, "height_pixels": 4176, "binning": "1",
  "scale_min": 1.221, "scale_max": 1.268, "scale_median": 1.24,
  "focal_mm": 530.0,
  "filters": ["Ha", "OIII"],
  "count": 12, "total_exposure_s": 43200.0,
  "first_capture": "2025-03-01T21:04:00", "last_capture": "2026-08-30T02:11:00",
  "reason": "no_camera_match",
  "suggested_rig_id": 3, "suggestion_basis": "scale",
  "sample_image_id": 81234
}
```

Field rules:
- **`key`**: `f"{subtype}|{cam_key}|{w}x{h}|{binning}|{scale_min:.4f}"`. Use `none` for null parts and `-` for the scale of the unscaled group. The key is **deterministic from the members**, so the POST can re-derive it (§5.2).
- **`camera_name`**: the most common raw name in the group.
- **`scale_median`**: `weighted_median` with weight 1 per row.
- **`focal_mm`**: the median of `_num(focallen) or _num(focal_length)`, or null.
- **`filters`**: distinct `normalize_filter(filter_name)` values, sorted with `filter_sort_key` if available, else alphabetically. `"None"` is dropped.
- **`reason`**: the second element of `assign_rig(rep, rigs)`, where `rep` is the member with the median scale (any member for the unscaled group).
- **`suggested_rig_id` / `suggestion_basis`**: `suggest_rig(rep, rigs)`.
- **`sample_image_id`**: `rep["id"]`.

Group ids are **not** returned to the client. The POST recomputes them.

4. Sort by `count` descending, then by `key`.

### 4.3 `member_ids(rows, rigs, key) -> Optional[List[int]]`

This runs the same partition and cluster steps as 4.2 and returns the ids of the group whose key equals `key`, or `None`. Share one internal `_groups(rows)` generator between 4.2 and 4.3 so they can't drift apart.

## 5. Backend API

### 5.1 `GET /api/equipment/unassigned` in `backend/app/api/equipment.py`

This is a read, so it needs a logged-in user but no admin.

```python
_UNASSIGNED_SQL = text("""
    SELECT id, camera_name, subtype::text AS subtype, width_pixels, height_pixels, binning,
           pixel_scale_arcsec, raw_header->'XPIXSZ' AS xpixsz, raw_header->'FOCALLEN' AS focallen,
           focal_length, filter_name, exposure_time_seconds, capture_date
    FROM images
    WHERE rig_id IS NULL AND frame_type = 'LIGHT'
      AND subtype IN ('SUB_FRAME', 'INTEGRATION_MASTER')
""")
```

(`xpixsz` and `focallen` come back as JSON values. `assign_rig` already copes via `unchar`/`_num`, the same as `tasks/equipment.py:_ROWS_SQL`.)

- Load the rigs with `await db.run_sync(lambda s: load_rig_infos_sync(s))`.
- Response: `{"total": <int images>, "groups": [...]}`. Build the groups with `await asyncio.to_thread(group_unassigned, rows, rigs)`.
- Don't cache it: it needs to update right after an assignment. A library with ~100k unassigned rows is still fine.

### 5.2 `POST /api/equipment/unassigned/assign` (admin)

- **Body schema.** Add to `backend/app/schemas/equipment.py`: `class UnassignedAssign(_Body): key: str = Field(min_length=1, max_length=400); rig_id: int`.
- **Steps:**
  1. `await _get_or_404(db, Rig, body.rig_id)`. On failure return 400 `"Unknown rig_id"`, not 404, to match `images.py:905`.
  2. Re-run `_UNASSIGNED_SQL`, load the rigs, and call `ids = member_ids(rows, rigs, body.key)`.
  3. If `ids` is `None`, return **409** `"This group changed; refresh and try again"`.
  4. Run `UPDATE images SET rig_id=:rig, rig_source='MANUAL' WHERE id = ANY(:ids) AND rig_id IS NULL`, then commit.
  5. Call `await _equipment_changed(queue=False)`. It drops the detection and recommendation caches.
  6. Call `from app.api.targets import _invalidate_targets_cache; await _invalidate_targets_cache()`. Wrap it in try/log, as `images.py:922` does.
  7. Return `{"updated_count": <rowcount>, "rig_id": ..., "key": ...}`.
- **Admin check:** the existing `test_writes_require_admin` (`tests/test_equipment_api.py:165`) covers this route automatically, because every POST on `api.router` must depend on `require_admin`.

### 5.3 Search: re-type `rig_id` in `backend/app/api/images.py`

- **Filter semantics:** in `_build_image_query` (line 62), `rig_id: Optional[int]` becomes `Optional[str]`, and the filter block at line 229 becomes:

```python
if rig_id not in (None, ""):
    if rig_id == "none":
        stmt = stmt.where(Image.rig_id.is_(None))
    else:
        try:
            stmt = stmt.where(Image.rig_id == int(rig_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="rig_id must be an integer or 'none'")
```

- **Endpoints to re-type:** change `rig_id: Optional[int] = Query(...)` to `Optional[str]` with the description `"Assigned rig id, or 'none' for no rig (R0)"` on **every** endpoint that declares it. Today those are lines 341 (list), 448, 1095 (bulk/subtype), 1235 (bulk/frame-type), 1362 (bulk/target) and 1478 (bulk/metadata). `grep -n "rig_id: Optional\[int\] = Query" backend/app/api/images.py` must return nothing afterwards.
- **Other callers:** check with `grep -rn "_build_image_query" backend/app`. Any other caller that passes an int still works through `int(rig_id)`, but `str(...)` is safer.

### 5.4 `PUT /api/images/bulk/rig` (new, in `images.py`, after `bulk_assign_target`)

- **Signature:** copy `bulk_assign_target`'s filter parameters exactly, including `frame_type` and the `rig_id: Optional[str]` filter. Replace its `target` param with `new_rig_id: str = Query(..., description="Rig id to assign, or 'none' to clear (auto-assign may then pick one)")`.
- **Auth:** follow the other `/bulk/*` endpoints and add no admin dependency, consistent with `PUT /images/{id}` rig override.
- **Steps:**
  1. Parse `new_rig_id`. `"none"` means `(None, None)`. An int is checked with `db.get(Rig, ...)`, giving 400 `"Unknown rig_id"` if missing, and then `(id, "MANUAL")`. Anything else is a 400.
  2. Build the statement with `_build_image_query(...)`. Select **ids plus frame_type and subtype only**: `stmt.with_only_columns(Image.id, Image.frame_type, Image.subtype)`, or build a `select(Image.id)` subquery from it. That avoids loading every ORM object with its `catalog_matches`.
  3. Split the ids into `eligible` (LIGHT + allocatable subtype) and `skipped` (everything else).
  4. Run `UPDATE images SET rig_id=:rig, rig_source=:src WHERE id = ANY(:ids)` over `eligible`, in chunks of 5000, then commit.
  5. Invalidate the targets cache (as in bulk_target). Also invalidate recommendations via `from app.api.equipment import _invalidate_recommendations`.
  6. Return `{"updated_count": len(eligible), "skipped_count": len(skipped), "errors": [...]}`. On an exception: roll back, append the error, and set `updated_count=0`. Mirror `bulk_assign_target`.

### 5.5 Things to leave alone

- `tasks/equipment.py` needs no change: MANUAL is already respected.
- `rigs.measured_scale_arcsec` is refreshed by the next task run, from LIGHT SUB_FRAME rows only (`tasks/equipment.py:306`). Manually allocated subs will feed it. That is intended.

## 6. Frontend

### 6.1 `frontend/src/api/client.js`

- **`fetchImages`** (line 104): add `rig_id: params.rig_id,` after `target_key`.
- **Equipment section** (near `triggerEquipmentAssign`, line 909):

```js
export async function fetchUnassignedImages() {
    return handleResponse(await fetch(`${API_BASE_URL}/equipment/unassigned`, { credentials: 'include' }));
}
export async function assignUnassignedGroup(key, rigId) {
    return handleResponse(await fetch(`${API_BASE_URL}/equipment/unassigned/assign`, {
        method: 'POST', headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ key, rig_id: rigId }), credentials: 'include',
    }));
}
```

- **Images section:** add `bulkAssignRig(newRigId, searchParams)`, a clone of `bulkUpdateFrameType` (line 486) that hits `/images/bulk/rig?new_rig_id=...`.

### 6.2 Equipment page: `frontend/src/pages/Equipment.jsx` + `Equipment.css`

Add a new component `UnassignedImagesSection({ rigs, isAdmin, showToast, onAssigned })`, rendered in the Rigs tab **below** the `equip-grid` (inside the fragment at line ~1243, and also shown when `rigs.length === 0` is false only). Details:

- **Data:** `useQuery({ queryKey: ['equipmentUnassigned'], queryFn: fetchUnassignedImages, staleTime: 60_000 })`.
- **Header:** "Unassigned images" plus a count badge (`total`) and a one-line explanation: "Light subs and masters that couldn't be matched to a rig automatically."
  - Collapsed by default when there are more than 0 groups. Remember the open state in `localStorage`, wrapped in try/catch.
  - Hide the whole section when `total === 0`.
- **One row per group** (a table or card list, whichever matches the existing CSS). Columns:
  - Type badge: Sub or Master.
  - Camera (`camera_name` or "Unknown camera").
  - `W×H`, plus `bin N` when binning isn't 1.
  - Scale: `scale_min–scale_max″`, or "unsolved".
  - Focal length, when known.
  - Filters, as chips.
  - `count` and `formatHours(total_exposure_s)`. Reuse the helper if one exists, else write a local one.
  - Date range (`formatDateTime` is already imported).
  - Reason in plain words, from `REASON_TEXT`:
    - `no_camera_match`: "Camera or frame size doesn't match any rig"
    - `scale_mismatch`: "Plate scale doesn't match any rig"
    - `ambiguous`: "Several rigs fit; couldn't choose"
    - `no_active_rig`: "Only inactive rigs match"
- **Rig `<select>`:** options are "Choose rig…" followed by every rig (`name · camera_name · effective_focal_mm mm`).
  - Its initial value is `suggested_rig_id ?? ''`. Keep the selection per `key` in component state.
  - When the selection equals the suggestion, show a small hint: "Suggested (by scale/focal length/camera)", mapped from `suggestion_basis`. For `exact`, show "matches rig".
- **Assign button:** only for admins; disabled until a rig is chosen.
  - On click: `assignUnassignedGroup(key, rigId)` → toast `"Assigned N images to <rig>"` → invalidate `['equipmentUnassigned']` and `['equipment']`.
  - A 409 gives the toast "This group changed — refreshed" and a refetch.
- **View link:** `<Link>` to Search with `rig_id=none&frame_type=LIGHT&subtype=<subtype>&camera=<camera_name>` (camera only when non-null), plus `pixel_scale_min=<scale_min - 0.005>&pixel_scale_max=<scale_max + 0.005>` when scaled. Tooltip: "Approximate — Search can't filter by frame size".
- **Loading/error:** a small spinner or a muted error line, and never break the rest of the page.

### 6.3 Search page: `frontend/src/pages/Search.jsx`

- **Filter state:** add `rig_id` to the initial `filters` state (~line 105), the URL-sync effect (~146), `loadImages` (after line 195: `if (searchParams.get('rig_id')) params.rig_id = searchParams.get('rig_id');`), and `clearFilters` (~296).
- **Rig filter:** in the "Image Properties" FilterSection, after Frame Type (line 584), add a **Rig** select with options `''` Any rig, `none` Unassigned, then each rig by name.
  - Rigs come from `useQuery({ queryKey: ['equipment'], queryFn: fetchEquipment, staleTime: 60_000 })`, the same key Equipment.jsx uses so the cache is shared.
  - The onChange applies immediately, as Frame Type does.
- **Assign Rig… button:** add a header button **"🔭 Assign Rig…"** after "Set Frame Type…" (line 481), disabled when `images.length === 0`. It opens a modal cloned from the Set Frame Type modal (lines ~1071–1165), with:
  - Copy: "Assigns a rig to every **Light sub-frame and master** in the current results (N shown). Other frames are skipped. Assigned rigs are marked manual and won't be changed by auto-assignment."
  - A select with each rig, plus `none` = "Clear rig (let auto-assign decide)".
  - Submit: `bulkAssignRig(value, effectiveSearchParams())`. Show `✓ Updated X image(s); skipped Y` and reload after 1.5 s, the same flow as frame type.
- **Chip:** pass `rigNames` (an `{id: name}` map) to `FilterChips`.

### 6.4 `frontend/src/components/layout/FilterChips.jsx`

- Accept a `rigNames = {}` prop.
- Add to `filterLabels`: `rig_id: { label: 'Rig', format: (v) => v === 'none' ? 'Unassigned' : (rigNames[v] || `#${v}`) }`.

## 7. Tests

### 7.1 New file `backend/tests/test_rig_allocation.py`

Pure, no DB. Build `RigInfo`s the way `tests/test_equipment_assignment.py` does.
1. **Cropped master:** dims don't match, scale is within 25% of one rig → `("scale")` suggestion, and `reason == "no_camera_match"`.
2. **Drizzled 2x master:** the scale is half the rig's, and FOCALLEN matches one rig → `"focal"`.
3. **Two active rigs on the camera**, no scale or focal evidence → `(None, None)`.
4. **One active and one inactive rig** on the camera, no evidence → the active one, `"camera"`.
5. **Unknown camera** → `(None, None)`.
6. **`assign_rig` would succeed** → `"exact"`.
7. **`group_unassigned` splits correctly:**
   - SUB_FRAME vs INTEGRATION_MASTER on the same camera and dims → 2 groups.
   - Scales 1.20/1.22/1.24 → 1 group, while 1.20/1.40 → 2 groups.
   - Unsolved rows → their own group with `scale_min is None`.
   - `None` camera works.
   - Sorted by count.
8. **Keys are stable:** `member_ids(rows, rigs, g["key"])` returns exactly that group's ids for every group, an unknown key returns `None`, and shuffling the row order gives the same keys.

### 7.2 `backend/tests/test_equipment_api.py`

- `UnassignedAssign` validation: an empty key is rejected, and `rig_id` is required.
- `test_writes_require_admin` already covers the new POST. Confirm it passes.

### 7.3 New `backend/tests/test_images_rig_filter.py`, or append to an existing images test if one fits

- `_build_image_query(rig_id="none")` compiles to SQL containing `rig_id IS NULL`, and `rig_id="7"` compiles to `rig_id =`.
- `rig_id="abc"` raises `HTTPException` 400.

## 8. Docs & release

- **`docs/features/EQUIPMENT.md`:** add an "Unassigned images" subsection under Assignment (the rules from §2, §4.1 and §5), and add three rows to the API table (`GET /api/equipment/unassigned`, `POST /api/equipment/unassigned/assign`, `PUT /api/images/bulk/rig`). Update the existing sentence about `?rig_id=`, adding "or `none`".
- **`README.md`:** add one line to the feature list if equipment is listed there.
- **Release:** bump `VERSION` (format `YYYYMMDD.NN`; if today's date is already present, increment NN), then run `docker compose build backend && docker compose up -d backend`. That command also builds the frontend if it's baked into the same image; check `docker-compose.yml`. **Do not** add anything to container startup.

## 9. Build order

1. `rig_allocation.py` + `test_rig_allocation.py`, then run `pytest backend/tests/test_rig_allocation.py -v`.
2. The equipment endpoints + schema + API tests.
3. The images `rig_id` re-type + `/bulk/rig` + filter test. Then run the full `pytest backend/tests/`.
4. `client.js` → Equipment section → Search + FilterChips. Then `npm run lint && npm run build` in `frontend/`.
5. Docs, then VERSION, then the Docker rebuild. Then do the manual checks in §10 in the browser pane.
6. Commit on branch `feat/r0b-rig-allocation`, ending the message with the repo's co-author line. Don't merge.

## 10. Acceptance

- [ ] Equipment → Rigs shows "Unassigned images" with groups for light subs and masters only. No darks, flats or planetary frames appear.
- [ ] A cropped master group shows a suggested rig pre-selected. Assigning it removes the group, and those images show the rig with source MANUAL in ImageDetail.
- [ ] "Assign images now" (`scope=all`, the auto task) does not undo manual allocations.
- [ ] The View link opens Search with the chips "Rig: Unassigned" and the correct type.
- [ ] Search → Rig: Unassigned lists only images with no rig. "Assign Rig…" updates only light subs and masters, and reports the others as skipped.
- [ ] `Rig: <name>` filtering still works, and existing links like `?rig_id=3` still work.
- [ ] A non-admin can view the panel but not assign from it (the POST returns 403).
- [ ] All tests pass, lint and build are clean, VERSION is bumped, and the container is rebuilt.
