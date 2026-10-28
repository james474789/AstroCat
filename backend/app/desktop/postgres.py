"""
The desktop app's private Postgres 18 + PostGIS server (replaces the compose ``db`` service).

Listens on 127.0.0.1 only, on a port picked per launch, with scram auth for one role.
Binaries come from the bundled runtime: <runtime>/pgsql/bin on Windows (EDB + PostGIS, run by
absolute path and kept off PATH), the conda env's bin on macOS (first on PATH).
"""

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.desktop.paths import PG_MAJOR, DesktopPaths

logger = logging.getLogger("astrocat.desktop")

PG_USER = "astrocat"
PG_DB = "astrocat"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class PostgresError(RuntimeError):
    pass


class Postgres:
    def __init__(self, paths: DesktopPaths, port: int, password: str, env: dict, bin_dir: Path | None = None):
        self.paths = paths
        self.port = port
        self.password = password
        self.bin_dir = bin_dir
        self.env = {**env, "PGPASSWORD": password}

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{PG_USER}:{self.password}@127.0.0.1:{self.port}/{PG_DB}"

    def _bin(self, name) -> str:
        if self.bin_dir is not None:
            found = shutil.which(name, path=str(self.bin_dir))
            if not found:
                raise PostgresError(f"{name} not found in {self.bin_dir}")
            return found
        found = shutil.which(name, path=self.env.get("PATH"))
        if not found:
            raise PostgresError(f"{name} not found in the bundled runtime")
        return found

    def _run(self, *argv, check=True, timeout=120, log: Path | None = None):
        """
        Run a Postgres tool. With ``log``, its output goes to that file instead of a pipe:
        `pg_ctl start` hands its stdout/stderr to the postmaster, so a pipe never closes and
        the call would never return.
        """
        cmd = [self._bin(argv[0]), *argv[1:]]
        if log is None:
            proc = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            output = proc.stderr.strip() or proc.stdout.strip()
        else:
            with open(log, "ab") as f:
                proc = subprocess.run(cmd, env=self.env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout,
                                      stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            output = f"see {log}"
        if check and proc.returncode != 0:
            raise PostgresError(f"{argv[0]} failed ({proc.returncode}): {output}")
        return proc

    @property
    def initialized(self) -> bool:
        return (self.paths.pgdata / "PG_VERSION").exists()

    def init(self):
        """First run: create the cluster. C locale and UTF-8 so it behaves the same on every OS."""
        pwfile = self.paths.temp / "pgpass.tmp"
        pwfile.write_text(self.password, encoding="utf-8")
        try:
            self._run("initdb", "-D", str(self.paths.pgdata), "-U", PG_USER,
                      "--auth=scram-sha-256", f"--pwfile={pwfile}", "--encoding=UTF8", "--no-locale")
        finally:
            pwfile.unlink(missing_ok=True)
        with open(self.paths.pgdata / "postgresql.conf", "a", encoding="utf-8") as f:
            f.write(
                "\n# AstroCat desktop\n"
                "listen_addresses = '127.0.0.1'\n"
                # uvicorn + 3 Celery workers each keep their own SQLAlchemy pools
                "max_connections = 200\n"
            )
        logger.info("initialized Postgres cluster in %s", self.paths.pgdata)

    def check_major(self):
        """
        Refuse to start a cluster made by another Postgres major: this server can't read it, and
        letting it try risks the data. Upgrading needs a dump/restore (not built yet).
        """
        found = (self.paths.pgdata / "PG_VERSION").read_text(encoding="utf-8").strip()
        if found != str(PG_MAJOR):
            raise PostgresError(
                f"Your library database was created by PostgreSQL {found}; this AstroCat ships "
                f"PostgreSQL {PG_MAJOR}. Install the previous version of AstroCat or see "
                "docs/install-desktop.md#upgrading.")

    def start(self):
        if not self.initialized:
            self.init()
        self.check_major()
        try:
            self._pg_ctl_start()
        except PostgresError:
            # A crash can leave postmaster.pid naming a PID the OS has since reused.
            # Only remove it when pg_ctl agrees no server is running on this data dir.
            pidfile = self.paths.pgdata / "postmaster.pid"
            status = self._run("pg_ctl", "status", "-D", str(self.paths.pgdata), check=False)
            if pidfile.exists() and status.returncode == 3:
                logger.warning("removing stale %s", pidfile)
                pidfile.unlink()
                self._pg_ctl_start()
            else:
                raise
        self._ensure_database()

    def _pg_ctl_start(self):
        self._run("pg_ctl", "start", "-D", str(self.paths.pgdata), "-w", "-t", "90",
                  "-l", str(self.paths.logs / "postgres.log"), "-o", f"-p {self.port}",
                  log=self.paths.logs / "pg_ctl.log")

    def _ensure_database(self):
        exists = self._run("psql", "-h", "127.0.0.1", "-p", str(self.port), "-U", PG_USER, "-d", "postgres",
                           "-tAc", f"SELECT 1 FROM pg_database WHERE datname = '{PG_DB}'").stdout.strip()
        if exists != "1":
            # psql rather than createdb: the pruned Windows pgsql/bin ships only the tools we use
            self._run("psql", "-h", "127.0.0.1", "-p", str(self.port), "-U", PG_USER, "-d", "postgres",
                      "-c", f"CREATE DATABASE {PG_DB}")
            logger.info("created database %s", PG_DB)

    def stop(self):
        if not (self.paths.pgdata / "postmaster.pid").exists():
            return
        self._run("pg_ctl", "stop", "-D", str(self.paths.pgdata), "-m", "fast", "-w", "-t", "60", check=False,
                  log=self.paths.logs / "pg_ctl.log")

    def backup(self, keep=7) -> Path:
        """Compressed custom-format dump into backups/, keeping the newest ``keep`` files."""
        dest = self.paths.backups / f"astrocat_{time.strftime('%Y%m%d_%H%M%S')}.dump"
        self._run("pg_dump", "-h", "127.0.0.1", "-p", str(self.port), "-U", PG_USER, "-d", PG_DB,
                  "-F", "c", "-f", str(dest), timeout=3600)
        dumps = sorted(self.paths.backups.glob("astrocat_*.dump"))
        for old in dumps[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dest
