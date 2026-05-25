from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Markdown, Static, TextArea

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.tui.app import BacklogTuiApp, default_editor_runner
from backlog_py.tui.models import (
    BoardSnapshot,
    ChecklistItemView,
    CreateTaskInput,
    EditTaskInput,
    SearchResultView,
    TaskView,
)


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
async def test_update_dialog_edits_selected_task_metadata():
    source = _MutableSource(_snapshot_for_metadata_edit())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()

        dialog = pilot.app.screen
        dialog.query_one("#edit-title", Input).value = "Updated card"
        dialog.query_one("#edit-status", Input).value = "In Progress"
        dialog.query_one("#edit-priority", Input).value = ""
        dialog.query_one("#edit-assignees", Input).value = "codex, reviewer"
        dialog.query_one("#edit-labels", Input).value = "tui, metadata"
        dialog.query_one("#edit-milestone", Input).value = ""
        dialog.query_one("#edit-dependencies", Input).value = "TASK-2"
        dialog.query_one("#edit-description", TextArea).text = "Updated description"
        await pilot.press("enter")
        await pilot.pause()

    assert source.edits == [
        (
            "TASK-1",
            EditTaskInput(
                title="Updated card",
                status="In Progress",
                description="Updated description",
                priority=None,
                clear_priority=True,
                assignees=("codex", "reviewer"),
                labels=("tui", "metadata"),
                milestone=None,
                clear_milestone=True,
                dependencies=("TASK-2",),
            ),
        )
    ]
    edited = app.snapshot.columns["In Progress"][0]
    assert edited.title == "Updated card"
    assert edited.priority is None
    assert edited.assignees == ("codex", "reviewer")
    assert edited.labels == ("tui", "metadata")
    assert edited.milestone is None
    assert edited.dependencies == ("TASK-2",)
    assert edited.description == "Updated description"
    assert app.selected_task_id == "TASK-1"


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
async def test_checklist_toggle_dialog_toggles_selected_task_item():
    source = _MutableSource(_snapshot_with_checklists())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("enter")
        await pilot.pause()

    assert source.checklist_toggles == [("TASK-1", "AC", 1, True)]
    assert app.snapshot.columns["To Do"][0].acceptance_criteria[0].checked is True


@pytest.mark.asyncio
async def test_markdown_preview_dialog_renders_selected_task_content():
    source = _MutableSource(_snapshot_for_markdown_preview())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

        dialog = pilot.app.screen
        assert dialog.query_one("#markdown-preview-title", Static).visual.plain == "Markdown preview - TASK-1"
        preview = dialog.query_one("#markdown-preview-body", Markdown)
        content = preview.source
        assert "# TASK-1 - Preview task" in content
        assert "## Details" in content
        assert "**Bold** item" in content
        assert "- [x] #1 Completed criterion" in content
        assert "- [ ] #1 Rendered preview checked" in content


@pytest.mark.asyncio
async def test_global_search_dialog_lists_results_and_jumps_to_task_result():
    source = _MutableSource(_snapshot_with_two_tasks())
    source.search_results = (
        SearchResultView(kind="task", identifier="TASK-2", title="Second", subtitle="In Progress", task_id="TASK-2"),
        SearchResultView(kind="document", identifier="guides/search.md", title="Search guide"),
        SearchResultView(kind="decision", identifier="decision-1", title="Use SQLite", subtitle="accepted"),
    )
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await _type_text(pilot, "needle")
        await pilot.pause()

        body = pilot.app.screen.query_one("#global-search-results", Static).visual.plain
        assert "TASK-2" in body
        assert "guides/search.md" in body
        assert "decision-1" in body
        assert source.search_queries[-1] == ("needle", 20)

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected_task_id == "TASK-2"


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
async def test_filter_input_backspace_edits_text_instead_of_jump_history():
    dependency = _task_view("TASK-1", "Dependency", "Done")
    dependent = _task_view("TASK-2", "Dependent", "To Do", dependencies=("TASK-1",))
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "Done"),
        columns={"To Do": (dependent,), "Done": (dependency,)},
        source="local",
        revision=None,
    )
    app = BacklogTuiApp(project=_project(), data_source=_MutableSource(snapshot))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert app.selected_task_id == "TASK-1"
        assert app.jump_history == ["TASK-2"]

        await pilot.press("/")
        await _type_text(pilot, "TASK-1")
        await pilot.press("backspace")
        await pilot.pause()

        assert app.filter_state.text == "TASK-"
        assert app.selected_task_id == "TASK-1"
        assert app.jump_history == ["TASK-2"]


@pytest.mark.asyncio
async def test_filter_input_escape_clears_filter_and_returns_board_focus():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await _type_text(pilot, "second")
        await pilot.pause()
        assert app.filter_state.text == "second"
        assert pilot.app.query("#task-card-TASK-1").nodes == []

        await pilot.press("escape")
        await pilot.pause()

        assert app.filter_state.text == ""
        assert pilot.app.query_one("#task-card-TASK-1")
        assert pilot.app.focused is pilot.app.query_one("#board-columns")


@pytest.mark.asyncio
async def test_filter_input_escape_keeps_selection_from_filtered_view():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await _type_text(pilot, "second")
        await pilot.pause()
        assert app.selected_task_id == "TASK-2"

        await pilot.press("escape")
        await pilot.pause()

        assert app.filter_state.text == ""
        assert app.selected_task_id == "TASK-2"
        assert pilot.app.query_one("#task-card-TASK-1")
        assert pilot.app.query_one("#task-card-TASK-2")
        assert pilot.app.focused is pilot.app.query_one("#board-columns")


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


def _snapshot_with_checklists() -> BoardSnapshot:
    task = _task_view(
        "TASK-1",
        "With checklist",
        "To Do",
        acceptance_criteria=(
            ChecklistItemView(item_id="1", text="First acceptance criterion", checked=False),
            ChecklistItemView(item_id="2", text="Second acceptance criterion", checked=True),
        ),
        definition_of_done=(
            ChecklistItemView(item_id="1", text="Verification passes", checked=False),
        ),
    )
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (task,), "In Progress": (), "Done": ()},
        source="local",
        revision=None,
    )


def _snapshot_for_metadata_edit() -> BoardSnapshot:
    first = _task_view(
        "TASK-1",
        "Editable",
        "To Do",
        description="Current description",
        priority="high",
        assignees=("alice",),
        labels=("old",),
        milestone="Release 1",
    )
    second = _task_view("TASK-2", "Dependency", "Done")
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (first,), "In Progress": (), "Done": (second,)},
        source="local",
        revision=None,
    )


def _snapshot_for_markdown_preview() -> BoardSnapshot:
    task = _task_view(
        "TASK-1",
        "Preview task",
        "To Do",
        description="## Details\n\n**Bold** item",
        acceptance_criteria=(
            ChecklistItemView(item_id="1", text="Completed criterion", checked=True),
        ),
        definition_of_done=(
            ChecklistItemView(item_id="1", text="Rendered preview checked", checked=False),
        ),
    )
    return BoardSnapshot(
        project_name="Demo",
        project_root=_project().root,
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (task,), "In Progress": (), "Done": ()},
        source="local",
        revision=None,
    )


def _task_view(
    task_id: str,
    title: str,
    status: str,
    *,
    description: str = "",
    priority: str | None = None,
    assignees: tuple[str, ...] = (),
    labels: tuple[str, ...] = (),
    milestone: str | None = None,
    dependencies: tuple[str, ...] = (),
    acceptance_criteria: tuple[ChecklistItemView, ...] = (),
    definition_of_done: tuple[ChecklistItemView, ...] = (),
    raw_source: str | None = None,
) -> TaskView:
    return TaskView(
        id=task_id,
        title=title,
        status=status,
        description=description,
        path=Path(f"backlog/tasks/{task_id.lower()}.md"),
        priority=priority,
        assignees=assignees,
        labels=labels,
        milestone=milestone,
        dependencies=dependencies,
        acceptance_criteria=acceptance_criteria,
        definition_of_done=definition_of_done,
        raw_source=raw_source,
    )


class _MutableSource:
    source_name = "local"

    def __init__(self, snapshot: BoardSnapshot):
        self._snapshot = snapshot
        self.creates = []
        self.edits = []
        self.moves = []
        self.archives = []
        self.checklist_toggles = []
        self.search_results = ()
        self.search_queries = []

    def replace_snapshot(self, snapshot: BoardSnapshot) -> None:
        self._snapshot = snapshot

    def load_board(self) -> BoardSnapshot:
        return self._snapshot

    def search(self, query: str, limit: int = 20):
        self.search_queries.append((query, limit))
        return self.search_results if query.strip() else ()

    def create_task(self, input: CreateTaskInput) -> TaskView:
        self.creates.append(input)
        task = _task_view("TASK-NEW", input.title, input.status or "To Do", priority=input.priority)
        self._snapshot = _snapshot_with_added_task(self._snapshot, task)
        return task

    def edit_task(self, task_id: str, input: EditTaskInput) -> TaskView:
        self.edits.append((task_id, input))
        task = self._find(task_id)
        updated = TaskView(
            id=task.id,
            title=input.title,
            status=input.status,
            description=input.description,
            path=task.path,
            priority=None if input.clear_priority else input.priority,
            assignees=input.assignees,
            labels=input.labels,
            milestone=None if input.clear_milestone else input.milestone,
            dependencies=input.dependencies,
            acceptance_criteria=task.acceptance_criteria,
            definition_of_done=task.definition_of_done,
            raw_source=task.raw_source,
        )
        columns = {
            status: tuple(item for item in tasks if item.id != task_id)
            for status, tasks in self._snapshot.columns.items()
        }
        columns[updated.status] = (*columns.get(updated.status, ()), updated)
        self._snapshot = BoardSnapshot(
            self._snapshot.project_name,
            self._snapshot.project_root,
            self._snapshot.statuses,
            columns,
            self._snapshot.source,
            self._snapshot.revision,
        )
        return updated

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

    def set_checklist_item(self, task_id: str, checklist: str, index: int, *, checked: bool) -> TaskView:
        self.checklist_toggles.append((task_id, checklist, index, checked))
        task = self._find(task_id)
        if checklist == "AC":
            acceptance_criteria = _toggle_item(task.acceptance_criteria, index, checked=checked)
            definition_of_done = task.definition_of_done
        else:
            acceptance_criteria = task.acceptance_criteria
            definition_of_done = _toggle_item(task.definition_of_done, index, checked=checked)
        updated = TaskView(
            id=task.id,
            title=task.title,
            status=task.status,
            description=task.description,
            path=task.path,
            priority=task.priority,
            assignees=task.assignees,
            labels=task.labels,
            milestone=task.milestone,
            dependencies=task.dependencies,
            acceptance_criteria=acceptance_criteria,
            definition_of_done=definition_of_done,
            raw_source=task.raw_source,
        )
        columns = {
            status: tuple(updated if item.id == task_id else item for item in tasks)
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
        return updated

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


def _toggle_item(items: tuple[ChecklistItemView, ...], index: int, *, checked: bool) -> tuple[ChecklistItemView, ...]:
    return tuple(
        ChecklistItemView(
            item_id=item.item_id,
            text=item.text,
            checked=checked if offset == index else item.checked,
        )
        for offset, item in enumerate(items, start=1)
    )
