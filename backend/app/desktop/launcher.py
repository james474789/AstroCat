"""
AstroCat desktop launcher: ``python -m app.desktop.launcher``.

Starts, in order: Postgres -> Redis -> the compose init scripts -> uvicorn, three Celery
workers and beat (the processes supervisord.conf runs in the container). Then it prints one
JSON line on stdout for the Tauri shell:

    {"event": "ready", "url": "http://127.0.0.1:<port>", "login_url": ".../api/auth/desktop-login?nonce=..."}

stdout carries only JSON event lines (status / ready / error); logs go to <data>/logs.
stdin takes commands: "restart" (re-read config.json, e.g. new library folders) and "quit".
EOF on stdin (the shell went away) also shuts everything down.

--headless runs without a shell (Ctrl+C to stop); --smoke starts, checks, and stops (CI).
"""

import argparse
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from app.desktop.paths import DesktopPaths, default_data_root, load_config, save_config
from app.desktop.postgres import Postgres, PostgresError
from app.desktop.supervisor import Child, Supervisor, kill_children_with_launcher, kill_strays, reap_orphans

logger = logging.getLogger("astrocat.desktop")

IS_WINDOWS = sys.platform == "win32"
BACKEND_ROOT = Path(__file__).resolve().parents[2]  # folder holding app/ and alembic.ini
FRONTEND_PORT_DOCKER = 8090  # client.js treats a page on this port as the split nginx setup
DESKTOP_SESSION_S = 30 * 24 * 3600
BACKUP_EVERY_S = 24 * 3600
APP_CHILDREN = ("uvicorn", "celery_worker", "celery_fullres", "celery_quality", "celery_beat")
WORKER_STOP_S = 60  # warm shutdown lets a running task finish; then the Job Object / kill
# Re-queue every unacked task before any worker starts (nothing is running, so all of them are
# orphans of a crash). kombu's synchronous loop, used on Windows, otherwise restores them only
# after visibility_timeout (1 h). Runs in the app's Python, which has kombu.
RESTORE_UNACKED = (
    "import os\n"
    "from kombu import Connection\n"
    "with Connection(os.environ['CELERY_BROKER_URL'], transport_options={'visibility_timeout': 0}) as c:\n"
    "    c.default_channel.qos.restore_visible(num=None, interval=1)\n"
)
# env-only integration keys (config.py) a desktop user can put under "api_keys" in config.json
API_KEY_VARS = ("ASTROMETRY_API_KEY", "LOCAL_ASTROMETRY_URL", "LOCAL_ASTROMETRY_API_KEY",
                "TELESCOPIUS_API_KEY", "METEOBLUE_API_KEY")


def emit(event: str, **fields):
    """One JSON line on stdout for the Tauri shell."""
    sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
    sys.stdout.flush()


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port != FRONTEND_PORT_DOCKER:
            return port


def runtime_path_dirs(prefix: Path) -> list[str]:
    """Where a conda-pack runtime keeps executables and DLLs (no conda activation happens)."""
    if IS_WINDOWS:
        subdirs = ["", "Library/mingw-w64/bin", "Library/usr/bin", "Library/bin", "Scripts", "bin"]
    else:
        subdirs = ["bin"]
    return [str(prefix / s) for s in subdirs]


BUILD_PREFIX_FILE = ".build-prefix"  # written by desktop/runtime/build_runtime.py (macOS)


def ensure_build_prefix_link(prefix: Path) -> Path | None:
    """
    macOS: conda-forge's Postgres (and some other conda binaries) only work from the path the
    runtime was built at, so point a symlink from there to this runtime. Returns the link, or
    None when the runtime records no build prefix (Windows, or a dev env used in place).
    """
    marker = prefix / BUILD_PREFIX_FILE
    if not marker.is_file():
        return None
    link = Path(marker.read_text(encoding="utf-8").strip())
    target = prefix.resolve()
    if link.is_symlink():
        if Path(os.readlink(link)) == target or link.resolve() == target:
            return link
    elif link.exists():
        if link.resolve() == target:
            return link  # the runtime really lives at its build path
        raise RuntimeError(f"{link} exists and is not AstroCat's runtime link; move it aside and restart")
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}")
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, tmp, target_is_directory=True)
    os.replace(tmp, link)  # atomic swap if an old link points at a previous install
    logger.info("linked %s -> %s", link, target)
    return link


def pgsql_bin(prefix: Path) -> Path | None:
    """Windows: EDB's Postgres + PostGIS live in <runtime>/pgsql, apart from the conda env."""
    bin_dir = prefix / "pgsql" / "bin"
    return bin_dir if bin_dir.is_dir() else None


def proj_dir(prefix: Path) -> Path | None:
    """Where PostGIS's PROJ finds proj.db: the OSGeo bundle's copy, else conda's share/proj."""
    bundled = sorted((prefix / "pgsql" / "share" / "contrib").glob("postgis-*/proj"))
    if bundled:
        return bundled[-1]
    conda = prefix / ("Library/share" if IS_WINDOWS else "share") / "proj"
    return conda if conda.is_dir() else None


def frontend_dist() -> str:
    """Built frontend shipped next to the backend in the runtime (frontend/dist in a checkout)."""
    for candidate in (BACKEND_ROOT / "frontend_dist", BACKEND_ROOT.parent / "frontend" / "dist"):
        if (candidate / "index.html").exists():
            return str(candidate)
    return ""


def build_env(paths: DesktopPaths, cfg: dict, *, pg_url: str, redis_port: int, nonce: str,
              prefix: Path | None = None) -> dict:
    """The environment docker-compose.yml gives the backend container, pointed at local paths."""
    env = dict(os.environ)
    prefix = prefix or Path(sys.prefix)
    # conda's dirs only: pgsql/bin stays off PATH so Python's .pyd files keep conda's DLLs
    # (postgres resolves its own from its exe folder first)
    env["PATH"] = os.pathsep.join(runtime_path_dirs(prefix) + [env.get("PATH", "")])
    pg_bin = pgsql_bin(prefix)
    if pg_bin:
        env["PG_BIN_DIR"] = str(pg_bin)  # api/admin.py backup/restore
    # what conda activation would export: PostGIS finds proj.db / GDAL data through these.
    # PROJ 8 (Windows bundle) reads PROJ_LIB, PROJ 9.1+ (macOS) PROJ_DATA.
    proj = proj_dir(prefix)
    if proj:
        env["PROJ_DATA"] = env["PROJ_LIB"] = str(proj)
        env["PROJ_USER_WRITABLE_DIRECTORY"] = str(paths.temp / "proj")
    gdal = prefix / ("Library/share" if IS_WINDOWS else "share") / "gdal"
    if gdal.is_dir():
        env["GDAL_DATA"] = str(gdal)
    # libpq's default gssencmode=prefer starts Kerberos' ccapiserver.exe on Windows, which outlives
    # psql/pg_dump and keeps the runtime folder locked (breaks updates and uninstall)
    env["PGGSSENCMODE"] = "disable"
    redis = f"redis://:{cfg['redis_password']}@127.0.0.1:{redis_port}"
    env.update({
        "DATABASE_URL": pg_url,
        "REDIS_URL": f"{redis}/0",
        "CELERY_BROKER_URL": f"{redis}/0",
        "CELERY_RESULT_BACKEND": f"{redis}/1",
        "SECRET_KEY": cfg["secret_key"],
        "COOKIE_SECURE": "false",
        "COOKIE_MAX_AGE": str(DESKTOP_SESSION_S),
        "CSRF_COOKIE_MAX_AGE": str(DESKTOP_SESSION_S),
        "ACCESS_TOKEN_EXPIRE_MINUTES": str(DESKTOP_SESSION_S // 60),
        "IMAGE_PATHS": ",".join(cfg.get("image_paths") or []),
        "THUMBNAIL_CACHE_PATH": str(paths.thumbnails),
        "FULLRES_CACHE_PATH": str(paths.fullres),
        "LOG_DIR": str(paths.logs),
        "DESKTOP_MODE": "true",
        "DESKTOP_LAUNCH_NONCE": nonce,
        "FRONTEND_DIST_DIR": frontend_dist(),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    for name, value in (cfg.get("api_keys") or {}).items():
        if name.upper() in API_KEY_VARS and value:
            env[name.upper()] = str(value)
    return env


def app_commands(paths: DesktopPaths, cfg: dict, api_port: int) -> dict[str, tuple[list[str], dict]]:
    """
    supervisord.conf's programs, adapted for a desktop: one uvicorn worker, a smaller main pool.
    Windows has no prefork pool, so the main worker uses threads and the heavy single-task
    workers use solo (the Supervisor enforces their memory ceiling instead of
    --max-memory-per-child, which only prefork supports).
    """
    py = sys.executable
    celery = [py, "-m", "celery", "-A", "app.worker"]
    concurrency = str(cfg.get("celery_workers", 4))
    main_pool = ["-P", "threads"] if IS_WINDOWS else []
    solo = ["-P", "solo"] if IS_WINDOWS else []
    quality_mem = [] if IS_WINDOWS else ["--max-memory-per-child=900000"]
    return {
        "uvicorn": ([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                     "--port", str(api_port), "--workers", "1"], {}),
        "celery_worker": (celery + ["worker", "--loglevel=info", "--concurrency", concurrency,
                                    "-Q", "indexer,thumbnails,celery"] + main_pool, {}),
        "celery_fullres": (celery + ["worker", "--loglevel=info", "--concurrency=1", "-Q", "fullres",
                                     "-n", "fullres@%h"] + solo, {}),
        "celery_quality": (celery + ["worker", "--loglevel=info", "--concurrency=1", "-Q", "quality",
                                     "-n", "quality@%h"] + quality_mem + solo, {"max_rss_mb": 900}),
        "celery_beat": (celery + ["beat", "--loglevel=info",
                                  f"--schedule={paths.temp / 'celerybeat-schedule'}"], {}),
    }


def redis_command(port: int, paths: DesktopPaths, password: str) -> list[str]:
    # Same persistence as the compose service (appendonly yes). The password keeps other local
    # programs off the broker (loopback alone doesn't).
    return ["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--dir", str(paths.redis),
            "--requirepass", password, "--appendonly", "yes", "--appendfsync", "everysec", "--save", ""]


def _resp(*words: str) -> bytes:
    return (f"*{len(words)}\r\n" + "".join(f"${len(w)}\r\n{w}\r\n" for w in words)).encode()


def redis_send(port: int, *words: str, password: str = "", timeout=2.0) -> bytes:
    """A minimal RESP client: enough for PING and SHUTDOWN without importing redis-py."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        if password:
            s.sendall(_resp("AUTH", password))
            if not s.recv(64).startswith(b"+OK"):
                return b""
        s.sendall(_resp(*words))
        try:
            return s.recv(64)
        except OSError:
            return b""


def wait_for(check, timeout: float, what: str, interval=0.5):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception as e:  # not up yet
            last_error = e
        time.sleep(interval)
    raise TimeoutError(f"{what} did not come up within {timeout:.0f}s ({last_error})")


def http_ok(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status == 200


class Launcher:
    def __init__(self, data_root: Path):
        self.paths = DesktopPaths(data_root)
        self.paths.ensure()
        self.cfg = load_config(self.paths)
        self.nonce = secrets.token_urlsafe(32)
        self.pg_port, self.redis_port, self.api_port = free_port(), free_port(), free_port()
        self.supervisor = Supervisor(self.paths.children_file)
        self.postgres = None
        self.env = None
        self._redis = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def login_url(self) -> str:
        return f"{self.url}/api/auth/desktop-login?nonce={self.nonce}"

    def _make_env(self):
        pg_url = Postgres(self.paths, self.pg_port, self.cfg["pg_password"], {}).url
        return build_env(self.paths, self.cfg, pg_url=pg_url, redis_port=self.redis_port, nonce=self.nonce)

    def start(self):
        kill_children_with_launcher()
        ensure_build_prefix_link(Path(sys.prefix))  # before anything runs a conda binary
        reap_orphans(self.paths.children_file, sys.prefix)
        self.env = self._make_env()

        emit("status", message="Starting database")
        # a postmaster child (PG 18 io_worker) that survived a crash keeps the shared memory and
        # socket, so the next start fails; nothing of ours can legitimately be running yet
        kill_strays(sys.prefix, "postgres")
        self.postgres = Postgres(self.paths, self.pg_port, self.cfg["pg_password"], self.env,
                                 bin_dir=pgsql_bin(Path(sys.prefix)))
        self.postgres.start()

        emit("status", message="Starting cache")
        password = self.cfg["redis_password"]
        self._redis = self.supervisor.add(Child("redis", redis_command(self.redis_port, self.paths, password),
                                                self.env, str(self.paths.redis), self.paths.logs))
        wait_for(lambda: redis_send(self.redis_port, "PING", password=password).startswith(b"+PONG"), 30, "Redis")
        self.supervisor.start_monitor()

        # the compose `command`: same scripts, same order
        emit("status", message="Preparing library")
        for module in ("app.scripts.initialize_db", "app.data.seed", "app.scripts.seed_named_stars"):
            self._run_init(module)

        threading.Thread(target=self._maybe_backup, daemon=True).start()
        self._restore_unacked()

        emit("status", message="Starting AstroCat")
        self._start_app_children()
        wait_for(lambda: http_ok(f"{self.url}/api/health"), 120, "the API")
        emit("ready", url=self.url, login_url=self.login_url, logs=str(self.paths.logs),
             data=str(self.paths.root))

    def _run_init(self, module: str):
        log = self.paths.logs / "init.log"
        with open(log, "ab") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {module}\n".encode())
            f.flush()
            proc = subprocess.run([sys.executable, "-m", module], env=self.env, cwd=str(BACKEND_ROOT),
                                  stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  creationflags=0x08000000 if IS_WINDOWS else 0, timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(f"{module} failed (exit {proc.returncode}); see {log}")

    def _restore_unacked(self):
        try:
            self._python("-c", RESTORE_UNACKED, timeout=60)
        except Exception:
            logger.exception("could not restore unacked Celery tasks")

    def _python(self, *args, timeout, check=True):
        return subprocess.run([sys.executable, *args], env=self.env, cwd=str(BACKEND_ROOT), capture_output=True,
                              text=True, timeout=timeout, check=check,
                              creationflags=0x08000000 if IS_WINDOWS else 0)

    def _shutdown_workers(self):
        """Warm shutdown: each worker finishes its current task and exits (TerminateProcess wouldn't)."""
        workers = [c for c in self.supervisor.children
                   if c.name.startswith("celery_") and c.name != "celery_beat" and c.running()]
        if not workers:
            return
        try:
            # workers don't reply to shutdown, so the CLI exits 69 ("no nodes replied") even
            # though every worker got it: don't treat that as failure
            self._python("-m", "celery", "-A", "app.worker", "control", "shutdown", timeout=30, check=False)
        except Exception:
            logger.warning("celery control shutdown failed; workers will be terminated", exc_info=True)
            return
        deadline = time.monotonic() + WORKER_STOP_S
        while time.monotonic() < deadline and any(c.running() for c in workers):
            time.sleep(0.5)

    def _start_app_children(self):
        for name, (argv, opts) in app_commands(self.paths, self.cfg, self.api_port).items():
            self.supervisor.add(Child(name, argv, self.env, str(BACKEND_ROOT), self.paths.logs, **opts))

    def _maybe_backup(self):
        """Daily dump into backups/ (the desktop stand-in for the db-backup container)."""
        if time.time() - float(self.cfg.get("last_backup") or 0) < BACKUP_EVERY_S:
            return
        try:
            dest = self.postgres.backup()
            self.cfg["last_backup"] = time.time()
            save_config(self.paths, self.cfg)
            logger.info("database backup written to %s", dest)
        except Exception:
            logger.exception("database backup failed")

    def reload(self):
        """Re-read config.json (new library folders) and restart the app processes with it."""
        self.cfg = load_config(self.paths)
        self.env = self._make_env()
        with self.supervisor._lock:
            for child in self.supervisor.children:
                if child.name in APP_CHILDREN:
                    child.env = self.env
        self.supervisor.restart(APP_CHILDREN)
        emit("status", message="Restarted with new settings")

    def stop(self):
        emit("status", message="Stopping")
        redis = self._redis
        self.supervisor.stop_monitor()
        self._shutdown_workers()
        # app processes first (reverse start order), then Redis cleanly, then Postgres
        for child in reversed(self.supervisor.children):
            if child is not redis:
                child.stop()
        if redis and redis.running():
            try:
                redis_send(self.redis_port, "SHUTDOWN", password=self.cfg["redis_password"])  # flushes the AOF
                redis.proc.wait(timeout=10)
            except Exception:
                pass
        self.supervisor.stop_all()
        if self.postgres:
            try:
                self.postgres.stop()
            except PostgresError:
                logger.exception("Postgres did not stop cleanly")
        emit("stopped")

    def smoke(self):
        """CI check: the API is up, desktop sign-in works, Postgres/PostGIS and Redis answer."""
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(self.login_url, timeout=10) as r:
            assert r.status == 200, f"desktop-login landed on {r.status}"
        assert any(c.name == "access_token" for c in jar), "desktop-login set no session cookie"
        with opener.open(f"{self.url}/api/auth/me", timeout=10) as r:
            assert json.load(r)["is_admin"], "desktop user is not an admin"
        with opener.open(f"{self.url}/api/health/db", timeout=10) as r:
            db = json.load(r)
            assert db["status"] == "healthy", db
        with opener.open(f"{self.url}/api/health/redis", timeout=10) as r:
            assert json.load(r)["status"] == "healthy"
        with opener.open(f"{self.url}/", timeout=10) as r:
            assert b"<html" in r.read(2048).lower() or not frontend_dist(), "frontend not served"
        wait_for(self._workers_answer, 120, "the Celery workers", interval=3)
        emit("smoke", ok=True, postgis=db.get("postgis_version"))

    def _workers_answer(self) -> bool:
        try:
            proc = self._python("-m", "celery", "-A", "app.worker", "inspect", "ping", "--json", timeout=60)
        except subprocess.CalledProcessError:
            return False
        # the main worker plus fullres@ and quality@
        return proc.stdout.count('"ok"') >= 3


def _serve_stdin(launcher: Launcher, done: threading.Event):
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "restart":
            try:
                launcher.reload()
            except Exception as e:
                emit("error", message=f"restart failed: {e}")
        elif cmd == "quit":
            break
    done.set()  # "quit" or EOF: the shell is gone


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="astrocat-launcher")
    parser.add_argument("--data-dir", type=Path, default=None, help="override the per-user data folder")
    parser.add_argument("--headless", action="store_true", help="no Tauri shell; stop with Ctrl+C")
    parser.add_argument("--smoke", action="store_true", help="start, run checks, stop (CI)")
    args = parser.parse_args(argv)

    data_root = args.data_dir or default_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(data_root / "logs" / "launcher.log", encoding="utf-8")]
        + ([logging.StreamHandler(sys.stderr)] if args.headless or args.smoke else []),
    )

    launcher = Launcher(data_root)
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())

    try:
        launcher.start()
        if args.smoke:
            launcher.smoke()
            return 0
        if args.headless:
            print(f"AstroCat is running: {launcher.login_url}", file=sys.stderr)
        else:
            threading.Thread(target=_serve_stdin, args=(launcher, done), daemon=True).start()
        while not done.wait(0.5):
            pass
        return 0
    except Exception as e:
        logger.exception("launcher failed")
        emit("error", message=str(e), logs=str(launcher.paths.logs))
        return 1
    finally:
        launcher.stop()


if __name__ == "__main__":
    sys.exit(main())
