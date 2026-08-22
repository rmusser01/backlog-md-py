from __future__ import annotations

from pathlib import Path
from typing import Callable

from backlog_py.core import editing
from backlog_py.core.editing import edit_via_scratch_copy
from backlog_py.core.models import BacklogProject
from backlog_py.tui import install_hint
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.security.paths import assert_path_within_base
from backlog_py.tui.data import BoardDataSource, DaemonReadError, LocalBoardDataSource, create_board_data_source
from backlog_py.tui.models import (
    BoardSnapshot,
    CreateTaskInput,
    DefinitionOfDoneDefaultsInput,
    DefinitionOfDoneDefaultsView,
    FilterState,
    SelectionState,
    SettingsInput,
    SettingsView,
    TaskView,
    create_status_choices,
    filter_snapshot,
    move_status_choices,
    select_after_refresh,
)



class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""


try:
    from textual import events
    from textual.app import App, ComposeResult, SuspendNotSupported
    from textual.binding import Binding
    from textual.widgets import Footer, Header, Input
    from textual.worker import Worker, WorkerFailed

    from backlog_py.tui.dialogs import (
        ArchiveTaskDialog,
        ChecklistToggleDialog,
        CreateTaskDialog,
        DefinitionOfDoneDefaultsDialog,
        EditTaskDialog,
        EditorConfirmDialog,
        GlobalSearchDialog,
        MoveTaskDialog,
        SettingsDialog,
        TaskMarkdownPreviewDialog,
    )
    from backlog_py.tui.screens import BoardScreen
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise TuiDependencyError(install_hint()) from exc
    raise


EditorRunner = Callable[[BacklogProject, Path], object]


class BacklogTuiApp(App[None]):
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("left", "cursor_left", "Left"),
        Binding("right", "cursor_right", "Right"),
        Binding("up", "cursor_up", "Up"),
        Binding("down", "cursor_down", "Down"),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("shift+h", "move_task_left", "Move Left"),
        Binding("shift+l", "move_task_right", "Move Right"),
        Binding("r", "refresh", "Refresh"),
        Binding("/", "focus_filter", "Filter"),
        Binding("s", "global_search", "Search"),
        Binding("c", "settings", "Config"),
        Binding("o", "definition_of_done_defaults", "DoD"),
        Binding("m", "move_task", "Move"),
        Binding("n", "create_task", "New"),
        Binding("u", "update_task", "Update"),
        Binding("p", "preview_task", "Preview"),
        Binding("a", "archive_task", "Archive"),
        Binding("e", "edit_task", "Edit"),
        Binding("x", "toggle_checklist", "Checklist"),
        Binding("d", "jump_to_dependency", "Dependency"),
        Binding("shift+d", "jump_to_dependent", "Dependent"),
        Binding("backspace", "jump_back", "Back"),
        Binding("enter", "focus_inspector", "Detail"),
    ]

    def __init__(
        self,
        project: BacklogProject,
        data_source: BoardDataSource | None = None,
        *,
        fallback_source_factory: Callable[[BacklogProject], BoardDataSource] = LocalBoardDataSource,
        refresh_interval: float = 5.0,
        editor_runner: EditorRunner | None = None,
    ) -> None:
        super().__init__()
        self.project = project
        self.data_source = data_source or create_board_data_source(project)
        self.fallback_source_factory = fallback_source_factory
        self.refresh_interval = refresh_interval
        self.snapshot: BoardSnapshot | None = None
        self.visible_snapshot: BoardSnapshot | None = None
        self.filter_state = FilterState()
        self.selection = SelectionState()
        self.modal_depth = 0
        self.deferred_refresh = False
        self.deferred_snapshot: BoardSnapshot | None = None
        self.reselect_task_id: str | None = None
        self.dependency_jump_source_task_id: str | None = None
        self.dependent_jump_source_task_id: str | None = None
        self.jump_history: list[str] = []
        self.editor_runner = editor_runner or default_editor_runner

    @property
    def selected_task_id(self) -> str | None:
        return self.selection.task_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield BoardScreen(self.project)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_board()
        self.set_interval(self.refresh_interval, self.refresh_board, name="board_refresh")
        self.set_focus(self.query_one("#board-columns"))

    def refresh_board(self) -> Worker[BoardSnapshot]:
        return self.run_worker(
            self._load_snapshot,
            thread=True,
            exclusive=True,
            name="load_board",
            exit_on_error=False,
        )

    def _load_snapshot(self) -> BoardSnapshot:
        return self.data_source.load_board()

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state.name == "ERROR":
            if event.worker.name == "load_board":
                await self._handle_load_error(event.worker.error)
            elif event.worker.name.startswith("mutation:") or event.worker.name == "editor":
                if event.worker.error is not None:
                    self.notify(str(event.worker.error), severity="error")
                self.refresh_board()
            return
        if event.state.name != "SUCCESS":
            return
        if event.worker.name.startswith("mutation:"):
            if isinstance(event.worker.result, (SettingsView, DefinitionOfDoneDefaultsView)):
                self.project = getattr(self.data_source, "project", self.project)
                self.query_one(BoardScreen).project = self.project
                self.refresh_board()
                return
            self.reselect_task_id = (
                event.worker.result.id
                if isinstance(event.worker.result, TaskView)
                else self.selection.task_id
            )
            self.refresh_board()
            return
        if event.worker.name == "editor":
            self.refresh_board()
            return
        if event.worker.name == "load_board":
            await self._apply_snapshot(event.worker.result)

    async def _handle_load_error(self, error: BaseException | None) -> None:
        if isinstance(error, DaemonReadError):
            self.notify(str(error), severity="warning")
            self.data_source = self.fallback_source_factory(self.project)
            self.refresh_board()
            return
        if error is not None:
            self.notify(str(error), severity="error")

    async def _apply_snapshot(self, snapshot: BoardSnapshot) -> None:
        if self.modal_depth > 0:
            self.deferred_snapshot = snapshot
            self.deferred_refresh = True
            return
        previous = self.visible_snapshot or self.snapshot or snapshot
        self.snapshot = snapshot
        self.visible_snapshot = filter_snapshot(snapshot, self.filter_state)
        if self.reselect_task_id is not None:
            self.selection = select_after_refresh(
                previous,
                self.visible_snapshot,
                SelectionState(task_id=self.reselect_task_id, status=self.selection.status, row=self.selection.row),
            )
            self.reselect_task_id = None
        else:
            self.selection = select_after_refresh(previous, self.visible_snapshot, self.selection)
        if self.selection.task_id is None:
            self.selection = self._first_selection(self.visible_snapshot)
        await self._render_board()

    async def _render_board(self) -> None:
        if self.visible_snapshot is None:
            return
        await self.query_one(BoardScreen).render_snapshot(self.visible_snapshot, self.selection, self.filter_state)

    def select_task(self, task_id: str) -> None:
        self._clear_jump_cycle_sources()
        self._select_task(task_id)

    def _select_task(self, task_id: str) -> None:
        if self.visible_snapshot is None:
            return
        for status in self.visible_snapshot.statuses:
            tasks = self.visible_snapshot.columns.get(status, ())
            for row, task in enumerate(tasks):
                if task.id == task_id:
                    self.selection = SelectionState(task_id=task.id, status=status, row=row)
                    self.call_later(self._render_board)
                    return

    def action_cursor_right(self) -> None:
        self._move_columns(1)

    def action_cursor_left(self) -> None:
        self._move_columns(-1)

    def action_cursor_down(self) -> None:
        self._move_rows(1)

    def action_cursor_up(self) -> None:
        self._move_rows(-1)

    def action_move_task_right(self) -> None:
        self._move_selected_task_to_adjacent_status(1)

    def action_move_task_left(self) -> None:
        self._move_selected_task_to_adjacent_status(-1)

    def action_jump_to_dependency(self) -> None:
        self._jump_to_first_known_dependency()

    def action_jump_to_dependent(self) -> None:
        self._jump_to_first_visible_dependent()

    def action_jump_back(self) -> None:
        self._jump_back()

    def key_right(self) -> None:
        self.action_cursor_right()

    def key_left(self) -> None:
        self.action_cursor_left()

    def key_down(self) -> None:
        self.action_cursor_down()

    def key_up(self) -> None:
        self.action_cursor_up()

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input):
            if self.focused.id == "filter-text" and event.key == "escape":
                self.query_one("#filter-text", Input).value = ""
                self.call_later(self.set_filters, text="")
                self.set_focus(self.query_one("#board-columns"))
                event.stop()
            return
        if event.key == "right":
            self.action_cursor_right()
            event.stop()
        elif event.key == "left":
            self.action_cursor_left()
            event.stop()
        elif event.key == "down":
            self.action_cursor_down()
            event.stop()
        elif event.key == "up":
            self.action_cursor_up()
            event.stop()

    def action_refresh(self) -> None:
        self.refresh_board()

    def action_focus_filter(self) -> None:
        self.set_focus(self.query_one("#filter-text", Input))

    def action_move_task(self) -> None:
        task = self._selected_task()
        if task is None or self.snapshot is None:
            return
        self._push_modal(
            MoveTaskDialog(move_status_choices(self.project, self.snapshot.statuses), task.status),
            self._move_task_result,
        )

    def action_create_task(self) -> None:
        if self.snapshot is None:
            return
        statuses = create_status_choices(self.project, self.snapshot.statuses)
        self._push_modal(
            CreateTaskDialog(statuses, self.project.config.default_status),
            self._create_task_result,
        )

    def action_update_task(self) -> None:
        task = self._selected_task()
        if task is None or self.snapshot is None:
            return
        statuses = move_status_choices(self.project, self.snapshot.statuses)
        self._push_modal(
            EditTaskDialog(task, statuses),
            lambda result: self._update_task_result(task.id, result),
        )

    def action_preview_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self._push_modal(TaskMarkdownPreviewDialog(task), lambda _result: None)

    def action_global_search(self) -> None:
        self._push_modal(
            GlobalSearchDialog(lambda query, limit: self.data_source.search(query, limit=limit)),
            self._global_search_result,
        )

    def action_settings(self) -> None:
        try:
            settings = self.data_source.load_settings()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self._push_modal(SettingsDialog(settings), self._settings_result)

    def action_definition_of_done_defaults(self) -> None:
        try:
            defaults = self.data_source.load_definition_of_done_defaults()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self._push_modal(DefinitionOfDoneDefaultsDialog(defaults), self._definition_of_done_defaults_result)

    def action_archive_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self._push_modal(ArchiveTaskDialog(task.id, task.title), self._archive_task_result)

    def action_toggle_checklist(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        if not task.acceptance_criteria and not task.definition_of_done:
            self.notify(f"{task.id} has no checklist items", severity="warning")
            return
        self._push_modal(
            ChecklistToggleDialog(task),
            lambda result: self._toggle_checklist_result(task.id, result),
        )

    def action_edit_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        try:
            path = self.data_source.task_path(task.id)
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self._push_modal(
            EditorConfirmDialog(task.id, path),
            lambda confirmed: self._edit_task_result(confirmed, task.id, path),
        )

    def action_focus_inspector(self) -> None:
        self.set_focus(self.query_one("#task-inspector"))

    async def set_filters(
        self,
        *,
        text: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        queue_category: str | None = None,
    ) -> None:
        self.filter_state = FilterState(
            text=self.filter_state.text if text is None else text,
            status=self.filter_state.status if status is None else status,
            priority=self.filter_state.priority if priority is None else priority,
            assignee=self.filter_state.assignee if assignee is None else assignee,
            label=self.filter_state.label if label is None else label,
            queue_category=self.filter_state.queue_category if queue_category is None else queue_category,
        )
        if self.snapshot is None:
            return
        previous = self.visible_snapshot or self.snapshot
        self.visible_snapshot = filter_snapshot(self.snapshot, self.filter_state)
        self.selection = select_after_refresh(previous, self.visible_snapshot, self.selection)
        if self.selection.task_id is None:
            self.selection = self._first_selection(self.visible_snapshot)
        await self._render_board()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-text":
            await self.set_filters(text=event.value)

    def _push_modal(self, screen, callback) -> None:
        self.modal_depth += 1

        def wrapped(result) -> None:
            try:
                callback(result)
            except Exception as exc:
                self.notify(str(exc), severity="error")
            finally:
                self.modal_depth = max(0, self.modal_depth - 1)
                if self.deferred_refresh and self.deferred_snapshot is not None:
                    snapshot = self.deferred_snapshot
                    self.deferred_snapshot = None
                    self.deferred_refresh = False
                    self.call_later(self._apply_snapshot, snapshot)

        self.push_screen(screen, wrapped)

    def _move_task_result(self, status: str | None) -> None:
        if status is None or self.selection.task_id is None:
            return
        task_id = self.selection.task_id
        self._run_mutation(
            lambda: self.data_source.move_task(task_id, status),
            reselect_task_id=task_id,
            name="mutation:move",
        )

    def _create_task_result(self, input: CreateTaskInput | None) -> None:
        if input is None:
            return
        self._run_mutation(lambda: self.data_source.create_task(input), name="mutation:create")

    def _update_task_result(self, task_id: str, input) -> None:
        if input is None:
            return
        self._run_mutation(
            lambda: self.data_source.edit_task(task_id, input),
            reselect_task_id=task_id,
            name="mutation:update",
        )

    def _archive_task_result(self, confirmed: bool) -> None:
        if not confirmed or self.selection.task_id is None:
            return
        task_id = self.selection.task_id
        self._run_mutation(lambda: self.data_source.archive_task(task_id), name="mutation:archive")

    def _toggle_checklist_result(self, task_id: str, result) -> None:
        if result is None:
            return
        self._run_mutation(
            lambda: self.data_source.set_checklist_item(
                task_id,
                result.checklist,
                result.index,
                checked=result.checked,
            ),
            reselect_task_id=task_id,
            name="mutation:checklist",
        )

    def _edit_task_result(self, confirmed: bool, task_id: str, path: Path) -> None:
        if not confirmed:
            return
        safe_path = assert_path_within_base(self.project.root.resolve(), path)
        self.reselect_task_id = task_id
        self.call_later(self._run_editor_flow, safe_path)

    def _global_search_result(self, task_id: str | None) -> None:
        if task_id is None:
            return
        visible_task_id = self._visible_task_id(task_id)
        if visible_task_id is None:
            self.notify(f"{task_id} is not visible on this board", severity="warning")
            return
        self._clear_jump_cycle_sources()
        self._select_task(visible_task_id)

    def _settings_result(self, input: SettingsInput | None) -> None:
        if input is None:
            return
        self._run_mutation(lambda: self.data_source.update_settings(input), name="mutation:settings")

    def _definition_of_done_defaults_result(self, input: DefinitionOfDoneDefaultsInput | None) -> None:
        if input is None:
            return
        self._run_mutation(
            lambda: self.data_source.update_definition_of_done_defaults(input),
            name="mutation:dod-defaults",
        )

    async def _run_editor_flow(self, path: Path) -> None:
        try:
            with self.suspend():
                await self._run_editor_worker(path)
        except SuspendNotSupported:
            await self._run_editor_worker(path)

    async def _run_editor_worker(self, path: Path) -> None:
        worker = self.run_worker(
            lambda: self.editor_runner(self.project, path),
            thread=True,
            exclusive=True,
            name="editor",
            exit_on_error=False,
        )
        try:
            await worker.wait()
        except WorkerFailed:
            # Swallowed only so the failure does not escape the suspend block:
            # ``on_worker_state_changed`` already notifies for a failed "editor"
            # worker (and refreshes the board), so an EditorConflictError still
            # reaches the user — exactly once — with the path to their bytes.
            pass

    def _run_mutation(
        self,
        operation: Callable[[], TaskView | SettingsView | DefinitionOfDoneDefaultsView],
        *,
        name: str,
        reselect_task_id: str | None = None,
    ) -> None:
        self.reselect_task_id = reselect_task_id
        self.run_worker(operation, thread=True, exclusive=False, name=name, exit_on_error=False)

    def _selected_task(self) -> TaskView | None:
        if self.visible_snapshot is None or self.selection.task_id is None:
            return None
        for tasks in self.visible_snapshot.columns.values():
            for task in tasks:
                if task.id == self.selection.task_id:
                    return task
        return None

    def _move_columns(self, delta: int) -> None:
        if self.visible_snapshot is None or not self.visible_snapshot.statuses:
            return
        current_status = self.selection.status or self.visible_snapshot.statuses[0]
        try:
            current_index = self.visible_snapshot.statuses.index(current_status)
        except ValueError:
            current_index = 0
        for index in range(current_index + delta, len(self.visible_snapshot.statuses) if delta > 0 else -1, delta):
            tasks = self.visible_snapshot.columns.get(self.visible_snapshot.statuses[index], ())
            if tasks:
                row = min(max(self.selection.row, 0), len(tasks) - 1)
                self.selection = SelectionState(task_id=tasks[row].id, status=self.visible_snapshot.statuses[index], row=row)
                self.call_later(self._render_board)
                return

    def _move_rows(self, delta: int) -> None:
        if self.visible_snapshot is None or self.selection.status is None:
            return
        tasks = self.visible_snapshot.columns.get(self.selection.status, ())
        if not tasks:
            return
        row = min(max(self.selection.row + delta, 0), len(tasks) - 1)
        self.selection = SelectionState(task_id=tasks[row].id, status=self.selection.status, row=row)
        self.call_later(self._render_board)

    def _move_selected_task_to_adjacent_status(self, delta: int) -> None:
        task = self._selected_task()
        if task is None or self.snapshot is None:
            return
        statuses = move_status_choices(self.project, self.snapshot.statuses)
        try:
            current_index = statuses.index(task.status)
        except ValueError:
            self.notify(f"{task.id} has unknown status: {task.status}", severity="warning")
            return
        target_index = current_index + delta
        if target_index < 0 or target_index >= len(statuses):
            return
        target_status = statuses[target_index]
        self._run_mutation(
            lambda: self.data_source.move_task(task.id, target_status),
            reselect_task_id=task.id,
            name="mutation:move",
        )

    def _jump_to_first_known_dependency(self) -> None:
        task = self._selected_task()
        if task is None or self.visible_snapshot is None:
            return
        visible_tasks = self._visible_tasks_in_order()
        by_id = {item.id.casefold(): item for item in visible_tasks}
        source_task = task
        if self.dependency_jump_source_task_id is not None:
            remembered_source = by_id.get(self.dependency_jump_source_task_id.casefold())
            if remembered_source is not None and _task_depends_on(remembered_source, task.id):
                source_task = remembered_source

        if not source_task.dependencies:
            self.dependency_jump_source_task_id = None
            self.notify(f"{source_task.id} has no dependencies", severity="information")
            return

        visible_dependencies = [
            dependency_task
            for dependency in source_task.dependencies
            if (dependency_task := by_id.get(dependency.casefold())) is not None
        ]
        if not visible_dependencies:
            self.dependency_jump_source_task_id = None
            self.notify(f"{source_task.id} dependencies are not visible on this board", severity="warning")
            return

        current_index = next(
            (index for index, dependency in enumerate(visible_dependencies) if dependency.id == task.id),
            -1,
        )
        target = visible_dependencies[(current_index + 1) % len(visible_dependencies)]
        self.dependency_jump_source_task_id = source_task.id
        self._record_jump_source(source_task.id, target.id)
        self._select_task(target.id)

    def _jump_to_first_visible_dependent(self) -> None:
        task = self._selected_task()
        if task is None or self.visible_snapshot is None:
            return
        visible_tasks = self._visible_tasks_in_order()
        by_id = {item.id.casefold(): item for item in visible_tasks}
        source_task = task
        if self.dependent_jump_source_task_id is not None:
            remembered_source = by_id.get(self.dependent_jump_source_task_id.casefold())
            if remembered_source is not None and _task_depends_on(task, remembered_source.id):
                source_task = remembered_source

        dependents = [
            candidate
            for candidate in visible_tasks
            if candidate.id != source_task.id and _task_depends_on(candidate, source_task.id)
        ]
        if not dependents:
            self.dependent_jump_source_task_id = None
            self.notify(f"No visible tasks depend on {source_task.id}", severity="information")
            return

        current_index = next((index for index, candidate in enumerate(dependents) if candidate.id == task.id), -1)
        target = dependents[(current_index + 1) % len(dependents)]
        self.dependent_jump_source_task_id = source_task.id
        self._record_jump_source(source_task.id, target.id)
        self._select_task(target.id)

    def _record_jump_source(self, source_task_id: str, target_task_id: str) -> None:
        if source_task_id != target_task_id and (not self.jump_history or self.jump_history[-1] != source_task_id):
            self.jump_history.append(source_task_id)

    def _jump_back(self) -> None:
        while self.jump_history:
            task_id = self.jump_history.pop()
            if self._visible_task_id(task_id) is not None:
                self._clear_jump_cycle_sources()
                self._select_task(task_id)
                return
        self.notify("No dependency navigation history", severity="information")

    def _clear_jump_cycle_sources(self) -> None:
        self.dependency_jump_source_task_id = None
        self.dependent_jump_source_task_id = None

    def _visible_task_id(self, task_id: str) -> str | None:
        folded_task_id = task_id.casefold()
        for task in self._visible_tasks_in_order():
            if task.id.casefold() == folded_task_id:
                return task.id
        return None

    def _visible_tasks_in_order(self) -> tuple[TaskView, ...]:
        if self.visible_snapshot is None:
            return ()
        return tuple(
            task
            for status in self.visible_snapshot.statuses
            for task in self.visible_snapshot.columns.get(status, ())
        )

    @staticmethod
    def _first_selection(snapshot: BoardSnapshot) -> SelectionState:
        for status in snapshot.statuses:
            tasks = snapshot.columns.get(status, ())
            if tasks:
                return SelectionState(task_id=tasks[0].id, status=status, row=0)
        return SelectionState()


def _task_depends_on(task: TaskView, dependency_id: str) -> bool:
    folded_dependency_id = dependency_id.casefold()
    return any(dependency.casefold() == folded_dependency_id for dependency in task.dependencies)


def run_tui_app(project: BacklogProject) -> None:
    BacklogTuiApp(project).run()


# Re-exported so existing callers and tests keep working: the flow itself lives
# in backlog_py.core.editing, shared with the CLI.
NON_BLOCKING_EDITOR_SECONDS = editing.NON_BLOCKING_EDITOR_SECONDS
EditorConflictError = editing.EditorAbort


def default_editor_runner(project: BacklogProject, path: Path) -> None:
    """Edit a task file without holding the project write lock while the user types.

    The flow lives in :mod:`backlog_py.core.editing` so the CLI and the TUI
    cannot drift on it; this only supplies the two surface-specific pieces (how
    to launch the editor, how to take the lock).
    """
    from backlog_py.cli.main import _configured_editor_command, _run_editor_command

    command = _configured_editor_command(project)

    def run_editor(scratch_path: Path) -> None:
        _run_editor_command(command, scratch_path)

    def apply_locked(apply: Callable[[], None]) -> None:
        with_project_write_lock(project, "tui_task_editor", apply)

    edit_via_scratch_copy(
        path,
        project.root,
        editor_label=command[0],
        run_editor=run_editor,
        apply_locked=apply_locked,
    )
