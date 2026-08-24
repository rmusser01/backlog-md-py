"""Transient progress lines for work that takes long enough to look stuck.

Deliberately opt-in per entry point rather than switched on by a TTY check.
stdout and stderr belong to whatever owns the terminal, and the TUI owns it
completely: loguru writing to stderr painted log lines straight through the
board (fixed in #174), and progress output would do exactly the same. Only the
plain CLI turns this on.
"""

from __future__ import annotations

import sys
from typing import TextIO

_stream: TextIO | None = None
_last_width = 0


def enable(stream: TextIO | None = None) -> None:
    """Send progress to `stream` (default stderr) if it is an interactive terminal."""
    global _stream
    target = stream if stream is not None else sys.stderr
    _stream = target if getattr(target, "isatty", lambda: False)() else None
    if _stream is not None:
        _route_logs_around_the_progress_line()


def _route_logs_around_the_progress_line() -> None:
    """Make loguru erase the progress line before it writes.

    Both write to stderr, so without this a warning lands on top of a progress
    line and the next progress write chews through the warning -- transient
    noise damaging real output. The TUI hit the same collision from the other
    side (#174).
    """
    from loguru import logger

    def sink(message: object) -> None:
        clear()
        sys.stderr.write(str(message))
        sys.stderr.flush()

    logger.remove()
    logger.add(sink, colorize=True)


def disable() -> None:
    """Stop reporting, clearing any line still on screen."""
    global _stream
    clear()
    _stream = None


def report(message: str) -> None:
    """Overwrite the current progress line. No-op unless reporting is enabled."""
    global _last_width
    if _stream is None:
        return
    padding = max(_last_width - len(message), 0)
    _stream.write(f"\r{message}{' ' * padding}")
    _stream.flush()
    _last_width = len(message)


def clear() -> None:
    """Remove the progress line so real output starts on a clean row."""
    global _last_width
    if _stream is None or _last_width == 0:
        return
    _stream.write("\r" + " " * _last_width + "\r")
    _stream.flush()
    _last_width = 0
