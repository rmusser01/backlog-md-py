"""The optional-TUI hint must name a command that works where the CLI is installed."""
from __future__ import annotations

from backlog_py.tui import install_hint


def test_hint_names_the_uv_command_inside_a_uv_tool_environment(monkeypatch):
    monkeypatch.setattr(
        "backlog_py.tui.sys.prefix",
        "/Users/example/.local/share/uv/tools/backlog-md-py",
    )

    hint = install_hint()

    assert "uv tool install" in hint
    assert "--with textual" in hint


def test_hint_falls_back_to_the_extra_outside_a_uv_tool_environment(monkeypatch):
    monkeypatch.setattr("backlog_py.tui.sys.prefix", "/Users/example/project/.venv")

    hint = install_hint()

    assert "backlog-md-py[tui]" in hint
    assert "uv tool install" not in hint
