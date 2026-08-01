"""Regression tests for browser-service robustness (no crashes / no hangs)."""
from __future__ import annotations

import http.client
import json
import shutil
import socket
import urllib.error
import urllib.request
from pathlib import Path

from backlog_py.browser.service import start_browser_service
from backlog_py.storage.project import discover_project

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _project(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _raw_request(
    service,
    method: str,
    path: str,
    *,
    host_header: str | None = "",
    origin: str | None = None,
    payload: object | None = None,
) -> dict[str, object]:
    """Issue a request with full control over the Host/Origin headers.

    ``host_header=""`` means "use the real bound authority"; ``None`` omits the
    header entirely.
    """
    authority = f"{service.host}:{service.port}"
    connection = http.client.HTTPConnection(service.host, service.port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host_header is not None:
            connection.putheader("Host", authority if host_header == "" else host_header)
        if origin is not None:
            connection.putheader("Origin", origin)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return {
            "status": response.status,
            "headers": dict(response.getheaders()),
            "body": response.read().decode("utf-8"),
        }
    finally:
        connection.close()


def test_get_board_with_broken_orchestration_returns_500_not_dropped_connection(tmp_path):
    project = _project(tmp_path)
    # Invalid orchestration policy makes board payload construction raise.
    (project.backlog_dir / "orchestration.yml").write_text("states: [not, a, mapping\n", encoding="utf-8")

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        try:
            urllib.request.urlopen(f"{service.root_url}/api/board", timeout=5)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        service.shutdown()

    assert status == 500, "board GET should return a 500 error page, not drop the connection"


def test_board_response_sets_security_headers(tmp_path):
    project = _project(tmp_path)
    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            headers = dict(response.headers)
    finally:
        service.shutdown()

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "no-referrer"


def test_oversized_post_body_is_rejected_quickly(tmp_path):
    project = _project(tmp_path)
    service = start_browser_service(project, host="127.0.0.1", port=0)
    host = service.host
    port = service.port
    try:
        request = (
            "POST /api/markdown/preview HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Origin: http://{host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 1000000000\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(request)
            status_line = b""
            while b"\r\n" not in status_line:
                chunk = sock.recv(256)
                if not chunk:
                    break
                status_line += chunk
    finally:
        service.shutdown()

    code = int(status_line.split(b" ")[1])
    assert code in (400, 413), f"oversized body should be rejected, got {status_line!r}"


# --- Host header validation (DNS rebinding) -------------------------------


def test_get_rejects_foreign_host_header(tmp_path):
    """A DNS-rebound page must not be able to read the backlog over GET."""
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        board = _raw_request(service, "GET", "/api/board", host_header=f"attacker.example.com:{service.port}")
        status = _raw_request(
            service,
            "GET",
            "/api/service/status",
            host_header=f"attacker.example.com:{service.port}",
        )
        docs = _raw_request(service, "GET", "/api/docs", host_header="attacker.example.com")
    finally:
        service.shutdown()

    assert board["status"] == 403, board
    assert status["status"] == 403, status
    assert docs["status"] == 403, docs
    assert "projectRoot" not in status["body"]
    assert "columns" not in board["body"]


def test_get_allows_loopback_host_header(tmp_path):
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        board = _raw_request(service, "GET", "/api/board")
        localhost = _raw_request(service, "GET", "/api/board", host_header=f"localhost:{service.port}")
        ipv6 = _raw_request(service, "GET", "/api/board", host_header=f"[::1]:{service.port}")
    finally:
        service.shutdown()

    assert board["status"] == 200, board
    assert localhost["status"] == 200, localhost
    assert ipv6["status"] == 200, ipv6


def test_get_rejects_loopback_host_header_with_wrong_or_missing_port(tmp_path):
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    other_port = service.port + 1 if service.port < 65535 else service.port - 1
    try:
        # Host without a port means the default port 80, which is never the
        # ephemeral port this service is bound to.
        missing_port = _raw_request(service, "GET", "/api/board", host_header="localhost")
        bracketed_missing_port = _raw_request(service, "GET", "/api/board", host_header="[::1]")
        wrong_port = _raw_request(service, "GET", "/api/board", host_header=f"127.0.0.1:{other_port}")
        absent = _raw_request(service, "GET", "/api/board", host_header=None)
    finally:
        service.shutdown()

    assert missing_port["status"] == 403, missing_port
    assert bracketed_missing_port["status"] == 403, bracketed_missing_port
    assert wrong_port["status"] == 403, wrong_port
    assert absent["status"] == 403, absent


def test_post_rejects_foreign_host_header_without_mutation(tmp_path):
    project = _project(tmp_path)
    task_path = project.backlog_dir / "tasks" / "task-1 - Example-task.md"
    before = task_path.read_text(encoding="utf-8")
    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _raw_request(
            service,
            "POST",
            "/api/tasks/TASK-1/status",
            host_header=f"attacker.example.com:{service.port}",
            origin=f"http://attacker.example.com:{service.port}",
            payload={"status": "Done"},
        )
    finally:
        service.shutdown()

    assert response["status"] == 403, response
    assert task_path.read_text(encoding="utf-8") == before


def test_post_allows_matching_loopback_host_and_origin(tmp_path):
    project = _project(tmp_path)
    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _raw_request(
            service,
            "POST",
            "/api/markdown/preview",
            origin=f"http://{service.host}:{service.port}",
            payload={"markdown": "# Title"},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200, response
    assert "<h1>Title</h1>" in response["body"]


# --- Origin required for mutating requests --------------------------------


def test_post_without_origin_header_is_rejected_without_mutation(tmp_path):
    project = _project(tmp_path)
    task_path = project.backlog_dir / "tasks" / "task-1 - Example-task.md"
    before = task_path.read_text(encoding="utf-8")
    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        status_move = _raw_request(
            service,
            "POST",
            "/api/tasks/TASK-1/status",
            payload={"status": "Done"},
        )
        shutdown = _raw_request(service, "POST", "/api/service/shutdown", payload={})
    finally:
        service.shutdown()

    assert status_move["status"] == 403, status_move
    assert shutdown["status"] == 403, shutdown
    assert task_path.read_text(encoding="utf-8") == before


# --- Content-Security-Policy ----------------------------------------------


def _csp_directives(policy: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for chunk in policy.split(";"):
        parts = chunk.split()
        if parts:
            directives[parts[0]] = parts[1:]
    return directives


def test_board_response_sets_content_security_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            board_headers = dict(response.headers)
        with urllib.request.urlopen(f"{service.root_url}api/board", timeout=5) as response:
            api_headers = dict(response.headers)
    finally:
        service.shutdown()

    policy = board_headers.get("Content-Security-Policy")
    assert policy, board_headers
    assert api_headers.get("Content-Security-Policy") == policy
    directives = _csp_directives(policy)
    assert directives["default-src"] == ["'none'"]
    # The board inlines its own CSS/JS and loads the vendored Mermaid build
    # from this same origin.
    assert directives["script-src"] == ["'self'", "'unsafe-inline'"]
    assert "'unsafe-inline'" in directives["style-src"]
    # Same-origin JSON fetches and the /api/board/events EventSource.
    assert directives["connect-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["form-action"] == ["'self'"]
    assert "data:" in directives["img-src"]
    assert "data:" in directives["font-src"]


def test_content_security_policy_allows_configured_remote_mermaid_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid@11/mermaid.min.js")
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            headers = dict(response.headers)
    finally:
        service.shutdown()

    directives = _csp_directives(headers["Content-Security-Policy"])
    assert directives["script-src"] == ["'self'", "'unsafe-inline'", "https://cdn.example.test"]


def test_content_security_policy_allows_self_styles(tmp_path, monkeypatch):
    """`style-src` must list 'self', not only 'unsafe-inline'.

    The board inlines its CSS today, so omitting 'self' is invisible - but the
    Mermaid bundle is already served from /assets, and the first stylesheet that
    follows it there would be blocked by a policy that never allows same-origin
    styles.
    """
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            headers = dict(response.headers)
    finally:
        service.shutdown()

    directives = _csp_directives(headers["Content-Security-Policy"])
    assert directives["style-src"] == ["'self'", "'unsafe-inline'"]


def test_content_security_policy_allows_bracketed_ipv6_mermaid_origin(tmp_path, monkeypatch):
    """A loopback IPv6 mermaid host must reach script-src, not be dropped.

    Dropping it while the URL is still emitted as the script `src` turns a
    working config into an opaque CSP violation in the console.
    """
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "http://[::1]:8080/mermaid.min.js")
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            headers = dict(response.headers)
    finally:
        service.shutdown()

    directives = _csp_directives(headers["Content-Security-Policy"])
    assert directives["script-src"] == ["'self'", "'unsafe-inline'", "http://[::1]:8080"]


def test_content_security_policy_ignores_malformed_mermaid_url(tmp_path, monkeypatch):
    # A hostile env value must not be able to inject extra CSP directives.
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://evil.test; script-src *:443/x.js")
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(service.root_url, timeout=5) as response:
            headers = dict(response.headers)
    finally:
        service.shutdown()

    policy = headers["Content-Security-Policy"]
    assert policy.count("script-src") == 1
    assert "*" not in _csp_directives(policy)["script-src"]


# --- Security headers apply to every method --------------------------------


_SECURITY_HEADERS = ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy")


def test_head_request_mirrors_get_headers_without_a_body(tmp_path):
    """`curl -I` is the obvious way to check the headers; it must not 501."""
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        head = _raw_request(service, "HEAD", "/")
        get = _raw_request(service, "GET", "/")
    finally:
        service.shutdown()

    assert head["status"] == 200, head
    assert head["body"] == ""
    for header in _SECURITY_HEADERS:
        assert head["headers"].get(header) == get["headers"].get(header), header


def test_head_request_honours_the_host_check(tmp_path):
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        response = _raw_request(service, "HEAD", "/api/board", host_header=f"attacker.example.com:{service.port}")
    finally:
        service.shutdown()

    assert response["status"] == 403, response


def test_unsupported_methods_are_rejected_with_security_headers(tmp_path):
    """OPTIONS/PUT/DELETE must not fall through to the stdlib 501.

    `BaseHTTPRequestHandler.send_error` answers before `do_*` runs, so those
    responses skipped both the Host check and every security header.
    """
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        responses = {method: _raw_request(service, method, "/") for method in ("OPTIONS", "PUT", "DELETE", "PATCH")}
        unknown = _raw_request(service, "TRACE", "/")
    finally:
        service.shutdown()

    for method, response in responses.items():
        assert response["status"] == 405, (method, response)
        assert response["headers"].get("Allow") == "GET, POST", (method, response)
        for header in _SECURITY_HEADERS:
            assert response["headers"].get(header), (method, header, response)

    # A verb with no handler at all still goes through the stdlib error path,
    # which must now also carry the headers.
    assert unknown["status"] in (405, 501), unknown
    for header in _SECURITY_HEADERS:
        assert unknown["headers"].get(header), (header, unknown)


def test_unsupported_method_with_foreign_host_is_forbidden(tmp_path):
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        response = _raw_request(service, "PUT", "/api/board", host_header=f"attacker.example.com:{service.port}")
    finally:
        service.shutdown()

    assert response["status"] == 403, response


# --- Endpoint id parsing (decode before validating) -----------------------


def test_task_detail_endpoint_rejects_encoded_separators(tmp_path):
    """`%2F` must be decoded before the single-segment check, not after."""
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        traversal = _raw_request(service, "GET", "/api/tasks/TASK-1%2F..%2Fsecret")
        backslash = _raw_request(service, "GET", "/api/tasks/TASK-1%5C..%5Csecret")
    finally:
        service.shutdown()

    assert traversal["status"] == 404, traversal
    assert json.loads(traversal["body"]) == {"error": "Not found"}
    assert backslash["status"] == 404, backslash
    assert json.loads(backslash["body"]) == {"error": "Not found"}


def test_status_endpoint_rejects_encoded_and_literal_separators_without_mutation(tmp_path):
    project = _project(tmp_path)
    task_path = project.backlog_dir / "tasks" / "task-1 - Example-task.md"
    before = task_path.read_text(encoding="utf-8")
    service = start_browser_service(project, host="127.0.0.1", port=0)
    origin = f"http://{service.host}:{service.port}"
    try:
        encoded = _raw_request(
            service,
            "POST",
            "/api/tasks/..%2F..%2Fetc/status",
            origin=origin,
            payload={"status": "Done"},
        )
        literal = _raw_request(
            service,
            "POST",
            "/api/tasks/a/b/status",
            origin=origin,
            payload={"status": "Done"},
        )
    finally:
        service.shutdown()

    assert encoded["status"] == 404, encoded
    assert json.loads(encoded["body"]) == {"error": "Not found"}
    assert literal["status"] == 404, literal
    assert json.loads(literal["body"]) == {"error": "Not found"}
    assert task_path.read_text(encoding="utf-8") == before


# --- Readonly listing routes degrade instead of blowing up ----------------


def test_documents_route_reports_listing_failure_clearly(tmp_path, monkeypatch):
    """One unreadable doc must not surface as an opaque 500."""
    from backlog_py.core.documents import DocumentService

    def explode(self):  # noqa: ANN001 - test double
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(DocumentService, "list_documents", explode)
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        response = _raw_request(service, "GET", "/api/docs")
    finally:
        service.shutdown()

    body = json.loads(response["body"])
    assert response["status"] == 500, response
    assert "document" in str(body.get("error", "")).casefold(), body
    assert body != {"error": "Internal server error"}


def test_decisions_route_reports_listing_failure_clearly(tmp_path, monkeypatch):
    from backlog_py.core.decisions import DecisionService

    def explode(self):  # noqa: ANN001 - test double
        raise ValueError("malformed frontmatter in decision-3")

    monkeypatch.setattr(DecisionService, "list_decisions", explode)
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        response = _raw_request(service, "GET", "/api/decisions")
    finally:
        service.shutdown()

    body = json.loads(response["body"])
    assert response["status"] == 500, response
    assert "decision" in str(body.get("error", "")).casefold(), body
    assert body != {"error": "Internal server error"}
