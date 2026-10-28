"""
A small cross-platform stand-in for supervisord.

Each Child is one long-running process with its own log file. The Supervisor restarts children
that exit (with backoff), recycles Windows workers that outgrow a memory ceiling (prefork's
--max-memory-per-child does not exist there), and stops everything in reverse start order.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("astrocat.desktop")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

BACKOFF_MAX_S = 60
STABLE_AFTER_S = 60  # a child that ran this long has its backoff reset


class Child:
    def __init__(self, name, argv, env, cwd, log_dir: Path, max_rss_mb=None, stop_timeout=15):
        self.name = name
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.log_path = log_dir / f"{name}.log"
        self.max_rss_mb = max_rss_mb
        self.stop_timeout = stop_timeout
        self.proc = None
        self.started_at = 0.0
        self.backoff = 1
        self.next_start = 0.0
        self._log = None

    def start(self):
        self._log = open(self.log_path, "ab", buffering=0)
        kwargs = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            self.argv, env=self.env, cwd=self.cwd,
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT, **kwargs,
        )
        self.started_at = time.monotonic()
        logger.info("started %s (pid %s)", self.name, self.proc.pid)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            # Windows has no SIGTERM: terminate() is TerminateProcess. Celery runs with acks_late,
            # so a task cut off mid-way is redelivered on the next start.
            self.proc.terminate()
            try:
                self.proc.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        logger.info("stopped %s (exit %s)", self.name, self.proc.returncode)
        self.proc = None
        if self._log:
            self._log.close()
            self._log = None


class Supervisor:
    def __init__(self, children_file: Path | None = None):
        self.children: list[Child] = []
        self.children_file = children_file
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._thread = None

    def add(self, child: Child) -> Child:
        with self._lock:
            child.start()
            self.children.append(child)
            self._record_pids()
        return child

    def start_monitor(self, interval=1.0):
        self._thread = threading.Thread(target=self._monitor, args=(interval,), daemon=True)
        self._thread.start()

    def _monitor(self, interval):
        while not self._stopping.wait(interval):
            with self._lock:
                for child in self.children:
                    self._check(child)

    def _check(self, child: Child):
        now = time.monotonic()
        if child.running():
            if child.max_rss_mb:
                rss = _rss_mb(child.proc.pid)
                if rss and rss > child.max_rss_mb:
                    logger.warning("%s using %d MB (> %d), recycling", child.name, rss, child.max_rss_mb)
                    child.stop()
                    child.next_start = now
            if child.running() and now - child.started_at > STABLE_AFTER_S:
                child.backoff = 1
            return
        if child.proc is not None:  # just exited
            logger.warning("%s exited with %s; restarting in %ss", child.name, child.proc.returncode, child.backoff)
            child.stop()
            child.next_start = now + child.backoff
            child.backoff = min(child.backoff * 2, BACKOFF_MAX_S)
            return
        if now >= child.next_start:
            child.start()
            self._record_pids()

    def restart(self, names):
        with self._lock:
            for child in self.children:
                if child.name in names:
                    child.stop()
                    child.backoff = 1
                    child.next_start = 0.0

    def stop_monitor(self):
        """Stop restarting children (call before stopping them one by one)."""
        self._stopping.set()
        if self._thread:
            self._thread.join(timeout=5)

    def stop_all(self):
        self.stop_monitor()
        with self._lock:
            for child in reversed(self.children):
                try:
                    child.stop()
                except Exception:
                    logger.exception("failed to stop %s", child.name)
            self.children.clear()
            self._record_pids()

    def _record_pids(self):
        if not self.children_file:
            return
        pids = [{"pid": c.proc.pid, "exe": c.argv[0]} for c in self.children if c.proc]
        try:
            self.children_file.write_text(json.dumps(pids), encoding="utf-8")
        except OSError:
            logger.debug("could not record child pids", exc_info=True)


def reap_orphans(children_file: Path, runtime_prefix: str):
    """
    Kill children a crashed launcher left behind (POSIX; on Windows the job object does this).
    Only PIDs whose command line still points into our runtime are touched, so a PID the OS
    has since reused for something else is left alone.
    """
    if IS_WINDOWS or not children_file.exists():
        return
    try:
        entries = json.loads(children_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    import signal
    for entry in entries:
        pid = entry.get("pid")
        try:
            cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if cmd and runtime_prefix in cmd:
            logger.warning("killing orphaned child pid %s", pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    children_file.unlink(missing_ok=True)


_WIN32 = None


def _win32():
    """kernel32/psapi with argtypes/restype declared, so 64-bit handles pass through intact."""
    global _WIN32
    if _WIN32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        H, D, B = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
        for name, res, args in [
            ("OpenProcess", H, [D, B, D]),
            ("CloseHandle", B, [H]),
            ("TerminateProcess", B, [H, wintypes.UINT]),
            ("GetCurrentProcess", H, []),
            ("QueryFullProcessImageNameW", B, [H, D, wintypes.LPWSTR, ctypes.POINTER(D)]),
            ("CreateJobObjectW", H, [ctypes.c_void_p, wintypes.LPCWSTR]),
            ("SetInformationJobObject", B, [H, ctypes.c_int, ctypes.c_void_p, D]),
            ("AssignProcessToJobObject", B, [H, H]),
            ("K32GetProcessMemoryInfo", B, [H, ctypes.c_void_p, D]),
        ]:
            fn = getattr(k, name)
            fn.restype, fn.argtypes = res, args
        psapi.EnumProcesses.restype = B
        psapi.EnumProcesses.argtypes = [ctypes.POINTER(D), D, ctypes.POINTER(D)]
        _WIN32 = (ctypes, wintypes, k, psapi)
    return _WIN32


def kill_strays(prefix: str, exe_name: str) -> int:
    """
    Kill every running ``exe_name`` whose executable lives under ``prefix`` (our runtime) and
    return how many were killed. Used before starting Postgres: a leftover postmaster child
    holds the shared memory block and listening socket, and the new server can't start.
    Processes outside the runtime (a system Postgres, say) are never touched.
    """
    prefix_norm = os.path.normcase(os.path.abspath(prefix))
    killed = 0
    for pid, exe in _processes():
        exe_norm = os.path.normcase(os.path.abspath(exe))
        stem = os.path.splitext(os.path.basename(exe_norm))[0]
        if pid == os.getpid() or stem != exe_name.lower() or not exe_norm.startswith(prefix_norm + os.sep):
            continue
        logger.warning("killing stray %s (pid %s)", exe, pid)
        if _kill(pid):
            killed += 1
    return killed


def _processes():
    """(pid, executable path) for every process we can inspect."""
    if not IS_WINDOWS:
        try:
            out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        rows = []
        for line in out.splitlines():
            pid, _, exe = line.strip().partition(" ")
            if pid.isdigit() and exe.strip():
                rows.append((int(pid), exe.strip()))
        return rows

    ctypes, wintypes, kernel32, psapi = _win32()
    pids = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    rows = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                rows.append((pid, buf.value))
        finally:
            kernel32.CloseHandle(handle)
    return rows


def _kill(pid) -> bool:
    if not IS_WINDOWS:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError:
            return False
    _, _, kernel32, _ = _win32()
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def kill_children_with_launcher():
    """
    Windows: put this process in a job object that kills every process in it when the last
    handle closes, so the children die with the launcher however it exits.
    """
    if not IS_WINDOWS:
        return
    ctypes, wintypes, kernel32, _ = _win32()

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning("CreateJobObject failed: %s", ctypes.get_last_error())
        return
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        logger.warning("SetInformationJobObject failed: %s", ctypes.get_last_error())
        return
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        logger.warning("AssignProcessToJobObject failed: %s", ctypes.get_last_error())
        return
    # the handle is deliberately never closed: it closes when this process ends
    globals()["_JOB_HANDLE"] = job


def _rss_mb(pid) -> int | None:
    """Resident memory of a Windows or macOS process in MB, or None if it can't be read."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()) // 1024  # ps reports KB
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _, _, kernel32, _ = _win32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize // (1024 * 1024)
    finally:
        kernel32.CloseHandle(handle)
