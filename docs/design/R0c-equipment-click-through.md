# R0c — Click through from Equipment to Search

Status: **Proposed** · Written: 2026-09-29 · Base commit: `9666a77` (`feat/r0b-rig-allocation`, not yet on `main`) · Parent: [R0b-rig-allocation.md](R0b-rig-allocation.md), shipped behaviour in [docs/features/EQUIPMENT.md](../features/EQUIPMENT.md)

## 0. Who builds this

**Recommended:** one `general-purpose` agent on **Opus**, working directly on the existing
branch `feat/r0b-rig-allocation`. R0b is unmerged and this extends its code, so don't use a
worktree or a new branch off `main`. Build backend and frontend in one pass.
- **Why Opus:** a new filter has to be threaded through 7 endpoints plus the Search page's four
  hand-maintained filter lists. Missing one would make a bulk action run on a different set
  of images than the ones on screen, which is silent data damage.

## 1. Problem

- **Rig cards** on Equipment → Rigs have no way to show the images taken with that rig.
  Search already filters exactly by `rig_id`, but nothing links to it.
- **Unassigned bucket "View" link** (`unassignedSearchLink`, `Equipment.jsx:1076`) is
  approximate. It filters by camera-name substring plus a plate-scale window, so it misses
  unsolved frames, ignores binning and focal length, and pulls in frames from other buckets.
  The user can't inspect what a bucket actually contains before assigning it.

## 2. Decisions (confirmed with the user)

| # | Decision |
|---|----------|
| D1 | Only **rig cards** link to Search. The Cameras, Optics and Filters tabs are unchanged. |
| D2 | A rig opens Search with **all lights on that rig**: `?rig_id=<id>&frame_type=LIGHT`. That is subs **and** masters. Calibration frames that auto-assignment put on the rig are not included (the user can remove the Frame Type chip to see them). |
| D3 | The link is **explicit**. The card's "N subs · last used …" count becomes a link, and a "View images" button is added to the card's action row. The card itself is not clickable, because it holds toggles and buttons. |
| D4 | A bucket opens Search through a new **exact** filter, `rig_bucket=<key>`. The server resolves it to the bucket's current members with the same `_groups` code as the panel. This replaces the approximate link. Search's bulk actions (Assign Rig…, Set Frame Type…, export, etc.) then work on exactly that bucket. |
| D5 | No schema change, no migration, no data migration, no caching. |

Non-goals: linking the camera/optic/filter tabs, making `rig.image_count` count masters, and
an inline thumbnail drawer on Equipment.

## 3. Backend

### 3.1 Bucket resolver in `backend/app/api/equipment.py`

Add this next to `_unassigned_inputs` (line ~852) and reuse it. Don't copy the SQL.

```python
async def bucket_image_ids(db: AsyncSession, key: str) -> List[int]:
    """Current member ids of an Unassigned bucket; [] when the key no longer exists."""
    from app.services.rig_allocation import members_by_key

    rows, rigs = await _unassigned_inputs(db)
    members = await asyncio.to_thread(members_by_key, rows, rigs, [key])
    return members.get(key, [])
```

- A stale or unknown key returns `[]`, not an error. The user may have just assigned the
  bucket, and an empty result page is the honest answer.
- The members are recomputed on every request, like `GET /unassigned` is. Log the time at
  `debug`. Don't add a cache: a cache would keep showing images after they were assigned.

### 3.2 `_build_image_query` in `backend/app/api/images.py`

Add one kwarg at the **end** of the signature, and one block at the end of the filter body,
as the design README §4 requires:

```python
    image_ids: Optional[Sequence[int]] = None,
...
    if image_ids is not None:
        if not image_ids:
            stmt = stmt.where(false())
        else:
            stmt = stmt.where(Image.id == any_(
                bindparam("image_ids", list(image_ids), type_=ARRAY(Integer))))
```

- **Use a single array bind (`= ANY(:ids)`), not `Image.id.in_(ids)`.** A bucket can have tens
  of thousands of members, and an expanding `IN` sends one parameter per id. asyncpg rejects
  anything over 32 767 parameters.
- Imports: `any_`, `bindparam`, `false`, `Integer` from `sqlalchemy`, and `ARRAY` from
  `sqlalchemy.dialects.postgresql`. Check which are already imported first.
- `list_images` counts with `select(func.count()).select_from(stmt.subquery())`. Confirm that the
  bound parameter survives the subquery. The test in §5 covers this.

### 3.3 Helper and endpoint parameter

In `images.py`, near `_build_image_query`:

```python
async def _rig_bucket_ids(db: AsyncSession, rig_bucket: Optional[str]) -> Optional[List[int]]:
    if not rig_bucket:
        return None
    from app.api.equipment import bucket_image_ids
    return await bucket_image_ids(db, rig_bucket)
```

On **every** endpoint that declares `rig_id: Optional[str] = Query(...)`, add this parameter
directly after `rig_id`:

```python
    rig_bucket: Optional[str] = Query(None, max_length=400,
        description="Unassigned bucket key from GET /api/equipment/unassigned (R0c)"),
```

Then call `image_ids=await _rig_bucket_ids(db, rig_bucket)` in the endpoint's
`_build_image_query(...)` call. Those endpoints are currently:
- `list_images` (`GET /`)
- `export_csv`
- `bulk/subtype`
- `bulk/frame-type`
- `bulk/target`
- `bulk/rig`
- `bulk/metadata`

`grep -n "rig_id: Optional\[str\] = Query" backend/app/api/images.py` and
`grep -n "rig_bucket: Optional\[str\] = Query" backend/app/api/images.py` must return the same
number of lines. In the bulk endpoints, resolve the ids **before** the `try:` that wraps the
update, so an error in the resolver isn't reported as a partial bulk failure.

### 3.4 Leave alone

- `GET /api/equipment/unassigned` and `POST .../unassigned/assign` are unchanged.
- The bucket key format (`{cam_key|none}|b{bin}|{focal_min:.1f}` or `|-`) is unchanged. The
  frontend parses it only to label a chip (§4.4).

## 4. Frontend

### 4.1 `frontend/src/pages/Equipment.jsx`: rig card (`RigCard`, line ~130)

- Add a helper: `const rigSearchLink = (rig) => `/search?rig_id=${rig.id}&frame_type=LIGHT``.
- **Count line** (`{rig.image_count} subs · last used …`, line ~198): wrap the
  `{rig.image_count} subs` part in `<Link to={rigSearchLink(rig)}>`. Its title is:
  "Open in Search: all light frames on this rig (subs and masters)".
  - The number counts subs only, while the link also shows masters. The tooltip explains
    this. Don't change the count.
- **Action row:** add a first button, `<Link className="btn btn-ghost btn-sm" to={rigSearchLink(rig)}><Images size={14} /> View images</Link>`.
  - Use the lucide `Images` icon, or `Search` if `Images` isn't available in the installed
    version. It is **not** disabled for non-admins, because viewing is a read.
- `Link` is already imported (it's used by the Unassigned section).

### 4.2 `Equipment.jsx`: Unassigned bucket rows

- Replace `unassignedSearchLink(g)` with:
  `const bucketSearchLink = (g) => `/search?rig_bucket=${encodeURIComponent(g.key)}``.
  - Delete the old helper. It will be unused, and lint would flag it.
- **Row's last cell:** the link text becomes "View images". Its title is
  `Show the ${g.count} images in this bucket`. The "Approximate" tooltip goes.
- **Images count cell:** also make the `{g.count}` number a `<Link>` to the same URL.

### 4.3 `frontend/src/api/client.js`

- In `fetchImages` (line ~104), add `rig_bucket: params.rig_bucket,` after `rig_id`.
- The bulk helpers and CSV export already forward the whole `URLSearchParams`, so they need
  no change once the backend accepts `rig_bucket`.

### 4.4 `frontend/src/pages/Search.jsx`

Thread `rig_bucket` through all four hand-maintained filter lists, next to `rig_id`:
- the initial `filters` state (~line 120)
- the URL-sync effect (~162)
- `loadImages` (~212): `if (searchParams.get('rig_bucket')) params.rig_bucket = searchParams.get('rig_bucket');`
- `clearFilters` (~314)

Other requirements:
- **No sidebar control.** A bucket is only ever entered through the link, and shown and
  removed as a chip.
- **Empty state:** when `rig_bucket` is set and the result total is 0, show this instead of
  the generic empty text: "This bucket no longer exists. Its images may have been assigned to a rig, or the rig list changed. Go back to Equipment → Unassigned images to see the current buckets."
  Include a link to `/equipment`.
- **Implicit Lights default:** don't change it. Bucket members are always LIGHT.

### 4.5 `frontend/src/components/layout/FilterChips.jsx`

Add a `rig_bucket` entry to `filterLabels`, with label **"Bucket"** and a formatter that
parses the key:

```js
function formatRigBucket(key) {
    const parts = String(key).split('|');
    if (parts.length < 3) return key;
    const focal = parts.pop();
    const bin = parts.pop().replace(/^b/, '');
    const cam = parts.join('|');
    const camLabel = cam === 'none' ? 'Unknown camera' : cam;
    const focalLabel = focal === '-' ? 'focal unknown' : `~${Math.round(Number(focal))} mm`;
    return `${camLabel} · bin ${bin} · ${focalLabel}`;
}
```

Put it at module level, not inside the component.

## 5. Tests (`backend/tests/`)

Append to `test_images_rig_filter.py` (created by R0b):
1. `_build_image_query(image_ids=[3, 5])`, compiled against the postgresql dialect, contains
   `= ANY` and exactly one bound parameter for the ids. It must not have one parameter per id.
2. `_build_image_query(image_ids=[])` compiles to a false predicate: `false` or `1 != 1`,
   whichever SQLAlchemy emits for the dialect.
3. `_build_image_query(image_ids=None)` has no id predicate. This is a regression guard.
4. Wrapping the statement as `select(func.count()).select_from(stmt.subquery())` still
   compiles, and it keeps the `image_ids` bind.
5. **Parity guard.** Iterate `app.api.images.router.routes`. For every route whose endpoint
   signature has a `rig_id` parameter, assert that it also has `rig_bucket`. This catches a
   future endpoint that gets one filter but not the other.
6. `_rig_bucket_ids(db, None)` and `_rig_bucket_ids(db, "")` return `None` without touching
   `db`: pass a sentinel object and assert that no attribute was accessed. Monkeypatch
   `app.api.equipment.bucket_image_ids` to check that a non-empty key is forwarded.

`members_by_key` itself is already covered by `test_rig_allocation.py`.

## 6. Docs & release

- **`docs/features/EQUIPMENT.md`:**
  - Under Rigs: rig cards link to Search, showing all lights on the rig.
  - Under "Unassigned images": "View images" opens Search with the exact bucket
    (`rig_bucket`), and bulk actions there apply to exactly those images.
  - In the API table, add `rig_bucket` to the note on the `/api/images` filters.
- **Release:** bump `VERSION` (`YYYYMMDD.NN`; if today's date is already there, increment NN).
  Then run `docker compose build backend && docker compose up -d backend`, and check that the
  container reports the new version.

## 7. Build order

1. `bucket_image_ids`, the `_build_image_query` kwarg, `_rig_bucket_ids`, and the parameter on
   all 7 endpoints. Then the tests from §5, then `pytest backend/tests/`.
2. `client.js`, then `Equipment.jsx` (rig card, then bucket rows), then `Search.jsx`, then
   `FilterChips.jsx`. Then `npm run lint && npm run build` in `frontend/`.
3. Docs, then VERSION, then the Docker rebuild.
4. Commit on `feat/r0b-rig-allocation` with a `feat(equipment): …` message ending in the repo's
   co-author line. Don't merge and don't push unless asked.
5. **No browser testing.** The user does the UI checks. Stop at tests, build and rebuild, then
   report the §8 checklist for the user to verify.

## 8. Acceptance (the user verifies the UI items)

- [ ] Each rig card has a "View images" button and a linked "N subs" count. Both open Search
      with the chips "Rig: <name>" and "Frame Type: LIGHT", and the results include that rig's
      masters.
- [ ] Non-admins can use both links.
- [ ] A bucket's "View images" opens Search with a "Bucket: … · bin N · ~F mm" chip. The result
      count equals the bucket's `count` in the panel, including unsolved frames.
- [ ] In that Search, "Assign Rig…" assigns exactly those images. When you return to
      Equipment, the bucket is gone.
- [ ] Opening a bucket link after its images were assigned shows the "bucket no longer exists"
      empty state, not an error.
- [ ] Removing the Bucket chip returns Search to its normal default results.
- [ ] CSV export and Set Frame Type… respect `rig_bucket`.
- [ ] `pytest backend/tests/` passes, lint and build are clean, VERSION is bumped, and the
      container is rebuilt.
