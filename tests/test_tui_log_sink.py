"""The TUI must not let log output land on the screen it is painting."""
from __future__ import annotations

from loguru import logger

from backlog_py.tui import tui_log_sink

def test_tui_log_sink_keeps_loguru_off_the_terminal(tmp_path, capsys, monkeypatch):
    """Anything logged while the TUI paints corrupts the screen it is painting.

    Five `logger.warning` sites fire during an ordinary board read (duplicate
    ids, unreadable files, symlink skips, index fallback). Against a real
    project that put 17 log lines through the frame, overwriting the header and
    smearing a column across the board.
    """
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    with tui_log_sink() as log_path:
        logger.warning("duplicate task id TASK-1")

    captured = capsys.readouterr()
    assert "duplicate task id" not in captured.err, "loguru still writes to the terminal"
    assert "duplicate task id" not in captured.out
    assert log_path.read_text(encoding="utf-8").count("duplicate task id") == 1


def test_tui_log_sink_restores_the_previous_sinks(tmp_path, capsys, monkeypatch):
    """The TUI owns the terminal only while it runs."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    with tui_log_sink():
        pass
    logger.warning("after the tui exits")

    assert "after the tui exits" in capsys.readouterr().err
