from __future__ import annotations

import os
import signal
# Daemon startup uses a fixed Python argv without a shell.
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone

from backlog_py import __version__
from backlog_py.runtime.locks import DaemonRuntimeLock, LockTimeoutError, prune_stale_locks
from backlog_py.runtime.state import (
    RuntimeRecord,
    StateLayout,
    allocate_log_path,
    delete_runtime_record,
    ensure_state_layout,
    prune_daemon_logs,
    read_runtime_record,
    write_runtime_record,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765


class DaemonNotRunningError(RuntimeError):
    """Raised when no healthy daemon runtime record exists."""


class DaemonStartError(RuntimeError):
    """Raised when a freshly launched daemon never becomes healthy."""


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
    if daemon_record_is_live(record):
        return DaemonStatus(record=record)
    # The validation above is unlocked and can take seconds; another process may
    # have started a daemon and replaced the record meanwhile. Re-check under the
    # runtime lock and only drop the exact record that was proven stale.
    _discard_stale_record(layout, record)
    raise DaemonNotRunningError("Daemon not running")


def daemon_record_is_live(record: RuntimeRecord) -> bool:
    """Whether a runtime record still describes a daemon we should keep."""
    if not is_pid_alive(record.pid):
        return False
    # The PID may be alive but reused: only a False (definitely not ours) answer
    # condemns the record; an uncertain None keeps it.
    return _daemon_endpoint_owned(record) is not False


def _record_identity(record: RuntimeRecord) -> tuple[int, str, int, str]:
    """Stable identity for a runtime record, distinguishing daemon instances."""
    return (record.pid, record.host, record.port, record.token)


def _delete_record_if_unchanged(layout: StateLayout, expected: RuntimeRecord) -> bool:
    """Delete the runtime record only when it is still `expected`.

    Callers must hold the daemon runtime lock.
    """
    current = read_runtime_record(layout)
    if current is None or _record_identity(current) != _record_identity(expected):
        return False
    delete_runtime_record(layout)
    return True


def _discard_stale_record(layout: StateLayout, record: RuntimeRecord) -> None:
    try:
        # Cleanup is opportunistic: keep status responsive, and let whoever holds
        # the lock (a start that is about to overwrite the record) win.
        with DaemonRuntimeLock(operation="daemon_status").acquire(timeout=2.0):
            _delete_record_if_unchanged(layout, record)
    except LockTimeoutError:
        # Someone else is mutating daemon state; never delete unsynchronized.
        pass


def _daemon_status_locked(layout: StateLayout) -> DaemonStatus:
    """Status check for callers that already hold the daemon runtime lock."""
    record = read_runtime_record(layout)
    if record is None:
        raise DaemonNotRunningError("Daemon not running")
    if daemon_record_is_live(record):
        return DaemonStatus(record=record)
    _delete_record_if_unchanged(layout, record)
    raise DaemonNotRunningError("Daemon not running")


def daemon_ensure(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    allow_remote: bool = False,
) -> DaemonStatus:
    """Return a healthy daemon status, starting one when needed.

    `allow_remote` is forwarded to `daemon_start`: dropping it would silently
    discard the caller's explicit opt-in to a non-loopback bind.
    """
    try:
        return daemon_status()
    except DaemonNotRunningError:
        return daemon_start(host=host, port=port, allow_remote=allow_remote)


def daemon_start(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    allow_remote: bool = False,
) -> DaemonStatus:
    """Start the singleton daemon unless a healthy runtime record already exists.

    `allow_remote` is forwarded to the child as the explicit opt-in required to
    bind a non-loopback host.
    """
    with DaemonRuntimeLock(operation="daemon_start").acquire():
        layout = ensure_state_layout()
        try:
            return _daemon_status_locked(layout)
        except DaemonNotRunningError:
            pass

        _prune_state_dir(layout)
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
        if allow_remote:
            command.append("--allow-remote")
        env = {
            **os.environ,
            "BACKLOG_PY_DAEMON_TOKEN": token,
            "BACKLOG_PY_DAEMON_LOG": str(log_path),
            # Managed by this parent: the parent writes the authoritative
            # runtime record after the health check, so the child must not also
            # write one (avoids a two-writer race on daemon.json).
            "BACKLOG_PY_DAEMON_MANAGED": "1",
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
        # Only record the daemon as running once the child has actually bound
        # the port. Otherwise a failed start (e.g. port collision) would be
        # recorded as healthy, and daemon_ensure would crash-loop respawning it.
        if not _wait_for_daemon_healthy(host, port, process, token=token):
            _terminate_process(process)
            raise DaemonStartError(
                f"Daemon failed to become healthy on {host}:{port}; see log {log_path}"
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
    with DaemonRuntimeLock(operation="daemon_stop").acquire():
        return _daemon_stop_locked(force=force, timeout=timeout)


def _daemon_stop_locked(*, force: bool, timeout: float) -> bool:
    layout = ensure_state_layout()
    record = read_runtime_record(layout)
    if record is None:
        return False
    if not is_pid_alive(record.pid):
        delete_runtime_record(layout)
        return False
    if _daemon_endpoint_owned(record) is False:
        # The PID was reused by an unrelated process; never signal it.
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


def _wait_for_daemon_healthy(
    host: str,
    port: int,
    process: "subprocess.Popen[bytes]",
    *,
    token: str,
    timeout: float = 10.0,
) -> bool:
    """Poll the daemon's authenticated /status endpoint until the child answers.

    /health is deliberately unauthenticated, so any other local listener that
    squats on the port would satisfy it while our child dies on a bind failure.
    Only the freshly minted token proves the listener is the child we launched.
    """
    url = f"http://{_bracketed_host(host)}:{port}/status"
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + max(timeout, 0)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False  # child exited before binding
        try:
            request = urllib.request.Request(url, headers=headers)
            # Loopback HTTP with the child's bearer token; this confirms identity.
            with urllib.request.urlopen(request, timeout=1) as response:  # nosec B310
                if response.status == 200:
                    # Re-check the child: a listener answering with our token is
                    # our daemon, but the record must name a process still alive.
                    return process.poll() is None
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.1)
    return False


def _prune_state_dir(layout: StateLayout) -> None:
    """Best-effort bounded cleanup of accumulated runtime state."""
    for prune in (lambda: prune_daemon_logs(layout), prune_stale_locks):
        with suppress(OSError):
            prune()


def _terminate_process(process: "subprocess.Popen[bytes]") -> None:
    with suppress(Exception):
        if process.poll() is None:
            process.terminate()
            with suppress(Exception):
                process.wait(timeout=2)


def _bracketed_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _status_url(record: RuntimeRecord) -> str:
    return f"http://{_bracketed_host(record.host)}:{record.port}/status"


def _daemon_endpoint_owned(record: RuntimeRecord) -> bool | None:
    """Whether the recorded endpoint answers as *our* daemon.

    Returns True when the authenticated /status endpoint responds, False when a
    different process (or nothing) holds the address — the signal that the
    recorded PID has been reused — and None when ownership is uncertain (e.g. a
    slow daemon timing out), so callers do not tear down a live daemon.
    """
    request = urllib.request.Request(
        _status_url(record), headers={"Authorization": f"Bearer {record.token}"}
    )
    try:
        # Endpoint is loopback HTTP with a bearer token; this confirms identity.
        with urllib.request.urlopen(request, timeout=2) as response:  # nosec B310
            return response.status == 200
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return False
        return None
    except OSError:
        return None


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
    while True:
        _reap_child(pid)
        if not is_pid_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))


def _reap_child(pid: int) -> None:
    """Reap `pid` when it is a child of this process.

    A daemon started from this same process stays a zombie until it is reaped,
    and a zombie still answers `kill(pid, 0)`, so stopping it would otherwise
    look like a timeout. Not-our-child raises and is ignored.
    """
    if os.name == "nt":
        return
    with suppress(OSError):
        os.waitpid(pid, os.WNOHANG)
