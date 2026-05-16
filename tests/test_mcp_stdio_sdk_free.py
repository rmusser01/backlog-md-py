import importlib.util
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backlog_py.mcp import server as mcp_server
from backlog_py.mcp.stdio_server import run_stdio


def test_create_server_no_longer_requires_mcp_sdk(monkeypatch):
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "mcp" else original_find_spec(name),
    )

    server = mcp_server.create_server()

    assert server.name == "backlog-md-py"


def test_stdio_server_handles_initialize_line():
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    stdout = io.StringIO()

    run_stdio(stdin=stdin, stdout=stdout)

    response = json.loads(stdout.getvalue())
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "backlog-md-py"


def test_stdio_server_omits_notification_output():
    stdin = io.StringIO('{"jsonrpc":"2.0","method":"ping"}\n')
    stdout = io.StringIO()

    run_stdio(stdin=stdin, stdout=stdout)

    assert stdout.getvalue() == ""


def test_stdio_forwarding_posts_to_daemon_without_local_dispatch(monkeypatch):
    fake = _FakeDaemon()
    monkeypatch.setattr(
        "backlog_py.mcp.stdio_server.handle_jsonrpc_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local dispatcher should not run")),
    )
    stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    stdout = io.StringIO()

    try:
        run_stdio(stdin=stdin, stdout=stdout, daemon_endpoint=fake.endpoint, token="secret")
    finally:
        fake.shutdown()

    response = json.loads(stdout.getvalue())
    assert response == {"jsonrpc": "2.0", "id": 7, "result": {"proxied": True}}
    assert fake.requests == [
        {
            "authorization": "Bearer secret",
            "body": {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        }
    ]


def test_stdio_forwarding_rejects_non_loopback_endpoint():
    stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    stdout = io.StringIO()

    with pytest.raises(ValueError, match="loopback HTTP"):
        run_stdio(
            stdin=stdin,
            stdout=stdout,
            daemon_endpoint="file:///tmp/daemon.sock",
            token="secret",
        )


class _FakeDaemon:
    def __init__(self) -> None:
        self.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.requests = self.requests
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}/mcp"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.server.requests.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                payload = {"jsonrpc": "2.0", "id": body["id"], "result": {"proxied": True}}
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

        return Handler
