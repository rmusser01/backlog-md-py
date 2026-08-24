"""Optional Textual TUI package for backlog-md-py."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
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


def tui_log_path() -> Path:
    """Where TUI-session log output is written."""
    from backlog_py.runtime.state import resolve_state_dir

    return resolve_state_dir() / "logs" / "tui.log"


@contextmanager
def tui_log_sink() -> Iterator[Path]:
    """Route loguru to a file while the TUI owns the terminal.

    loguru's default sink is stderr, which is the same terminal Textual is
    painting, so any log line lands *on top of* the interface. Five
    ``logger.warning`` sites fire during an ordinary board read -- duplicate
    ids, unreadable task files, symlinked files skipped, SQLite index fallback
    -- and against a real project that put seventeen lines through one frame,
    overwriting the header and smearing a column across the board.

    Dropping the messages instead would be worse: an unreadable task file that
    nobody hears about is how thirty-two of them accumulated in one project. So
    they are kept, just not on the screen.

    Yields:
        Path: the file receiving log output for this session.
    """
    from loguru import logger

    log_path = tui_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Nothing in this package configures loguru, so the only handler being
    # removed here is the default stderr one; it is restored on the way out.
    logger.remove()
    sink_id = logger.add(log_path, level="WARNING", enqueue=False, backtrace=False)
    try:
        yield log_path
    finally:
        logger.remove(sink_id)
        logger.add(sys.stderr, level="WARNING")
