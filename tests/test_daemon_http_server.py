import json
import socket
import urllib.error
import urllib.request

import pytest

from backlog_py.mcp.http_server import create_mcp_http_server, start_mcp_http_server


def test_mcp_http_server_supports_ipv6_loopback(ipv6_loopback_available):
    service = start_mcp_http_server(host="::1", port=0, token="secret")
    try:
        assert service.server.address_family == socket.AF_INET6
        assert service.endpoint == f"http://[::1]:{service.port}/mcp"
    finally:
        service.shutdown()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.test"])
def test_create_mcp_http_server_rejects_non_loopback_hosts(host):
    with pytest.raises(ValueError, match="loopback"):
        create_mcp_http_server(host=host, port=0, token="secret")


def test_create_mcp_http_server_allows_non_loopback_only_on_explicit_opt_in():
    # TEST-NET-1 is never assigned locally, so getting past the loopback guard
    # surfaces as a bind error rather than a ValueError (and binds nothing).
    with pytest.raises(OSError) as exc:
        create_mcp_http_server(host="192.0.2.1", port=0, token="secret", allow_remote=True)

    assert not isinstance(exc.value, ValueError)


def test_create_mcp_http_server_still_requires_a_token():
    with pytest.raises(ValueError, match="token"):
        create_mcp_http_server(host="127.0.0.1", port=0, token="")


def test_http_responses_carry_security_headers():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = urllib.request.urlopen(f"http://{service.host}:{service.port}/health")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
    finally:
        service.shutdown()


def test_http_endpoint_rejects_foreign_host_header():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/health",
            host_header="backlog.example.com",
        )

        assert _status_code(response) == 403
    finally:
        service.shutdown()


def test_http_endpoint_rejects_host_header_with_a_foreign_port():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/status",
            host_header=f"127.0.0.1:{service.port + 1}",
            token="secret",
        )

        assert _status_code(response) == 403
    finally:
        service.shutdown()


def test_http_endpoint_accepts_loopback_host_header():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/status",
            host_header=f"localhost:{service.port}",
            token="secret",
        )

        assert _status_code(response) == 200
    finally:
        service.shutdown()


def test_http_endpoint_rejects_a_missing_host_header():
    """Fail closed like the browser service: no Host means no rebinding check."""
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/health",
            omit_host=True,
        )

        assert _status_code(response) == 403
    finally:
        service.shutdown()


def test_http_endpoint_rejects_a_host_header_with_userinfo():
    """urlparse('//evil.com@127.0.0.1:PORT').hostname silently drops the userinfo."""
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/status",
            host_header=f"evil.example.com@127.0.0.1:{service.port}",
            token="secret",
        )

        assert _status_code(response) == 403
    finally:
        service.shutdown()


@pytest.mark.parametrize("host_header", ["", "127.0.0.1:", "127.0.0.1:notaport", "[::1", "::1:80"])
def test_http_endpoint_rejects_malformed_host_headers(host_header):
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_request(
            service.host,
            service.port,
            "GET",
            "/status",
            host_header=host_header,
            token="secret",
        )

        assert _status_code(response) == 403
    finally:
        service.shutdown()


def _raw_request(host, port, method, path, *, host_header=None, omit_host=False, token=None, timeout=5.0):
    import socket

    lines = [f"{method} {path} HTTP/1.1"]
    if not omit_host:
        lines.append(f"Host: {host_header if host_header is not None else f'{host}:{port}'}")
    if token is not None:
        lines.append(f"Authorization: Bearer {token}")
    lines.append("Connection: close")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("latin1")


def test_http_endpoint_requires_daemon_token():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(_request(service.endpoint, {"jsonrpc": "2.0", "id": 1, "method": "ping"}))

        assert exc.value.code == 401
    finally:
        service.shutdown()


def test_http_endpoint_handles_authorized_initialize_and_sets_session_header():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = urllib.request.urlopen(
            _request(
                service.endpoint,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                token="secret",
            )
        )

        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert response.headers["Mcp-Session-Id"]
        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "backlog-md-py"
    finally:
        service.shutdown()


def test_http_endpoint_handles_authorized_batch_request():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = urllib.request.urlopen(
            _request(
                service.endpoint,
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ],
                token="secret",
            )
        )

        payload = json.loads(response.read().decode("utf-8"))
        assert [item["id"] for item in payload] == [1, 2]
        assert payload[0]["result"] == {}
        assert "tools" in payload[1]["result"]
    finally:
        service.shutdown()


def _request(url: str, payload: object, *, token: str | None = None) -> urllib.request.Request:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def _raw_mcp_post(host, port, extra_headers, *, token="secret", body=b"", timeout=5.0):
    import socket

    request = (
        "POST /mcp HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Authorization: Bearer {token}\r\n"
        "Content-Type: application/json\r\n"
        + extra_headers
        + "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("latin1")


def _status_code(raw_response: str) -> int:
    return int(raw_response.split("\r\n", 1)[0].split(" ")[1])


def test_http_endpoint_rejects_garbage_content_length():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_mcp_post(service.host, service.port, "Content-Length: not-a-number\r\n")
        assert _status_code(response) == 400
    finally:
        service.shutdown()


def test_http_endpoint_rejects_oversized_content_length_without_reading_body():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        response = _raw_mcp_post(
            service.host, service.port, "Content-Length: 1000000000\r\n", body=b""
        )
        assert _status_code(response) == 413
    finally:
        service.shutdown()
