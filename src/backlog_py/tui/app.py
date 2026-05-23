from __future__ import annotations

from typing import Callable

from backlog_py.core.models import BacklogProject
from backlog_py.tui.data import BoardDataSource, DaemonReadError, LocalBoardDataSource, create_board_data_source
from backlog_py.tui.models import BoardSnapshot, FilterState, SelectionState, filter_snapshot, select_after_refresh


INSTALL_HINT = "Install with backlog-md-py[tui] to use the Textual TUI."


class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""


try:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Footer, Header
    from textual.worker import Worker

    from backlog_py.tui.screens import BoardScreen
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise TuiDependencyError(INSTALL_HINT) from exc
    raise


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
        if event.worker.name != "load_board" or not event.state.name == "SUCCESS":
            if event.worker.name == "load_board" and event.state.name == "ERROR":
                await self._handle_load_error(event.worker.error)
            return
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
        previous = self.visible_snapshot or self.snapshot or snapshot
        self.snapshot = snapshot
        self.visible_snapshot = filter_snapshot(snapshot, self.filter_state)
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
        self.notify("Filters will be interactive in the next TUI slice.")

    def action_move_task(self) -> None:
        self.notify("Move is implemented in the next TUI slice.")

    def action_create_task(self) -> None:
        self.notify("Create is implemented in the next TUI slice.")

    def action_archive_task(self) -> None:
        self.notify("Archive is implemented in the next TUI slice.")

    def action_edit_task(self) -> None:
        self.notify("Edit is implemented in the next TUI slice.")

    def action_focus_inspector(self) -> None:
        self.set_focus(self.query_one("#task-inspector"))

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
