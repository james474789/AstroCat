# D3: Desktop apps handover (state, how it works, remaining work)

**Created:** 2026-10-28
**Status:** v1 released as [v20261028.08](https://github.com/james474789/AstroCat/releases/tag/v20261028.08), with the remaining work specified below.
**Supersedes:** D2 §9 (Phase E). D2's Phase E designs are refined and corrected here; where the two differ, this doc wins.
**Background (read only when a section points you there):**
- [D1](20261027-D1-desktop-apps.md): the architecture and the Phase 0 findings
- [D2](20261028-D2-desktop-parity-and-release.md): the parity fixes and CI guardrails
- [desktop-parity.md](../desktop-parity.md): the contributor checklist

This doc is self-contained. An agent that has read it can do any remaining item without the conversation that produced it. Where it's wrong or out of date, stop and ask the user; don't improvise.

---

## 1. Where things stand

| | State |
|---|---|
| Deliverables | Docker images (`james474789/astrocat-{backend,frontend}`), a Windows x64 NSIS installer and an Apple Silicon `.dmg`. All three come from one codebase and one `VERSION`. |
| First release | `v20261028.08`. Assets: `AstroCat_26.1028.8_x64-setup.exe` (148 MB), `AstroCat_26.1028.8_aarch64.dmg` (491 MB), `docker-compose-example.yml`, `install-desktop.md`. Docker Hub has `20261028.08` and `:latest`. |
| Branch | `main` at `94ea46a`. The `desktop-apps` branch is merged and no longer used. |
| CI | `ci.yml` runs on PRs and on pushes to `main`. It runs the backend tests on Linux, Windows and macOS, the frontend build, and a path-filtered desktop runtime build plus smoke test on Windows and macOS. Everything is green. |
| Verified by the user | The Windows app was installed and used, from a CI artifact. The macOS app launches. |
| **Not verified** | The macOS app beyond launching. **Neither release download** has been installed and checked (§5.1). |
| Production | The user's Portainer stack has **not** been redeployed onto `:latest`. Ask before touching it; the runbook is in the user's memory, "Deploy to prod runbook". |
| Loose ends | A stale `v20261028.07` git tag, with no release attached (§5.8). The old example DB password is still in git history (§5.8). |

## 2. Rules for any agent working here

These come from CLAUDE.md, which is gitignored and local, and from the user's standing feedback.

1. **Commit and push only on explicit instruction.** Otherwise leave edits uncommitted. Approval covers one piece of work, not later pieces.
2. **Bump `VERSION`** (`YYYYMMDD.NN`) on every change. If the file is locked, write it with PowerShell `Set-Content -NoNewline`.
3. **Rebuild Docker after code changes**, before reporting a change as done. Use `docker compose build backend && docker compose up -d backend`, and the same for the frontend. Host ports are 6089 (backend) and 6090 (frontend).
4. **No browser or UI testing.** The user does that. Stop at tests, build and rebuild, then hand the user a short checklist.
5. **Get to 95% confidence before changing code.** The questions in §7 are the only ones expected; ask each when its item comes up.
6. **Design docs** get a `yyyymmdd` prefix. Plans include token and effort estimates and a recommended model per stage.
7. **`backend/app/desktop/` stays stdlib-only.** Docker never sets `desktop_mode` or `frontend_dist_dir`, so the Docker behaviour must not change.
8. **Follow [desktop-parity.md](../desktop-parity.md) for every feature.** `backend/tests/test_desktop_parity.py` enforces most of it.
9. **Releases and production are outward-facing.** Tagging `v*` publishes the GitHub Release and pushes Docker `:latest`. Confirm with the user before each tag. Never delete tags or rewrite history without asking; the auto-mode safety check blocks it anyway.

## 3. How it works (the minimum to work on it)

```
Tauri shell (desktop/src-tauri/src/main.rs)
  splash → main window → http://127.0.0.1:<port>/api/auth/desktop-login?nonce=… → /
  tray: Open · Choose library folders… · Open logs folder · Quit
  spawns ──► <resources>/runtime/python -m app.desktop.launcher   (JSON event lines on stdout;
                                                                    "restart"/"quit" on stdin)
             ├─ Postgres 18 + PostGIS 3.6 (pg_ctl, 127.0.0.1, scram, daily pg_dump → backups/, keep 7)
             ├─ Redis 8.10 (password, appendonly)
             ├─ initialize_db → app.data.seed → seed_named_stars
             ├─ uvicorn (1 worker, also serves frontend/dist)
             ├─ celery main (-Q indexer,thumbnails,celery; threads pool on Windows)
             ├─ celery fullres / celery quality (solo on Windows; RSS ceiling enforced by Supervisor)
             └─ celery beat
```

| Area | Files |
|---|---|
| Launcher, supervisor, Postgres and paths | `backend/app/desktop/{launcher,supervisor,postgres,paths}.py` |
| Desktop-only backend behaviour | `backend/app/config.py` (`desktop_mode`, `desktop_launch_nonce`, `frontend_dist_dir`, `pg_bin_dir`), `api/auth.py` (`desktop_login`, `setup-status.desktop_mode`), `main.py` (SPA catch-all) |
| Path helpers | `backend/app/utils/path_security.py`: `is_within_roots`, plus the LIKE-prefix helpers with `ESCAPE '!'`. **Every file-path `LIKE` must use them**, because Windows backslashes are Postgres's default LIKE escape character. |
| Runtime build | `desktop/runtime/build_runtime.py`, `environment.yml`, `THIRD_PARTY_NOTICES.md`. Windows Postgres is EDB 18.6 plus the OSGeo PostGIS 3.6.2 bundle. macOS uses conda-forge, built at `/Users/Shared/AstroCat/runtime` and symlinked at every start, because conda Postgres can't be relocated. |
| Shell | `desktop/src-tauri/{src/main.rs,tauri.conf.json,Cargo.toml,capabilities/default.json}`, `desktop/splash/` |
| Version mapping | `desktop/scripts/version.py` maps `YYYYMMDD.NN` to `YY.MMDD.NN` (for example `26.1028.8`) and writes `src-tauri/tauri.version.json`. `--check-tag` makes a release fail if the tag doesn't match `VERSION`. |
| CI | `.github/workflows/{ci,release}.yml`. The shared runtime build and smoke steps are in `.github/actions/desktop-runtime/action.yml`. On failure, `desktop/scripts/ci_annotate.py` turns log errors into annotations. |
| User data | `%LOCALAPPDATA%\AstroCat` or `~/Library/Application Support/AstroCat`: `pgdata`, `redis`, `thumbnails`, `fullres`, `logs`, `backups`, `temp`, `config.json` (secrets, `image_paths`, `api_keys`, `celery_workers`, `pg_major`) |

**Local dev on Windows:**
- Backend tests: `pytest backend/tests`. A ready venv with `pyvips-binary` exists under an old scratchpad; make a fresh one if it's gone.
- A runtime refresh: `python desktop/runtime/build_runtime.py --app-only` (only if a runtime exists under `desktop/src-tauri/resources/runtime/`). Then run the smoke test: `…\runtime\python.exe -m app.desktop.launcher --smoke --data-dir %TEMP%\astrocat-smoke`.
- **GitHub CLI:** `gh` is installed at `C:\Program Files\GitHub CLI\gh.exe` and signed in, but it isn't on the agent's PATH. Call it by full path, from PowerShell, with the sandbox disabled, because its login lives in the Windows keyring.
- **Don't poll the GitHub API unauthenticated.** The limit is 60 requests an hour, and a polling loop burned it once. Use `gh run watch <id> --exit-status` in a background command instead.

**CI quirks learned the hard way:**
- `macos-14` (arm64) runners are often unavailable. A job cancelled with "not acquired by Runner … capacity constraints" is GitHub's problem, not ours: re-run it with `gh run rerun <id> --failed`.
- `release.yml` publish downloads only `desktop-*` artifacts. The `*.dockerbuild` records break `download-artifact`.
- Windows CI runners can create symlinks; the user's PC can't. Tests that depend on symlinks behave differently between the two.

**How to release:**
1. Bump `VERSION` on `main` via a PR.
2. After the user confirms, run `git tag v$(cat VERSION) && git push origin v$(cat VERSION)`.
3. Watch the Release run, re-running macOS if it gets cancelled.
4. Check the Release page has its 4 assets.

## 4. Execution strategy

**Work serially in one session, one item per PR, in the order of §5.** Don't spawn implementation sub-agents.

Why:
- Each item is 100–400 lines, but several touch the same few files: `main.rs`, `tauri.conf.json`, `release.yml`, `launcher.py`, `Admin.jsx`. Parallel writers would conflict, and each would need its own VERSION bump, Docker rebuild and CI run.
- A sub-agent starts cold. It re-reads this doc and the files it touches, about 40–80k tokens, before doing anything useful. That costs more than the items save.
- The time sink is CI: a desktop leg takes 3–15 minutes, and macOS runners queue. So batch each item into one push, and run everything possible locally first.

**Two exceptions:**
1. **§5.1 (acceptance testing) is the user's work, not an agent's.** Hand them the checklist and work on §5.2 meanwhile. Any bug they find jumps the queue.
2. **If the user explicitly asks for parallel work,** §5.3 (API keys screen) and §5.5 (dmg trim) touch disjoint files from §5.2 and could run in separate sessions, each in its own git worktree. Never run two that both touch `release.yml` or `main.rs`.

**Stop points:**
- After each item, stop for the user's review and commit approval.
- Before any tag or release.
- Before any production deploy.

## 5. Remaining work, in priority order

### 5.1 Acceptance test of the release downloads (user; the agent prepares)

The agent writes a checklist for the user to run on **both** machines, using the installers from the [release page](https://github.com/james474789/AstroCat/releases/tag/v20261028.08), not CI artifacts:
- Install over the previous CI build, and also do one install from scratch.
  - Windows: remove `%LOCALAPPDATA%\AstroCat`.
  - Mac: remove `~/Library/Application Support/AstroCat` and the `/Users/Shared/AstroCat/runtime` link.
- First launch: the splash screen appears and you're signed in automatically.
- Tray → Choose library folders. Index a folder with FITS, CR2 and JPEG files.
- Thumbnails appear. Admin → Indexer shows **non-zero per-folder counts**; this was the D2 A1 fix.
- Rescan the same folder. It should **not** re-queue every file (the D2 LIKE fix). Delete one file and rescan; it should disappear.
- Open the full-resolution viewer. Plate-solve one image with an astrometry.net key added to `config.json`.
- Search by object name and by coordinates. The Statistics page loads.
- Tray → Quit, then reopen. Data is still there. Sleep and wake while running. A backup appears in `backups/` after the first day.
- Uninstall: the data folder is kept, as documented.

Bugs found here are fixed first, each with a test and a parity check: does Docker need the same fix?

### 5.2 Auto-updater (highest-value feature)

**Why:** without it, every desktop user stays on whatever version they installed.

**Shell:**
- Add `tauri-plugin-updater = "2"` and `tauri-plugin-process = "2"` (for the relaunch) to `Cargo.toml`. Register both in `main()`, and add `updater:default` and `process:allow-restart` to `capabilities/default.json`.
- In `tauri.conf.json`, set `bundle.createUpdaterArtifacts: true`. Add a `plugins.updater` block with `pubkey` (the public key) and `endpoints: ["https://github.com/james474789/AstroCat/releases/latest/download/latest.json"]`. On Windows, set `windows.installMode: "passive"`.
- **The macOS update artifact is `AstroCat.app.tar.gz` plus `.sig`.** It's built only when the `app` bundle target is built. Change the macOS matrix `bundles` from `dmg` to `app,dmg`. On Windows the NSIS target produces `…-setup.exe` and `…-setup.exe.sig`.

**Flow in `main.rs`:**
- On startup, after the `ready` event, run a background task (and repeat every 24 h) that calls `app.updater()?.check().await`.
- If an update exists, add a tray item **"Install update <version> and restart"**, and show a dialog-plugin message once per version. Never install automatically.
- On click:
  1. Download (`download`).
  2. Call the existing `shutdown(app)`. This sends `quit` to the launcher and waits for Postgres and the workers to stop. **This step is required:** NSIS can't replace a running `python.exe`, and stopping Postgres uncleanly risks recovery on the next start.
  3. Then `install`, then `app.restart()`.
- Data directories aren't touched. Schema migrations run as usual on the next start (`initialize_db` plus data migrations on worker start).

**Signing key** (Tauri's own minisign; free, and separate from OS code signing):
- The agent runs `npx tauri signer generate -w <scratchpad>/astrocat-updater.key` and gives the **user** the private key file and its password. The user stores them as repo secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, then deletes the local file.
- The agent must never print the private key or commit it. Only the public key goes in `tauri.conf.json`.

**`release.yml`:**
- Pass the two secrets as env to the "Build app" step.
- Upload the update artifacts and their `.sig` files with the installers: widen `matrix.artifact` to a multi-line path.
- In `publish`, before the release step, a small Python step builds `latest.json`:

  ```json
  {"version": "<semver>", "notes": "See the release notes.", "pub_date": "<UTC ISO>",
   "platforms": {
     "windows-x86_64": {"url": ".../download/v<VERSION>/AstroCat_<semver>_x64-setup.exe", "signature": "<contents of .sig>"},
     "darwin-aarch64": {"url": ".../download/v<VERSION>/AstroCat.app.tar.gz", "signature": "<contents of .sig>"}}}
  ```

  `<semver>` comes from `desktop/scripts/version.py`. Attach `latest.json` and the update artifacts.
- Put the `latest.json` builder in `desktop/scripts/updater_manifest.py` with a unit test in `desktop/runtime/test_build_runtime.py`, or a sibling test file that CI runs.

**Risks to resolve, recorded in this doc when known:**
- An unsigned, un-notarised `.app` replaced in place by the updater must still launch on macOS. Gatekeeper quarantine normally applies only to browser downloads, but **confirm this on the user's Mac**: install vN, release vN+1, update.
- **This can only be tested end to end across two releases.** Plan it as: release A, the first with the updater, installed by hand; then release B, a VERSION bump only; then update A to B on both machines. Ask the user before making each release.
- `releases/latest` must point at a non-prerelease. Don't mark future releases as prereleases unless the user wants a beta channel.

**Docs:** add an "Updating" section to `docs/install-desktop.md`, and a D1 row for the updater.

### 5.3 API keys screen (desktop)

- **Backend:** add desktop-only routes in `backend/app/api/settings.py`. They return 404 when `settings.desktop_mode` is false (mirror `desktop_login`), and require admin and CSRF like the other settings writes.
  - `GET /api/settings/desktop-keys` returns `{NAME: {"set": bool, "hint": "…last4"}}` for `launcher.API_KEY_VARS`: `ASTROMETRY_API_KEY`, `LOCAL_ASTROMETRY_URL`, `LOCAL_ASTROMETRY_API_KEY`, `TELESCOPIUS_API_KEY`, `METEOBLUE_API_KEY`. Never return full values. The URL may be returned in full: it's not a secret.
  - `PUT /api/settings/desktop-keys` with `{NAME: "value" | null}`. It writes `config.json["api_keys"]` atomically (temp file + `os.replace`) and keeps every other key. `null` removes the key. The path comes from a new env var `DESKTOP_CONFIG_PATH`, set in `launcher.build_env()`. Add it to `Settings` as `desktop_config_path: str = ""`; the C3.3 parity test will require the decision.
  - Import `API_KEY_VARS` from `app.desktop.launcher`. It's stdlib-only, so it's safe to import.
- **Applying the change:** the keys reach processes as env vars at spawn. In the launcher's monitor loop, watch `config.json`'s mtime. On a change, after a 1 s debounce, call the existing `Launcher.reload()`, the same path the tray's `restart` uses. This avoids Tauri IPC from the remote-origin webview, which would need a `remote` capability. Make sure the tray's own write doesn't cause a double reload: the tray already sends `restart`, so either drop that send or ignore an mtime change that comes within 2 s of a `restart`.
- **Frontend:** in `frontend/src/pages/Admin.jsx`, when `setup-status.desktop_mode` is true (see how `AuthContext.jsx` already exposes it), add an "API keys" card. It has a masked input per key, a "set" badge, and Save. After Save, show "Restarting services…", poll `/api/health`, then refetch. Use the existing Admin card and Lucide patterns. Hide the card for Docker users, who keep using `.env`.
- **Tests:**
  - routes: 404 when desktop mode is off, 403 for non-admins, masking, the atomic write keeps other keys, and `null` removes
  - the launcher's mtime detection and debounce, using a fake clock
  - `npm run build` passes
- **Docs:** update the "Optional API keys" section of `docs/install-desktop.md`.

### 5.4 Moving an existing Docker library into the desktop app

**What already exists:**
- Admin → Backup (`GET /api/admin/backup/download`) produces a plain-SQL `.sql.gz` from Docker's PostGIS (PG 16).
- Admin → Upload (`POST /api/admin/backup/upload`) restores one through `psql`; on desktop it uses `settings.pg_bin_dir`.
- Restoring PG 16 plain SQL into PG 18 works. **Check that PostGIS objects restore cleanly:** the extension versions differ, 3.4 versus 3.6. The `CREATE EXTENSION` in the dump may need `IF NOT EXISTS` handling, or a pre-created extension.

**What's missing is path remapping.** Path columns, from `backend/app/models/image.py`: `images.file_path` (unique, indexed), `thumbnail_path`, `sidecar_path` and `pixinsight_annotation_path`. **Before coding,** grep the other models and any JSONB columns for stored paths, and list them here.

**Backend:** `POST /api/admin/remap-paths` (admin), taking `{"mappings": [{"from": "/data/mount1", "to": "D:\\Astro"}], "dry_run": true}`.
- For each mapping and each path column, run a single `UPDATE` that applies to rows matching the `from` prefix. Use the `path_security` LIKE helpers with `ESCAPE '!'`. It replaces the prefix with `to`, then converts the remaining `/` to the native separator when `to` is a Windows path.
- **Do it all in one transaction.** `file_path` is unique, so a remap that collides must fail the whole thing and report which rows collided.
- A dry run returns counts per mapping and per column, plus 5 before/after samples.
- Thumbnails: set `thumbnail_path = NULL` for remapped rows; they're regenerated on demand. Docker thumbnails aren't copied.
- Return the suggested `image_paths` (the `to` roots) for the UI to save into `config.json`, reusing the §5.3 write helper. If §5.3 isn't done yet, do it first.

**Prefix suggestions:** `GET /api/admin/path-prefixes` returns the distinct first-two-level prefixes of `file_path`, with counts. Docker libraries are `/data/mountN/...`, so two levels is right for Docker sources.

**Frontend:** a desktop-only wizard in Admin with 4 steps:
1. Upload the Docker backup (the existing upload flow).
2. Show the prefixes, with a folder picker for each. A plain text input is fine: the native dialog would need Tauri IPC, so the user can paste a path.
3. Dry run.
4. Apply. Then the services restart.

**Tests:**
- the remap SQL builder: POSIX→Windows, POSIX→POSIX, `_`/`%`/`!` in names, trailing slashes, a collision rolling back the transaction
- route gating

**Docs:** a "Moving from Docker" section in `docs/install-desktop.md`.

**Ask the user (Q3)** whether they want this at all before building it. It's only useful if people actually migrate.

### 5.5 Smaller macOS download (491 MB, versus 148 MB on Windows)

- **Measure first.** Download the dmg from the release (or the CI `desktop-macos-14` artifact), mount it, and run `du -sh` on the top 30 directories under `AstroCat.app/Contents/Resources/runtime`. Likely culprits:
  - conda's full `postgresql` (docs, `pgxs`, unused extensions, `llvmjit*`)
  - GDAL and PROJ data pulled in by PostGIS. Keep `proj.db`; drop the grids.
  - LLVM libraries
  - the full `opencv` build
  - the `include/` folder, static libraries and test folders that the Windows prune already removes
- **Fix:** add darwin rules to `build_runtime.py`'s prune lists. If the measurement shows unreachable dylibs are significant, add an `otool -L` reachability prune that mirrors `prune_unreached_dlls`. Set `jit = off` in the generated `postgresql.conf`, in `postgres.py`, before dropping `llvmjit`.
- **Verify** with the macOS CI smoke test, which exercises PostGIS and `pg_trgm`. Also check `ST_Transform` (PROJ) works, adding it to the smoke test if it isn't covered. **Target: under 300 MB.**

### 5.6 Postgres major-version upgrade (defer until a PG bump is planned)

Already in place: `postgres.py` refuses to start a cluster whose `PG_VERSION` ≠ `PG_MAJOR` (18), with an error that points to the "Upgrading" section of `docs/install-desktop.md`.

When a bump is planned:
- **Release N-1** (the last release on the old major) writes `backups/pre-upgrade.dump` with `pg_dump -Fc` on every clean shutdown.
- **Release N**, on finding an older `PG_VERSION`:
  1. Renames `pgdata` to `pgdata.pg<old>`.
  2. Runs `initdb`.
  3. Runs `pg_restore` from `pre-upgrade.dump`. If that's missing, it falls back to the newest daily backup, with a clear warning naming the backup's date.
  4. Updates `config.json["pg_major"]`.
  5. Keeps the old folder until the user deletes it, and shows an Admin notice.
- Test with a seeded fake old cluster in the CI smoke test.

### 5.7 Code signing (gated by Q4)

The `APPLE_*` slots already exist in `release.yml`; empty values are unset, which gives an unsigned build.
- **Apple:** a Developer ID certificate and notarisation, at $99/year.
- **Windows:** add an Azure Trusted Signing step for the NSIS output, at about $10/month.

After either one, remove that platform's unsigned-install steps from `docs/install-desktop.md`. With signing in place, the §5.2 macOS update risk goes away.

### 5.8 Small loose ends (one PR, any time)

- **Frontend lint in CI.** `frontend/package.json` has `lint` (stylelint + `lint:scope` + eslint), but `ci.yml` only builds. Run `npm run lint` locally first. If it passes, add it to the `frontend` job. If it fails, fix real errors, and ask the user before changing lint rules.
- **The stale `v20261028.07` tag** has no release. Deleting it needs the user's go-ahead, or the user can run `git push origin --delete v20261028.07` themselves. Its Docker images `20261028.07` stay on Docker Hub, which is harmless.
- **The old DB password in git history** (`.env.example`, commit `80a6b21`). Tell the user once more: rotate it wherever it's used. Don't rewrite history unless they ask.
- **The Node 20 deprecation warning** for `actions/checkout@v4` and `actions/setup-python@v5`. Bump them when v5/v6 versions running on Node 24 are available.

### Out of scope (decided)

LAN or multi-user desktop (Docker is that option), Intel Macs, and Linux desktop.

## 6. Verification pattern for every item

1. Run the new or changed tests, then the full backend suite locally on Windows. Check for no new failures.
2. If `frontend/` changed, run `npm run build`, and `npm run lint` once §5.8 is done.
3. Rebuild Docker for every container whose code changed. Check `/api/health` on port 6089 and look for tracebacks in the logs.
4. Run the desktop smoke test locally if a runtime exists; otherwise rely on the CI `desktop-smoke` job.
5. Push once; it's a PR to `main`. Watch it with `gh run watch`, and re-run cancelled macOS jobs.
6. Report to the user: the files changed, test counts per platform, what isn't verified (UI, macOS by hand), and the user's checklist for the item.

## 7. Decisions only the user can make (ask when the item comes up)

| # | Question | Before | Recommendation |
|---|---|---|---|
| Q1 | Redeploy production onto `:latest` (20261028.08)? | any time | Yes, after a quick look at the Docker instance on 6089/6090 |
| Q2 | The updater: generate the signing key, store the two secrets, and make the two test releases | §5.2 | Yes; it's the most valuable remaining item |
| Q3 | Build the Docker-to-desktop migration at all? | §5.4 | Only if you or others will actually move libraries |
| Q4 | Pay for Apple and/or Windows code signing? | §5.7 | Wait for feedback on v1 |
| Q5 | Delete the stale `v20261028.07` tag? | §5.8 | Yes |

## 8. Estimates and recommended models

| Item | Effort | Tokens (approx.) | Model | Notes |
|---|---|---|---|---|
| Orient (this doc + touched files) | 15 min | 30k | — | once per session |
| 5.1 Checklist + fixing what the user finds | 0.5 h + fixes | 15k + per bug | Sonnet 5.5 | bugs vary |
| 5.2 Auto-updater | 1–1.5 days, including 2 releases | 200k | Opus 5.5 | Rust + workflow + an end-to-end test across releases |
| 5.3 API keys screen | 0.5 day | 100k | Sonnet 5.5 | |
| 5.4 Docker → desktop migration | 1 day | 200k | Opus 5.5 | data-correctness risk |
| 5.5 dmg trim | 0.5 day + CI | 80k | Sonnet 5.5 | measure first |
| 5.6 PG upgrade | 1 day | 200k | Opus 5.5 | defer |
| 5.7 Signing | 2 h | 20k | Sonnet 5.5 | after purchase |
| 5.8 Loose ends | 1 h | 25k | Haiku 5.5 or Sonnet 5.5 | |
| **5.1–5.3 + 5.5 + 5.8 (the likely next batch)** | **≈3 days** | **≈450k** | one serial session: Opus 5.5 for 5.2, Sonnet 5.5 for the rest | |
