from __future__ import annotations

import hmac
import json
import socket
import threading
import uuid
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import urlparse

from backlog_py.mcp.protocol import McpRequestContext, handle_jsonrpc_text
from backlog_py.security.http import LOOPBACK_HOSTNAMES, host_header_is_loopback, http_url


# The daemon exposes the whole MCP surface, including every write tool, behind a
# bearer token over cleartext HTTP. Binding anything but loopback would publish
# it to the network, so the address is restricted exactly like the browser
# service (see backlog_py.browser.service.create_browser_server).
# Sourced from the shared helper so the browser and MCP servers cannot drift
# on what counts as loopback. Re-exported: cli/main.py imports this name.
LOOPBACK_HOSTS = LOOPBACK_HOSTNAMES

# Port implied by a Host header that carries no explicit port.


# Cap a single JSON-RPC request body so a malicious/oversized Content-Length
# cannot exhaust daemon memory. 10 MiB is far above any legitimate tool call.
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024

# Socket timeout (seconds) so a slow or idle client cannot pin a handler thread
# indefinitely (slowloris).
REQUEST_TIMEOUT_SECONDS = 30


class _RequestBodyError(Exception):
    """Signals an unreadable request body with the HTTP status to return."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class McpHttpService:
    """Background SDK-free MCP HTTP service used by tests and daemon startup."""

    server: McpThreadingHTTPServer
    thread: threading.Thread
    host: str
    port: int
    token: str
    endpoint: str

    def shutdown(self) -> None:
        """Stop the background HTTP service."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def create_mcp_http_server(
    *,
    host: str,
    port: int,
    token: str,
    context: McpRequestContext | None = None,
    allow_remote: bool = False,
) -> "McpThreadingHTTPServer":
    """Create a loopback SDK-free MCP HTTP server without starting it.

    Non-loopback binds require `allow_remote=True`: they publish every MCP tool,
    including writes, over cleartext to the network.
    """
    if not token:
        raise ValueError("MCP HTTP daemon token is required")
    if not allow_remote and host not in LOOPBACK_HOSTS:
        raise ValueError("MCP HTTP daemon only supports loopback hosts")
    server_class = _McpIPv6ThreadingHTTPServer if ":" in host else McpThreadingHTTPServer
    server = server_class((host, port), _McpHttpHandler)
    server.daemon_token = token
    server.mcp_context = context or McpRequestContext()
    server.allow_remote = bool(allow_remote)
    return server


def start_mcp_http_server(
    *,
    host: str,
    port: int,
    token: str,
    context: McpRequestContext | None = None,
    allow_remote: bool = False,
) -> McpHttpService:
    """Start a background SDK-free MCP HTTP server."""
    server = create_mcp_http_server(
        host=host, port=port, token=token, context=context, allow_remote=allow_remote
    )
    thread = threading.Thread(target=server.serve_forever, name="backlog-md-py-mcp-http", daemon=True)
    thread.start()
    actual_host, actual_port = server.server_address[:2]
    return McpHttpService(
        server=server,
        thread=thread,
        host=str(actual_host),
        port=int(actual_port),
        token=token,
        endpoint=endpoint_for_server(server),
    )


def endpoint_for_server(server: ThreadingHTTPServer) -> str:
    """Return the server's `/mcp` endpoint URL."""
    host, port = server.server_address[:2]
    return http_url(str(host), int(port), "/mcp")


class McpThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying daemon auth and MCP context."""

    daemon_token: str
    mcp_context: McpRequestContext
    allow_remote: bool = False


class _McpIPv6ThreadingHTTPServer(McpThreadingHTTPServer):
    address_family = socket.AF_INET6


class _McpHttpHandler(BaseHTTPRequestHandler):
    server: McpThreadingHTTPServer
    timeout = REQUEST_TIMEOUT_SECONDS

    def do_GET(self) -> None:
        if not self._host_header_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/status":
            if not self._is_authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "endpoint": endpoint_for_server(self.server)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._host_header_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        path = urlparse(self.path).path
        if path != "/mcp":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not self._is_authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        try:
            text = self._read_body()
        except _RequestBodyError as exc:
            self._send_json(exc.status, {"error": exc.message})
            return
        response_session_id = self.headers.get("Mcp-Session-Id")
        if response_session_id is None and _contains_initialize(text):
            response_session_id = uuid.uuid4().hex
        context = replace(self.server.mcp_context, session_id=response_session_id)
        response = handle_jsonrpc_text(text, context=context)
        headers = {}
        if response_session_id is not None:
            headers["Mcp-Session-Id"] = response_session_id
        if response is None:
            self._send_empty(HTTPStatus.NO_CONTENT, headers=headers)
            return
        self._send_text(HTTPStatus.OK, response, headers=headers)

    def log_message(self, format: str, *args: object) -> None:
        _ = format, args

    def _host_header_allowed(self) -> bool:
        """Reject Host headers that do not name this loopback listener.

        Blocks DNS rebinding: a browser lured to an attacker domain that resolves
        to 127.0.0.1 still sends that domain in Host, so it never matches.
        """
        if getattr(self.server, "allow_remote", False):
            return True  # explicitly published off-loopback; Host cannot be pinned
        return host_header_is_loopback(
            self.headers.get("Host"), int(self.server.server_address[1])
        )

    def _is_authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.daemon_token}"
        return hmac.compare_digest(header, expected)

    def _read_body(self) -> str:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return ""
        try:
            length = int(raw_length)
        except ValueError:
            raise _RequestBodyError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
        if length < 0:
            raise _RequestBodyError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
        if length > MAX_REQUEST_BODY_BYTES:
            raise _RequestBodyError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        try:
            raw = self.rfile.read(length)
        except OSError:
            raise _RequestBodyError(HTTPStatus.BAD_REQUEST, "Failed to read request body")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _RequestBodyError(HTTPStatus.BAD_REQUEST, "Request body must be UTF-8")

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._send_text(status, json.dumps(payload, sort_keys=True), headers=headers)

    def _send_text(
        self,
        status: HTTPStatus,
        text: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_empty(self, status: HTTPStatus, *, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self._send_security_headers()
        self.end_headers()

    def _send_security_headers(self) -> None:
        # Purely defensive, additive headers: block MIME sniffing, framing, and
        # referrer leakage, mirroring the browser service.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")


def _contains_initialize(text: str) -> bool:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if isinstance(payload, dict):
        return payload.get("method") == "initialize"
    if isinstance(payload, list):
        return any(isinstance(item, dict) and item.get("method") == "initialize" for item in payload)
    return False
