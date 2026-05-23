from pathlib import Path

import pytest

pytest.importorskip("textual")
pytestmark = pytest.mark.asyncio

from textual.widgets import Footer, Static

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.tui.app import BacklogTuiApp
from backlog_py.tui.data import DaemonReadError
from backlog_py.tui.models import BoardSnapshot, ChecklistItemView, TaskView


async def test_app_renders_board_columns_inspector_and_footer():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert pilot.app.query_one("#board-title", Static).visual.plain == "Demo"
        assert pilot.app.query_one("#column-To-Do")
        assert pilot.app.query_one("#column-In-Progress")
        assert "TASK-1" in pilot.app.query_one("#task-inspector", Static).visual.plain
        assert pilot.app.query_one(Footer)


async def test_keyboard_selection_moves_between_cards_and_columns():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()

        assert pilot.app.selected_task_id == "TASK-2"


async def test_vim_navigation_aliases_move_between_cards_and_columns():
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={
            "To Do": (
                _task_view("TASK-1", "First", "To Do"),
                _task_view("TASK-2", "Second", "To Do"),
            ),
            "In Progress": (_task_view("TASK-3", "Third", "In Progress"),),
            "Done": (),
        },
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        assert pilot.app.selected_task_id == "TASK-2"

        await pilot.press("k")
        await pilot.pause()
        assert pilot.app.selected_task_id == "TASK-1"

        await pilot.press("l")
        await pilot.pause()
        assert pilot.app.selected_task_id == "TASK-3"

        await pilot.press("h")
        await pilot.pause()
        assert pilot.app.selected_task_id == "TASK-1"


async def test_mouse_click_selects_task_card_where_headless_textual_supports_it():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert await pilot.click("#task-card-TASK-2")
        await pilot.pause()

        assert pilot.app.selected_task_id == "TASK-2"


async def test_daemon_read_failure_switches_to_local_source_with_notice():
    failing = _FailingDaemonSource(DaemonReadError("daemon unavailable"))
    local = _StaticSource(_snapshot(source="local"))
    notices = []
    app = BacklogTuiApp(
        project=_project(),
        data_source=failing,
        fallback_source_factory=lambda project: local,
    )
    app.notify = lambda message, **kwargs: notices.append((message, kwargs))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert pilot.app.data_source is local
        assert pilot.app.snapshot.source == "local"
        assert any("daemon unavailable" in message for message, _ in notices)


async def test_column_ids_remain_unique_for_statuses_with_same_sanitized_text():
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("A B", "A/B"),
        columns={
            "A B": (_task_view("TASK-1", "One", "A B"),),
            "A/B": (_task_view("TASK-2", "Two", "A/B"),),
        },
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert len(pilot.app.query(".board-column").nodes) == 2
        assert pilot.app.query_one("#column-A-B")
        assert pilot.app.query_one("#column-A-B-2")


async def test_enter_focuses_task_inspector():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert pilot.app.focused is pilot.app.query_one("#task-inspector")


async def test_dependency_state_is_visible_on_card_and_inspector():
    done = _task_view("TASK-1", "Done dependency", "Done")
    open_task = _task_view("TASK-2", "Open dependency", "In Progress")
    dependent = _task_view(
        "TASK-3",
        "Dependent task",
        "To Do",
        dependencies=("TASK-1", "TASK-2", "TASK-99"),
    )
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (dependent,), "In Progress": (open_task,), "Done": (done,)},
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        card = pilot.app.query_one("#task-card-TASK-3", Static).visual.plain
        inspector = pilot.app.query_one("#task-inspector", Static).visual.plain

        assert "Deps: 1/3 done, 1 open, 1 missing" in card
        assert "Dependency Status: 1/3 done, 1 open, 1 missing" in inspector
        assert "Open Dependencies: TASK-2" in inspector
        assert "Missing Dependencies: TASK-99" in inspector


async def test_dependency_shortcut_jumps_to_first_known_dependency():
    done = _task_view("TASK-1", "Done dependency", "Done")
    open_task = _task_view("TASK-2", "Open dependency", "In Progress")
    dependent = _task_view(
        "TASK-3",
        "Dependent task",
        "To Do",
        dependencies=("TASK-99", "task-2", "TASK-1"),
    )
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (dependent,), "In Progress": (open_task,), "Done": (done,)},
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert pilot.app.selected_task_id == "TASK-3"

        await pilot.press("d")
        await pilot.pause()

        assert pilot.app.selected_task_id == "TASK-2"


def _project() -> BacklogProject:
    root = Path("/tmp/backlog-tui-demo")
    return BacklogProject(
        root=root,
        backlog_dir=root / "backlog",
        config_path=root / "backlog" / "config.yml",
        config=BacklogConfig(project_name="Demo", statuses=["To Do", "In Progress", "Done"]),
    )


def _snapshot(*, source="local", title="Parser bug") -> BoardSnapshot:
    first = _task_view(
        "TASK-1",
        title,
        "To Do",
        description="Fix the parser path",
        acceptance_criteria=(ChecklistItemView(item_id="1", text="Render the board", checked=True),),
        definition_of_done=(ChecklistItemView(item_id="1", text="Tests pass", checked=False),),
    )
    second = _task_view("TASK-2", "Daemon status", "In Progress", priority="high", labels=("daemon",))
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (first,), "In Progress": (second,), "Done": ()},
        source=source,
        revision=None,
    )


def _task_view(
    task_id: str,
    title: str,
    status: str,
    *,
    description: str = "",
    priority: str | None = None,
    labels: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    acceptance_criteria: tuple[ChecklistItemView, ...] = (),
    definition_of_done: tuple[ChecklistItemView, ...] = (),
) -> TaskView:
    return TaskView(
        id=task_id,
        title=title,
        status=status,
        description=description,
        path=Path(f"backlog/tasks/{task_id.lower()}.md"),
        priority=priority,
        labels=labels,
        dependencies=dependencies,
        acceptance_criteria=acceptance_criteria,
        definition_of_done=definition_of_done,
    )


class _StaticSource:
    source_name = "local"

    def __init__(self, snapshot: BoardSnapshot):
        self._snapshot = snapshot

    def load_board(self) -> BoardSnapshot:
        return self._snapshot

    def create_task(self, input):
        raise NotImplementedError

    def move_task(self, task_id, status):
        raise NotImplementedError

    def archive_task(self, task_id):
        raise NotImplementedError

    def task_path(self, task_id):
        raise NotImplementedError


class _FailingDaemonSource(_StaticSource):
    source_name = "daemon"

    def __init__(self, error: Exception):
        self.error = error

    def load_board(self) -> BoardSnapshot:
        raise self.error
