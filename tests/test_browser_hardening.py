"""Regression tests for browser-service robustness (no crashes / no hangs)."""
from __future__ import annotations

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


def test_oversized_post_body_is_rejected_quickly(tmp_path):
    project = _project(tmp_path)
    service = start_browser_service(project, host="127.0.0.1", port=0)
    host = service.host
    port = service.port
    try:
        request = (
            "POST /api/markdown/preview HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
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
