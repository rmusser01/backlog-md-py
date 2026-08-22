"""Optional Textual TUI package for backlog-md-py."""
from __future__ import annotations

import sys
from pathlib import Path


def install_hint() -> str:
    """Return an actionable hint for installing the optional Textual dependency.

    A `uv tool install` environment cannot be reached by `pip install`, and the
    extra is not re-resolvable in place, so it needs its own command.
    """
    parents = Path(sys.prefix).parents
    if len(parents) >= 2 and parents[0].name == "tools" and parents[1].name == "uv":
        return (
            "Textual is not installed in this uv tool environment. Reinstall the tool with it: "
            "uv tool install --force --with textual backlog-md-py"
        )
    return "Install with backlog-md-py[tui] to use the Textual TUI."
