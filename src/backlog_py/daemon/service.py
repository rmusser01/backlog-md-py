from __future__ import annotations

import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from backlog_py import __version__
from backlog_py.daemon.lifecycle import DEFAULT_HOST, DEFAULT_PORT, daemon_record_is_live
from backlog_py.mcp.http_server import create_mcp_http_server, endpoint_for_server
from backlog_py.runtime.locks import DaemonRuntimeLock, LockTimeoutError
from backlog_py.runtime.state import (
    RuntimeRecord,
    StateLayout,
    ensure_state_layout,
    read_runtime_record,
    write_runtime_record,
)


def run_foreground_service(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    allow_remote: bool = False,
) -> None:
    """Run the foreground daemon HTTP service.

    `allow_remote` is the explicit opt-in required to bind a non-loopback host;
    without it the server refuses, since it would publish every MCP write tool
    over cleartext to the network.
    """
    layout = ensure_state_layout()
    token = os.environ.get("BACKLOG_PY_DAEMON_TOKEN") or os.urandom(32).hex()
    default_log_path = layout.logs_dir / "backlog-md-py-daemon.log"
    log_path = Path(os.environ.get("BACKLOG_PY_DAEMON_LOG", str(default_log_path)))
    server = create_mcp_http_server(host=host, port=port, token=token, allow_remote=allow_remote)
    actual_host, actual_port = server.server_address[:2]
    endpoint = endpoint_for_server(server)
    # When launched by daemon_start (managed), the parent writes the runtime
    # record after verifying health; writing here too would race it. A manually
    # run foreground daemon records itself, but only when doing so cannot strand
    # a daemon that already owns the record.
    if not os.environ.get("BACKLOG_PY_DAEMON_MANAGED"):
        _record_foreground_daemon(
            layout,
            RuntimeRecord(
                pid=os.getpid(),
                host=str(actual_host),
                port=int(actual_port),
                endpoint=endpoint,
                token=token,
                started_at=_utc_now(),
                version=__version__,
                log_path=log_path,
            ),
        )

    shutdown_requested = Event()

    def request_shutdown(signum: int, frame: object) -> None:
        _ = signum, frame
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    server.timeout = 0.25
    try:
        while not shutdown_requested.is_set():
            server.handle_request()
    finally:
        server.server_close()


def _record_foreground_daemon(layout: StateLayout, record: RuntimeRecord) -> bool:
    """Record this foreground daemon unless another live daemon owns the record.

    The runtime record is a singleton whose token is the only way to reach and
    stop the daemon it names: overwriting a live daemon's record would leave it
    running, unreachable and unstoppable. Returns whether the record was written.
    """
    try:
        with DaemonRuntimeLock(operation="daemon_run_foreground").acquire():
            existing = read_runtime_record(layout)
            if existing is not None and existing.pid != record.pid and daemon_record_is_live(existing):
                print(
                    "A daemon is already running at "
                    f"{existing.endpoint} (pid {existing.pid}); leaving its runtime record intact. "
                    f"This foreground daemon on {record.endpoint} stays unmanaged; "
                    "stop the other daemon first to record this one.",
                    flush=True,
                )
                return False
            write_runtime_record(record, layout)
            return True
    except LockTimeoutError:
        print(
            "Could not acquire the daemon runtime lock; leaving the runtime record untouched. "
            f"This foreground daemon on {record.endpoint} stays unmanaged.",
            flush=True,
        )
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
