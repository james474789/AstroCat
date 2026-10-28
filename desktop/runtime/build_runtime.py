"""
Build the self-contained runtime the desktop app ships (D1, docs/design/20261027-D1-desktop-apps.md).

    python desktop/runtime/build_runtime.py [--out DIR] [--redis-dir DIR]

Produces (default desktop/src-tauri/resources/runtime/):
    <python + Postgres(+PostGIS on macOS) + libvips from conda-forge, backend requirements from pip>
    backend/app, backend/alembic, backend/alembic.ini, backend/data, backend/VERSION
    backend/frontend_dist      the built frontend (run `npm run build` in frontend/ first)

The Tauri shell starts `<runtime>/python -m app.desktop.launcher` with cwd <runtime>/backend.
The env is created in place: the Phase 0 spike showed it runs from wherever it is installed
without conda-pack/conda-unpack.

Needs micromamba on PATH (or --micromamba). Windows also needs:
    --redis-dir     the unpacked redis-windows MSYS2 release (redis-server.exe and its msys DLLs)
    --edb-zip       EDB's PostgreSQL 18 Windows x64 binaries zip
    --postgis-zip   OSGeo's PostGIS bundle for PG 18
conda-forge has no Windows PostGIS, so on Windows Postgres + PostGIS ship as <runtime>/pgsql
(EDB's server with PostGIS merged in), separate from the conda env; see "Windows PostGIS spike"
in the design doc. The DLL-closure prunes need `pefile` in the Python running this script
(`pip install pefile`); without it they are skipped.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IS_WINDOWS = sys.platform == "win32"
DEFAULT_OUT = REPO / "desktop" / "src-tauri" / "resources" / "runtime"

# conda-forge's Postgres can't be moved: its share/lib/zoneinfo paths are compiled in, and a
# copy run from anywhere else fails (initdb: "postgres.bki does not exist"). So on macOS the env
# is built at a fixed path that exists on every Mac, then moved into the app bundle; the
# launcher points a symlink from this path to wherever the app's runtime is at each start.
# BUILD_PREFIX_FILE records it for the launcher (app.desktop.launcher.ensure_build_prefix_link).
MACOS_BUILD_PREFIX = Path("/Users/Shared/AstroCat/runtime")
BUILD_PREFIX_FILE = ".build-prefix"

# conda's pyvips is the one that finds conda's libvips (vips-42.dll); pip's pin would replace it
CONDA_PROVIDED = {"pyvips"}

# Postgres tools the launcher and api/admin.py use; other .exe files in Library/bin are dropped
PG_TOOLS = {"postgres", "initdb", "pg_ctl", "pg_dump", "pg_dumpall", "pg_restore", "psql",
            "pg_isready", "pg_controldata", "pg_resetwal", "pg_upgrade"}
REDIS_FILES = ["redis-server.exe", "redis-cli.exe", "msys-2.0.dll", "msys-crypto-3.dll",
               "msys-ssl-3.dll", "msys-gcc_s-seh-1.dll"]

# Not needed at run time; removing them keeps the installer smaller. share/ is pruned per
# folder because PostGIS needs share/proj and share/gdal, and Postgres its own share dir.
PRUNE_DIRS = [
    "include", "Include", "Library/include", "conda-meta",
    *(f"{base}/{d}" for base in ("share", "Library/share")
      for d in ("doc", "man", "info", "gtk-doc", "locale", "gir-1.0", "ghostscript")),
    "Lib/test", "Lib/idlelib", "Lib/tkinter",
    "lib/python3.12/test", "lib/python3.12/idlelib", "lib/python3.12/tkinter",
]
PRUNE_GLOBS = [
    "**/__pycache__", "**/*.a", "**/*.pdb", "Library/lib/*.lib", "libs/*.lib",
    "Library/lib/ImageMagick-*", "Library/etc/ImageMagick-*",
    "Scripts/*.exe",                      # pip entry points embed the build path; the launcher uses python -m
    "**/cv2/opencv_videoio_ffmpeg*",      # only cvtColor is used
    *(f"**/vips-modules-*/vips-{m}.dll" for m in ("heif", "jxl", "magick", "openslide", "poppler")),
    "Library/bin/ccapiserver.exe",        # Kerberos helper that outlives psql and locks the folder
]
KEEP_TESTS = {"astropy"}  # astropy/__init__ imports astropy.tests

# Windows pgsql/: the tools the launcher and api/admin.py run (by absolute path, never via PATH)
PGSQL_TOOLS = {"postgres", "initdb", "pg_ctl", "pg_dump", "pg_restore", "psql", "pg_isready", "pg_upgrade"}
# EDB's zip has no MSVC runtime; the conda env's vc14_runtime copies go next to postgres.exe
MSVC_RUNTIME = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"]
EDB_LICENSES = ["server_license.txt", "commandlinetools_3rd_party_licenses.txt"]
SMOKE_IMPORTS = "import astropy, rawpy, sep, cv2, numpy, scipy, asyncpg, psycopg2, celery, uvicorn, kombu, pyvips"


def run(*argv, **kw):
    print("+", " ".join(str(a) for a in argv), flush=True)
    subprocess.run([str(a) for a in argv], check=True, **kw)


def env_python(prefix: Path) -> Path:
    return prefix / "python.exe" if IS_WINDOWS else prefix / "bin" / "python3"


def path_dirs(prefix: Path) -> list[Path]:
    subdirs = ["", "Library/mingw-w64/bin", "Library/usr/bin", "Library/bin", "Scripts", "bin"] if IS_WINDOWS else ["bin"]
    return [prefix / s for s in subdirs]


def filtered_requirements(text: str) -> str:
    """backend/requirements.txt without the packages conda already provides."""
    keep = []
    for line in text.splitlines():
        name = line.split("#")[0].strip().split("[")[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<", "!="):
            name = name.split(sep)[0]
        if name.strip().lower() not in CONDA_PROVIDED:
            keep.append(line)
    return "\n".join(keep) + "\n"


def create_env(out: Path, micromamba: str):
    run(micromamba, "create", "-y", "-p", out, "-f", REPO / "desktop" / "runtime" / "environment.yml")
    reqs = filtered_requirements((REPO / "backend" / "requirements.txt").read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(reqs)
    try:
        run(env_python(out), "-m", "pip", "install", "--no-cache-dir", "--no-compile", "-r", f.name)
    finally:
        os.unlink(f.name)


def copy_app(out: Path):
    backend = out / "backend"
    if backend.exists():
        shutil.rmtree(backend)
    src = REPO / "backend"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests")
    for name in ("app", "alembic", "data"):
        shutil.copytree(src / name, backend / name, ignore=ignore)
    shutil.copy2(src / "alembic.ini", backend / "alembic.ini")
    shutil.copy2(REPO / "VERSION", backend / "VERSION")
    dist = REPO / "frontend" / "dist"
    if not (dist / "index.html").exists():
        sys.exit("frontend/dist is missing: run `npm run build` in frontend/ first")
    shutil.copytree(dist, backend / "frontend_dist")


def copy_redis(out: Path, redis_dir: Path | None):
    """Windows only: macOS gets redis-server from conda-forge (environment.yml)."""
    if not IS_WINDOWS:
        return
    if not redis_dir:
        sys.exit("Windows build needs --redis-dir (the unpacked redis-windows MSYS2 release)")
    for name in REDIS_FILES:
        src = redis_dir / name
        if not src.exists():
            sys.exit(f"{name} missing from {redis_dir}")
        shutil.copy2(src, out / name)  # runtime root is first on the launcher's PATH


def _unzip(zip_path: Path, dest: Path) -> Path:
    if zip_path.is_dir():  # already unpacked (local builds)
        return zip_path
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return dest


def _find_dir(root: Path, marker: str) -> Path:
    """The folder under root that contains ``marker`` (a relative path)."""
    for p in root.rglob(Path(marker).name):
        candidate = p
        for _ in Path(marker).parts:
            candidate = candidate.parent
        if (candidate / marker).exists():
            return candidate
    sys.exit(f"{marker} not found in {root}")


def postgis_extension_files(ext_dir: Path) -> list[Path]:
    """
    postgis.control, the install script for its default_version, and the upgrade paths from
    the same minor series; the 130-odd scripts for upgrading from PostGIS 2.x-3.5 aren't needed
    in a cluster that is always created with this version.
    """
    control = ext_dir / "postgis.control"
    version = next(line.split("=", 1)[1].strip().strip("'\"")
                   for line in control.read_text(encoding="utf-8").splitlines()
                   if line.strip().startswith("default_version"))
    minor = ".".join(version.split(".")[:2])
    keep = [control, ext_dir / f"postgis--{version}.sql", ext_dir / f"postgis--ANY--{version}.sql",
            *ext_dir.glob(f"postgis--{minor}.*--ANY.sql")]
    missing = [k.name for k in keep if not k.exists()]
    if missing:
        sys.exit(f"PostGIS bundle is missing {missing}")
    return keep


def build_pgsql(out: Path, edb_zip: Path | None, postgis_zip: Path | None):
    """
    Windows only: EDB's Postgres 18 with PostGIS core merged in, pruned to what AstroCat runs.
    Same-named DLLs keep EDB's copy (the spike checked every symbol PostGIS's DLLs need is in it).
    Raster/GDAL, sfcgal, topology, tiger, pgRouting, MobilityDB etc. are left out of the bundle.
    """
    if not IS_WINDOWS:
        return
    if not (edb_zip and postgis_zip):
        sys.exit("Windows build needs --edb-zip and --postgis-zip (see the design doc for the pinned files)")
    pgsql = out / "pgsql"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        edb = _find_dir(_unzip(edb_zip, tmp / "edb"), "bin/postgres.exe")
        bundle = _find_dir(_unzip(postgis_zip, tmp / "postgis"), "lib/postgis-3.dll")

        for sub_dir in ("bin", "lib", "share"):
            shutil.copytree(edb / sub_dir, pgsql / sub_dir,
                            ignore=shutil.ignore_patterns("locale", "pgxs", "pkgconfig", "*.lib", "*.a", "*.pdb"))
        for exe in (pgsql / "bin").glob("*.exe"):
            if exe.stem.lower() not in PGSQL_TOOLS:
                exe.unlink()

        for dll in (bundle / "bin").glob("*.dll"):
            if not (pgsql / "bin" / dll.name).exists():
                shutil.copy2(dll, pgsql / "bin" / dll.name)
        shutil.copy2(bundle / "lib" / "postgis-3.dll", pgsql / "lib" / "postgis-3.dll")
        ext = pgsql / "share" / "extension"
        for f in postgis_extension_files(bundle / "share" / "extension"):
            shutil.copy2(f, ext / f.name)
        proj_db = next((bundle / "share" / "contrib").glob("postgis-*/proj/proj.db"), None)
        if proj_db is None:
            sys.exit("proj.db missing from the PostGIS bundle")
        dest = pgsql / proj_db.relative_to(bundle)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(proj_db, dest)  # the 200+ datum grids next to it are never used

        licenses = out / "licenses"
        licenses.mkdir(exist_ok=True)
        # the bundle's root LICENSE is h3-pg's (Apache-2.0); PostGIS's GPL text is bin/COPYING
        shutil.copy2(bundle / "bin" / "COPYING", licenses / "PostGIS-COPYING.txt")
        for name in EDB_LICENSES:
            found = next(edb.parent.rglob(name), None)
            if found:
                shutil.copy2(found, licenses / f"PostgreSQL-EDB-{name}")

    roots = [*(pgsql / "bin").glob("*.exe"), *(pgsql / "lib").glob("*.dll")]
    prune_unreached_dlls(pgsql / "bin", roots, [pgsql / "bin"])
    for name in MSVC_RUNTIME:
        src = out / name
        if not src.exists():
            sys.exit(f"{name} missing from the conda env (vc14_runtime)")
        shutil.copy2(src, pgsql / "bin" / name)


def copy_notices(out: Path):
    licenses = out / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(REPO / "desktop" / "runtime" / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md")


def prune(out: Path):
    for rel in PRUNE_DIRS:
        shutil.rmtree(out / rel, ignore_errors=True)
    for pattern in PRUNE_GLOBS:
        for p in out.glob(pattern):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    for tests in out.glob("**/site-packages/*/tests"):
        if tests.parent.name not in KEEP_TESTS:
            shutil.rmtree(tests, ignore_errors=True)
    if IS_WINDOWS:
        bin_dir = out / "Library" / "bin"
        for exe in bin_dir.glob("*.exe"):
            if exe.stem.lower() not in PG_TOOLS:
                exe.unlink()
        prune_conda_dlls(out)


def prune_conda_dlls(out: Path):
    """
    conda-forge libvips drags in ImageMagick, Ghostscript, Poppler, librsvg, AV1/HEVC codecs
    and HDF5 that AstroCat never uses (~136 MB of Library/bin). Roots: every .pyd, the Python
    executables, Library/lib DLLs, vips-42.dll and the kept libvips modules; DLLs loaded by
    name at run time are all among them. (pgsql/ is separate and pruned in build_pgsql.)
    """
    bin_dir = out / "Library" / "bin"
    roots = [*(p for p in out.glob("**/*.pyd") if "pgsql" not in p.parts), *out.glob("*.exe"),
             *out.glob("python3*.dll"), *bin_dir.glob("*.exe"), *(out / "Library" / "lib").glob("*.dll"),
             *out.glob("**/vips-modules-*/*.dll")]
    if (bin_dir / "vips-42.dll").exists():
        roots.append(bin_dir / "vips-42.dll")
    search = [d for d in path_dirs(out) + [out / "DLLs"] if d.is_dir()]
    prune_unreached_dlls(bin_dir, roots, search)


def prune_unreached_dlls(bin_dir: Path, roots: list[Path], search: list[Path]):
    """Delete DLLs in ``bin_dir`` that no root reaches through imports or delay-imports."""
    try:
        import pefile
    except ImportError:
        print(f"pefile not installed: skipping the DLL-closure prune of {bin_dir}", flush=True)
        return

    index = {}
    for d in search:
        for dll in d.glob("*.dll"):
            index.setdefault(dll.name.lower(), dll)

    reached, stack = set(), list(roots)
    while stack:
        path = stack.pop()
        key = str(path).lower()
        if key in reached:
            continue
        reached.add(key)
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ])
        except pefile.PEFormatError:
            continue
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
            name = entry.dll.decode(errors="ignore").lower()
            dep = (path.parent / name) if (path.parent / name).exists() else index.get(name)
            if dep is not None:
                stack.append(dep)
        pe.close()

    removed = 0
    for dll in bin_dir.glob("*.dll"):
        if str(dll).lower() not in reached:
            removed += dll.stat().st_size
            dll.unlink()
    print(f"DLL-closure prune removed {removed / 1e6:.0f} MB from {bin_dir}", flush=True)


def smoke(out: Path):
    env = {**os.environ, "PATH": os.pathsep.join([str(d) for d in path_dirs(out)] + [os.environ.get("PATH", "")]),
           "PGGSSENCMODE": "disable"}
    run(env_python(out), "-c", SMOKE_IMPORTS, env=env)
    if not shutil.which("redis-server", path=env["PATH"]):
        sys.exit("redis-server missing from the runtime")
    pg_path = str(out / "pgsql" / "bin") if IS_WINDOWS else env["PATH"]
    for tool in ("initdb", "pg_ctl", "pg_dump", "psql"):
        if not shutil.which(tool, path=pg_path):
            sys.exit(f"{tool} missing from the runtime")
    if IS_WINDOWS and not (out / "pgsql" / "lib" / "postgis-3.dll").exists():
        sys.exit("PostGIS missing from pgsql/")


def size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--micromamba", default=shutil.which("micromamba") or "micromamba")
    parser.add_argument("--redis-dir", type=Path, default=None)
    parser.add_argument("--edb-zip", type=Path, default=None)
    parser.add_argument("--postgis-zip", type=Path, default=None)
    parser.add_argument("--app-only", action="store_true", help="refresh backend/frontend in an existing runtime")
    args = parser.parse_args()
    out = args.out.resolve()

    if not args.app_only:
        if out.exists():
            shutil.rmtree(out)
        if sys.platform == "darwin":
            build_at = MACOS_BUILD_PREFIX
            if build_at.is_symlink() or build_at.is_file():
                build_at.unlink()
            elif build_at.exists():
                shutil.rmtree(build_at)
            build_at.parent.mkdir(parents=True, exist_ok=True)
            create_env(build_at, args.micromamba)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(build_at), str(out))
            (out / BUILD_PREFIX_FILE).write_text(str(build_at), encoding="utf-8")
        else:
            create_env(out, args.micromamba)
        copy_redis(out, args.redis_dir)
        prune(out)
        build_pgsql(out, args.edb_zip, args.postgis_zip)
        copy_notices(out)
        smoke(out)
    copy_app(out)
    print(f"runtime ready: {out} ({size_mb(out):.0f} MB)")


if __name__ == "__main__":
    main()
