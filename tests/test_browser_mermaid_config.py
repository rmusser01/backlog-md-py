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
    assert 'data-mermaid-url="/assets/mermaid.min.js"' in html
    # Privacy: the default board loads Mermaid from the local asset, not a CDN.
    assert "cdn.jsdelivr.net" not in html
    assert "mermaid@" not in html


def test_mermaid_url_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://example.test/mermaid.js")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url="https://example.test/mermaid.js"' in html
    assert "assets/mermaid.min.js" not in html


def test_mermaid_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "")
    html = render_board_html(_project(tmp_path))
    assert 'data-mermaid-url=""' in html


_VALID_SRI = "sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/uxy9rx7HNQlGYl1kPzQho1wx4JwY8wC"


def test_mermaid_sri_attribute_is_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_SRI", raising=False)
    html = render_board_html(_project(tmp_path))

    # The local vendored asset needs no integrity attribute.
    assert "data-mermaid-sri" not in html
    assert 'data-mermaid-url="/assets/mermaid.min.js"' in html


def test_mermaid_sri_is_ignored_for_the_local_vendored_asset(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", _VALID_SRI)
    html = render_board_html(_project(tmp_path))

    # Integrity only applies to a third-party build; enforcing a stale digest
    # on the vendored asset would silently disable diagrams.
    assert "data-mermaid-sri" not in html


def test_mermaid_sri_is_ignored_when_rendering_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", _VALID_SRI)
    html = render_board_html(_project(tmp_path))

    # Nothing is loaded at all, so there is no subresource to gate.
    assert 'data-mermaid-url=""' in html
    assert "data-mermaid-sri" not in html


def test_mermaid_sri_is_exposed_for_remote_url(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", _VALID_SRI)
    html = render_board_html(_project(tmp_path))

    assert 'data-mermaid-url="https://cdn.example.test/mermaid.min.js"' in html
    assert f'data-mermaid-sri="{_VALID_SRI}"' in html


def test_mermaid_sri_ignores_malformed_value(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", 'not-a-digest" onload="alert(1)')
    html = render_board_html(_project(tmp_path))

    assert "data-mermaid-sri" not in html
    assert "onload=" not in html


def _captured_warnings():
    """Collect loguru warnings; caplog does not receive loguru output."""
    from loguru import logger as loguru_logger

    messages: list[str] = []
    sink_id = loguru_logger.add(lambda message: messages.append(str(message)), level="WARNING")
    return messages, sink_id


def _render_capturing_warnings(project) -> tuple[str, list[str]]:
    from loguru import logger as loguru_logger

    messages, sink_id = _captured_warnings()
    try:
        html = render_board_html(project)
    finally:
        loguru_logger.remove(sink_id)
    return html, messages


def test_remote_mermaid_without_integrity_warns(tmp_path, monkeypatch):
    """Loading third-party JS with no digest must not be silent."""
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_SRI", raising=False)

    html, warnings = _render_capturing_warnings(_project(tmp_path))

    assert "data-mermaid-sri" not in html
    joined = "\n".join(warnings)
    assert "https://cdn.example.test" in joined, warnings
    assert "BACKLOG_PY_BROWSER_MERMAID_SRI" in joined, warnings


def test_remote_mermaid_with_malformed_integrity_warns_that_it_was_rejected(tmp_path, monkeypatch):
    """A typo'd digest must not silently degrade to "no integrity"."""
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", "sha384-not a digest")

    html, warnings = _render_capturing_warnings(_project(tmp_path))

    assert "data-mermaid-sri" not in html
    joined = "\n".join(warnings).casefold()
    assert "reject" in joined or "malformed" in joined or "invalid" in joined, warnings


def test_local_and_disabled_mermaid_do_not_warn(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_SRI", raising=False)
    _, local_warnings = _render_capturing_warnings(project)

    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "")
    _, disabled_warnings = _render_capturing_warnings(project)

    assert not [line for line in local_warnings if "mermaid" in line.casefold()], local_warnings
    assert not [line for line in disabled_warnings if "mermaid" in line.casefold()], disabled_warnings


def test_valid_remote_integrity_does_not_warn(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", _VALID_SRI)

    html, warnings = _render_capturing_warnings(_project(tmp_path))

    assert f'data-mermaid-sri="{_VALID_SRI}"' in html
    assert not [line for line in warnings if "mermaid" in line.casefold()], warnings


_BASE64URL_SRI = "sha384-oqVuAfXRKap7fdgcCY5uykM6-R9GqQ8K_uxy9rx7HNQlGYl1kPzQho1wx4JwY8wC"


def test_mermaid_sri_accepts_base64url_and_option_suffix(tmp_path, monkeypatch):
    """Base64url digests and the spec-legal `?option` suffix are valid SRI."""
    project = _project(tmp_path)
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", _BASE64URL_SRI)
    assert f'data-mermaid-sri="{_BASE64URL_SRI}"' in render_board_html(project)

    with_option = f"{_VALID_SRI}?ct=application/javascript"
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", with_option)
    html = render_board_html(project)
    assert 'data-mermaid-sri="sha384-' in html
    assert "?ct=application/javascript" in html


def test_mermaid_sri_still_rejects_quote_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_URL", "https://cdn.example.test/mermaid.min.js")
    monkeypatch.setenv("BACKLOG_PY_BROWSER_MERMAID_SRI", 'sha384-abc?x=" onload="alert(1)')
    html = render_board_html(_project(tmp_path))

    assert "data-mermaid-sri" not in html
    assert "onload=" not in html


def test_board_applies_integrity_and_crossorigin_when_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLOG_PY_BROWSER_MERMAID_URL", raising=False)
    html = render_board_html(_project(tmp_path))

    assert "script.integrity = mermaidIntegrity;" in html
    assert 'script.crossOrigin = "anonymous";' in html


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
