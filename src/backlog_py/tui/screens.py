from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, HorizontalScroll
from textual.widgets import Static

from backlog_py.core.models import BacklogProject
from backlog_py.tui.models import BoardSnapshot, FilterState, SelectionState, dependency_states
from backlog_py.tui.widgets import BoardColumn, BoardHeader, FilterBar, TaskInspector

BOARD_LOADING_MESSAGE = "Loading board..."


class BoardScreen(Container):
    def __init__(self, project: BacklogProject) -> None:
        super().__init__(id="board-screen")
        self.project = project

    def compose(self) -> ComposeResult:
        yield BoardHeader(self.project.config.project_name)
        yield FilterBar(id="filter-bar")
        with Container(id="board-root"):
            with HorizontalScroll(id="board-columns", classes="board-columns"):
                with Horizontal(id="board-column-strip"):
                    # The first scan takes seconds on a large project, and an
                    # empty board reads as a hang. `render_snapshot` clears the
                    # strip before mounting columns, so this needs no teardown
                    # of its own and cannot outlive the load it describes.
                    yield Static(BOARD_LOADING_MESSAGE, id="board-loading")
            yield TaskInspector(id="task-inspector")

    async def render_snapshot(
        self,
        snapshot: BoardSnapshot,
        selection: SelectionState,
        filters: FilterState,
    ) -> None:
        header = self.query_one(BoardHeader)
        header.update_snapshot(snapshot, filters)

        column_strip = self.query_one("#board-column-strip")
        await column_strip.remove_children()
        seen: dict[str, int] = {}
        dependency_state_by_task = dependency_states(snapshot)
        await column_strip.mount(
            *[
                BoardColumn(
                    status=status,
                    tasks=snapshot.columns.get(status, ()),
                    selected_task_id=selection.task_id,
                    dependency_states=dependency_state_by_task,
                    id=column_widget_id(status, seen),
                )
                for status in snapshot.statuses
            ]
        )

        selected = _selected_task(snapshot, selection.task_id)
        self.query_one(TaskInspector).update_task(
            selected,
            None if selected is None else dependency_state_by_task.get(selected.id),
        )


def widget_id(value: str) -> str:
    # ASCII-only: a non-ASCII status name would otherwise produce an invalid
    # Textual identifier. Column uniqueness is handled by column_widget_id.
    return "".join(
        character if character.isascii() and character.isalnum() else "-" for character in value
    ).strip("-")


def column_widget_id(status: str, seen: dict[str, int]) -> str:
    base = f"column-{widget_id(status) or 'status'}"
    count = seen.get(base, 0) + 1
    seen[base] = count
    return base if count == 1 else f"{base}-{count}"


def _selected_task(snapshot: BoardSnapshot, task_id: str | None):
    if task_id is None:
        return None
    for status in snapshot.statuses:
        for task in snapshot.columns.get(status, ()):
            if task.id == task_id:
                return task
    return None
