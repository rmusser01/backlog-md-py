"""Regression tests: Mermaid is vendored and served locally by default."""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from backlog_py.browser.service import render_board_html, start_browser_service
from backlog_py.storage.project import discover_project

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _project(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return discover_project(Path.cwd(), explicit_cwd=repo)


def test_mermaid_defaults_to_local_vendored_asset_no_third_party(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url="assets/mermaid.min.js"' in html
    # Privacy: the default board must not reference any third-party host.
    assert "cdn.jsdelivr.net" not in html
    assert "cdn." not in html


def test_mermaid_url_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://example.test/mermaid.js")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url="https://example.test/mermaid.js"' in html
    assert "assets/mermaid.min.js" not in html


def test_mermaid_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url=""' in html


def test_service_serves_vendored_mermaid(tmp_path):
    service = start_browser_service(_project(tmp_path), host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(f"{service.root_url}/assets/mermaid.min.js", timeout=5) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()
    finally:
        service.shutdown()

    assert "javascript" in content_type
    assert len(body) > 1_000_000  # the full vendored UMD build
    assert b"mermaid" in body
