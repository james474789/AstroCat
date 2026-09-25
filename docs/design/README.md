# Feature Design Docs (Parallel Workstream)

Status: **Proposed** · Written: 2026-09-25 · Base commit: `06ffce9` (`main`)

This folder has one design doc per feature. Each doc is written so a separate agent or developer
can implement it independently. This README is the **coordination contract** between them: read it
before starting any of the four.

| ID | Feature | Doc | Depends on | New migration |
|----|---------|-----|-----------|---------------|
| F1 | Frame-type classification (Light/Dark/Flat/Bias) | [F1-frame-types.md](F1-frame-types.md) | none | yes |
| F2 | Target integration dashboard ("Targets") | [F2-target-integration.md](F2-target-integration.md) | F1 (schema only) | yes |
| F7 | Imaging sessions (group by observing night) | [F7-imaging-sessions.md](F7-imaging-sessions.md) | F1 (schema only) | yes |
| F16 | Mosaic detection & coverage map | [F16-mosaics.md](F16-mosaics.md) | F1 (schema only) | yes |

## 1. Recommended execution plan

```
Phase 0 (~30 min, one agent): F1a = F1 schema slice only
          └─ FrameType enum + Image.frame_type/frame_type_source columns + migration
             merged to main
Phase 1 (parallel, 4 agents, each branches from main after F1a):
          ├─ F1b  rest of F1 (classifier, backfill, API, UI)
          ├─ F2   targets
          ├─ F7   sessions
          └─ F16  mosaics
Phase 2: merge in order F1b → F2 → F7 → F16 (see §3 migration rule)
```

F2, F7 and F16 all need to exclude calibration frames ("lights only"). Landing the F1 **schema**
first gives every other branch a real column to filter on, which avoids stubs and conflicting
column definitions. If Phase 0 cannot happen first, each agent codes against the contract in §2
exactly as written, and F1 must merge first.

## 2. Shared contracts (do not change without updating this file)

### 2.1 Frame type (owned by F1)

In `backend/app/models/image.py`:

```python
class FrameType(str, enum.Enum):
    LIGHT = "LIGHT"
    DARK = "DARK"
    FLAT = "FLAT"
    BIAS = "BIAS"
    DARK_FLAT = "DARK_FLAT"

class Image(Base):
    ...
    frame_type = Column(Enum(FrameType, name="frametype"), nullable=False,
                        default=FrameType.LIGHT, server_default="LIGHT", index=True)
    frame_type_source = Column(String(20), nullable=True)  # HEADER | FILENAME | PATH | DEFAULT | MANUAL
```

The canonical "counts toward integration" predicate for F2, F7 and F16 is:

```python
from app.models.image import Image, FrameType, ImageSubtype
LIGHT_SUBS = (Image.frame_type == FrameType.LIGHT) & (Image.subtype == ImageSubtype.SUB_FRAME)
```

F1 exposes this as `app/utils/frame_filters.py::light_subs_clause()`. Other features should import
that helper. If it does not exist yet on their branch, they should inline the expression above with a
`# TODO(F1): use light_subs_clause()` comment.

### 2.2 Observing night (owned by F7, used by F2)

F2 needs a "number of nights" figure. It uses the simple SQL expression
`count(distinct date(capture_date - interval '12 hours'))` and does **not** depend on F7's
`imaging_sessions` table. After F7 merges, a follow-up can switch F2 to `count(distinct session_id)`.

### 2.3 Filter-name normalization (owned by F2)

F2 creates `backend/app/utils/filter_names.py::normalize_filter(name) -> str`. F7 and F16 show raw
`filter_name` values. After F2 merges they *may* switch to the normalized names, but this is not
required for their acceptance.

### 2.4 Sky footprints (owned by F16)

F16 starts populating the existing but unused `images.field_boundary` column through
`backend/app/services/footprint.py`. No other feature depends on it.

## 3. Alembic migration rule (important)

The current head is **`a8f9e0d1c2b3`** (`add_extraction_error_column`). `initialize_db` runs
`alembic upgrade head`, which **fails if there are multiple heads**, so the chain must stay linear.

| Migration | revision | down_revision while developing | down_revision at merge |
|-----------|----------|--------------------------------|------------------------|
| F1 frame types | `f1a7c3e90001` | `a8f9e0d1c2b3` | `a8f9e0d1c2b3` |
| F2 targets | `f2b8d4fa0002` | `f1a7c3e90001` | `f1a7c3e90001` |
| F7 sessions | `f7c9e50b0003` | `f1a7c3e90001` | `f2b8d4fa0002` |
| F16 mosaics | `f16da61c0004` | `f1a7c3e90001` | `f7c9e50b0003` |

When rebasing onto main before merge, the agent updates **only** the `down_revision` line (and the
`Revises:` docstring) to the value in the last column. Then check that
`cd backend && alembic heads` prints exactly one head.

All migrations must be **defensive** (see `CLAUDE.md`). On a fresh DB, `create_all` has already built
every table, column, index and enum from the models before `upgrade head` runs from baseline
`eff1c1ee4206`. So every migration must check with `sa.inspect(conn)` before creating anything, and
create enum types with `DO $$ BEGIN CREATE TYPE ... EXCEPTION WHEN duplicate_object THEN NULL; END $$;`.
Copy the pattern in `backend/alembic/versions/2026_09_01_1200-a8f9e0d1c2b3_add_extraction_error_column.py`.

New models must also be imported in `backend/app/models/__init__.py`, so that `create_all` and
`initialize_db` see them.

## 4. Merge-conflict hotspots

These files are touched by more than one feature. Keep edits in them **small, additive and
localized**, and append new items at the end of lists rather than reordering.

| File | Touched by | Guidance |
|------|-----------|----------|
| `backend/app/models/image.py` | F1, F2, F7, F16 | Add new columns in a clearly commented block per feature, placed just above `# Timestamps`. |
| `backend/app/models/__init__.py` | F7, F16 | Append imports. |
| `backend/app/api/images.py::_build_image_query` | F1, F2, F7, F16 | Add one kwarg per feature at the **end** of the signature plus one `if` block at the end of the body. The same filters are re-declared on 4 endpoints (`list_images`, `export_csv`, `bulk_update_subtype`, `bulk_sync_metadata`); add the new `Query` param at the end of each. |
| `backend/app/schemas/image.py` | F1, F2, F7, F16 | Append optional fields to `ImageBase` / `ImageDetail`. |
| `backend/app/tasks/indexer.py::_process_image_impl` | F1, F2, F7, F16 | Each feature adds **one call** to its own service function, in this order: F1 classify (right after extraction, see F1 §3.4); then, after catalog matching and before `session.commit()`: F16 footprint → F2 target → F7 session. Put the logic in the feature's own module, not inline. |
| `backend/app/tasks/astrometry.py` (on SOLVED) | F2, F16 | Same rule: one call each to the feature's service. |
| `backend/app/main.py` | F2, F7, F16 | Append one `include_router` line each. |
| `backend/app/worker.py` | F7, F16 | Append to `include` / `beat_schedule`. Route new task modules explicitly (see each doc). |
| `frontend/src/App.jsx` | F2, F7, F16 | Append routes. |
| `frontend/src/components/layout/Layout.jsx` `navItems` | F2, F7, F16 | Insert after `Catalogs`, in the order Targets, Sessions, Mosaics. |
| `frontend/src/api/client.js` | all | Append a clearly headed section per feature at the end of the API functions. |
| `frontend/src/pages/ImageDetail.jsx` | F1, F2, F7, F16 | Each adds one small row in the existing "File Information"/"Equipment" sections, or one link. |
| `docs/development/UTILITY_SCRIPTS.md` | F1, F2, F7, F16 | Append a section per new script. |

## 5. Common conventions for all four features

- **DB access:** API routes use async `get_db`. Celery tasks use sync `SessionLocal`. If a service is
  called from both `indexer.py` (sync) and `astrometry.py` (async), put the core logic in a **pure
  function** (no session) and write thin sync and async wrappers. This avoids the duplicated
  `CatalogMatcher`/`SyncCatalogMatcher` pattern.
- **Tasks must be idempotent.** `acks_late` and `reject_on_worker_lost` are on, so re-running any new
  task or backfill must be safe.
- **Backfills are scripts:** `python -m app.scripts.backfill_<feature>`. They process in batches of
  about 1000 rows with a commit per batch, are resumable (they only touch rows still missing the
  derived value unless `--all` is passed), and print progress. Where the doc says so, expose them
  as a Celery task and an Admin button too.
- **Backend changes need a container restart** (`docker compose restart backend`). There is no
  `--reload` under supervisord.
- **Tests:** pytest from `backend/`. Put pure logic (classifiers, grouping, geometry) in functions
  that can be unit-tested without a DB. Follow the style of `backend/tests/test_indexer_resilience.py`.
- **Frontend:** plain JSX plus a per-page CSS file using the existing CSS variables ("Deep Space"
  design system), TanStack Query for fetching, and all HTTP through `src/api/client.js`. Charts use
  `recharts` (already a dependency) and icons use `lucide-react`.
- **Stats caching:** `app/api/stats.py` caches in Redis with `cache_response`. New aggregate
  endpoints should cache similarly, with a TTL of 60–300 s.
- **Docs:** each feature adds a `docs/features/<FEATURE>.md` describing the shipped behaviour and
  updates `docs/core/DATABASE_SCHEMA.md`.
