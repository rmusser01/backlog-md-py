import json
import urllib.error
import urllib.request

import pytest

from backlog_py.mcp.http_server import start_mcp_http_server


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
