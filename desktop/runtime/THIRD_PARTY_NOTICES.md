# Third-party software in the AstroCat desktop app

AstroCat is AGPL-3.0. The desktop app bundles the separate programs and libraries listed below.
Each one stays under its own licence. Licence texts that ship with a component sit next to this
file in `licenses/`, or with that component's Python package (`*.dist-info`).

## Database server (separate processes; AstroCat talks to them only over the network)
| Component | Version | Licence | Notes |
|---|---|---|---|
| PostgreSQL | 18 | PostgreSQL Licence | Windows: the EDB binaries (`PostgreSQL-EDB-server_license.txt`). macOS: conda-forge. |
| PostGIS | 3.6 | GPL-2.0-or-later | `PostGIS-COPYING.txt`. Source: https://download.osgeo.org/postgis/source/ (tag 3.6.2 on Windows) |
| GEOS | 3.14 | LGPL-2.1-or-later | Dynamically linked by PostGIS. Source: https://libgeos.org/usage/download/ |
| PROJ | 8.2 (Windows), 9.x (macOS) | MIT | |
| protobuf-c | 1.x | BSD-2-Clause | |
| libxml2, libxslt | 2.x | MIT | |
| libtiff | 4.x | libtiff licence | |
| SQLite | 3.x | Public domain | |
| liblzma (xz) | 5.x | 0BSD | |
| curl | 8.x | curl licence | |
| OpenSSL, ICU, zlib, gettext, iconv, lz4, zstd | | see `PostgreSQL-EDB-commandlinetools_3rd_party_licenses.txt` (Windows) | |
| libstdc++, libgcc_s | | GPL-3.0 with the GCC Runtime Library Exception | MinGW runtime used by the PostGIS bundle |
| winpthreads | | MIT / ZPL-2.1 | |
| Microsoft Visual C++ runtime | 14.x | Visual Studio redistributable licence | `vcruntime140*.dll`, `msvcp140.dll` (conda-forge `vc14_runtime`) |

## Cache / message broker (separate process)
| Component | Version | Licence | Notes |
|---|---|---|---|
| Redis | 8.10 | AGPL-3.0 (chosen from RSALv2 / SSPLv1 / AGPLv3) | Source: https://github.com/redis/redis. Windows build: https://github.com/redis-windows/redis-windows (Apache-2.0 build scripts) |
| MSYS2 runtime (`msys-2.0.dll`) | | LGPL-3.0 (Cygwin-derived) | Windows only |

## Python runtime and libraries
| Component | Licence |
|---|---|
| CPython 3.12 | PSF-2.0 |
| libvips 8.18, pyvips | LGPL-2.1-or-later, MIT |
| NumPy, SciPy, Astropy, SEP, OpenCV (headless), Pillow, tifffile, rawpy (LibRaw) | BSD / Apache-2.0 / MIT / HPND / LGPL-2.1 or CDDL-1.0 (LibRaw) |
| FastAPI, Starlette, Uvicorn, SQLAlchemy, Alembic, GeoAlchemy2, asyncpg, psycopg2, Celery, kombu, redis-py, pydantic, httpx and the other packages in `backend/requirements.txt` | MIT / BSD / Apache-2.0 / LGPL-3.0 (psycopg2) |

## Desktop shell
| Component | Licence |
|---|---|
| Tauri 2 and its plugins | MIT or Apache-2.0 |
| WebView2 (Windows) / WKWebView (macOS) | Provided by the operating system |
