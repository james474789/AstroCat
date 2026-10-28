# D2: Desktop parity, drift guardrails and first release

**Status:** ready to implement · **Created:** 2026-10-28 · **Branch:** `desktop-apps` (not yet merged to `main`)
**Builds on:** [D1, desktop apps](20261027-D1-desktop-apps.md). Read D1's "Architecture" and "Phase 0 findings" sections first. They take about 10 minutes and explain every desktop file named here.

This is a handover document. It tells the implementing agent what to build, in what order, how to check it, and where to stop and ask the user. It already resolves the design questions, so the agent should not have to rediscover them. If something on the ground contradicts this doc, stop and ask the user. Don't improvise.

---

## 0. Where things stand

- One codebase produces three deliverables: Docker images, a Windows NSIS `.exe` and an Apple Silicon `.dmg`.
- CI run 40662877402 (commit `1030b96`) passed every job.
- The user has installed and launched **both** desktop apps successfully.
- `VERSION` is `20261028.01` (`20261028.02` after this doc). The working tree is clean apart from this doc.
- Nothing is released. There are no git tags, and `desktop-apps` hasn't been merged.

Key desktop files, as a map. Line numbers are from `1030b96`:

| Area | File |
|---|---|
| Process supervisor (replaces supervisord) | `backend/app/desktop/launcher.py`. `app_commands()` is at about line 188, `build_env()` at about 140, `API_KEY_VARS` at line 56 |
| Postgres lifecycle and backups | `backend/app/desktop/postgres.py` |
| Desktop data dirs and `config.json` | `backend/app/desktop/paths.py` (`PG_MAJOR = 18`; `config.json` has `pg_major`) |
| Docker's process list | `backend/supervisord.conf` |
| Runtime bundle build | `desktop/runtime/build_runtime.py`, `desktop/runtime/environment.yml` |
| Tauri shell | `desktop/src-tauri/src/main.rs`, `desktop/src-tauri/tauri.conf.json` |
| CI | `.github/workflows/ci.yml` (Linux only), `.github/workflows/release.yml` (desktop matrix and smoke test) |
| Install docs | `docs/install-desktop.md` |

## 1. Rules the implementing agent must follow

These come from the project's CLAUDE.md and the user's standing feedback.

1. **Never commit or push** without the user's explicit instruction. Leave edits uncommitted.
2. **Bump `VERSION`** (`YYYYMMDD.NN`) on every change, however small. If the file is locked by Docker, write it with PowerShell `Set-Content -NoNewline`.
3. **Rebuild Docker** after any `backend/` or `frontend/` change, before reporting it done:
   - `docker compose build backend && docker compose up -d backend`
   - and/or `docker compose build frontend && docker compose up -d frontend`

   On this dev instance the host ports are 6089 (backend) and 6090 (frontend).
4. **No browser or UI testing.** The user does UI checks. Stop at tests, build and rebuild.
5. **95% confidence before changing code.** This doc is meant to get you there. The questions in §8 are the only open ones; ask them when the doc says to.
6. **Name design docs** with a `yyyymmdd` prefix.
7. **Keep `backend/app/desktop/` stdlib-only.** It runs before the app's dependencies load.
8. **Docker never sets `desktop_mode` or `frontend_dist_dir`.** Every change must leave the Docker path behaving exactly as it does now.
9. **CLAUDE.md is gitignored** (`.gitignore:134`). Anything that must reach other contributors goes in a tracked doc. §5.4 says which one.

## 2. Execution strategy: serial, one agent

**Recommendation: do Phases A–C serially in one session. Don't use sub-agents for the implementation.**

Why:
- **The tasks are small and share files.** A1–A3, B and C each touch 20–150 lines. Several touch the same files: `ci.yml`, `launcher.py`, the test suite and `VERSION`. Parallel writers would collide, and each would need its own Docker rebuild and VERSION bump.
- **Sub-agents cost more than they save here.** Each one starts cold and re-reads D1, this doc and the files it touches, roughly 40–80k tokens before any useful work. Five of them would cost about 300k tokens of overhead to save perhaps an hour of wall time on work that is mostly waiting for CI.
- **CI round trips are the real time cost, not the coding.** A desktop leg builds a whole conda runtime. Check the actual job durations with `gh run view 40662877402` (or the GitHub API if `gh` isn't on PATH). The win is to **batch everything into one push**: run as much as possible locally first (§6), then push once and fix in one more round if needed.

**Where a sub-agent is worth it:**
- An **Explore** agent (read-only, on a cheap model) for the one wide search in C3: listing every hard-coded POSIX path literal. It returns a short list instead of filling the main context with grep output. This is optional; a single `Grep` usually suffices.
- **Phase E items** are independent features with their own PRs. Run each in **its own session** only if the user asks for parallel work. Otherwise do them serially after D, in the order given.

**Order:** A → B → C → push once and check CI → stop and report → D (only on the user's go-ahead) → E (each item gated on its decision in §8).

---

## 3. Phase A: parity fixes

### A1. Mount statistics break when a library folder isn't exactly two levels deep

**Bug.** `update_mount_stats` in `backend/app/tasks/indexer.py` (about line 754) groups images by `SUBSTRING(file_path FROM '^/[^/]+/[^/]+')`, the first two path levels. It then looks each configured root up by exact match in that dict:

- Docker with `/data/mount1`: works.
- Docker with a deeper root such as `/data/images/astro`: shows 0. It's broken there too, only latent.
- Windows (`D:\Astro\...`): the regex returns NULL, so the count is 0.
- macOS (`/Users/me/Astro`): the count lands under `/Users/me`, so it shows 0.

The counts end up in `SystemStats` rows `category = "mount:<root>"`. Three places read them: `api/indexer.py` (about line 350, the Admin → Indexer mount list), `api/admin.py` (about line 312, the disk stats) and the dashboard. **Only the writer needs to change.**

**Fix.** Replace the single grouped query with one query per configured root, run as a prefix match:

```python
def _root_prefix_pattern(root: str) -> tuple[str, str]:
    """(exact, like_pattern) for a library root; '!' is the LIKE escape char."""
    r = root.rstrip("/\\")
    sep = "\\" if ("\\" in r or re.match(r"^[A-Za-z]:", r)) else "/"
    esc = r.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return r, esc + sep + "%"
```

```sql
SELECT COUNT(*), COALESCE(SUM(file_size_bytes), 0) FROM images
WHERE file_path = :exact OR file_path LIKE :pattern ESCAPE '!'
```

- **Use `ESCAPE '!'`.** Postgres's default LIKE escape character is a backslash, and Windows paths are full of them. Without an explicit escape the pattern silently mis-matches. This is the trap in this task.
- **Check the stored separator first.** Find what `file_path` holds on Windows: look at where the indexer builds it (`os.path.join` from `os.walk` keeps the root as configured, plus `\`). If the indexer normalises separators, match that and ignore the regex above.
- **Case sensitivity.** Windows paths compare case-insensitively, but stored paths start with the root exactly as configured, so a case-sensitive LIKE is correct. Don't use ILIKE; it would make Docker's matching looser.
- **Nested roots** (one configured root inside another) will each count the shared files. That's acceptable and matches what users expect. Note it in a code comment.
- **Performance.** There's one query per root, with typically 1–5 roots. Check that `images.file_path` has a btree index (`\d images` or the models). A `text_pattern_ops` index isn't needed: the query runs on the Celery beat schedule (`worker.py` about line 77), not per request.

**Tests** (new `backend/tests/test_mount_stats.py`, in the same stub style as the other task tests):
- `_root_prefix_pattern` on `/data/mount1`, `/data/images/astro/`, `D:\Astro`, `D:\Astro\`, a root containing `_` and `%`, and `/Users/me/My_Photos`.
- Check that the query text carries `ESCAPE '!'`, and that the per-root results land in `mount:<root>` exactly as `settings.image_paths_list` spells the root.

### A2. On Windows, deleting a cached image fails silently

**Bug.** `_rmtree_atomic` in `backend/app/services/fullres/cache.py` (line 194) renames the pyramid directory to `.del-…`, then removes it. If the rename raises `OSError`, it just returns. Windows refuses to rename a directory while any file in it is open, and uvicorn serving a tile is enough to trigger that. So on Windows, `delete_key` and `evict()` (line 234) can quietly free nothing.

- **First, read `evict()`.** Check whether it counts the bytes as freed even when the delete did nothing. If it does, the cache can grow past its cap indefinitely. That makes this a real bug, not a cosmetic one.

**Fix:**
- Make `_rmtree_atomic` return `bool` (deleted or not).
- On `PermissionError`/`OSError` from the rename, retry 3 times with a short sleep (50 ms, 150 ms, 400 ms). If the rename still fails, fall back to `shutil.rmtree(path, ignore_errors=True)`. A partially deleted pyramid has its manifest gone, so readers treat it as a cache miss and it gets rebuilt; check this is true in `cache.py` before relying on it. Then return `not path.exists()`.
- `evict()` only counts bytes as freed when the function returns `True`.
- Keep the rename-first behaviour on POSIX. It's what gives readers an atomic view.

**Related race in `services/fullres/pyramid.py` (line 86).** `os.rename(tmp, final)` after an `if final.exists()` check. On Windows, `os.rename` onto an existing directory raises `FileExistsError` (POSIX would raise `OSError` ENOTEMPTY). Right now that falls into the `except BaseException` branch, which deletes `tmp` and **re-raises**, failing a build that only lost a harmless race. Catch `FileExistsError` and `OSError` with `errno.ENOTEMPTY`/`EEXIST` around the rename: remove `tmp` and carry on, the same as the `final.exists()` branch already does.

**Tests** (extend the existing fullres tests, or add `test_fullres_windows_rename.py`):
- Monkeypatch `os.rename` to raise `PermissionError` and check that the fallback deletes the directory and `evict` accounts correctly.
- Have it raise `PermissionError` and make `rmtree` a no-op: `False` is returned and the bytes aren't counted.
- Make `pyramid` rename raise `FileExistsError`: no exception, `tmp` removed, the manifest returned.

### A3. A real-looking password in `.env.example`

`.env.example` lines 20–22 contain `POSTGRES_PASSWORD=i$$T2Qob2d`, repeated in `DATABASE_URL`. It was committed in `80a6b21`.

- Replace both with the placeholder `change-me-to-a-long-random-string`. Keep the `$$` escaping note if there is one, because compose needs `$$` for a literal `$`.
- Grep the repo for the same string (`docker-compose*.yml`, docs, scripts) and replace it everywhere it appears as an example.
- **Tell the user, but don't act:** the value is still in git history. If it's their live server password, they should change it on the server. Rewriting history is their decision. Don't do it.
- Don't touch the user's real `.env` (it's gitignored).

**Phase A done when:**
- The new tests pass locally.
- The full suite has no new failures.
- Docker backend has been rebuilt and restarted.
- `VERSION` has been bumped.

---

## 4. Phase B: fix the two pre-existing test failures

These fail on `main`/HEAD as well, before any D1 or D2 change:
- `test_feedback_list_and_explain` in `backend/tests/test_recommendations_api.py`
- `test_reindex_all_runs_incremental_backfills_after_scan` in `backend/tests/test_reindex_backfill.py`

They must pass before Phase C, so the new cross-platform CI starts green.

- **Timebox: about 40k tokens.** Run each failing test on its own with `-x -vv`, read the assertion, then decide whether the test or the code is stale. Check `git log -p` on both files for when the code under test last changed.
- **Stale test** (the code changed on purpose): update the test to the current, intended behaviour.
- **Real code bug:** stop and describe it to the user before fixing. It's outside the desktop scope, and the user may want it tracked separately.
- **Never** delete a test or mark it `xfail` just to get CI green.

---

## 5. Phase C: guardrails against drift

The goal: when someone adds a feature for Docker, CI fails if it would break or be missing on desktop.

### C1. Backend tests on all three operating systems (`ci.yml`)

Turn the `backend` job into a matrix over `ubuntu-latest`, `windows-latest` and `macos-14`, with `fail-fast: false`.

- **libvips:**
  - Ubuntu: keep `apt-get install libvips42`.
  - Windows and macOS: `pip install pyvips-binary`, which ships libvips wheels. If `requirements.txt` pins `pyvips`, check that `pyvips-binary` works with that version. The runtime uses `pyvips=3.2`.
- **Shell:** set `shell: bash` on run steps so the same commands work on Windows.
- **Build helpers:** keep the `desktop/runtime` test step. It needs `pefile`; check whether it's installed in that step and add it if not.

**Expect failures on Windows.** Tests may assert POSIX paths (`/data/...`) or use `os.sep`-sensitive comparisons. Handle each one:
1. If the **code** has a POSIX assumption that matters on desktop, fix the code. That's the purpose of this job.
2. If the **test** hard-codes POSIX paths for no reason, make it platform-neutral: use `tmp_path` or `Path`, and build expected values with `os.path.join`.
3. If the behaviour is legitimately **Docker-only**, for example a script that only runs inside the container such as `scripts/update_classifications.py`, use `pytest.mark.skipif(sys.platform == "win32", reason="Docker-only: …")`. Give the specific reason. This should be rare; list each one in the PR description.

**Run this locally on Windows before pushing** (§6). It saves most of the CI round trips.

### C2. Desktop smoke test on pull requests

Today `--smoke` only runs in `release.yml`. Add a `desktop-smoke` job to `ci.yml`:

- **Trigger:** `pull_request` and push to `main`, path-filtered with a `dorny/paths-filter` step, or job-level `if` on changed files. Run it when any of these change:
  - `backend/app/**`
  - `backend/requirements.txt`
  - `backend/alembic/**`
  - `desktop/**`
  - `.github/workflows/**`
- **Matrix:** `windows-latest`, `macos-14`.
- **Steps:** reuse `release.yml`'s runtime-build and smoke steps verbatim, but stop before the Tauri build. No installer is needed here.
  - **Don't copy-paste.** Move the shared steps into a composite action, `.github/actions/desktop-runtime/action.yml`, with inputs `os` and `smoke`. Use it from both workflows so they can't drift apart. This is the part of C2 that matters most.
- **Cache** the micromamba package cache, and the downloaded EDB, PostGIS and Redis zips, with `actions/cache`. Key the cache on the hashes of `desktop/runtime/environment.yml` + `backend/requirements.txt` + the pinned URLs. Cache the downloads, not the built env: the macOS env must be built at `/Users/Shared/AstroCat/runtime` and moved, so a cached env is fragile.
- **Annotations:** keep `ci_annotate.py` on failure, as `release.yml` does, so failures are readable without signing in.

### C3. Parity tests: one source of truth for processes and env vars

New `backend/tests/test_desktop_parity.py`. Pure Python, no services, so it runs in the C1 matrix.

1. **The process list matches supervisord.** Parse `backend/supervisord.conf` with `configparser`; `%(ENV_…)s` interpolation must be off, so use `interpolation=None`. For every `[program:*]`, assert that `app_commands()` has a key with the same name. Then extract these from both command lines and assert they're equal:
   - the subcommand (`worker`/`beat` versus uvicorn's `app.main:app`)
   - the queue list (`-Q …`, compared as a set)
   - the node name (`-n …@%h`, where `%%h` in supervisord becomes `%h`)

   Deliberate differences (pool, concurrency, `--max-memory-per-child`, host and port) are not compared. List them in a comment. Programs that exist on only one side, if any, go in an explicit `ALLOWED_DIFFERENCES = {...}` dict, each with a reason.
2. **Celery routing.** Every queue named in `celery_app.conf.task_routes` (or wherever routes live in `app/worker.py`) is consumed by at least one desktop worker. This catches a new queue that Docker got via a supervisord edit but the launcher never learned about.
3. **Env-var coverage.** For every field on `app.config.Settings` (its `model_fields`), each must be one of:
   - set by `build_env()`, or
   - one of `API_KEY_VARS`, or
   - in an explicit `DESKTOP_DEFAULT_OK` allowlist (fine at its default on desktop), each entry with a one-line reason.

   A new setting therefore fails the test until someone decides how desktop handles it. Call `build_env()` with a fake `DesktopPaths` built under `tmp_path` and a minimal `cfg`; the existing `test_desktop_launcher.py` already does this, so reuse its fixture.
4. **No new hard-coded container paths.** Scan `backend/app/**/*.py`, excluding `desktop/` and `tests/`, for the string literals `"/data/`, `'/data/`, `"/app/` and `"/var/log`. Assert that the set of `(file, literal)` hits equals a checked-in allowlist. Today's allowlist:
   - the `config.py` defaults (lines 52–70)
   - the `logging_config.py` default
   - the `version.py` `/app/VERSION` fallback
   - the `seed_named_stars.py` container fallback
   - the docstrings and examples in `api/indexer.py` and `api/targets.py`
   - `scripts/update_classifications.py` and `scripts/update_planetary.py` (Docker-only one-offs)

   Building this list is the one optional Explore sub-agent job (§2). After A1, `tasks/indexer.py` no longer has a hit.

### C4. Runtime dependency check

`build_runtime.py` installs `backend/requirements.txt` with pip into the conda env, and drops `pyvips` because conda provides it. Add to `desktop/runtime/test_build_runtime.py`:
- A test that every package in `filtered_requirements()` either has a binary wheel for the target platforms, or is in a `CONDA_PROVIDED` set. Keep this static (a set maintained in the test); don't call PyPI.
- A docstring that tells contributors: *a new requirement with C extensions must be checked against the desktop runtime build (CI `desktop-smoke` will catch it)*.

This is cheap. Its main value is the message: a failure points straight at the cause.

### C4b. Postgres major-version guard (cheap now, prevents corruption later)

In `postgres.py`, before `pg_ctl start`:
- If `pgdata/PG_VERSION` exists and its content ≠ `str(PG_MAJOR)`, raise a clear `RuntimeError`, e.g. "Your library database was created by PostgreSQL X; this AstroCat ships PostgreSQL Y. Install the previous version or see docs/install-desktop.md#upgrading." The launcher already surfaces startup errors to the splash screen.
- Add a unit test with a fake `PG_VERSION`.
- The actual upgrade path is E4. This guard just makes a future mismatch fail loudly instead of corrupting data.

### C5. The parity checklist (tracked)

Create `docs/desktop-parity.md`, about one page, and link it from README's contributing or downloads section and from D1. It's the checklist for any feature work:
- Paths come from `settings`. Never write literal `/data`, `/app` or `/var/log` paths (C3.4 enforces this).
- No `sh -c`, POSIX-only syscalls or `os.rename` over existing directories without a Windows fallback.
- A new Celery queue goes in `supervisord.conf` **and** `launcher.app_commands()` (C3.1 and C3.2 enforce this).
- A new setting gets a decision for desktop: set it in `build_env()`, add it to `API_KEY_VARS`, or add it to `DESKTOP_DEFAULT_OK` (C3.3 enforces this).
- A new system binary (anything called through `subprocess`) must be bundled in **both** runtime builds, with its notice in `THIRD_PARTY_NOTICES.md`.
- A new Python dependency with native code must pass `desktop-smoke` (C2).
- Auth changes must keep `desktop-login` working; it's covered by `test_desktop_mode.py`.
- How to run the desktop stack locally: copy the CLAUDE.md desktop commands.

Also add a short pointer in the local CLAUDE.md "Common Development Tasks" section: "Desktop parity: read docs/desktop-parity.md". It's local only, but helps future agents on this machine.

**Phase C done when:**
- Every new test passes locally on Windows. macOS and Linux are covered by CI.
- `ci.yml` and `release.yml` both use the composite action.
- The Docker rebuild is done, if backend code changed.
- `VERSION` has been bumped.
- Then **stop**. Ask the user for permission to commit and push to `desktop-apps`, and say that this triggers the `release.yml` dry run plus the new `ci.yml` jobs.

---

## 6. How to verify cheaply (do this before any push)

The dev box is Windows 11, so the Windows leg can be checked locally.

1. **Backend tests on Windows:**
   - Create a venv: `python -m venv .venv-ci && .venv-ci\Scripts\pip install -r backend\requirements.txt pyvips-binary`.
   - Then run `.venv-ci\Scripts\python -m pytest backend\tests -q -p no:warnings` with the same `SECRET_KEY`/`DATABASE_URL` env as `ci.yml`.

   This reproduces most of what the C1 Windows leg will catch. Put the venv in the scratchpad, or gitignore `.venv-ci`.
2. **Desktop runtime and smoke on Windows:** a runtime may already exist under `desktop/src-tauri/resources/runtime/`. If it does, refresh the code only with `python desktop/runtime/build_runtime.py --app-only`. Then run the smoke test:

   ```
   desktop\src-tauri\resources\runtime\python.exe -m app.desktop.launcher --smoke --data-dir %TEMP%\astrocat-smoke
   ```

   This proves A1, A2 and C4b inside the real bundled stack.
3. **macOS:** CI is the only check. Batch everything into one push.
4. **Docker:** rebuild and restart backend (and frontend if touched). Confirm `GET http://localhost:6089/api/health` (or the existing health route) answers, and the logs show no tracebacks. Don't do UI checks.
5. **Linting the workflows:** if `actionlint` is available, run it on both workflows. If not, review the YAML carefully. A broken workflow costs a full CI round trip.

---

## 7. Phase D: first public release (only on the user's explicit go-ahead)

Each step is outward-facing. Confirm with the user before **each** of D2, D4 and D5.

1. **Prerequisites.** Phases A–C are committed on `desktop-apps`, and both workflows are green on the dry run.
2. **Merge.** Open a PR from `desktop-apps` to `main`, with the body ending in the attribution line. Merge it after the user approves. In the same PR, remove the temporary `push: branches: [desktop-apps]` trigger from `release.yml` (about line 21) and its "Remove after merge" comment.
3. **Secrets.** The user adds the repo secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. You can't do this; tell them where: Settings → Secrets and variables → Actions.
4. **Tag.** Bump `VERSION` and commit it on `main`. Then `git tag v$(cat VERSION) && git push origin v$(cat VERSION)`. `version.py --check-tag` makes the release fail if the tag and `VERSION` differ.
5. **Watch the release run.** It pushes `james474789/astrocat-{backend,frontend}:<version>` **and `:latest`**. This changes what the user's production Portainer stack pulls next. See the user's memory "Deploy to prod runbook"; ask before redeploying prod.
6. **Check the release page.** It has the `.exe`, the `.dmg`, `docker-compose-example.yml` and the install notes. The README download links resolve.

---

## 8. Decisions needed from the user

Ask each one when you reach the phase that needs it, not all up front. Use the AskUserQuestion tool and put the recommended option first.

| # | Question | Needed before | Recommendation |
|---|---|---|---|
| Q1 | If the leaked `.env.example` password is in use anywhere, will you rotate it? Do you want history rewritten? | A3 (you only report this) | Rotate it; don't rewrite history |
| Q2 | For a pre-existing failure that is a real code bug, fix it in D2 or separately? | B, if it happens | Separately |
| Q3 | Go-ahead to commit and push Phases A–C to `desktop-apps` | End of C | — |
| Q4 | Merge, add secrets, tag the first release | D | — |
| Q5 | Order and selection of the Phase E items | E | E1 → E2 → E3 → E5 → E4 → E6 |
| Q6 | Code signing: pay for Apple ($99/yr) and/or Azure Trusted Signing (about $10/mo)? | E6 | Decide after v1 feedback |

---

## 9. Phase E: closing the feature gaps (post-v1, each item gated by Q5)

Each item is its own change (and, after the merge, its own PR from `main`). Each one gets the full A-style treatment: tests, VERSION bump, rebuild, stop for review. The designs below are deliberately concrete. Where an item needs a fact checked first, it says so.

### E1. Auto-updater (desktop users can't currently update)

- **Plugin:** add `tauri-plugin-updater` (Rust crate + capability) and set `bundle.createUpdaterArtifacts: true` in `tauri.conf.json`.
- **Endpoint:** `https://github.com/james474789/AstroCat/releases/latest/download/latest.json`.
- **Signing key:** generate one with `npx tauri signer generate`. **The user** stores the private key and password as the secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. Only the public key goes in `tauri.conf.json`. This is Tauri's own minisign signature, separate from OS code signing, and free.
- **`release.yml`:** the desktop jobs upload the `.sig` files alongside the installers. The publish job assembles `latest.json`:

  ```
  { version, notes, pub_date, platforms: {
      "windows-x86_64": { url, signature },
      "darwin-aarch64": { url, signature } } }
  ```

  and attaches it. `version` is the semver from `desktop/scripts/version.py`.
- **UX:** check on start and every 24 h. Show a tray item, "Update available → Install and restart". Never auto-install.
- **Before installing:** call the existing `shutdown()` path in `main.rs` so the launcher stops Postgres and the workers cleanly (warm Celery shutdown, `pg_ctl stop -m fast`). On Windows the NSIS installer can't replace a locked `python.exe`.
- **Data dirs are untouched by an update.** Database migrations run as they do now (`initialize_db` plus data migrations on worker start).
- **Risk to check on the Mac:** the unsigned and un-notarized `.app` replaced by the updater must still launch. Gatekeeper quarantine normally applies only to browser downloads, but confirm this with the user on a real Mac. Write the result into D1/D2.
- **Tests:** Rust unit tests are optional. The key check is a dry-run release producing a valid `latest.json`; validate it in a CI step with a tiny Python schema check.

### E2. API keys screen (desktop users currently hand-edit `config.json`)

- **Backend:** add desktop-only routes in `backend/app/api/settings.py`, returning 404 when `desktop_mode` is off. This mirrors the `desktop-login` gating.
  - `GET /api/settings/desktop-keys` returns `{name: {set: bool, hint: "…last 4"}}` for `API_KEY_VARS`. It never returns full values.
  - `PUT /api/settings/desktop-keys` (admin and CSRF, as the other settings writes) takes `{name: value | null}`. It writes atomically (tmp + `os.replace`) into `config.json["api_keys"]`.
  - The launcher passes the config path in a new env var `DESKTOP_CONFIG_PATH`, set by `build_env()`. That also satisfies C3.3.
- **Applying the change:** the keys reach processes as env vars at spawn. Add a check to the launcher's monitor loop: if `config.json`'s mtime changes, call the existing `Launcher.reload()` (the same thing "Choose library folders…" triggers through stdin `restart`). This avoids any Tauri IPC from the remote-origin webview. The PUT response tells the UI "restarting services…". The frontend then polls `/api/health` and reloads.
- **Frontend:** in Admin, when `setup-status.desktop_mode` is true, add an "API keys" card with a masked input per key and a Save button. Use the existing Admin card patterns and Lucide icons. Docker users keep using `.env`, so hide the card there.
- **Tests:** route gating (404 off, 403 non-admin), masking, an atomic write that keeps the other `config.json` keys, and launcher mtime detection (unit test with a fake clock or file).

### E3. Moving an existing Docker library into the desktop app

The building blocks exist. Docker's Admin → Backup (`api/admin.py` line 356) downloads a plain-SQL `.sql.gz`, and Upload (line 428) restores one through `psql`. Docker runs PostGIS on PG 16, and desktop is PG 18; restoring a 16 plain-SQL dump into 18 works.

What's missing is **path remapping**, because the stored paths are container paths (`/data/mount1/...`).

- **Check first:** grep the models for every column holding a file system path: `images.file_path`, plus any thumbnail, fullres or sidecar path columns, and any JSONB that embeds paths. List them in the doc before coding.
- **Backend:** a new admin endpoint, `POST /api/admin/remap-paths`, taking `{mappings: [{from: "/data/mount1", to: "D:\\Astro"}], dry_run: bool}`.
  - For each mapped column, use a single `UPDATE … SET col = :to || REPLACE(SUBSTRING(col FROM length(:from)+1), '/', :sep) WHERE col = :from OR col LIKE :from_pattern ESCAPE '!'`, reusing A1's helper. On desktop, `sep` is the native separator.
  - A dry run returns the counts per mapping and 5 sample before/after pairs.
  - Run it in one transaction.
  - Afterwards, set the desktop `image_paths` to the `to` roots (through E2's config write, or return them for the UI to save).
- **Thumbnails** are regenerated on demand. Thumbnail paths stored in the DB should be cleared, or remapped if the files are copied over; decide this once the columns are known.
- **Frontend:** a small wizard in Admin (desktop only): upload the Docker backup → the server suggests mappings from the distinct path prefixes → the user picks the target folder for each → dry run → apply.
- **Docs:** add a section to `docs/install-desktop.md`.
- **Tests:** remap SQL against stub rows, covering POSIX→Windows and POSIX→POSIX, `_`/`%` in names, and roots with a trailing slash.

### E4. Postgres major-version upgrade (not needed until PG 19 ships in the runtime)

C4b already makes a mismatch fail safely. The full path, when needed:
- The runtime ships only the new major. So the **last release on the old major** writes `backups/pre-upgrade.dump` (`pg_dump -Fc`) on every clean shutdown, gated by a flag file `upgrade-ready`.
- The **first release on the new major**, on finding an old `PG_VERSION`:
  1. Moves `pgdata` to `pgdata.pg<old>`.
  2. Runs `initdb`.
  3. Runs `pg_restore` from `pre-upgrade.dump`, falling back to the newest daily backup with a clear warning that it's from <date>.
  4. Updates `config.json["pg_major"]`.
  5. Keeps `pgdata.pg<old>` until the user deletes it. Add an Admin notice.
- **Tests:** a launcher unit test with fake dirs. A real dump/restore runs in the CI smoke via a seeded fake old cluster, but only once this is actually built.
- **Recommendation:** write this design into D1 now. Implement it only when a PG bump is planned.

### E5. Smaller macOS download (511 MB vs 155 MB for Windows)

- **Measure first:** unpack the dmg artifact, or the CI runtime folder, and `du -sh` the top 30 directories in `runtime/`. Likely culprits:
  - conda's full `postgresql` package (docs, `pgxs`, unneeded extensions)
  - GDAL, PROJ and their data pulled in by PostGIS (PROJ's `proj.db` is needed; the grids are not)
  - LLVM/JIT libraries (`llvmjit`)
  - `opencv`'s full build
  - duplicate libs that Windows' `prune_unreached_dlls` already removes
- **Fix:** add darwin rules to `build_runtime.py`'s prune list. On macOS the equivalent of the PE-import prune is `otool -L` walking from the roots; implement it only if the measurement shows it's worth it. Set `jit = off` in the generated `postgresql.conf`, and drop `llvmjit*`.
- **Verify** with the CI smoke on macOS (it exercises PostGIS, `pg_trgm`, PROJ through `ST_Transform` if the smoke covers it; check). **Target under 300 MB.**

### E6. Code signing (gated by Q6)

The signing slots already exist in `release.yml`, and are skipped when the secrets are empty. Once the user buys:
- **Apple:** fill in the `APPLE_*` secrets. The workflow then notarizes and staples.
- **Azure Trusted Signing:** add the signing step for the NSIS output.

Then remove the unsigned-install steps from `docs/install-desktop.md`. Mostly configuration; small.

### Explicitly still out of scope

These stay out of scope: LAN or multi-user desktop (Docker remains that option), Intel Macs and Linux desktop.

---

## 10. Estimates and recommended models

| Stage | Effort | Tokens (approx.) | Recommended model | Notes |
|---|---|---|---|---|
| Read D1 + D2, orient | 15 min | 40k | — | once per session |
| A1 mount stats | 1.5 h | 50k | Sonnet 5.5 | the LIKE-escape trap is the main risk |
| A2 fullres Windows | 1.5 h | 50k | Sonnet 5.5 | |
| A3 `.env.example` | 10 min | 5k | Sonnet 5.5 | |
| B pre-existing tests | 1 h | 40k (timebox) | Sonnet 5.5 | stop if it's a real bug |
| C1 OS matrix + local Windows run | 2–3 h | 80k | Sonnet 5.5 | time depends on how many tests assume POSIX |
| C2 composite action + PR smoke | 2 h + CI | 60k | Opus 5.5 | workflow refactor; one CI round |
| C3 parity tests | 2 h | 60k | Opus 5.5 | judgement on the allowlists |
| C4/C4b/C5 | 1 h | 30k | Sonnet 5.5 | |
| CI round and fixes | 1–2 h wall | 30k | same session | batch into one push |
| **A–C total** | **≈1.5–2 days** | **≈450k** | one serial Opus 5.5 session, or Sonnet 5.5 throughout with Opus for C2/C3 | |
| D release | 1 h + CI | 20k | Sonnet 5.5 | user-gated |
| E1 updater | 1 day | 200k | Sonnet 5.5 | needs the user's signing secrets |
| E2 API keys screen | 0.5 day | 100k | Sonnet 5.5 | |
| E3 Docker → desktop | 1 day | 200k | Opus 5.5 | data-correctness risk |
| E4 PG upgrade | 1 day | 200k | Opus 5.5 | defer until needed |
| E5 dmg trim | 0.5 day + CI | 80k | Sonnet 5.5 | measure first |
| E6 signing | 2 h | 20k | Sonnet 5.5 | after purchase |

**Single-model recommendation if the session can't switch models:** use Opus 5.5 for A–C. The cost difference on about 450k tokens is smaller than one wasted CI round caused by a subtle workflow or parity-test mistake.

## 11. Report back format

At each stop point (end of C, after D, after each E item), report:
- what changed (files)
- the test results, with counts per platform where known
- what wasn't verified (macOS locally, UI)
- the exact question for the user from §8

Keep it short. The user reads the next-step box, not long prose.
