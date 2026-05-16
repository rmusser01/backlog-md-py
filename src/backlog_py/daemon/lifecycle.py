from __future__ import annotations

import os
import signal
# Daemon startup uses a fixed Python argv without a shell.
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from backlog_py import __version__
from backlog_py.runtime.locks import DaemonRuntimeLock
from backlog_py.runtime.state import (
    RuntimeRecord,
    allocate_log_path,
    delete_runtime_record,
    ensure_state_layout,
    read_runtime_record,
    write_runtime_record,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765


class DaemonNotRunningError(RuntimeError):
    """Raised when no healthy daemon runtime record exists."""


class DaemonStopTimeoutError(TimeoutError):
    """Raised when a daemon process does not exit before the stop timeout."""


@dataclass(frozen=True)
class DaemonStatus:
    """Current singleton daemon status."""

    record: RuntimeRecord
    running: bool = True


def daemon_status() -> DaemonStatus:
    """Return status for a healthy daemon or clean up stale state."""
    layout = ensure_state_layout()
    record = read_runtime_record(layout)
    if record is None:
        raise DaemonNotRunningError("Daemon not running")
    if not is_pid_alive(record.pid):
        delete_runtime_record(layout)
        raise DaemonNotRunningError("Daemon not running")
    return DaemonStatus(record=record)


def daemon_ensure(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> DaemonStatus:
    """Return a healthy daemon status, starting one when needed."""
    try:
        return daemon_status()
    except DaemonNotRunningError:
        return daemon_start(host=host, port=port)


def daemon_start(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> DaemonStatus:
    """Start the singleton daemon unless a healthy runtime record already exists."""
    with DaemonRuntimeLock(operation="daemon_start").acquire():
        try:
            return daemon_status()
        except DaemonNotRunningError:
            pass

        layout = ensure_state_layout()
        log_path = allocate_log_path(layout)
        token = os.urandom(32).hex()
        endpoint = f"http://{host}:{port}/mcp"
        command = [
            sys.executable,
            "-m",
            "backlog_py",
            "daemon",
            "run",
            "--foreground",
            "--host",
            host,
            "--port",
            str(port),
        ]
        env = {
            **os.environ,
            "BACKLOG_PY_DAEMON_TOKEN": token,
            "BACKLOG_PY_DAEMON_LOG": str(log_path),
        }
        with log_path.open("a", encoding="utf-8") as log_handle:
            # Fixed argv invokes this package's daemon entry point.
            process = subprocess.Popen(  # nosec B603
                command,
                env=env,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
        record = RuntimeRecord(
            pid=int(process.pid),
            host=host,
            port=port,
            endpoint=endpoint,
            token=token,
            started_at=_utc_now(),
            version=__version__,
            log_path=log_path,
        )
        write_runtime_record(record, layout)
        return DaemonStatus(record=record)


def daemon_stop(*, force: bool = False, timeout: float = 5.0) -> bool:
    """Stop the recorded daemon process and remove the runtime record."""
    layout = ensure_state_layout()
    record = read_runtime_record(layout)
    if record is None:
        return False
    if not is_pid_alive(record.pid):
        delete_runtime_record(layout)
        return False

    os.kill(record.pid, signal.SIGTERM)
    if _wait_for_exit(record.pid, timeout):
        delete_runtime_record(layout)
        return True
    if not force:
        raise DaemonStopTimeoutError(f"Daemon process {record.pid} did not stop before timeout")

    os.kill(record.pid, signal.SIGKILL)
    if not _wait_for_exit(record.pid, timeout):
        raise DaemonStopTimeoutError(f"Daemon process {record.pid} did not stop before timeout")
    delete_runtime_record(layout)
    return True


def is_pid_alive(pid: int) -> bool:
    """Return whether a process id currently exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0)
    while is_pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))
    return True
