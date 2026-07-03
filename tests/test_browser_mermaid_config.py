"""Regression tests: the browser Mermaid module URL is configurable."""
from __future__ import annotations

import shutil
from pathlib import Path

from backlog_py.browser.service import render_board_html
from backlog_py.storage.project import discover_project

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _project(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return discover_project(Path.cwd(), explicit_cwd=repo)


def test_mermaid_url_defaults_to_cdn(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"' in html


def test_mermaid_url_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "/assets/mermaid.local.mjs")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url="/assets/mermaid.local.mjs"' in html
    assert "cdn.jsdelivr.net" not in html


def test_mermaid_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url=""' in html
    assert "cdn.jsdelivr.net" not in html
