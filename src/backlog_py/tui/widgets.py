from __future__ import annotations

import hashlib
import re

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, Static

from backlog_py.tui.models import BoardSnapshot, DependencyState, FilterState, TaskView


class BoardHeader(Horizontal):
    def __init__(self, project_name: str) -> None:
        super().__init__(id="board-header")
        self.project_name = project_name

    def compose(self) -> ComposeResult:
        yield Static(self.project_name, id="board-title", markup=False)
        yield Static("", id="board-meta", markup=False)

    def update_snapshot(self, snapshot: BoardSnapshot, filters: FilterState) -> None:
        source = snapshot.source
        filter_summary = _filter_summary(filters)
        suffix = source
        if filter_summary:
            suffix = f"{suffix} | {filter_summary}"
        self.query_one("#board-title", Static).update(snapshot.project_name)
        self.query_one("#board-meta", Static).update(suffix)


class BoardColumn(VerticalScroll):
    def __init__(
        self,
        *,
        status: str,
        tasks: tuple[TaskView, ...],
        selected_task_id: str | None,
        dependency_states: dict[str, DependencyState],
        id: str,
    ) -> None:
        super().__init__(id=id, classes="board-column")
        self.status = status
        self.tasks = tasks
        self.selected_task_id = selected_task_id
        self.dependency_states = dependency_states

    def compose(self) -> ComposeResult:
        yield Static(f"{self.status} ({len(self.tasks)})", classes="column-title", markup=False)
        if not self.tasks:
            yield Static("No tasks", classes="empty-column", markup=False)
            return
        for task in self.tasks:
            yield TaskCard(
                task,
                selected=task.id == self.selected_task_id,
                dependency_state=self.dependency_states.get(task.id),
            )


class FilterBar(Static):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter", id="filter-text")


class TaskCard(Static):
    def __init__(self, task: TaskView, *, selected: bool, dependency_state: DependencyState | None = None) -> None:
        classes = "task-card"
        if dependency_state is not None and dependency_state.is_blocked:
            classes = f"{classes} blocked"
        if selected:
            classes = f"{classes} selected"
        super().__init__(
            _task_card_text(task, dependency_state),
            id=f"task-card-{_widget_id(task.id)}",
            classes=classes,
            markup=False,
        )
        self.task_view = task

    def on_click(self, event: events.Click) -> None:
        self.app.select_task(self.task_view.id)
        event.stop()


class TaskInspector(Static, can_focus=True):
    def update_task(self, task: TaskView | None, dependency_state: DependencyState | None = None) -> None:
        # Render as a literal Text so task content containing Rich-markup
        # metacharacters (e.g. "list[str]" or "[/]") is neither swallowed nor
        # able to raise MarkupError.
        if task is None:
            self.update(Text("No task selected"))
            return
        self.update(Text(_inspector_text(task, dependency_state)))


def _task_card_text(task: TaskView, dependency_state: DependencyState | None = None) -> str:
    priority = f" [{task.priority}]" if task.priority else ""
    lines = [f"{task.id}{priority}", task.title]
    if task.queue_category:
        lines.append(f"Queue: {task.queue_category}")
    if dependency_state is not None and dependency_state.total:
        lines.append(f"Deps: {_dependency_summary_text(dependency_state)}")
    return "\n".join(lines)


def _inspector_text(task: TaskView, dependency_state: DependencyState | None = None) -> str:
    lines = [
        f"{task.id} - {task.title}",
        f"Status: {task.status}",
        f"Path: {task.path.as_posix()}",
    ]
    if task.queue_category:
        lines.append(f"Queue: {task.queue_category}")
    if task.effective_status:
        lines.append(f"Effective Status: {task.effective_status}")
    if task.orchestration_version is not None:
        lines.append(f"Orchestration Version: {task.orchestration_version}")
    if task.priority:
        lines.append(f"Priority: {task.priority}")
    if task.assignees:
        lines.append(f"Assignees: {', '.join(task.assignees)}")
    if task.labels:
        lines.append(f"Labels: {', '.join(task.labels)}")
    if task.milestone:
        lines.append(f"Milestone: {task.milestone}")
    if task.dependencies:
        lines.append(f"Dependencies: {', '.join(task.dependencies)}")
    if dependency_state is not None and dependency_state.total:
        lines.append(f"Dependency Status: {_dependency_summary_text(dependency_state)}")
        if dependency_state.open:
            lines.append(f"Open Dependencies: {', '.join(dependency_state.open)}")
        if dependency_state.missing:
            lines.append(f"Missing Dependencies: {', '.join(dependency_state.missing)}")
    if task.description:
        lines.extend(["", task.description])
    if task.acceptance_criteria:
        lines.extend(["", "Acceptance Criteria:"])
        lines.extend(_checklist_lines(task.acceptance_criteria))
    if task.definition_of_done:
        lines.extend(["", "Definition of Done:"])
        lines.extend(_checklist_lines(task.definition_of_done))
    if task.run_history_issues:
        lines.extend(["", "Run History Issues:"])
        lines.extend(f"- {issue}" for issue in task.run_history_issues)
    if task.run_history_events:
        lines.extend(["", "Run History:"])
        lines.extend(
            f"- {event.timestamp} {event.type} {event.result} by {event.actor}".rstrip()
            for event in task.run_history_events[-5:]
        )
    return "\n".join(lines)


def _checklist_lines(items) -> list[str]:
    lines = []
    for item in items:
        marker = "x" if item.checked else " "
        item_id = f" #{item.item_id}" if item.item_id else ""
        lines.append(f"- [{marker}]{item_id} {item.text}")
    return lines


def _dependency_summary_text(state: DependencyState) -> str:
    parts = [f"{len(state.complete)}/{state.total} done"]
    if state.open:
        parts.append(f"{len(state.open)} open")
    if state.missing:
        parts.append(f"{len(state.missing)} missing")
    return ", ".join(parts)


def _filter_summary(filters: FilterState) -> str:
    parts = []
    if filters.text.strip():
        parts.append(f"text={filters.text.strip()}")
    if filters.status:
        parts.append(f"status={filters.status}")
    if filters.priority:
        parts.append(f"priority={filters.priority}")
    if filters.assignee:
        parts.append(f"assignee={filters.assignee}")
    if filters.label:
        parts.append(f"label={filters.label}")
    if filters.queue_category:
        parts.append(f"queue={filters.queue_category}")
    return ", ".join(parts)


_VALID_WIDGET_ID_RE = re.compile(r"[a-zA-Z_-][a-zA-Z0-9_-]*$")


def _widget_id(value: str) -> str:
    # Textual identifiers are ASCII-only and must be unique. Keep already-valid
    # ids stable; otherwise sanitize to ASCII and append a short hash so that
    # distinct ids (e.g. non-ASCII, or "task-1.2" vs "task-1_2") never collide.
    if _VALID_WIDGET_ID_RE.match(value):
        return value
    ascii_safe = "".join(
        character if character.isascii() and (character.isalnum() or character in "_-") else "-"
        for character in value
    ).strip("-")
    digest = hashlib.blake2s(value.encode("utf-8"), digest_size=4).hexdigest()
    return f"{ascii_safe or 'id'}-{digest}"
