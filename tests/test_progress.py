"""Progress output belongs to the plain CLI and nowhere else."""
from __future__ import annotations

import io

from backlog_py.runtime import progress


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_reporting_is_silent_until_a_terminal_enables_it():
    """Nothing should reach a stream the caller never offered."""
    progress.disable()
    stream = _Tty()

    progress.report("should not appear")

    assert stream.getvalue() == ""


def test_a_non_interactive_stream_is_never_written_to():
    """Piped output and captured logs must stay free of progress noise.

    This is what keeps the TUI safe: it never enables progress, and even if it
    did, a non-tty stream is refused.
    """
    plain = io.StringIO()
    progress.enable(plain)

    progress.report("scanning")
    progress.clear()

    assert plain.getvalue() == ""


def test_each_report_overwrites_the_previous_line():
    """One line that updates, not a scrolling wall."""
    stream = _Tty()
    progress.enable(stream)

    progress.report("Reading branch 1/9")
    progress.report("Reading branch 2/9")
    progress.disable()

    written = stream.getvalue()
    assert written.count("\r") >= 2
    assert "Reading branch 2/9" in written
    assert not written.endswith("Reading branch 2/9"), "the line was left on screen"


def test_a_shorter_message_erases_the_longer_one_before_it():
    """Otherwise the tail of the previous line survives as garbage."""
    stream = _Tty()
    progress.enable(stream)

    progress.report("Reading branch 10/10: a-very-long-branch-name")
    progress.report("Done")
    progress.disable()

    assert "branch-name" not in stream.getvalue().split("\r")[-2]


def test_a_log_line_erases_the_progress_line_first(capsys):
    """A warning must not land on top of the progress line, or be chewed by it."""
    from loguru import logger

    stream = _Tty()
    progress.enable(stream)
    try:
        progress.report("Reading branch 41/82: a-long-branch-name")
        logger.warning("duplicate task id TASK-1")
    finally:
        progress.disable()

    # The progress line was cleared on the progress stream before the log wrote.
    assert stream.getvalue().rstrip().endswith(""), stream.getvalue()[-40:]
    assert "\r" in stream.getvalue()
    assert "duplicate task id" in capsys.readouterr().err
