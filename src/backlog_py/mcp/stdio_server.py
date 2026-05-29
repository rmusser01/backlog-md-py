from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from backlog_py.daemon.lifecycle import DaemonNotRunningError, daemon_status
from backlog_py.mcp.protocol import INTERNAL_ERROR, McpRequestContext, error_response, handle_jsonrpc_text
from backlog_py.storage.project import discover_project


@dataclass(frozen=True)
class SdkFreeMcpServer:
    """Small SDK-free MCP stdio server facade."""

    name: str = "backlog-md-py"

    def run(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """Run the newline-delimited stdio JSON-RPC loop."""
        run_stdio(stdin=stdin, stdout=stdout)


def create_server() -> SdkFreeMcpServer:
    """Create the SDK-free MCP server facade."""
    return SdkFreeMcpServer()


def run_stdio(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    *,
    context: McpRequestContext | None = None,
    daemon_endpoint: str | None = None,
    token: str | None = None,
) -> None:
    """Run an SDK-free MCP stdio loop using one JSON-RPC message per line."""
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    forward_target = _resolve_forward_target(daemon_endpoint=daemon_endpoint, token=token)
    local_context = _context_with_project_hint(context)
    session_id: str | None = None
    for line in input_stream:
        if not line.strip():
            continue
        if forward_target is None:
            response = handle_jsonrpc_text(line, context=local_context)
        else:
            response, session_id = _forward_jsonrpc_text(line, forward_target, session_id=session_id)
        if response is None:
            continue
        output_stream.write(f"{response}\n")
        output_stream.flush()


def main() -> None:
    """Run the SDK-free MCP stdio server."""
    run_stdio()


def _context_with_project_hint(context: McpRequestContext | None) -> McpRequestContext:
    if context is not None and context.project_hint is not None:
        return context
    project_hint = _discover_project_hint()
    if context is None:
        return McpRequestContext(project_hint=project_hint)
    return McpRequestContext(
        project_hint=project_hint,
        client_id=context.client_id,
        session_id=context.session_id,
    )


def _discover_project_hint() -> str | None:
    try:
        return str(discover_project(Path.cwd()).root)
    except (FileNotFoundError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class _ForwardTarget:
    endpoint: str
    token: str


def _resolve_forward_target(*, daemon_endpoint: str | None, token: str | None) -> _ForwardTarget | None:
    if daemon_endpoint is not None or token is not None:
        if not daemon_endpoint or not token:
            raise ValueError("Both daemon_endpoint and token are required for stdio forwarding")
        _validate_daemon_endpoint(daemon_endpoint)
        return _ForwardTarget(endpoint=daemon_endpoint, token=token)
    try:
        status = daemon_status()
    except DaemonNotRunningError:
        return None
    _validate_daemon_endpoint(status.record.endpoint)
    return _ForwardTarget(endpoint=status.record.endpoint, token=status.record.token)


def _forward_jsonrpc_text(
    text: str,
    target: _ForwardTarget,
    *,
    session_id: str | None,
) -> tuple[str | None, str | None]:
    request = urllib.request.Request(
        target.endpoint,
        data=text.encode("utf-8"),
        headers=_forward_headers(target.token, session_id=session_id),
        method="POST",
    )
    try:
        # Endpoint is validated to loopback HTTP before this request is built.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            body = response.read().decode("utf-8")
            return body or None, response.headers.get("Mcp-Session-Id") or session_id
    except urllib.error.URLError as exc:
        return _forwarding_error(text, exc), session_id


def _forward_headers(token: str, *, session_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _forwarding_error(text: str, exc: Exception) -> str:
    return json.dumps(
        error_response(_request_id(text), INTERNAL_ERROR, "Daemon forwarding failed", {"detail": str(exc)}),
        sort_keys=True,
    )


def _request_id(text: str) -> object | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload.get("id")
    return None


def _validate_daemon_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "http" or parsed.hostname not in loopback_hosts:
        raise ValueError("Daemon forwarding requires a loopback HTTP endpoint")
