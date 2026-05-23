from __future__ import annotations

from typing import NoReturn

from backlog_py.core.models import BacklogProject


INSTALL_HINT = "Install with backlog-md-py[tui] to use the Textual TUI."


class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""


def run_tui_app(project: BacklogProject) -> NoReturn:
    """Launch the optional Textual TUI."""
    _ = project
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise TuiDependencyError(INSTALL_HINT) from exc
        raise
    raise RuntimeError("Textual TUI app is not implemented yet.")
