from pathlib import Path

import pytest

pytest.importorskip("textual")

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.tui.app import BacklogTuiApp, default_editor_runner
from backlog_py.tui.models import BoardSnapshot, CreateTaskInput, TaskView


@pytest.mark.asyncio
async def test_move_dialog_updates_selected_task_status():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert source.moves == [("TASK-1", "Done")]
        assert app.snapshot.columns["Done"][0].id == "TASK-1"


@pytest.mark.asyncio
async def test_shift_l_moves_selected_task_to_next_status_without_dialog():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("shift+l")
        await pilot.pause()

        assert source.moves == [("TASK-1", "In Progress")]
        assert app.snapshot.columns["In Progress"][0].id == "TASK-1"
        assert app.selected_task_id == "TASK-1"


@pytest.mark.asyncio
async def test_shift_h_moves_selected_task_to_previous_status_without_dialog():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert app.selected_task_id == "TASK-2"

        await pilot.press("shift+h")
        await pilot.pause()

        assert source.moves == [("TASK-2", "To Do")]
        assert app.snapshot.columns["To Do"][-1].id == "TASK-2"
        assert app.selected_task_id == "TASK-2"


@pytest.mark.asyncio
async def test_create_dialog_creates_task_with_first_slice_fields():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await _type_text(pilot, "New task")
        await pilot.press("tab")
        await _type_text(pilot, "In Progress")
        await pilot.press("tab")
        await _type_text(pilot, "high")
        await pilot.press("tab")
        await _type_text(pilot, "alice,bob")
        await pilot.press("tab")
        await _type_text(pilot, "ui,tui")
        await pilot.press("tab")
        await _type_text(pilot, "Release 1")
        await pilot.press("tab")
        await _type_text(pilot, "TASK-1")
        await pilot.press("tab")
        await _type_text(pilot, "Works from keyboard\nKeeps metadata")
        await pilot.press("tab")
        await _type_text(pilot, "Tests pass")
        await pilot.press("tab")
        await _type_text(pilot, "Description body")
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()

    assert source.creates == [
        CreateTaskInput(
            title="New task",
            status="In Progress",
            priority="high",
            assignees=("alice", "bob"),
            labels=("ui", "tui"),
            milestone="Release 1",
            dependencies=("TASK-1",),
            acceptance_criteria=("Works from keyboard", "Keeps metadata"),
            definition_of_done_add=("Tests pass",),
            description="Description body",
        )
    ]


@pytest.mark.asyncio
async def test_archive_confirmation_archives_selected_task():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.press("enter")
        await pilot.pause()

    assert source.archives == ["TASK-1"]


@pytest.mark.asyncio
async def test_editor_confirmation_suspends_runs_editor_refreshes_and_reselects_task():
    source = _MutableSource(_snapshot())
    editor_calls = []
    project = _project()

    def fake_editor(project_arg, path):
        editor_calls.append((project_arg.root, path))
        source.replace_snapshot(_snapshot(title="Edited title"))

    app = BacklogTuiApp(project=project, data_source=source, editor_runner=fake_editor)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.press("enter")
        await pilot.pause()

    assert editor_calls == [(project.root, source.task_path("TASK-1").resolve())]
    assert app.selected_task_id == "TASK-1"
    assert app.snapshot.columns["To Do"][0].title == "Edited title"


def test_default_editor_runner_uses_project_write_lock(monkeypatch):
    project = _project()
    path = project.root / "backlog" / "tasks" / "task-1.md"
    operations = []
    editor_calls = []

    monkeypatch.setattr("backlog_py.cli.main._configured_editor_command", lambda project: ["fake-editor"])
    monkeypatch.setattr("backlog_py.cli.main._run_editor_command", lambda command, path: editor_calls.append((command, path)))

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.app.with_project_write_lock", fake_lock)

    default_editor_runner(project, path)

    assert operations == [(project.root, "tui_task_editor")]
    assert editor_calls == [(["fake-editor"], path)]


@pytest.mark.asyncio
async def test_refresh_while_modal_open_is_deferred_until_modal_closes():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        source.replace_snapshot(_snapshot(title="Externally changed"))
        app.action_refresh()
        await pilot.pause()
        assert app.snapshot.columns["To Do"][0].title != "Externally changed"
        await pilot.press("escape")
        await pilot.pause()

    assert app.snapshot.columns["To Do"][0].title == "Externally changed"


@pytest.mark.asyncio
async def test_text_filter_limits_visible_cards_without_raw_markdown_match():
    source = _MutableSource(_snapshot_with_two_tasks(raw_secret="hidden-raw"))
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await _type_text(pilot, "second")
        await pilot.pause()

        assert pilot.app.query("#task-card-TASK-1").nodes == []
        assert pilot.app.query_one("#task-card-TASK-2")


@pytest.mark.asyncio
async def test_filter_input_accepts_vim_navigation_letters():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await _type_text(pilot, "hjk deps labels")
        await pilot.pause()

        assert app.filter_state.text == "hjk deps labels"


@pytest.mark.asyncio
async def test_metadata_filter_controls_limit_visible_cards():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.app.set_filters(status="In Progress", priority="high", assignee="alice")
        await pilot.pause()

        assert pilot.app.query("#task-card-TASK-1").nodes == []
        assert pilot.app.query_one("#task-card-TASK-2")


def _project() -> BacklogProject:
    root = Path("/tmp/backlog-tui-demo")
    return BacklogProject(
        root=root,
        backlog_dir=root / "backlog",
        config_path=root / "backlog" / "config.yml",
        config=BacklogConfig(project_name="Demo", statuses=["To Do", "In Progress", "Done"]),
    )


async def _type_text(pilot, text: str) -> None:
    keys = ["enter" if character == "\n" else character for character in text]
    if keys:
        await pilot.press(*keys)


def _snapshot(*, title="Parser bug") -> BoardSnapshot:
    task = _task_view("TASK-1", title, "To Do")
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (task,), "In Progress": (), "Done": ()},
        source="local",
        revision=None,
    )


def _snapshot_with_two_tasks(*, raw_secret: str | None = None) -> BoardSnapshot:
    first = _task_view("TASK-1", "First", "To Do", raw_source=raw_secret)
    second = _task_view(
        "TASK-2",
        "Second",
        "In Progress",
        priority="high",
        assignees=("alice",),
    )
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (first,), "In Progress": (second,), "Done": ()},
        source="local",
        revision=None,
    )


def _task_view(
    task_id: str,
    title: str,
    status: str,
    *,
    priority: str | None = None,
    assignees: tuple[str, ...] = (),
    raw_source: str | None = None,
) -> TaskView:
    return TaskView(
        id=task_id,
        title=title,
        status=status,
        description="",
        path=Path(f"backlog/tasks/{task_id.lower()}.md"),
        priority=priority,
        assignees=assignees,
        raw_source=raw_source,
    )


class _MutableSource:
    source_name = "local"

    def __init__(self, snapshot: BoardSnapshot):
        self._snapshot = snapshot
        self.creates = []
        self.moves = []
        self.archives = []

    def replace_snapshot(self, snapshot: BoardSnapshot) -> None:
        self._snapshot = snapshot

    def load_board(self) -> BoardSnapshot:
        return self._snapshot

    def create_task(self, input: CreateTaskInput) -> TaskView:
        self.creates.append(input)
        task = _task_view("TASK-NEW", input.title, input.status or "To Do", priority=input.priority)
        self._snapshot = _snapshot_with_added_task(self._snapshot, task)
        return task

    def move_task(self, task_id: str, status: str) -> TaskView:
        self.moves.append((task_id, status))
        task = self._find(task_id)
        moved = TaskView(
            id=task.id,
            title=task.title,
            status=status,
            description=task.description,
            path=task.path,
            priority=task.priority,
            assignees=task.assignees,
            labels=task.labels,
            milestone=task.milestone,
            dependencies=task.dependencies,
            acceptance_criteria=task.acceptance_criteria,
            definition_of_done=task.definition_of_done,
            raw_source=task.raw_source,
        )
        columns = {
            column_status: tuple(item for item in tasks if item.id != task_id)
            for column_status, tasks in self._snapshot.columns.items()
        }
        columns[status] = (*columns.get(status, ()), moved)
        self._snapshot = BoardSnapshot(
            self._snapshot.project_name,
            self._snapshot.project_root,
            self._snapshot.statuses,
            columns,
            self._snapshot.source,
            self._snapshot.revision,
        )
        return moved

    def archive_task(self, task_id: str) -> TaskView:
        self.archives.append(task_id)
        task = self._find(task_id)
        columns = {
            status: tuple(item for item in tasks if item.id != task_id)
            for status, tasks in self._snapshot.columns.items()
        }
        self._snapshot = BoardSnapshot(
            self._snapshot.project_name,
            self._snapshot.project_root,
            self._snapshot.statuses,
            columns,
            self._snapshot.source,
            self._snapshot.revision,
        )
        return task

    def task_path(self, task_id: str) -> Path:
        return self._snapshot.project_root / self._find(task_id).path

    def _find(self, task_id: str) -> TaskView:
        for tasks in self._snapshot.columns.values():
            for task in tasks:
                if task.id == task_id:
                    return task
        raise KeyError(task_id)


def _snapshot_with_added_task(snapshot: BoardSnapshot, task: TaskView) -> BoardSnapshot:
    columns = dict(snapshot.columns)
    columns[task.status] = (*columns.get(task.status, ()), task)
    return BoardSnapshot(
        snapshot.project_name,
        snapshot.project_root,
        snapshot.statuses,
        columns,
        snapshot.source,
        snapshot.revision,
    )
