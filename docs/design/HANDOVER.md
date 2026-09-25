# Handover: F1 + F2 Complete, F7 + F16 Not Started

Written: 2026-09-25 · Author: Claude (orchestrating session) · For: whoever picks up F7/F16 next

**Read this before `README.md`'s coordination contract** — that doc's migration table
(§3) is now partly stale (see [§2](#2-correction-to-readmemds-migration-table) below).
Everything else in `README.md` (merge-hotspot guidance, conventions, testing style)
still applies and is worth reading in full.

## 1. What's done

**F1 (frame-type classification)** and **F2 (target integration dashboard)** are
merged to `main`, pushed to `origin/main`, deployed to the live stack, and verified
against real data. **F7 (sessions)** and **F16 (mosaics)** have not been started —
no branches, no code, nothing beyond the design docs.

Current `main` HEAD: **`a4a5d1a`** (pushed). Key commits, oldest to newest:
- `8549b9e` — F1 schema slice only (`FrameType` enum, `frame_type`/`frame_type_source`
  columns, migration `f1a7c3e90001`). Landed alone first, per the original Phase 0 plan.
- `b46f09f` — merge of the full F1b implementation (classifier, indexer hook, backfill,
  stats, API, frontend) together with the full F2 implementation (resolver, targets
  API, Targets/TargetDetail pages). Both were built in parallel worktrees off `8549b9e`
  and merged together in one commit; see [§3](#3-how-the-merge-actually-went) for what
  that involved.
- `a4a5d1a` — `VERSION` bump to `20260925.01` (see project memory
  `feedback_version_bump.md` — bump `VERSION` on every completed feature going forward).

Alembic head: **`f2b8d4fa0002`**. Chain is linear:
`a8f9e0d1c2b3 → f1a7c3e90001 (F1) → f2b8d4fa0002 (F2)`.

Live stack: backend + frontend containers rebuilt and restarted on this code. Both
backfills (`backfill_frame_types`, `backfill_targets`) have been run against the real
93,733-image library — see [§5](#5-live-data-state) for the actual numbers, so a future
agent doesn't need to re-derive them or wonder if the backfill is safe to re-run (it is;
both are idempotent).

## 2. Correction to README.md's migration table

`README.md` §3's table says F7's `down_revision` should be `f1a7c3e90001` *while
developing* and rebased to `f2b8d4fa0002` *at merge*. That two-step dance existed
because the original plan had all four features developing in parallel off the same
base. **That's no longer the situation** — F2 has already merged, so `f2b8d4fa0002` is
now simply the current head. F7 should branch from current `main` and use
`down_revision = 'f2b8d4fa0002'` from the start; there's no rebase step needed. F16
still comes after: its `down_revision` should be whatever F7's migration revision ID
ends up being, exactly as the table already says.

## 3. How the merge actually went

F1b and F2 were built by two parallel subagents (Sonnet), each in its own git worktree,
each with explicit instructions not to touch the live docker stack or run migrations —
only the orchestrating session did that, after review. That pattern worked well and is
worth repeating for F7/F16.

**The append-only merge-hotspot discipline in `README.md` §4 reduces conflicts but does
not eliminate them.** Two branches appending to the end of the same function (e.g.
`_build_image_query`) still produce a git conflict — the lines are adjacent, so git
can't auto-resolve even though the intent is non-overlapping. Merging F1b + F2 produced
conflicts in 8 files: `api/images.py`, `api/stats.py`, `schemas/image.py`, `client.js`,
`FilterChips.jsx`, `ImageDetail.jsx`, `Search.jsx`, `rebuild_and_seed.ps1`. All were
resolved by hand (keeping both features' additions, in landing order). Two things to
watch for specifically when resolving these:

1. **Bare closing braces get deduped by git's diff.** When two independently-added
   functions both end in a lone `}` line, git sometimes treats them as the same
   "context" line and drops one. This silently produces a syntax error if you just
   accept one side. Caught this twice (`client.js`, `ImageDetail.jsx`) — always
   `py_compile` / build after resolving, don't just trust that the conflict markers are
   gone.
2. **Genuine functional conflicts do happen**, not just append collisions. F1b patched
   `GET /api/stats/top-objects` with a `lights_clause()` filter; F2 independently
   rewrote the same endpoint to read from the targets list instead (per F2's own spec).
   These aren't compatible edits to merge line-by-line — one has to supersede the
   other. F2's version was kept (matches its spec, and its own `_light_subs_clause()`
   preserves the lights-only behavior F1b was adding). **For F7/F16: check whether
   either touches `stats.py`, `catalogs.py`, or other endpoints already modified by
   F1/F2, and read both versions before resolving rather than assuming append-only.**

Practical process that worked, in order: (1) merge branch A into a throwaway trial
branch, resolve conflicts, run full test suite + frontend build/lint, (2) once that's
green, redo the *same* merge for real on `main` — conflicts recur identically, and the
already-resolved file contents can be pulled straight from the trial branch via
`git show <trial-branch>:<path> > <path>` instead of re-resolving by hand.

## 4. Live-stack deployment notes

This is a real, single-user production stack — NAS-mounted image sources
(`astro_finals`/`astro_source` in `docker-compose.yml`), automated daily DB backups,
no bind-mount live-reload (backend image is built from source, so code changes need
`docker compose build backend && docker compose up -d backend`). A few things that
weren't obvious going in:

- **`docker-compose.yml` at the repo root is gitignored** (the user's local file,
  distinct from `docker-compose-dev.yml`/`docker-compose-example.yml`, which are
  tracked). Any startup-chain changes (new backfill scripts, etc.) need to be added to
  *all three* — the tracked ones for the repo, the local one manually since agents in
  worktrees can't see or edit it.
- **`FRONTEND_PORT`/`BACKEND_PORT` are non-default** in this user's `.env` (frontend is
  on 6090, not 8090). Check `docker port <container>` rather than assuming compose
  defaults when verifying anything by hand.
- **Windows/git-bash path mangling**: `docker exec`/`docker compose run` with absolute
  container paths (e.g. `/app/alembic/...`) get silently rewritten to Windows paths by
  git-bash's MSYS path conversion. Prefix with `MSYS_NO_PATHCONV=1` or the command
  fails confusingly.
- **Run migrations and backfills as explicit one-off steps before restarting the full
  stack**, not blindly through the compose `command:` chain on first deploy — that way
  you can dry-run (where the script supports it — `backfill_frame_types.py` has
  `--dry-run`; `backfill_targets.py` does not, per its spec) and sanity-check the
  distribution before it's live. Once verified, the same backfills are safely left in
  the startup chain permanently since they're designed to be no-ops on subsequent
  restarts (see the one exception in §6).
- **UI verification requires the user to log in themselves.** The app is behind auth;
  entering credentials/passwords into the login form is off-limits regardless of
  whether the user offers them. Ask them to log in in the browser pane, then continue.
- Both **backend and frontend** containers need rebuilding — easy to rebuild backend
  and forget the frontend has new pages too (Targets/TargetDetail in this case).

## 5. Live data state

As of 2026-09-25, after both backfills ran against the real library (93,733 images):

**Frame types:** 91,521 LIGHT, 383 DARK, 1,729 FLAT, 100 DARK_FLAT, 0 BIAS (this user
apparently uses dark-flats rather than bias frames — not a bug, just their workflow).

**Targets:** of 91,098 LIGHT sub-frames, 34,040 (37.4%) resolved to a target — 17,446
via HEADER, 13,104 via MATCH, 3,490 via HEADER_RAW (unresolved header text, still gets
an `OBJ:` key). The remaining 57,058 are genuinely unassigned (no header object name,
not plate-solved) — verified this isn't a resolver bug by checking for images with a
populated `object_name` but `target_key IS NULL`: zero such rows exist.

## 6. Known follow-ups (not blockers, not fixed)

- **`backfill_targets.py` re-scans all ~57k unassigned images on every container
  restart.** Unassigned rows share `target_source = NULL` with never-processed rows, so
  the "only touch rows still needing it" resume logic can't tell them apart. Harmless
  (0 changes each time, a few seconds of work) but not a true no-op like F1's backfill.
  A real fix would need a sentinel value (e.g. `target_source = 'NONE'`) to distinguish
  "processed, no match" from "not yet processed" — out of scope for this handover, flag
  it if picking up related work.
- `backend/app/schemas/image.py`'s `UpdateImageRequest` has a pre-existing wart (a
  duplicate `model_config` line, a `catalog_matches` field that looks misplaced in a
  request schema) — this predates F1/F2, confirmed by checking `main` before either
  feature touched the file. Not caused by this work, left alone.
- Git commit `027c431` on `main` (before this session's work started) is titled *"feat:
  add imaging sessions feature to group images by observing night"* but its actual diff
  only added the `docs/design/*.md` files — no `ImagingSession` model was ever
  implemented by it. The commit message doesn't match its contents. Not a functional
  issue, just confusing if you go looking for F7 code based on git log and don't find
  any — there isn't any yet.

## 7. Cost calibration for F7/F16

Actual token spend for F1b + F2 (Sonnet, two parallel subagents): **382,745 +
377,274 = 760,019 tokens**, plus roughly 350–450k in orchestration overhead (merge
conflict resolution, live deployment, verification) — **~1.1–1.2M total**, about **2x**
the original per-feature estimate. The gap wasn't waste — the agents did more thorough
work (more test cases, more files) than scoped for.

Applying that calibration, plus F7/F16's intrinsically higher complexity (F7: moon-
illumination/observing-night math; F16: spherical geometry, PostGIS, detection
algorithm) and their later, more conflict-prone merge position:

| | Estimate |
|---|---|
| F7 implementation (recommend Opus 5.5) | 650–900k |
| F16 implementation (recommend Opus 5.5) | 800k–1.1M |
| Merge + deploy + verify, both | 700k–1.05M |
| **Total** | **~2.15M–3.05M (14–20% of a 15M budget)** |

Recommend the same execution pattern used for F1/F2: parallel worktree subagents,
explicit instruction not to touch the live stack, trial-merge in a throwaway branch
before touching `main`, dry-run backfills where the script supports it, rebuild both
containers, browser walkthrough with the user logged in, bump `VERSION` on completion.

## 8. Repo state

No open feature branches, no worktrees — all cleaned up after the F1+F2 merge.
`docs/design/F7-imaging-sessions.md` and `docs/design/F16-mosaics.md` are unmodified
from the original design pass and are the specs to build against. `docs/features/
FRAME_TYPES.md` and `docs/features/TARGETS.md` (written by the F1b/F2 agents) describe
what actually shipped, if useful as a reference for how a feature doc should look.
