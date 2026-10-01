# T1 - Target ↔ master linking

Status: **Implemented** (uncommitted; VERSION 20261001.02) · Written: 2026-10-01 · Base commit: `d14848c` (`main`)

Fixes the Targets page reporting "no masters" for targets whose field clearly appears in a
master image (e.g. `/targets/SH2159` vs `/images/387`).

## 1. Problem

`/targets/SH2159` shows 0 masters. Image 387 (`NGC7635 - Lobster Claw HAROGOB_20hr.tif`, an
`INTEGRATION_MASTER`) lists `Sh2-159` as an identified, in-field object (separation 0.606°).

## 2. Root cause (verified against the live DB)

Masters are attached to a target **only by the image's single primary `target_key`**:

- `api/targets.py::_compute_targets_list` (master count): `target_key IN keys AND subtype = INTEGRATION_MASTER`
- `api/targets.py::get_target_detail` (masters gallery): `target_key == :key AND subtype = INTEGRATION_MASTER`

Each image gets exactly one `target_key` (`services/targets.py::resolve_target`), chosen as the
**nearest-to-centre** `is_in_field` catalog match within `0.5 × field_radius`. That is a *winner
takes all* pick, and in crowded fields it is a coin flip:

| Object in the Bubble Nebula field | Sep. from image 387 centre | Canonical key |
|---|---|---|
| Sh2-162 | 0.595° | `NGC7635` (override) |
| **Sh2-159** | **0.606°** | **`SH2159`** (no cross-ID) |
| NGC7635 / C11 | 0.612° | `NGC7635` |
| Sh2-157 | 0.692° | `SH2157` |

Facts from the DB:

- 38 *subs* (all file-named `NGC7635`, no `OBJECT` header) happened to centre nearest Sh2-159, so they
  became target `SH2159`. 645 other NGC7635 subs, and the master (id 387), resolved to `NGC7635`.
- So `SH2159` has subs but no master, `NGC7635` has the master, and **both describe the same sky**.
- 14 masters have Sh2-159 in field; only the one NGC7635 master is relevant here, the rest are wide
  Cassiopeia/Cepheus mosaics (that is why a naive "any in-field match" rule is too noisy, see §3.2).

Two related defects found on the way:

1. **Master-only targets are invisible.** `_compute_targets_list` is seeded from LIGHT *subs*, so a
   target with masters but no subs never appears in `/targets` and `GET /targets/{key}` returns 404.
   Currently **26 targets** are in this state.
2. **Masters without `field_radius_degrees` can never MATCH** (59 of 424 masters), so they fall to
   `NONE` (66 masters have no `target_key`).

## 3. Proposed fix

Two independent changes. A is the actual fix for the report; B removes the underlying fragmentation.

### 3.1 Change A (recommended, required): link masters by *central catalog matches*, not only primary key

Introduce a many-to-many notion: a master is **linked** to every target that is a *central object*
in it. Primary `target_key` stays as-is (cover image, filters, sort, goals unchanged); linking is
used only for master presence/gallery.

**Linked-target set for a master** = canonicalised keys of all `image_catalog_matches` rows where

- `is_in_field` is true,
- catalog type ≠ `NAMED_STAR`,
- `angular_separation_degrees ≤ MATCH_CENTRAL_FRACTION × field_radius` (same 0.5 threshold the
  resolver already uses, so the definition of "central" stays in one place),
- canonicalised through the alias index (`C11 → NGC7635`, `NGC3031 → M81`),

plus the master's own primary `target_key` (so existing behaviour is a strict subset).

For image 387 this yields `{NGC7635, SH2157, SH2159, SH2162→NGC7635}`: `SH2159` now sees its master,
while M52 / NGC7510 / NGC7538 / NGC7654 / Sh2-158 (all > 0.89° out) do not.

For masters with no stored radius, derive one with `app/utils/field_geometry.py` (already used by the
resolver for sidecar solves); if that is impossible, fall back to primary key only.

**Implementation sketch**

- New pure function in `services/targets.py`:
  `linked_target_keys(matches, field_radius, alias_index, primary_key) -> set[str]`, reusing the
  candidate filter currently inlined in `resolve_target` (extract it into a shared
  `_central_candidates(matches, field_radius)` so both callers agree).
- **Compute at query time, not as a column** (decision in §5 Q1): masters are few (424), so
  `_compute_targets_list` does one query for `INTEGRATION_MASTER` ids + their matches
  (joined on the existing `ix_image_catalog_matches_image_id`), builds
  `{target_key -> [master_id, ...]}` in Python via `linked_target_keys`, and:
  - `master_count` = len of the linked set for that key (replaces the SQL `count`),
  - `get_target_detail` masters gallery = `Image.id IN linked_ids` (replaces `target_key == key`).
  The map is built once per list-cache refresh (TTL 120 s) and reused by the detail endpoint via
  the cached list (add `_master_ids` to the summary, or a second small cache key
  `cache:targets:masters`; prefer the latter to keep the list payload unchanged).
- Alias index is already loaded process-wide (`_alias_index_cache`); use the async getter the
  astrometry task uses.
- Folder-scoped lists (`path` param) apply the existing `path_clause` to the master query.
- Cache invalidation: the existing `_invalidate_targets_cache` already runs on re-match / manual
  assign; no change.

**Frontend:** no required change (`master_count`, `masters[]` keep their shape). Optional: on a
linked-but-not-primary master card show a small "also contains" hint, using a new `primary: bool`
field per master. Left out of v1 unless wanted (§5 Q3).

### 3.2 Why not "any in-field match"?

Image 387's field radius is 1.78°; it contains 10 catalogued objects. Counting all of them would show
a wide Cassiopeia mosaic as a "master" of ten unrelated targets. The central-fraction rule reuses the
project's existing definition of "this is what the image is of".

### 3.3 Change B (optional, recommended): stop the SH2159/NGC7635 split at the source

Even with A, `SH2159` remains a 38-sub fragment of the NGC7635 data set. Options:

1. **Pin Sh2-159 → NGC7635** in `SH2_CROSS_ID_OVERRIDES` (one line) and rerun
   `scripts/recanonicalize_targets.py` (shipped as a **new data migration**
   `000N_recanonicalize_after_sh2159`, per CLAUDE.md "Shipping a One-off Data Repair"). Cleanest for
   this field, but it is a per-pair astronomy call: Sh2-159 is part of the Bubble complex, not
   strictly the same object as NGC7635 (the Bubble is Sh2-162). Same trade-off already accepted for
   Sh2-162.
2. **Tie-break in `resolve_target`**: when several candidates sit within a small margin of each other
   (e.g. 0.05° or 10 % of the best separation), prefer the catalog priority
   (Messier > NGC/IC > Caldwell > Sh2) over raw distance. This fixes the *class* of flip-flops
   (any NGC/Sh2 overlap) but changes keys for existing rows across the library, so it needs a
   dry-run report (`replay`-style script listing key changes) before the data migration.
3. Do nothing; rely on A and merge targets manually via MANUAL assignment.

Recommendation: **A + B1 now**, B2 as a follow-up design if more splits turn up (query: targets whose
subs' file names/headers agree with another target).

### 3.4 Change C (small, same PR): show master-only targets

Seed `_compute_targets_list` with the union of keys from LIGHT subs **and** linked-master keys, so
the 26 master-only targets (and `SH2159`-style links) get a row with `total_seconds = 0`,
`master_count > 0`, cover = best master. Needs the front end to tolerate zero-integration rows
(Targets list bar chart, `min_hours` filter already hides them by default at > 0). Detail 404 message
("has no integration yet") is updated accordingly. If this is too broad for T1, split it out (§5 Q2).

## 4. Testing

Pure unit tests in `backend/tests/test_targets.py` (no DB):

- `linked_target_keys` for image 387's match list: includes `SH2159`, `NGC7635`, `SH2157`; excludes
  `M52`, `NGC7510`, `NGC7538`, `NGC7654`, `SH2158`.
- Canonicalisation: `C11` and `Sh2-162` collapse to `NGC7635`.
- `NAMED_STAR` and `is_in_field = False` are skipped; no radius → primary key only; MANUAL primary key
  is always included.
- `resolve_target` behaviour is unchanged after the `_central_candidates` extraction (existing tests).
- If B2 is chosen: tie-break cases around the margin boundary.

Manual check (user, per project preference): `/targets/SH2159` shows master 387; `/targets` M52 /
NGC457 / IC63 masters unchanged; a wide mosaic master does not appear on unrelated targets.

## 5. Decisions (answered 2026-10-01)

1. Links computed at **query time** (no table, no migration).
2. Master-only targets (Change C) are **included**.
3. **No** UI hint on non-primary masters; no frontend change beyond tolerating zero-integration rows.
4. **No** Sh2-159 → NGC7635 merge and no tie-break: Change B is dropped. `SH2159` stays its own target.
5. Threshold reuses `MATCH_CENTRAL_FRACTION` (0.5 × field radius).

Scope is therefore Change A + Change C only. The original questions are kept below for reference.

### Original questions

1. **Computed vs stored links.** Query-time computation (recommended; no migration, always in sync with
   alias-index changes) or a persisted `master_target_links` table (faster, but needs a migration and
   must be maintained on every re-match)?
2. **Master-only targets (C).** Include in this change, or separate?
3. **UI.** Is a "also contains" hint on non-primary masters wanted, or keep the gallery as-is?
4. **Sh2-159 → NGC7635 (B1).** Are you happy to treat Sh2-159 as the Bubble/NGC7635 target? If not, A alone
   still fixes the master display and the 38 subs stay as a separate `SH2159` target.
5. **Threshold.** Reuse `MATCH_CENTRAL_FRACTION` (0.5 × field radius) for "central", or a stricter value
   for linking?

## 6. Rollout

Backend and frontend (if Q3 = yes) both baked into images: bump `VERSION` (`YYYYMMDD.NN`), then
`docker compose build backend frontend && docker compose up -d backend frontend`. B1 ships as data
migration `000N_…` in `services/data_migrations.py::REGISTRY`; it clears `cache:targets:*` itself.

## 7. Implementation notes (as built)

- `services/targets.py`: `_central_candidates` (shared with `resolve_target`) and `linked_target_keys`.
- `api/targets.py::_compute_master_links` feeds `master_count`, the master gallery in `get_target_detail`,
  and covers. Change B was dropped (see §5).
- **Guard against noise:** linked keys only count for a target that already has subs, or that is some
  master's own primary `target_key`. Without this, wide masters spawned ~480 extra master-only targets.
  Result on live data: 26 master-only targets (matches §2), `SH2159` 5 masters (incl. 387),
  `NGC7635` 10.
- **Covers:** unchanged where a target has its own primary master; a target with only linked masters
  uses its best linked master instead of a sub.
- Tests: `backend/tests/test_targets.py` (`linked_target_keys`). No frontend change was needed.
