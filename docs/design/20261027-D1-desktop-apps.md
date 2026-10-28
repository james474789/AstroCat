# D1: Native desktop apps (Windows + macOS) from one codebase

**Status:** implemented, not yet built in CI. Phase 0 findings are in (see the end) and their fixes are applied (uncommitted). Postgres is now 18. On Windows, Postgres + PostGIS come from EDB Postgres 18.6 plus the OSGeo PostGIS 3.6.2 bundle (conda-forge has no Windows PostGIS), and the build now does this.
- **Verified on Windows (2026-10-27).** A runtime assembled from the spike's conda env, the `pgsql/` produced by `build_runtime.build_pgsql`, Redis and the app passed `launcher --smoke` twice:
  - fresh: `initdb`, migrations and seeding
  - restart of the existing cluster
  - each run checked PostGIS 3.6 (GEOS and PROJ), Redis with a password, the API, desktop sign-in, the frontend and all three workers, then stopped with the workers' warm shutdown (exit 0) and no leftover processes
  - the generated `pgsql/` is byte-identical to the spike's validated tree (112 MB)
- **Not yet run:** the Tauri shell (no Rust toolchain here) and anything on macOS. The first CI dry run covers both.
**Date:** 2026-10-27

**Keeping Docker and desktop in step:** follow [docs/desktop-parity.md](../desktop-parity.md) for any backend change (D2 added the CI checks behind it).

## Goal
Each GitHub Release offers three downloads built from the same commit and `VERSION`:

| Target | Artifact | Built by |
|---|---|---|
| Docker | `james474789/astrocat-{backend,frontend}:<VERSION>` and `:latest` | `release.yml` → `docker` |
| Windows 10/11 x64 | NSIS `.exe`, per-user, no admin rights | `release.yml` → `desktop` (windows-latest) |
| macOS 12+ Apple Silicon | `.dmg` | `release.yml` → `desktop` (macos-14) |

Decisions:
- Builds are unsigned for now. The CI signing variables are listed in `release.yml`.
- Desktop is single-user with auto-login and binds only to loopback.
- Apple Silicon only.
- The shell is a Tauri v2 native window.

## Why Postgres and Redis are bundled rather than replaced
- **Postgres.** The backend uses JSONB operators, `percentile_cont`, `DISTINCT ON`, `pg_trgm` GIN indexes, partial unique indexes, native enums, PostGIS cone searches, about 108 raw `text()` queries and 29 Postgres-only migrations. Porting to SQLite would be a rewrite, so a real Postgres 18 + PostGIS 3.6 server runs privately instead. It is 18 rather than Docker's 16 because conda-forge builds PostGIS 3.6 for osx-arm64 only against Postgres 18. Desktop clusters are always new, so the mismatch doesn't matter, but desktop `pg_dump` backups restore only into 18 or later.
- **Redis.** It holds application state, not just the Celery queue: settings cache, locks, the astrometry pending/dispatched ZSETs, the fullres LRU, bulk progress, response caches and `LLEN` on queue names. A Redis-compatible server is bundled.

## Architecture
```
Tauri shell (desktop/src-tauri, Rust)
  splash page → spawns <runtime>/python -m app.desktop.launcher   (cwd <runtime>/backend)
  ← JSON lines on stdout: status / ready{url, login_url} / error
  → stdin: "restart" (library folders changed), "quit"; EOF also stops it
  main window → login_url → /api/auth/desktop-login?nonce=… → 303 to /
  tray: Open · Choose library folders… · Open logs folder · Quit

Python launcher (backend/app/desktop, stdlib only)
  postgres  initdb (first run) / pg_ctl start, 127.0.0.1:<random>, scram
  redis     redis-server --appendonly yes, 127.0.0.1:<random>
  init      app.scripts.initialize_db → app.data.seed → app.scripts.seed_named_stars (= compose `command`)
  children  uvicorn (1 worker, also serves frontend_dist) · celery main · celery fullres@ · celery quality@ · beat
  daily     pg_dump -Fc into backups/ (keeps 7), replacing the db-backup container
```

### Backend changes (active only in desktop mode; Docker behaviour is unchanged)
- **New settings in `config.py`:**
  - `desktop_mode`
  - `desktop_launch_nonce`
  - `frontend_dist_dir`
- **`GET /api/auth/desktop-login`** (`api/auth.py`):
  - returns 404 unless `desktop_mode` is on and a nonce is configured
  - returns 403 for a non-loopback client or a wrong nonce (constant-time compare)
  - creates the local admin `astrocat@localhost` on first use, with a random password nobody types
  - `admin-sign-up` is blocked in desktop mode
  - `setup-status` now also returns `desktop_mode`
- **Frontend serving** (`main.py`): when `frontend_dist_dir` is set, FastAPI serves `index.html` (no-cache) and the assets, with an SPA fallback. `/api/*` always wins, and paths can't escape the dist folder.
- **Indexer paths:** the scan and batch routes (`api/indexer.py`) used to require a `/data/` prefix. They now check that the path is inside a configured `IMAGE_PATHS` root (`utils/path_security.is_within_roots`), which works for Windows drive paths too. In Docker the UI only ever offers paths under `IMAGE_PATHS`, so behaviour there is the same.
- **Thumbnails:** the thumbnail task reads `settings.thumbnail_cache_path` instead of a hard-coded `/data/thumbnails` fallback.
- **Frontend:** in desktop mode, Log out and Admin → Users are hidden.

### Platform differences inside the launcher
| | Windows | macOS |
|---|---|---|
| Main Celery pool | `-P threads` (no prefork on Windows) | prefork |
| quality / fullres | `-P solo`; the launcher recycles quality above 900 MB working set | prefork, `--max-memory-per-child=900000` |
| Children die with launcher | Job object with KILL_ON_JOB_CLOSE | stdin EOF; orphans from a crash are reaped on the next start (PIDs in `temp/children.json`, checked against the runtime path) |
| Redis-compatible server | redis-windows MSYS2 build of Redis 8.10.2, downloaded by CI from `WINDOWS_REDIS_ZIP_URL`; `build_runtime.py` copies only the 6 needed files | conda-forge `redis-server` 8.10 |
| Postgres + PostGIS | `<runtime>/pgsql`: EDB Postgres 18.6 + OSGeo PostGIS 3.6.2, pruned (CI downloads both pinned by SHA-256; `build_runtime.py --edb-zip/--postgis-zip`). Tools run by absolute path (`PG_BIN_DIR` / `settings.pg_bin_dir`), kept off PATH; `PROJ_LIB` points at the bundle's `proj.db` | conda-forge `postgresql` 18 + `postgis` 3.6 |
| Data folder | `%LOCALAPPDATA%\AstroCat` | `~/Library/Application Support/AstroCat` |

Session cookies and the JWT last 30 days, and every launch signs in again with a fresh nonce.

### Runtime bundle (`desktop/runtime`)
- `environment.yml` (conda-forge): python 3.12, libvips 8.18, conda's pyvips 3.2, postgresql 18, plus `sel(osx)` postgis 3.6 and redis-server 8.10. micromamba evaluates `sel()` against the host OS, so each OS builds natively in CI.
- `build_runtime.py`:
  - creates the env with micromamba directly at `desktop/src-tauri/resources/runtime`. CI sets `CONDA_OVERRIDE_OSX=12.0` so macOS builds don't silently need 13.3+.
  - pip-installs `backend/requirements.txt` without the `pyvips` pin, so the conda pyvips stays
  - on Windows, copies the 6 needed redis-windows files
  - prunes, roughly halving the Windows env (about 1 GiB → about 518 MiB; 101 MiB as xz). It removes:
    - `*.pdb`, `.lib`/`.a`, headers
    - unneeded `share` subfolders, one at a time (proj, gdal and postgresql are kept)
    - the stdlib test/idlelib/tkinter
    - `site-packages/*/tests`, except astropy's
    - `Scripts/*.exe`, cv2's ffmpeg DLL, the five libvips optional modules, ImageMagick/Ghostscript resources and `ccapiserver.exe`
    - non-Postgres `.exe` files in `Library/bin`
    - DLLs in `Library/bin` that a pefile closure walk can't reach
  - smoke-imports the native packages
  - copies `backend/{app,alembic,data,alembic.ini}`, `VERSION` and `frontend/dist` (as `backend/frontend_dist`)
- The launcher sets `PATH`, `PROJ_DATA`/`PROJ_LIB` and `GDAL_DATA`, the variables conda activation would normally export.

### Versioning
`desktop/scripts/version.py` maps `VERSION` `YYYYMMDD.NN` to `YY.MMDD.NN` (for example `20261027.01` → `26.1027.1`). The result is monotonic and fits the 16-bit Windows version fields. MSI is not built because its minor version is limited to 255; NSIS is used instead. A release is cut by pushing the tag `v<VERSION>`, and the workflow checks that the tag matches.

## Releasing
1. Bump `VERSION`.
2. `git tag v$(cat VERSION) && git push origin v$(cat VERSION)`
3. `release.yml` then:
   - builds and pushes both Docker images
   - builds the runtime on each OS
   - runs `launcher --smoke`, which checks real Postgres + PostGIS, Redis, migrations, the API, desktop sign-in, the frontend and three Celery workers
   - builds the installers and publishes the GitHub Release, with `docker-compose-example.yml` and `docs/install-desktop.md` attached
4. **Actions → Release → Run workflow** builds everything without publishing (a dry run).

One-time repository setup:
- secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
- variables `WINDOWS_REDIS_ZIP_URL` and `WINDOWS_REDIS_ZIP_SHA256`

## Not in v1
- **Auto-update.** `tauri-plugin-updater` needs a minisign key pair: generate it with `npx tauri signer generate`, store it as the `TAURI_SIGNING_PRIVATE_KEY` secret, and put the public key in `tauri.conf.json`. Then add the plugin and `createUpdaterArtifacts`.
- **Code signing:**
  - macOS: an Apple Developer ID plus notarization; the `APPLE_*` secrets are already wired in.
  - Windows: Azure Trusted Signing through `bundle.windows.signCommand`.
- **Moving a Docker library into the desktop app.** `file_path` values are container paths, so this needs a path-remap tool.
- **Other scope:** LAN or multi-user access on desktop, Intel Macs, Linux desktop, and Postgres major-version upgrades. `pg_major` is recorded in `config.json` for when they are needed.

## Phase 0 findings (2026-10-27)
The spikes ran on Windows 11 x64 with micromamba 2.10.0, in a scratch folder outside the repo. macOS was checked only with the conda solver (`--platform osx-arm64`) and pip's resolver; nothing ran on a Mac. Every download came from conda-forge, PyPI or the GitHub releases of micromamba, redis-windows and Garnet.

### Outcome
| Topic | Result |
|---|---|
| **PostGIS on Windows** | **Blocker.** conda-forge has no win-64 `postgis` build for any version. PostGIS is needed: geography columns, `ST_DWithin`/`ST_Distance` cone searches and `ST_MakePoint` appear in about 20 source files. The options are listed below and need a decision. |
| Postgres version | **18, not 16.** On osx-arm64 the only PostGIS is 3.6.x, built against libpq 18. The original `postgresql=16` + `postgis=3.4` in `desktop/runtime/environment.yml` could not be solved on either target; it has since been changed to 18. Desktop clusters are always new, so differing from Docker (16/3.4) is fine. Desktop `pg_dump` backups restore only into 18 or later. |
| Python stack on Windows | All imports work: astropy, rawpy, sep, cv2, pyvips, asyncpg, psycopg2, numpy, scipy, celery and uvicorn. pyvips needs a fix (see Gotchas). |
| Postgres on Windows | conda-forge Postgres 18.6: `initdb` (scram) → `pg_ctl start` → `CREATE EXTENSION pg_trgm` → `pg_dump -Fc` → `pg_ctl stop -m fast` all work. `pg_trgm` ships with `postgresql`. asyncpg (`percentile_cont`, JSONB `#>>`) and psycopg2 (`similarity()`) work over scram. `CREATE EXTENSION postgis` fails with "extension is not available". |
| Relocation (Windows) | **conda-pack is not needed.** The env was built at one path, renamed, and run from the new path without `conda-unpack`: imports, dzsave, `initdb`, `pg_trgm`, asyncpg, psycopg2 and `pg_dump` all pass. conda-pack plus `conda-unpack` also works (the unpack took 26 s here). **macOS is different, see the next row.** |
| Relocation (macOS) | **conda-forge Postgres can't be moved on macOS/Linux.** `initdb` from a moved env looks for `postgres.bki` under the build path, and `pg_config --sharedir` reports it too. Even with `initdb -L`, the server can't find its share/lib/zoneinfo. This was found by the first install on a real Mac and reproduced on Linux (conda-forge `postgresql` 18 + `postgis` 3.6). **Fix:** `build_runtime.py` builds the macOS env at `/Users/Shared/AstroCat/runtime`, moves it into the bundle and records that path in `<runtime>/.build-prefix`. At every start, `launcher.ensure_build_prefix_link` points a symlink from that path to the app's runtime, and it replaces a link left by an earlier install. Tested on Linux: `initdb`, server start, `pg_trgm` and PostGIS 3.6.4 all work from a moved env through the link. The CI `--smoke` now runs from the moved runtime, so it exercises this. One limitation: a second macOS user account with AstroCat in a *different* location can't take over the first user's link. |
| Windows Redis | **Redis 8.10.2, MSYS2 build from redis-windows.** It passes everything. Garnet's release builds need a separately installed .NET 10 runtime (details below). |
| Celery pools on Windows | `threads` works for main, `solo` for quality and fullres, and beat runs as its own process. Embedded `-B` is refused on Windows. Crash recovery of `acks_late` tasks needs a launcher step (Celery section). |

### Repository variables (Windows Redis)
```
WINDOWS_REDIS_ZIP_URL    = https://github.com/redis-windows/redis-windows/releases/download/8.10.2/Redis-8.10.2-Windows-x64-msys2.zip
WINDOWS_REDIS_ZIP_SHA256 = 7c8cebd50347eaa1d9e784da842ed47a4f33394637835531d6614777b950ee85
```
The SHA-256 was computed on download and matches the hash in the release notes. 8.10.2 is the same version as conda-forge's osx-arm64 `redis-server`, so both platforms run the same Redis.

### Proposed `desktop/runtime/environment.yml`
```yaml
name: astrocat-runtime
channels:
  - conda-forge
dependencies:
  - python=3.12
  - pip
  - libvips=8.18
  - pyvips=3.2                  # conda build; pip's pyvips 2.2.3 can't find conda's DLL on Windows
  - postgresql=18
  - sel(osx): postgis=3.6       # the only PostGIS series for osx-arm64
  - sel(osx): redis-server=8.10
  # Windows PostGIS: not on conda-forge; EDB + OSGeo instead (adopted, see "Windows PostGIS spike")
```
- Then `pip install` `backend/requirements.txt` **with the `pyvips==2.2.3` line removed**, so pip doesn't replace the conda pyvips. Docker keeps its pin.
- micromamba evaluates `sel()` against the **host** OS, not `--platform`. That is fine because CI builds natively on each OS, but a cross-platform dry run needs a file without selectors.
- Solved versions on 2026-10-27:
  - win-64: python 3.12.16, postgresql 18.6, libvips 8.18.7, pyvips 3.2.0
  - osx-arm64: the same, plus postgis 3.6.4 (GDAL 3.13, PROJ 9.8/9.9, GEOS 3.14) and redis-server 8.10.2

### Sizes (Windows, Python + Postgres + libvips, no PostGIS or Redis)
| | On disk | tar.gz | tar.xz -6 (≈ NSIS LZMA) |
|---|---|---|---|
| After pip install | 1017 MiB | 323 MiB | – |
| Pruned (all tests re-run) | 518 MiB | 172 MiB | 101 MiB |

Add 17 MB for Redis: `redis-server.exe`, `redis-cli.exe` and the `msys-2.0`, `msys-crypto-3`, `msys-ssl-3` and `msys-gcc_s-seh-1` DLLs. The other `.exe` files and `msys-stdc++` in the zip aren't needed. PostGIS will add more once that is solved.

What pruning removed (505 MB in total):
| MB | What |
|---|---|
| 136 | DLLs in `Library/bin` that nothing reaches. This was found by walking PE imports (plus delay-imports) from every `.pyd`, `python.exe`, the Postgres tools, `Library/lib/*.dll` and `vips-42.dll`. conda-forge libvips drags in ImageMagick, Ghostscript (25 MB), Poppler, librsvg, x265, aom, rav1e, SVT-AV1, HDF5 and msys X11, none of which the app uses. |
| 78 | `*.pdb` |
| 73 | `*.lib` / `*.a` |
| 45 | `.exe` files in `Library/bin` other than postgres, initdb, pg_ctl, pg_dump(all), pg_restore, psql, pg_isready, pg_controldata, pg_resetwal and pg_upgrade |
| 43 | `Library/include`, `Include` |
| 41 | `share/{gir-1.0,locale,doc,man,…}` |
| 31 | `site-packages/**/tests` (**keep `astropy/tests`**: `astropy/__init__` imports it) |
| 26 | `cv2/opencv_videoio_ffmpeg*.dll` (only `cvtColor` is used) |
| 19 | Ghostscript/ImageMagick resource folders |
| 7 | `fonts/` |
| <5 | pgxs, pkgconfig, the stdlib `test`/`idlelib`/`tkinter`, and libvips' optional modules (heif, jxl, magick, openslide, poppler) |

Not pruned: `cv2.pyd` (71 MB), scipy (135 MB including `.libs`) and ICU (`icudt78.dll`, 32 MB, which Postgres links). A PostGIS build would need `GDAL_DATA`/`PROJ_DATA` kept, so prune `share/` per folder rather than all of it.

### Gotchas the launcher and build must handle
1. **pyvips.** pip's `pyvips==2.2.3` replaces the conda package. It then looks for `libvips-42.dll`, but conda-forge names the DLL `vips-42.dll`, so `import pyvips` fails. conda's `pyvips 3.2.0` loads in API mode. The app's exact `write_dzi` call (`new_from_memory` → `dzsave(layout="dz", suffix=".jpg[Q=88,strip]")` with the `eval` progress signal) works on 3.2.0.
2. **Kerberos in libpq: set `PGGSSENCMODE=disable` for every child.**
   - libpq defaults to `gssencmode=prefer`. The first `psql` connection started krb5's `ccapiserver.exe`, which kept running after psql exited and kept the env folder locked, so the folder could not be renamed. That would break updates and uninstall.
   - If `ccapiserver.exe` is pruned, the same probe crashes `psql`, `pg_isready` and `pg_dump` with 0xC0000005.
   - With `PGGSSENCMODE=disable` everything works. `api/admin.py`'s `pg_dump`/`psql` inherit it from the environment. asyncpg is unaffected, and psycopg2-binary bundles its own libpq.
   - Prune `ccapiserver.exe` as well.
3. **Don't pipe `pg_ctl start` output.** The postmaster inherits pg_ctl's stdout and stderr. If they are pipes, pg_ctl appears to hang after "server started" because the pipe never closes. Use `-l <logfile>` and send pg_ctl's own output to a file or DEVNULL.
4. **Leftover Postgres processes.**
   - Killing the postmaster without its children left a PG 18 `io_worker` holding the listening socket and the shared memory. The next start then failed with "pre-existing shared memory block is still in use".
   - While it was stuck, clients connected over TCP but never got a reply, so `psql` hung.
   - The Job Object (`KILL_ON_JOB_CLOSE`) covers this when the launcher dies. Before `pg_ctl start`, the launcher should also kill any `postgres.exe` whose image path is under the runtime folder, not only check `postmaster.pid`.
5. **pip entry-point `.exe` files embed the build path.** The launcher already runs `python -m celery` and should keep doing so.
6. **`PATH` must include `Library/bin`.** Otherwise libvips logs module-load warnings. The launcher already sets it.
7. **kombu resolved to 5.6.2** (Celery 5.3.6 allows anything below 6), the same as the unpinned Docker image. Pin it if reproducible builds matter.

### Windows Redis comparison
| | redis-windows MSYS2 8.10.2 | redis-windows Cygwin 8.10.2 | Microsoft Garnet 2.2.4 |
|---|---|---|---|
| Licence | Redis 8 is RSALv2 / SSPLv1 / **AGPLv3**: use AGPLv3, the same licence as AstroCat. `msys-2.0.dll` is the Cygwin-derived runtime (LGPLv3), OpenSSL is Apache-2.0 and the build scripts are Apache-2.0. | Same, with `cygwin1.dll` | MIT |
| Size | 13.9 MB zip; 17 MB of needed files | 14.8 MB zip; 8 DLLs | 26 MB zip, 81 MB unpacked, **plus a .NET 10 runtime** |
| Start and stop | `redis-server.exe --port P --bind 127.0.0.1 --dir "<native Windows path, spaces OK>" --appendonly yes --appendfsync everysec --save ""`. Ready in 0.2 s. `SHUTDOWN` exits 0. | Same; ready in 0.26 s | `GarnetServer.exe --port --bind`. The release zip is framework-dependent and fails with "You must install or update .NET". |
| Tests | All pass | All pass | Not run |

- **Why not Garnet.** A self-contained Garnet means building from source with `dotnet publish --self-contained` and the .NET 10 SDK in CI, which makes it larger. kombu compatibility stays unproven. Neither Redis build has that cost.
- **Command checks** (redis-py 5.0.1, on both Redis builds):
  - SET NX EX
  - ZADD / ZRANGEBYSCORE (with LIMIT) / ZREM / ZCARD / ZPOPMIN / ZREMRANGEBYSCORE / ZRANGE
  - HSET / HINCRBY / HGET / HGETALL / HDEL
  - SCAN MATCH COUNT, MGET, EXISTS, LPUSH / LTRIM / LRANGE, LLEN
  - pipeline / MULTI
  - PUBLISH / SUBSCRIBE
  - EXPIRE / PERSIST / SETEX expiry
  - `lock()`: acquire, contend, extend and release (Lua EVALSHA)
  - `INFO memory`, BGREWRITEAOF
- **AOF.** AOF data survived a hard kill of `redis-server`.
- **Log warnings.**
  - "Redis does not require authentication": any local process can use the loopback port. Consider `--requirepass` with a password from `config.json`; the broker URLs would then carry it.
  - "Can't set maximum open files": harmless.

### Celery on Windows (5.3.6, kombu 5.6.2, `worker.py` settings: `acks_late`, `reject_on_worker_lost`, prefetch 1, Redis result backend)
- **main** `--pool=threads --concurrency=4 -Q indexer,thumbnails,celery`: 20 tasks ran on all 4 threads, and each did SET NX EX plus `lock()` with a shared counter. The counter came out exactly right.
- **quality** and **fullres** `--pool=solo`: the sep and pyvips tasks ran.
- **beat** as its own process works. `worker -B` exits with "-B option does not work on Windows. Please run celery beat as a separate service", so beat stays a separate child, as planned.
- `celery inspect ping` reaches all three workers. `LLEN <queue>` counts waiting messages.
- **Crash recovery needs a launcher step.**
  - Windows workers use kombu's synchronous loop. Its `restore_visible` runs only when a poll returns nothing. BRPOP's 1 s timeout reply always counts as an event, so the restore effectively happens only when a worker starts, and only for messages older than `visibility_timeout`, which defaults to 1 hour.
  - In the test, a task orphaned by hard-killing its solo worker came back only when an unrelated worker started, about 105 s later.
  - `--without-gossip --without-mingle --without-heartbeat` did not change this.
  - **Fix (tested).** Before starting any worker, the launcher restores every unacked message; nothing is running at that point, so they are all orphans:
    ```python
    with Connection(broker_url, transport_options={"visibility_timeout": 0}) as c:
        c.default_channel.qos.restore_visible(num=None, interval=1)
    ```
    The orphaned task went back on its queue and finished 4.2 s after the worker started.
- **Graceful stop.**
  - `CTRL_BREAK_EVENT` kills a worker with no warm shutdown (exit 0xC000013A).
  - `celery -A app.worker control shutdown -d <name>@<host>` finished the running task and exited with code 0 in about 3.7 s.
  - The launcher should stop workers this way, then fall back to the Job Object kill after a timeout.
- **Time limits.** `task_time_limit` and `task_soft_time_limit` were not exercised. Celery enforces them only with prefork, so expect no per-task timeout on Windows.

### macOS arm64 (checked with the solver and resolver only)
- **conda-forge osx-arm64 has every native package:**
  - python 3.12, postgresql 18, libvips 8.18, pyvips 3.2
  - postgis 3.6.4, the only series available
  - redis-server 8.10.2; 7.4.7 also exists, and 7.2.5 is the last BSD-licensed build
  - **Valkey is not on conda-forge.**
- **PyPI** has cp312 arm64 wheels for every pinned requirement. Building from source isn't needed.
- **Minimum macOS.**
  - `scipy==1.13.1` needs **macOS 12**; with macOS 11 the resolve fails.
  - conda-forge's newer `postgresql` 18.x osx-arm64 builds, including every 18.6 build, need `__osx >= 13.3`.
  - On a `macos-14` runner the solver picks those, which would quietly raise the floor above the "macOS 12+" this doc promises.
  - So either set `CONDA_OVERRIDE_OSX=12.0` in CI (which gives postgresql 18.4 with PostGIS 3.6.4), or declare macOS 13.3+.
- **Relocation:** see "Relocation (macOS)" in the outcome table. A moved conda Postgres fails, so it runs through a symlink at its build path.

### Blocker: PostGIS on Windows. Options
1. **Postgres from EDB's Windows binaries zip, plus the OSGeo PostGIS bundle for PG 18 / PostGIS 3.6 (postgis.net), on Windows only.** conda still provides Python and libvips. This is the supported combination and the recommended next spike. It wasn't tried because neither download comes from the sources allowed for this spike. The spike would check:
   - DLL search order between the bundle's GDAL/PROJ and conda's (Postgres is its own process, so `PATH` order is what matters)
   - whether the MSVC runtime comes from the conda env
   - licence: PostGIS is GPL-2.0-or-later and ships as a separate program
2. **conda-forge Postgres 18 with the OSGeo PostGIS DLLs and extension files copied in.** Fewer moving parts, but it mixes an EDB-built extension with a conda-forge server, so the ABI is a risk.
3. **Build PostGIS for win-64 ourselves**, since the conda-forge feedstock has no Windows build. Days of work plus upkeep.
4. **Replace PostGIS with `earthdistance`/`cube`** (contrib, which already ships with conda's `postgresql`). This goes against "keep the SQL as it is" and touches about 20 files and both platforms.

### Changes this implies: applied (uncommitted, 2026-10-27)
The build/launcher session made these changes. This record comes from its report; the spike session has not re-read the code.
- `desktop/runtime/environment.yml` is the proposed file above.
- `build_runtime.py`:
  - drops the pyvips pin from the pip install
  - no longer passes `redis-server` as an extra argument, because it comes from the env file
  - copies only the 6 needed redis-windows files
  - prunes with the rules above, including the pefile DLL-closure pass, keeping `astropy/tests` and removing `ccapiserver.exe`
- Launcher:
  - `PGGSSENCMODE=disable` for every child
  - pg_ctl start and stop output goes to `logs/pg_ctl.log`, not to pipes
  - `kill_strays(sys.prefix, "postgres")` runs before start
  - unacked Celery messages are restored before workers start (tested against a throwaway Redis)
  - workers stop with `celery control shutdown` (waits up to 60 s, then terminates)
  - Redis runs with `--requirepass`, using a hex `redis_password` from `config.json` that the broker URLs carry
  - `PG_MAJOR` is 18
- CI:
  - `CONDA_OVERRIDE_OSX=12.0` on the runtime build step
  - pefile installed for the prune
  - still to do by hand: set the repository variables `WINDOWS_REDIS_ZIP_URL` and `WINDOWS_REDIS_ZIP_SHA256` to the values above
- **Windows PostGIS: adopted** (option 1, from the spike below). The pieces:
  - `build_runtime.build_pgsql` merges EDB's `bin`/`lib`/`share` with PostGIS core:
    - it drops locale, pgxs, static libs, extra tools and the PostGIS upgrade scripts from before 3.6
    - it prunes `pgsql/bin` by DLL closure and adds the MSVC runtime
    - it ships `PostGIS-COPYING.txt`, EDB's licence files and `desktop/runtime/THIRD_PARTY_NOTICES.md` under `<runtime>/licenses/`
  - `environment.yml` has conda Postgres on macOS only.
  - The launcher passes `PG_BIN_DIR` and runs the tools by absolute path, and sets `PROJ_LIB`/`PROJ_DATA` plus `PROJ_USER_WRITABLE_DIRECTORY`.
  - `api/admin.py` backup/restore uses `settings.pg_bin_dir`.
  - The database is created with `psql`, because `createdb` isn't shipped.
  - CI fetches both zips pinned by SHA-256, falling back to OSGeo's `archive/` URL once 3.6.3 ships.
- **Found by the Windows smoke run and fixed:**
  - `GetCurrentProcess()`'s pseudo-handle overflowed ctypes. All Win32 signatures are now declared.
  - `celery control shutdown` exits 69 because workers don't reply, which skipped the graceful wait.

### Windows PostGIS spike (option 1: EDB Postgres 18 + OSGeo PostGIS 3.6), 2026-10-27
**Status: done. GO.** Every static and runtime check passed on the pruned tree, which is exactly what would ship. The user approved running the downloaded binaries.

**Downloads**
| | URL | Size | Hash |
|---|---|---|---|
| EDB PostgreSQL 18.6-5, Windows x64 binaries | `https://get.enterprisedb.com/postgresql/postgresql-18.6-5-windows-x64-binaries.zip` | 384,620,317 B | SHA-256 `e2246ba91d22345bc3d017586c09ede52d9df180b1eeb480f050445f1cad84e2` (matches EDB's `<url>.sha256`; MD5 `a396b007…6c6e3` matches `<url>.md5`) |
| OSGeo PostGIS bundle 3.6.2 for PG 18 | `https://download.osgeo.org/postgis/windows/pg18/postgis-bundle-pg18-3.6.2x64.zip` | 118.3 MiB | OSGeo publishes **MD5 only**: `9e28723541938d1b1a8efb59a5922741` (matches). SHA-256 computed here: `0f41241cc536f7404dda43fd2a3f20ffe1fa1d71a8d4f6341428cd25931bf419` |

- The EDB link comes from the "Binaries from installer Version 18.6" row of the binaries page (`sbp.enterprisedb.com/getfile.jsp?fileid=1260609`, which redirects to the URL above).
- **OSGeo moves superseded bundles into `…/pg18/archive/`** (3.6.1 is already there), so a pinned URL breaks when 3.6.3 ships. CI should pin the SHA-256 and either re-upload the zip as a release asset or fall back to the `archive/` URL.
- The OSGeo bundle is PostGIS 3.6.2, while conda-forge on macOS has 3.6.4. That's the same minor series, and the drift is harmless.

**Layout used** (as the runtime would ship it)
```
runtime/                 conda env: python 3.12, libvips 8.18, pyvips 3.2 (no conda postgresql) + pip requirements
runtime/pgsql/           EDB pgsql/{bin,lib,share} + PostGIS core from the bundle (pgAdmin 4, StackBuilder, doc, include dropped)
```
- When a file name existed in both zips, EDB's copy was kept: `libcrypto-3-x64`, `libssl-3-x64`, `libcurl`, `libiconv-2`, `liblz4`, `libzstd` and `zlib1`.
- A PE export/import check confirmed that every symbol the bundle's DLLs import from those (PROJ→libcurl; libtiff→zstd, zlib; libxml2-2→iconv, zlib) exists in EDB's copy.
- `postgis-3.dll` imports only `libgeos_c`, `libproj_8_2`, `libprotobuf-c-1`, `libxml2-2`, `libstdc++-6`, `libwinpthread-1`, `msvcrt` and `postgres.exe`. It doesn't link GDAL, so raster support and GDAL can be dropped.

**DLL isolation (checked statically and confirmed at runtime)**
- PG 18 loads extensions with plain `LoadLibrary(<full path>)` (`src/port/win32dlopen.c`). Windows' standard search order then resolves their dependencies from **the exe's folder (`pgsql/bin`) first**, then System32, and PATH comes last.
- Walking the import tables of all kept exes and every kept `lib/*.dll`, everything resolves inside `pgsql/` or System32. **Nothing needs PATH.** The conda env has 8 DLLs with the same names: OpenSSL, curl, libxml2, zstd, zlib, and different MinGW builds of `libgcc_s_seh-1` and `libwinpthread-1`. Under this order they can't shadow EDB's copies.
- **The MSVC runtime is not in EDB's zip.** `vcruntime140`, `vcruntime140_1` and `msvcp140` resolved from System32 only because this machine has the VC++ redistributable. **Ship them in `pgsql/bin`.** The conda env's `vc14_runtime` 14.51 copies at the env root work; the pruned tree includes them. That way Postgres doesn't depend on PATH or a redistributable install. The UCRT (`api-ms-win-crt-*`) is part of Windows 10/11.
- `pgsql/bin` doesn't need to be on anyone's PATH. The launcher and `api/admin.py` should call `pgsql/bin/*.exe` by absolute path (`settings.pg_bin_dir`). Keep conda's dirs first on PATH for Python children so their `.pyd` dependencies stay conda's.
- **At runtime**, the backend process that had just run PostGIS, PROJ, GEOS and `pg_trgm` had 60 modules loaded:
  - **29 from `pgsql/`**, including the MSVC runtime copies, ICU 77, GEOS, PROJ 8.2, OpenSSL, curl, libxml2 and zstd
  - **none from the conda env**, even with the conda dirs first on PATH
  - the rest from Windows

**Environment variables for the postgres process**
- **`PROJ_LIB=<runtime>/pgsql/share/contrib/postgis-3.6/proj`**. The bundle's PROJ is **8.2.1**, which reads `PROJ_LIB`. `PROJ_DATA` only arrived in PROJ 9.1, so set both if the launcher shares code with macOS (PROJ 9.x).
  - Only `proj.db` (7.9 MB) is needed. The other 205 files in that folder (62 MB) are datum grids for transformations the app never does.
  - With `PROJ_LIB` set, `PostGIS_Full_Version()` reports `DATABASE_PATH=…\pgsql\share\contrib\postgis-3.6\proj\proj.db`.
  - **Without it**, PROJ finds no `proj.db`. The cone search, `ST_Distance`, `ST_Intersects` and `ST_Transform(4326→3857)` still return the same results, because PostGIS falls back to the `spatial_ref_sys` definitions. Set it anyway, so EPSG lookups go through `proj.db`.
  - PROJ reports `USER_WRITABLE_DIRECTORY=%LOCALAPPDATA%\proj`. It didn't create that folder (no network grids were used), but setting **`PROJ_USER_WRITABLE_DIRECTORY=<data>/temp/proj`** keeps it out of the user profile.
- **`GDAL_DATA`: not needed**, because GDAL isn't shipped.
- **`PGGSSENCMODE`: not needed for EDB's tools (confirmed).** EDB's `libpq.dll` uses Windows SSPI (`secur32`): it has no `gss_init_sec_context` and no krb5 DLLs.
  - With the variable unset, EDB's `psql`, `pg_isready`, `pg_dump` and `pg_restore` all worked, and no `ccapiserver.exe` was started.
  - psycopg2-binary bundles its own libpq.
  - Keep `PGGSSENCMODE=disable` set anyway; it's free.

**Pruned `pgsql/` (115 MB on disk, 26.1 MB as tar.xz -6)**
- `bin/`: postgres, initdb, pg_ctl, pg_dump, pg_restore, psql, pg_isready and pg_upgrade, plus the 28 DLLs they and the kept modules need: ICU 77 (36 MB), GEOS, PROJ 8.2, protobuf-c, libxml2/libxml2-2, libxslt, OpenSSL, curl, sqlite, libtiff, lzma, lz4, zstd, zlib, iconv, intl, libstdc++, libgcc_s and winpthread. Add the 3 MSVC runtime DLLs.
- `lib/`: EDB's 91 module DLLs (pg_trgm, plpgsql and the rest of contrib) plus `postgis-3.dll`. `*.lib`, pgxs and pkgconfig are dropped.
- `share/`: EDB's `share` without `locale/` (24 MB of translations), plus `extension/postgis.control`, `postgis--3.6.2.sql`, `postgis--ANY--3.6.2.sql` and `postgis--3.6.{0,1}--ANY.sql`, plus `contrib/postgis-3.6/proj/proj.db`.
- Dropped from the bundle (1,055 extension files and 13 module DLLs): postgis_raster (and GDAL, 36 MB), sfcgal (CGAL, 12 MB), topology, tiger geocoder, address_standardizer, pgRouting, MobilityDB, h3, pointcloud, ogr_fdw, pg_sphere and the loader tools.
- For comparison: the EDB zip unpacks to 1,010 MiB (pgAdmin 4 alone is 813 MiB); merged with the full bundle and with pgAdmin, StackBuilder, doc and include dropped, it is 427 MiB.

**Licences**
- **PostgreSQL** (EDB build): PostgreSQL Licence, `server_license.txt`. EDB's `commandlinetools_3rd_party_licenses.txt` covers its OpenSSL, ICU, zlib, libxml2, libxslt, gettext, iconv, lz4 and zstd.
- **PostGIS: GPL-2.0-or-later.** The text is the bundle's `bin/COPYING`. The bundle's root `LICENSE`/`README.md` are **h3-pg's (Apache-2.0)**, so don't ship those as "the PostGIS licence". PostGIS runs inside the separate postgres process and AstroCat only talks SQL to it, so it's aggregation, not linking. GPL-2.0+ is also compatible with AstroCat's AGPL-3.0. Offer the source (3.6.2 tag).
- **Not covered by any shipped notice, so add our own THIRD_PARTY file:**
  - GEOS 3.14: LGPL-2.1+, dynamically linked, so ship its notice and a source offer
  - PROJ 8.2.1: MIT
  - protobuf-c: BSD-2
  - libxml2-2: MIT
  - libtiff: libtiff licence
  - sqlite: public domain
  - liblzma: 0BSD
  - curl: curl licence
  - libstdc++ / libgcc_s: GPL-3 with the GCC Runtime Library Exception
  - winpthreads: MIT/ZPL
- **MSVC runtime:** redistributable under the Visual Studio licence; it's the conda-forge `vc14_runtime` files.

**Runtime results**
All runs used the pruned tree, `scripts/pgis_test.py` in the spike scratch folder, and the launcher's PATH order (conda dirs first, `pgsql/bin` last).

| Check | Result |
|---|---|
| 1. `initdb` (scram-sha-256, `--no-locale`, UTF8) → `pg_ctl start -l <file> -w` (0.3 s) → `CREATE EXTENSION postgis`, `pg_trgm` | Pass. Extensions: pg_trgm 1.6, plpgsql 1.0, postgis 3.6.2 |
| `PostGIS_Full_Version()` | `POSTGIS="3.6.2" PGSQL="180" GEOS="3.14.1dev-CAPI-1.20.4" PROJ="8.2.1 NETWORK_ENABLED=OFF …" LIBXML="2.12.5" LIBJSON="0.12" LIBPROTOBUF="1.2.1" WAGYU="0.5.0"` |
| 2. The app's cone search (`ST_DWithin(center_location, ST_SetSRID(ST_MakePoint(ra - 180, dec), 4326)::geography, r)`), 1° around M31 | M31 at 0.0000° and M32 at 0.4029° (the real separation is about 0.40°); M33 and M42 excluded |
| RA=0 wrap (search at RA 0.05°, row at RA 359.90°, r = 0.5°) | Found |
| `ST_Intersects` on a geography polygon (`field_boundary`) | M42's field matched |
| GiST index on `center_location` | Used, per EXPLAIN |
| `ST_Transform` (PROJ) / `ST_Buffer` (GEOS) / `similarity()` (pg_trgm) | All work |
| 3. Loaded DLLs in the backend | 29 from `pgsql/`, 0 from conda |
| 4. `pg_dump -Fc` → `pg_restore -l` lists the PostGIS table; `psql` | Pass, both with and without `PGGSSENCMODE`; no `ccapiserver.exe` |
| 5. Relocation: runtime renamed to a path **with spaces**, then a fresh `initdb` and the existing cluster restarted | Pass. Python imports, pyvips dzsave, sep, cv2 and astropy also pass from the moved folder |
| Stop (`pg_ctl stop -m fast`) | Clean every time; no leftover processes |

**Go/no-go: GO for option 1.** On Windows, ship the pruned EDB 18.6 + OSGeo PostGIS 3.6.2 `pgsql/` next to the conda env. The build needs:
- **Downloads:** fetch both zips by the URLs above and verify the pinned SHA-256s. Mirror the OSGeo zip, because its URL moves when 3.6.3 ships.
- **Build:** generate the pruned tree (the spike's `prune_pgsql.py` rules). Copy `vcruntime140.dll`, `vcruntime140_1.dll` and `msvcp140.dll` into `pgsql/bin`. Ship PostGIS's `bin/COPYING` and a THIRD_PARTY notice file.
- **Launcher:**
  - point `pg_bin_dir` at `pgsql/bin` and call the tools by absolute path
  - set `PROJ_LIB` (and `PROJ_DATA`) to `pgsql/share/contrib/postgis-3.6/proj` and `PROJ_USER_WRITABLE_DIRECTORY` to the data folder
  - leave `pgsql/bin` off PATH
- **Env file:** drop conda's `postgresql` on Windows (`sel(win)` stays out of `environment.yml`, or prune it), so there is only one Postgres in the bundle.

About +26 MB compressed for Postgres and PostGIS on Windows.

## Estimates and recommended models for the remaining work
| Step | Effort | Tokens | Model |
|---|---|---|---|
| Phase 0 spikes | 0.5–1 day | ~150k | Opus 5.5 |
| First CI dry run and fixes (Rust compile, bundling, smoke) | 0.5–1 day, several CI iterations | ~150k | Sonnet 5.5 |
| Updater key and plugin | 0.25 day | ~40k | Sonnet 5.5 |
