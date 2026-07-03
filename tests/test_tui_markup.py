"""Regression test: task content containing Rich-markup metacharacters."""
from __future__ import annotations

import pytest

pytest.importorskip("textual")

from backlog_py.tui.app import BacklogTuiApp
from backlog_py.tui.widgets import TaskInspector

from test_tui_interactions import _MutableSource, _project, _snapshot, _task_view
from backlog_py.tui.models import BoardSnapshot


@pytest.mark.asyncio
async def test_inspector_renders_markup_like_title_without_crashing():
    snapshot = _snapshot(title="Fix list[str] and [/] handling")
    app = BacklogTuiApp(project=_project(), data_source=_MutableSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        inspector = app.query_one(TaskInspector)
        rendered = inspector.render()
        text = rendered.plain if hasattr(rendered, "plain") else str(rendered)

    assert "list[str]" in text, "markup metacharacters were swallowed by the renderer"
    assert "[/]" in text


@pytest.mark.asyncio
async def test_board_renders_non_ascii_task_id_without_crashing():
    task = _task_view("tâche-1", "Unicode id", "To Do")
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (task,), "In Progress": (), "Done": ()},
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_MutableSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.query(".task-card").nodes, "board failed to render a card for a non-ASCII task id"
