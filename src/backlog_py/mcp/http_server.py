from __future__ import annotations

import hmac
import json
import threading
import uuid
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import urlparse

from backlog_py.mcp.protocol import McpRequestContext, handle_jsonrpc_text


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
) -> "McpThreadingHTTPServer":
    """Create a loopback SDK-free MCP HTTP server without starting it."""
    if not token:
        raise ValueError("MCP HTTP daemon token is required")
    server = McpThreadingHTTPServer((host, port), _McpHttpHandler)
    server.daemon_token = token
    server.mcp_context = context or McpRequestContext()
    return server


def start_mcp_http_server(
    *,
    host: str,
    port: int,
    token: str,
    context: McpRequestContext | None = None,
) -> McpHttpService:
    """Start a background SDK-free MCP HTTP server."""
    server = create_mcp_http_server(host=host, port=port, token=token, context=context)
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
    return f"http://{host}:{port}/mcp"


class McpThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying daemon auth and MCP context."""

    daemon_token: str
    mcp_context: McpRequestContext


class _McpHttpHandler(BaseHTTPRequestHandler):
    server: McpThreadingHTTPServer
    timeout = REQUEST_TIMEOUT_SECONDS

    def do_GET(self) -> None:
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
        self.end_headers()
        self.wfile.write(data)

    def _send_empty(self, status: HTTPStatus, *, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()


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
