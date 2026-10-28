# Desktop parity checklist

AstroCat ships from one codebase as Docker images and as Windows and macOS desktop apps. Docker runs the backend under supervisord in a Linux container; the desktop apps run the same processes through a Python launcher (`backend/app/desktop/launcher.py`) with a bundled Postgres, PostGIS and Redis. How the desktop build works: [D1](design/20261027-D1-desktop-apps.md). Why these checks exist: [D2](design/20261028-D2-desktop-parity-and-release.md).

Run through this list for any backend change. CI enforces the starred items: the backend tests run on Linux, Windows and macOS, and `desktop-smoke` builds and starts the real desktop runtime on Windows and macOS whenever backend or desktop code changes.

## Checklist

- **Paths come from `settings`.** Never write a literal `/data`, `/app` or `/var/log` path. On desktop these are per-user folders such as `%LOCALAPPDATA%\AstroCat` and the library roots can be `D:\Astro`. ★ `test_desktop_parity.py::test_no_new_hard_coded_container_paths`
- **Paths are not always POSIX.** Build them with `pathlib`/`os.path`, and don't split on `/`. In SQL, a `LIKE` on `file_path` needs an explicit escape character (`ESCAPE '!'`, see `_root_prefix_pattern` in `tasks/indexer.py`), because Postgres' default escape is the backslash Windows paths are full of.
- **No POSIX-only process tricks.** No `sh -c`, `fork`, signals other than SIGINT/SIGTERM, or other POSIX-only syscalls in app code.
- **Renames need a Windows fallback.** Windows won't rename a directory while a file in it is open, and `os.rename` onto an existing directory raises `FileExistsError` there. See `_rmtree_atomic` in `services/fullres/cache.py`.
- **A new Celery queue** goes in `backend/supervisord.conf` **and** `launcher.app_commands()`. ★ `test_desktop_parity.py` (process list and routing tests)
- **A new setting** in `app/config.py` needs a desktop decision: set it in `launcher.build_env()`, add it to `launcher.API_KEY_VARS` (keys the user puts in `config.json`), or add it to `DESKTOP_DEFAULT_OK` in `test_desktop_parity.py` with a reason. ★ `test_desktop_parity.py::test_every_setting_has_a_desktop_decision`
- **A new system binary** (anything called through `subprocess`) must be bundled in **both** runtime builds (`desktop/runtime/build_runtime.py`, `environment.yml`), with its licence notice in `desktop/runtime/THIRD_PARTY_NOTICES.md`. ★ `desktop-smoke` if the smoke test reaches it
- **A new Python dependency** must install from a wheel on Windows x64 and macOS arm64, or come from conda-forge. Add it to the lists in `desktop/runtime/test_build_runtime.py` once checked. ★ `test_every_runtime_requirement_installs_from_a_wheel_or_conda` and `desktop-smoke`
- **Auth changes** must keep desktop sign-in (`/api/auth/desktop-login`) working. ★ `test_desktop_mode.py` and `desktop-smoke`
- **Desktop-only behaviour** sits behind `settings.desktop_mode` / `frontend_dist_dir`. Docker never sets them, so Docker behaviour must not change.
- **`backend/app/desktop/` is stdlib-only.** It runs before the app's dependencies load.
- **Postgres versions differ.** Docker runs PostgreSQL 16, desktop runs 18. Use SQL that works on both.

## Running the desktop stack locally

```bash
# Build the bundled runtime (micromamba on PATH; build the frontend first; `pip install pefile` for the prune).
# Windows also needs --redis-dir, --edb-zip and --postgis-zip (zips or unpacked folders; pinned URLs/hashes
# in .github/actions/desktop-runtime/action.yml)
python desktop/runtime/build_runtime.py
python desktop/runtime/build_runtime.py --app-only   # refresh backend/frontend code only

# Run the stack without the shell (Ctrl+C to stop)
cd desktop/src-tauri/resources/runtime/backend && ../python.exe -m app.desktop.launcher --headless

# Start, check and stop, as CI does (macOS: ../bin/python3)
../python.exe -m app.desktop.launcher --smoke --data-dir "$TEMP/astrocat-smoke"

# Shell (Rust + Tauri CLI): icons, then build the installer
cd desktop && npm ci && npm run icons && npm run build
```

Backend tests on Windows or macOS without the runtime: `pip install -r backend/requirements.txt pyvips-binary`, then `python -m pytest backend/tests` with `SECRET_KEY` and `DATABASE_URL` set as in `.github/workflows/ci.yml`.
