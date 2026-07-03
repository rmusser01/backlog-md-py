from __future__ import annotations

import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from backlog_py import __version__
from backlog_py.daemon.lifecycle import DEFAULT_HOST, DEFAULT_PORT
from backlog_py.mcp.http_server import create_mcp_http_server, endpoint_for_server
from backlog_py.runtime.state import RuntimeRecord, ensure_state_layout, write_runtime_record


def run_foreground_service(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the foreground daemon HTTP service."""
    layout = ensure_state_layout()
    token = os.environ.get("BACKLOG_PY_DAEMON_TOKEN") or os.urandom(32).hex()
    default_log_path = layout.logs_dir / "backlog-md-py-daemon.log"
    log_path = Path(os.environ.get("BACKLOG_PY_DAEMON_LOG", str(default_log_path)))
    server = create_mcp_http_server(host=host, port=port, token=token)
    actual_host, actual_port = server.server_address[:2]
    endpoint = endpoint_for_server(server)
    # When launched by daemon_start (managed), the parent writes the runtime
    # record after verifying health; writing here too would race it. A manually
    # run foreground daemon is the sole writer and records itself.
    if not os.environ.get("BACKLOG_PY_DAEMON_MANAGED"):
        write_runtime_record(
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
            layout,
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
