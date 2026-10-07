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
