from __future__ import annotations

from pathlib import Path
from typing import Callable

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.security.paths import assert_path_within_base
from backlog_py.tui.data import BoardDataSource, DaemonReadError, LocalBoardDataSource, create_board_data_source
from backlog_py.tui.models import (
    BoardSnapshot,
    CreateTaskInput,
    FilterState,
    SelectionState,
    TaskView,
    create_status_choices,
    filter_snapshot,
    move_status_choices,
    select_after_refresh,
)


INSTALL_HINT = "Install with backlog-md-py[tui] to use the Textual TUI."


class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""


try:
    from textual import events
    from textual.app import App, ComposeResult, SuspendNotSupported
    from textual.binding import Binding
    from textual.widgets import Footer, Header, Input
    from textual.worker import Worker

    from backlog_py.tui.dialogs import ArchiveTaskDialog, CreateTaskDialog, EditorConfirmDialog, MoveTaskDialog
    from backlog_py.tui.screens import BoardScreen
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise TuiDependencyError(INSTALL_HINT) from exc
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
        Binding("r", "refresh", "Refresh"),
        Binding("/", "focus_filter", "Filter"),
        Binding("m", "move_task", "Move"),
        Binding("n", "create_task", "New"),
        Binding("a", "archive_task", "Archive"),
        Binding("e", "edit_task", "Edit"),
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

    def key_right(self) -> None:
        self.action_cursor_right()

    def key_left(self) -> None:
        self.action_cursor_left()

    def key_down(self) -> None:
        self.action_cursor_down()

    def key_up(self) -> None:
        self.action_cursor_up()

    def on_key(self, event: events.Key) -> None:
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

    def action_archive_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self._push_modal(ArchiveTaskDialog(task.id, task.title), self._archive_task_result)

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
    ) -> None:
        self.filter_state = FilterState(
            text=self.filter_state.text if text is None else text,
            status=self.filter_state.status if status is None else status,
            priority=self.filter_state.priority if priority is None else priority,
            assignee=self.filter_state.assignee if assignee is None else assignee,
            label=self.filter_state.label if label is None else label,
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

    def _archive_task_result(self, confirmed: bool) -> None:
        if not confirmed or self.selection.task_id is None:
            return
        task_id = self.selection.task_id
        self._run_mutation(lambda: self.data_source.archive_task(task_id), name="mutation:archive")

    def _edit_task_result(self, confirmed: bool, task_id: str, path: Path) -> None:
        if not confirmed:
            return
        safe_path = assert_path_within_base(self.project.root.resolve(), path)
        self.reselect_task_id = task_id
        self.call_later(self._run_editor_flow, safe_path)

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
        except Exception:
            pass

    def _run_mutation(
        self,
        operation: Callable[[], TaskView],
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

    @staticmethod
    def _first_selection(snapshot: BoardSnapshot) -> SelectionState:
        for status in snapshot.statuses:
            tasks = snapshot.columns.get(status, ())
            if tasks:
                return SelectionState(task_id=tasks[0].id, status=status, row=0)
        return SelectionState()


def run_tui_app(project: BacklogProject) -> None:
    BacklogTuiApp(project).run()


def default_editor_runner(project: BacklogProject, path: Path) -> None:
    from backlog_py.cli.main import _configured_editor_command, _run_editor_command

    command = _configured_editor_command(project)

    def edit_task_file() -> None:
        _run_editor_command(command, path)

    with_project_write_lock(project, "tui_task_editor", edit_task_file)
