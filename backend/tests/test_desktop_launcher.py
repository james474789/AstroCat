"""
D1 desktop launcher: config bootstrap, the environment/commands it hands the app processes,
and the supervisor's restart and stop behaviour. No Postgres or Redis needed.
"""

import json
import socket
import sys
import threading
import time

import pytest

from app.config import Settings
from app.desktop import launcher, supervisor
from app.desktop.paths import DesktopPaths, load_config


@pytest.fixture
def paths(tmp_path):
    p = DesktopPaths(tmp_path / "AstroCat")
    p.ensure()
    return p


def test_first_run_config_has_usable_secrets_and_is_stable(paths):
    cfg = load_config(paths)
    # config.py rejects short keys and ones containing words like "dev"/"test"
    Settings(secret_key=cfg["secret_key"], DATABASE_URL="postgresql+asyncpg://u:p@h/d")
    assert cfg["image_paths"] == [] and cfg["pg_password"]
    assert load_config(paths) == cfg  # second launch reuses the same secrets


def test_config_gains_new_keys_without_losing_old_ones(paths):
    paths.config_file.write_text(json.dumps({"secret_key": "a" * 64, "image_paths": ["D:/Astro"]}))
    cfg = load_config(paths)
    assert cfg["secret_key"] == "a" * 64 and cfg["image_paths"] == ["D:/Astro"]
    assert "pg_password" in json.loads(paths.config_file.read_text())


def test_env_points_the_app_at_local_services(paths):
    cfg = load_config(paths)
    cfg["image_paths"] = ["D:/Astro/Lights", "E:/Masters"]
    env = launcher.build_env(paths, cfg, pg_url="postgresql+asyncpg://astrocat:pw@127.0.0.1:5999/astrocat",
                             redis_port=6999, nonce="n0nce")
    assert env["DATABASE_URL"].endswith("@127.0.0.1:5999/astrocat")
    pw = cfg["redis_password"]
    assert env["CELERY_BROKER_URL"] == f"redis://:{pw}@127.0.0.1:6999/0"
    assert env["CELERY_RESULT_BACKEND"] == f"redis://:{pw}@127.0.0.1:6999/1"
    assert env["REDIS_URL"] == env["CELERY_BROKER_URL"]
    assert env["PGGSSENCMODE"] == "disable"
    assert env["IMAGE_PATHS"] == "D:/Astro/Lights,E:/Masters"
    assert env["DESKTOP_MODE"] == "true" and env["DESKTOP_LAUNCH_NONCE"] == "n0nce"
    assert env["COOKIE_SECURE"] == "false"
    assert env["THUMBNAIL_CACHE_PATH"] == str(paths.thumbnails)
    assert env["LOG_DIR"] == str(paths.logs)
    assert env["PATH"].startswith(launcher.runtime_path_dirs(launcher.Path(sys.prefix))[0])

    s = Settings(_env_file=None, **{k.lower(): v for k, v in env.items()
                                    if k in {"SECRET_KEY", "IMAGE_PATHS", "DESKTOP_MODE", "COOKIE_MAX_AGE"}},
                 DATABASE_URL=env["DATABASE_URL"])
    assert s.desktop_mode is True
    assert s.image_paths_list == ["D:/Astro/Lights", "E:/Masters"]


@pytest.mark.parametrize("windows", [True, False])
def test_worker_commands_match_supervisord_with_platform_pools(paths, monkeypatch, windows):
    monkeypatch.setattr(launcher, "IS_WINDOWS", windows)
    cmds = launcher.app_commands(paths, {"celery_workers": 4}, api_port=5123)
    assert list(cmds) == list(launcher.APP_CHILDREN)

    uvicorn, _ = cmds["uvicorn"]
    assert uvicorn[-6:] == ["--host", "127.0.0.1", "--port", "5123", "--workers", "1"]

    main, _ = cmds["celery_worker"]
    assert "indexer,thumbnails,celery" in main
    quality, quality_opts = cmds["celery_quality"]
    fullres, _ = cmds["celery_fullres"]
    assert "quality@%h" in quality and "fullres@%h" in fullres
    if windows:
        assert main[-2:] == ["-P", "threads"]
        assert quality[-2:] == ["-P", "solo"] and fullres[-2:] == ["-P", "solo"]
        assert not any(a.startswith("--max-memory-per-child") for a in quality)
    else:
        assert "-P" not in main and "-P" not in quality
        assert "--max-memory-per-child=900000" in quality
    assert quality_opts == {"max_rss_mb": 900}


def test_free_port_never_returns_the_docker_frontend_port(monkeypatch):
    ports = iter([8090, 8090, 51234])

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def bind(self, addr):
            assert addr == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", next(ports))

    monkeypatch.setattr(launcher.socket, "socket", lambda *a: FakeSock())
    assert launcher.free_port() == 51234


def test_redis_send_speaks_resp():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def serve():
        conn, _ = srv.accept()
        received["data"] = conn.recv(64)
        conn.sendall(b"+PONG\r\n")
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    assert launcher.redis_send(port, "PING") == b"+PONG\r\n"
    t.join()
    srv.close()
    assert received["data"] == b"*1\r\n$4\r\nPING\r\n"


def _wait(cond, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_supervisor_restarts_a_crashing_child_with_backoff(paths, monkeypatch):
    sup = supervisor.Supervisor(paths.children_file)
    marker = paths.temp / "runs.txt"
    code = f"open(r'{marker}', 'a').write('x')"
    child = sup.add(supervisor.Child("flaky", [sys.executable, "-c", code], None, None, paths.logs))
    sup.start_monitor(interval=0.1)
    try:
        assert _wait(lambda: marker.exists() and len(marker.read_text()) >= 3), "child was not restarted"
        assert child.backoff > 1  # doubled after each quick exit
    finally:
        sup.stop_all()
    assert sup.children == []
    assert json.loads(paths.children_file.read_text()) == []


def test_supervisor_stops_long_running_children_and_records_pids(paths):
    sup = supervisor.Supervisor(paths.children_file)
    a = sup.add(supervisor.Child("a", [sys.executable, "-c", "import time; time.sleep(60)"], None, None, paths.logs))
    b = sup.add(supervisor.Child("b", [sys.executable, "-c", "import time; time.sleep(60)"], None, None, paths.logs))
    recorded = {e["pid"] for e in json.loads(paths.children_file.read_text())}
    assert recorded == {a.proc.pid, b.proc.pid}
    procs = [a.proc, b.proc]
    sup.stop_all()
    assert all(p.poll() is not None for p in procs)


def test_restart_brings_named_children_back(paths):
    sup = supervisor.Supervisor(paths.children_file)
    child = sup.add(supervisor.Child("web", [sys.executable, "-c", "import time; time.sleep(60)"], None, None, paths.logs))
    first_pid = child.proc.pid
    sup.start_monitor(interval=0.1)
    try:
        sup.restart({"web"})
        assert _wait(lambda: child.running() and child.proc.pid != first_pid)
    finally:
        sup.stop_all()


def test_api_keys_from_config_reach_the_app_but_nothing_else(paths):
    cfg = load_config(paths)
    cfg["api_keys"] = {"ASTROMETRY_API_KEY": "abc123", "astrometry_api_key_typo": "x", "PATH": "/evil"}
    env = launcher.build_env(paths, cfg, pg_url="postgresql+asyncpg://a:b@127.0.0.1:1/astrocat",
                             redis_port=2, nonce="n")
    assert env["ASTROMETRY_API_KEY"] == "abc123"
    assert "/evil" not in env["PATH"].split(launcher.os.pathsep)
    assert "ASTROMETRY_API_KEY_TYPO" not in env


def test_redis_runs_with_a_password_and_aof(paths):
    cmd = launcher.redis_command(6400, paths, "f00d")
    assert cmd[cmd.index("--requirepass") + 1] == "f00d"
    assert cmd[cmd.index("--bind") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--appendonly") + 1] == "yes"


def test_redis_send_authenticates_first():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    seen = []

    def serve():
        conn, _ = srv.accept()
        seen.append(conn.recv(64))
        conn.sendall(b"+OK\r\n")
        seen.append(conn.recv(64))
        conn.sendall(b"+PONG\r\n")
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    assert launcher.redis_send(port, "PING", password="pw") == b"+PONG\r\n"
    t.join()
    srv.close()
    assert seen == [b"*2\r\n$4\r\nAUTH\r\n$2\r\npw\r\n", b"*1\r\n$4\r\nPING\r\n"]


def test_kill_strays_only_touches_our_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    ours = str(runtime / "Library" / "bin" / ("postgres.exe" if sys.platform == "win32" else "postgres"))
    procs = [
        (101, ours),
        (102, str(tmp_path / "elsewhere" / "postgres")),             # a system Postgres
        (103, str(runtime / "python")),                              # ours, but not postgres
        (104, str(tmp_path / "runtime-old" / "bin" / "postgres")),   # prefix lookalike
    ]
    killed = []
    monkeypatch.setattr(supervisor, "_processes", lambda: procs)
    monkeypatch.setattr(supervisor, "_kill", lambda pid: killed.append(pid) or True)
    assert supervisor.kill_strays(str(runtime), "postgres") == 1
    assert killed == [101]


def test_process_listing_sees_this_process():
    exes = {pid: exe for pid, exe in supervisor._processes()}
    import os
    assert os.getpid() in exes


def test_windows_pgsql_runtime_sets_tool_dir_and_proj(paths, tmp_path):
    prefix = tmp_path / "runtime"
    (prefix / "pgsql" / "bin").mkdir(parents=True)
    proj = prefix / "pgsql" / "share" / "contrib" / "postgis-3.6" / "proj"
    proj.mkdir(parents=True)
    env = launcher.build_env(paths, load_config(paths), pg_url="postgresql+asyncpg://a:b@127.0.0.1:1/astrocat",
                             redis_port=2, nonce="n", prefix=prefix)
    assert env["PG_BIN_DIR"] == str(prefix / "pgsql" / "bin")
    assert env["PROJ_LIB"] == env["PROJ_DATA"] == str(proj)
    assert env["PROJ_USER_WRITABLE_DIRECTORY"] == str(paths.temp / "proj")
    # postgres finds its DLLs next to postgres.exe; PATH stays conda's so .pyd files keep conda's
    assert str(prefix / "pgsql" / "bin") not in env["PATH"].split(launcher.os.pathsep)


def test_conda_postgres_runtime_has_no_pg_bin_dir(paths, tmp_path):
    prefix = tmp_path / "runtime"
    (prefix / "share" / "proj").mkdir(parents=True)
    (prefix / "Library" / "share" / "proj").mkdir(parents=True)
    env = launcher.build_env(paths, load_config(paths), pg_url="postgresql+asyncpg://a:b@127.0.0.1:1/astrocat",
                             redis_port=2, nonce="n", prefix=prefix)
    assert "PG_BIN_DIR" not in env or env["PG_BIN_DIR"] == launcher.os.environ.get("PG_BIN_DIR")
    assert env["PROJ_DATA"].endswith("proj")


def test_admin_backup_tools_follow_pg_bin_dir(monkeypatch):
    from app.api import admin
    from app.config import settings
    monkeypatch.setattr(settings, "pg_bin_dir", "")
    assert admin._pg_tool("pg_dump") == "pg_dump"  # Docker: PATH
    monkeypatch.setattr(settings, "pg_bin_dir", "/rt/pgsql/bin")
    expected = "pg_dump.exe" if launcher.os.name == "nt" else "pg_dump"
    assert admin._pg_tool("pg_dump") == launcher.os.path.join("/rt/pgsql/bin", expected)


def _can_symlink(tmp_path):
    try:
        (tmp_path / "probe-link").symlink_to(tmp_path, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_build_prefix_link_follows_the_runtime_wherever_it_is(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("creating symlinks needs privileges on this machine")
    link = tmp_path / "Shared" / "AstroCat" / "runtime"
    first = tmp_path / "Applications" / "AstroCat.app" / "runtime"
    first.mkdir(parents=True)
    (first / launcher.BUILD_PREFIX_FILE).write_text(str(link))

    assert launcher.ensure_build_prefix_link(first) == link
    assert link.resolve() == first.resolve()
    assert launcher.ensure_build_prefix_link(first) == link  # idempotent

    moved = tmp_path / "Translocated" / "AstroCat.app" / "runtime"  # app moved / updated
    moved.mkdir(parents=True)
    (moved / launcher.BUILD_PREFIX_FILE).write_text(str(link))
    launcher.ensure_build_prefix_link(moved)
    assert link.resolve() == moved.resolve()

    import shutil
    shutil.rmtree(moved)  # stale link to a deleted install gets replaced too
    launcher.ensure_build_prefix_link(first)
    assert link.resolve() == first.resolve()


def test_build_prefix_link_refuses_to_replace_a_real_folder(tmp_path):
    link = tmp_path / "Shared" / "AstroCat" / "runtime"
    link.mkdir(parents=True)  # someone else's folder, not our link
    rt = tmp_path / "app" / "runtime"
    rt.mkdir(parents=True)
    (rt / launcher.BUILD_PREFIX_FILE).write_text(str(link))
    with pytest.raises(RuntimeError):
        launcher.ensure_build_prefix_link(rt)
    assert link.is_dir() and not link.is_symlink()


def test_no_build_prefix_file_means_nothing_to_link(tmp_path):
    assert launcher.ensure_build_prefix_link(tmp_path) is None
