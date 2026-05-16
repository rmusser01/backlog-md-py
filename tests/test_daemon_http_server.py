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
