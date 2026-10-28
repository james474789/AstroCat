"""
D2 C3: keep the desktop launcher in step with the Docker image.

Docker runs the app through supervisord.conf and docker-compose.yml; the desktop app runs the
same processes through app/desktop/launcher.py. These tests fail when one side learns about a
process, queue, setting or container path that the other doesn't. docs/desktop-parity.md says
what to do when one fails. Pure Python: no services needed.
"""

import configparser
import os
import re
import shlex
from collections import Counter
from pathlib import Path

import pytest

from app.config import Settings
from app.desktop import launcher
from app.desktop.paths import DesktopPaths, load_config

BACKEND = Path(__file__).resolve().parents[1]
SUPERVISORD_CONF = BACKEND / "supervisord.conf"

# Programs that exist on only one side, with the reason. Empty today.
ALLOWED_DIFFERENCES: dict[str, str] = {}

# Deliberately not compared between supervisord and the launcher: the pool (-P threads/solo on
# Windows), --concurrency, --max-memory-per-child (the Supervisor enforces a ceiling on
# Windows), uvicorn's --host/--port/--workers, --loglevel and beat's --schedule path.


def _supervisord_programs() -> dict[str, list[str]]:
    cp = configparser.ConfigParser(interpolation=None)  # leave %(ENV_...)s alone
    cp.read(SUPERVISORD_CONF, encoding="utf-8")
    programs = {}
    for section in cp.sections():
        if not section.startswith("program:"):
            continue
        argv = shlex.split(cp[section]["command"])
        if argv[:2] == ["sh", "-c"]:  # sh -c "exec celery ..."
            argv = [a for a in shlex.split(argv[2]) if a != "exec"]
        programs[section.split(":", 1)[1]] = [a.replace("%%", "%") for a in argv]
    return programs


def _option(argv: list[str], flag: str):
    if flag in argv:
        return argv[argv.index(flag) + 1]
    return None


def _shape(argv: list[str]) -> dict:
    """The parts of a command line both sides must agree on."""
    names = [Path(a).name for a in argv]
    if "uvicorn" in names:
        sub = argv[names.index("uvicorn") + 1]
    else:
        sub = argv[argv.index("app.worker") + 1]
    queues = _option(argv, "-Q")
    return {
        "subcommand": sub,
        "queues": set(queues.split(",")) if queues else None,
        "node": _option(argv, "-n"),
    }


@pytest.fixture
def paths(tmp_path):
    p = DesktopPaths(tmp_path / "AstroCat")
    p.ensure()
    return p


@pytest.fixture(params=[True, False], ids=["windows", "posix"])
def desktop_commands(request, paths, monkeypatch):
    monkeypatch.setattr(launcher, "IS_WINDOWS", request.param)
    return {name: argv for name, (argv, _opts) in
            launcher.app_commands(paths, {"celery_workers": 4}, api_port=5123).items()}


# --- 1. processes ------------------------------------------------------------------

def test_launcher_runs_every_supervisord_program(desktop_commands):
    docker = _supervisord_programs()
    assert docker, f"no [program:*] sections parsed from {SUPERVISORD_CONF}"
    only_docker = set(docker) - set(desktop_commands) - set(ALLOWED_DIFFERENCES)
    only_desktop = set(desktop_commands) - set(docker) - set(ALLOWED_DIFFERENCES)
    assert not only_docker, (f"supervisord.conf runs {sorted(only_docker)} but launcher.app_commands() "
                             "doesn't; add them there (or to ALLOWED_DIFFERENCES with a reason)")
    assert not only_desktop, (f"launcher.app_commands() runs {sorted(only_desktop)} but supervisord.conf "
                              "doesn't; add them there (or to ALLOWED_DIFFERENCES with a reason)")


def test_each_program_has_the_same_subcommand_queues_and_node_name(desktop_commands):
    for name, argv in _supervisord_programs().items():
        if name in ALLOWED_DIFFERENCES:
            continue
        assert _shape(desktop_commands[name]) == _shape(argv), f"{name} differs between supervisord and the launcher"


# --- 2. Celery routing ---------------------------------------------------------------

def test_every_routed_queue_has_a_desktop_worker(desktop_commands):
    from app.worker import celery_app

    routed = {r["queue"] for r in celery_app.conf.task_routes.values()}
    default = celery_app.conf.task_default_queue or "celery"
    consumed = set()
    for argv in desktop_commands.values():
        q = _option(argv, "-Q")
        if q:
            consumed |= set(q.split(","))
    missing = (routed | {default}) - consumed
    assert not missing, (f"queues {sorted(missing)} are routed to in app/worker.py but no desktop worker "
                         "consumes them; add them to launcher.app_commands() and supervisord.conf")


# --- 3. settings -------------------------------------------------------------------

# Settings the desktop app deliberately leaves at their config.py default.
DESKTOP_DEFAULT_OK = {
    "app_name": "display name, same everywhere",
    "debug": "off in both deployments",
    "jwt_algorithm": "same algorithm everywhere",
    "cookie_samesite": "same-origin app; lax as in Docker",
    "rate_limit_enabled": "same protection as Docker",
    "rate_limit_login": "same limit as Docker",
    "rate_limit_search": "same limit as Docker",
    "rate_limit_general": "same limit as Docker",
    "csrf_enabled": "the desktop UI is the same SPA and sends the token",
    "csrf_cookie_name": "fixed name shared with the frontend",
    "csrf_header_name": "fixed name shared with the frontend",
    "csrf_cookie_secure": "plain http on loopback, like COOKIE_SECURE=false",
    "csrf_cookie_samesite": "same-origin app; lax as in Docker",
    "thumbnail_max_size": "rendering tunable, Docker default",
    "fullres_cache_max_gb": "cache cap tunable, Docker default",
    "fullres_max_megapixels": "rendering tunable, Docker default",
    "fullres_workers": "the launcher runs the fullres worker with --concurrency=1",
    "fullres_debayer": "rendering tunable, Docker default",
    "star_metrics_enabled": "on, as in Docker",
    "star_metrics_backfill": "on, as in Docker",
    "star_metrics_sweep_batch": "sweeper tunable, Docker default",
    "star_metrics_queue_max": "sweeper tunable, Docker default",
    "meteoblue_min_interval_h": "API politeness tunable, Docker default",
    "seeing_coord_decimals": "cache-key precision, Docker default",
    "seeing_enabled": "on, as in Docker",
    "api_prefix": "fixed; the frontend calls /api",
}


def test_every_setting_has_a_desktop_decision(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"PATH": ""})  # only what build_env sets itself
    prefix = tmp_path / "runtime"
    (prefix / "pgsql" / "bin").mkdir(parents=True)  # Windows layout, so PG_BIN_DIR is set
    env = launcher.build_env(paths, load_config(paths), pg_url="postgresql+asyncpg://a:b@127.0.0.1:1/astrocat",
                             redis_port=1, nonce="n", prefix=prefix)
    set_by_launcher = {k.lower() for k in env}
    api_keys = {k.lower() for k in launcher.API_KEY_VARS}

    fields = set(Settings.model_fields)
    undecided = fields - set_by_launcher - api_keys - set(DESKTOP_DEFAULT_OK)
    assert not undecided, (f"settings {sorted(undecided)} have no desktop decision: set them in "
                           "launcher.build_env(), add them to launcher.API_KEY_VARS, or add them to "
                           "DESKTOP_DEFAULT_OK in this file with a reason (docs/desktop-parity.md)")

    stale = set(DESKTOP_DEFAULT_OK) - fields
    assert not stale, f"DESKTOP_DEFAULT_OK names settings that no longer exist: {sorted(stale)}"
    overlap = set(DESKTOP_DEFAULT_OK) & (set_by_launcher | api_keys)
    assert not overlap, f"{sorted(overlap)} are set by the launcher; drop them from DESKTOP_DEFAULT_OK"
    assert api_keys <= fields, f"API_KEY_VARS names unknown settings: {sorted(api_keys - fields)}"


# --- 4. container paths ----------------------------------------------------------------

CONTAINER_PATH_RE = re.compile(r"""["'](?:/data/|/app/|/var/log)""")

# (file, literal) -> count of the container paths that are allowed to stay hard-coded.
ALLOWED_CONTAINER_PATHS = Counter({
    ("app/config.py", '"/data/'): 3,           # Docker defaults; desktop overrides via build_env()
    ("app/config.py", '"/var/log'): 1,         # LOG_DIR default; desktop overrides via build_env()
    ("app/logging_config.py", '"/var/log'): 1,  # fallback when settings can't load
    ("app/version.py", '"/app/'): 1,           # /app/VERSION fallback inside the image
    ("app/scripts/seed_named_stars.py", '"/app/'): 1,  # container fallback for the data file
    ("app/api/indexer.py", '"/data/'): 4,      # docstring examples
    ("app/api/targets.py", '"/data/'): 3,      # docstring example
    ("app/scripts/update_classifications.py", "'/data/"): 1,  # Docker-only one-off script
    ("app/scripts/update_planetary.py", "'/data/"): 2,        # Docker-only one-off script
})


def test_no_new_hard_coded_container_paths():
    found = Counter()
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("app/desktop/", "app/tests/")):
            continue
        for match in CONTAINER_PATH_RE.findall(path.read_text(encoding="utf-8")):
            found[(rel, match)] += 1
    assert found == ALLOWED_CONTAINER_PATHS, (
        "container path literals changed. Take paths from settings (docs/desktop-parity.md); if a "
        f"literal really is Docker-only, update ALLOWED_CONTAINER_PATHS. Now: {dict(found)}")
