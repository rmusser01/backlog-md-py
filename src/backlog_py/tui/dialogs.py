from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown, Static, TextArea

from backlog_py.tui.models import (
    ChecklistItemView,
    ChecklistName,
    ChecklistToggleInput,
    CreateTaskInput,
    EditTaskInput,
    SearchResultView,
    SettingsInput,
    SettingsView,
    TaskView,
)


Searcher = Callable[[str, int], Sequence[SearchResultView]]


class MoveTaskDialog(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, statuses: tuple[str, ...], current_status: str) -> None:
        super().__init__()
        self.statuses = statuses
        if current_status in statuses and statuses.index(current_status) + 1 < len(statuses):
            self.index = statuses.index(current_status) + 1
        else:
            self.index = max(0, statuses.index(current_status) if current_status in statuses else 0)

    def compose(self) -> ComposeResult:
        with Vertical(id="move-dialog", classes="dialog"):
            yield Static("Move task", classes="dialog-title", markup=False)
            yield Static(self.statuses[self.index] if self.statuses else "", id="move-status", markup=False)
            yield Button("Move", id="move-submit", variant="primary")

    def key_down(self) -> None:
        if self.statuses:
            self.index = min(self.index + 1, len(self.statuses) - 1)
            self.query_one("#move-status", Static).update(self.statuses[self.index])

    def key_up(self) -> None:
        if self.statuses:
            self.index = max(self.index - 1, 0)
            self.query_one("#move-status", Static).update(self.statuses[self.index])

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "move-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        self.dismiss(self.statuses[self.index] if self.statuses else None)


class CreateTaskDialog(ModalScreen[CreateTaskInput | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, statuses: tuple[str, ...], default_status: str) -> None:
        super().__init__()
        self.statuses = statuses
        self.default_status = default_status if default_status in statuses else (statuses[0] if statuses else "To Do")

    def compose(self) -> ComposeResult:
        with Vertical(id="create-dialog", classes="dialog"):
            yield Static("New task", classes="dialog-title", markup=False)
            yield Input(placeholder="Title", id="create-title")
            yield Input(value=self.default_status, placeholder="Status", id="create-status")
            yield Input(placeholder="Priority", id="create-priority")
            yield Input(placeholder="Assignees", id="create-assignees")
            yield Input(placeholder="Labels", id="create-labels")
            yield Input(placeholder="Milestone", id="create-milestone")
            yield Input(placeholder="Dependencies", id="create-dependencies")
            yield TextArea("", id="create-ac", compact=True)
            yield TextArea("", id="create-dod", compact=True)
            yield TextArea("", id="create-description", compact=True)
            yield Label("", id="create-error")
            yield Button("Create", id="create-submit", variant="primary")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#create-title", Input))

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        title = self.query_one("#create-title", Input).value.strip()
        if not title:
            self.query_one("#create-error", Label).update("Title is required")
            return
        status = self.query_one("#create-status", Input).value.strip()
        if status not in self.statuses:
            self.query_one("#create-error", Label).update("Status must be one of the available board statuses")
            return
        self.dismiss(
            CreateTaskInput(
                title=title,
                status=status,
                priority=_optional_input(self.query_one("#create-priority", Input).value),
                assignees=parse_multivalue(self.query_one("#create-assignees", Input).value),
                labels=parse_multivalue(self.query_one("#create-labels", Input).value),
                milestone=_optional_input(self.query_one("#create-milestone", Input).value),
                dependencies=parse_multivalue(self.query_one("#create-dependencies", Input).value),
                acceptance_criteria=parse_multivalue(self.query_one("#create-ac", TextArea).text),
                definition_of_done_add=parse_multivalue(self.query_one("#create-dod", TextArea).text),
                description=self.query_one("#create-description", TextArea).text.strip(),
            )
        )


class EditTaskDialog(ModalScreen[EditTaskInput | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, task: TaskView, statuses: tuple[str, ...]) -> None:
        super().__init__()
        self.task_view = task
        self.statuses = statuses

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-dialog", classes="dialog"):
            yield Static(f"Edit task - {self.task_view.id}", classes="dialog-title", markup=False)
            yield Input(value=self.task_view.title, placeholder="Title", id="edit-title")
            yield Input(value=self.task_view.status, placeholder="Status", id="edit-status")
            yield Input(value=self.task_view.priority or "", placeholder="Priority", id="edit-priority")
            yield Input(value=", ".join(self.task_view.assignees), placeholder="Assignees", id="edit-assignees")
            yield Input(value=", ".join(self.task_view.labels), placeholder="Labels", id="edit-labels")
            yield Input(value=self.task_view.milestone or "", placeholder="Milestone", id="edit-milestone")
            yield Input(value=", ".join(self.task_view.dependencies), placeholder="Dependencies", id="edit-dependencies")
            yield TextArea(self.task_view.description, id="edit-description", compact=True)
            yield Label("", id="edit-error")
            yield Button("Update", id="edit-submit", variant="primary")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#edit-title", Input))

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        title = self.query_one("#edit-title", Input).value.strip()
        if not title:
            self.query_one("#edit-error", Label).update("Title is required")
            return
        status = self.query_one("#edit-status", Input).value.strip()
        if status not in self.statuses:
            self.query_one("#edit-error", Label).update("Status must be one of the available board statuses")
            return
        priority = _optional_input(self.query_one("#edit-priority", Input).value)
        milestone = _optional_input(self.query_one("#edit-milestone", Input).value)
        self.dismiss(
            EditTaskInput(
                title=title,
                status=status,
                description=self.query_one("#edit-description", TextArea).text.strip(),
                priority=priority,
                clear_priority=priority is None,
                assignees=parse_multivalue(self.query_one("#edit-assignees", Input).value),
                labels=parse_multivalue(self.query_one("#edit-labels", Input).value),
                milestone=milestone,
                clear_milestone=milestone is None,
                dependencies=parse_multivalue(self.query_one("#edit-dependencies", Input).value),
            )
        )


class TaskMarkdownPreviewDialog(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, task: TaskView) -> None:
        super().__init__()
        self.task_view = task

    def compose(self) -> ComposeResult:
        with Vertical(id="markdown-preview-dialog", classes="dialog"):
            yield Static(
                f"Markdown preview - {self.task_view.id}",
                id="markdown-preview-title",
                classes="dialog-title",
                markup=False,
            )
            yield Markdown(_task_markdown_preview(self.task_view), id="markdown-preview-body")
            yield Button("Close", id="markdown-preview-close")

    def key_enter(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "markdown-preview-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class GlobalSearchDialog(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, searcher: Searcher, *, limit: int = 20) -> None:
        super().__init__()
        self.searcher = searcher
        self.limit = limit
        self.results: tuple[SearchResultView, ...] = ()
        self.index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="global-search-dialog", classes="dialog"):
            yield Static("Global search", classes="dialog-title", markup=False)
            yield Input(placeholder="Search tasks, documents, and decisions", id="global-search-query")
            yield Static("Type to search tasks, documents, and decisions", id="global-search-results", markup=False)
            yield Button("Jump", id="global-search-submit", variant="primary")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#global-search-query", Input))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "global-search-query":
            self._refresh_results(event.value)

    def key_down(self) -> None:
        self._move_selection(1)

    def key_up(self) -> None:
        self._move_selection(-1)

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "global-search-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _refresh_results(self, query: str) -> None:
        text = query.strip()
        if not text:
            self.results = ()
            self.index = 0
            self.query_one("#global-search-results", Static).update("Type to search tasks, documents, and decisions")
            return
        try:
            self.results = tuple(self.searcher(text, self.limit))
        except Exception as exc:
            self.results = ()
            self.index = 0
            self.query_one("#global-search-results", Static).update(f"Search failed: {exc}")
            return
        self.index = min(self.index, max(len(self.results) - 1, 0))
        self.query_one("#global-search-results", Static).update(_search_result_text(self.results, self.index))

    def _move_selection(self, delta: int) -> None:
        if not self.results:
            return
        self.index = min(max(self.index + delta, 0), len(self.results) - 1)
        self.query_one("#global-search-results", Static).update(_search_result_text(self.results, self.index))

    def _submit(self) -> None:
        if not self.results:
            self.dismiss(None)
            return
        self.dismiss(self.results[self.index].task_id)


class SettingsDialog(ModalScreen[SettingsInput | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, settings: SettingsView) -> None:
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog", classes="dialog"):
            yield Static("Project settings", classes="dialog-title", markup=False)
            yield Input(value=self.settings.project_name, placeholder="Project name", id="settings-project-name")
            yield Input(value=self.settings.default_assignee or "", placeholder="Default assignee", id="settings-default-assignee")
            yield Input(value=self.settings.default_status, placeholder="Default status", id="settings-default-status")
            yield Input(value=self.settings.date_format, placeholder="Date format", id="settings-date-format")
            yield Input(
                value=_bool_text(self.settings.include_datetime_in_dates),
                placeholder="Include datetime in dates",
                id="settings-include-datetime",
            )
            yield Input(value=str(self.settings.default_port), placeholder="Default browser port", id="settings-default-port")
            yield Input(
                value=_bool_text(self.settings.auto_open_browser),
                placeholder="Auto-open browser",
                id="settings-auto-open-browser",
            )
            yield Input(
                value="" if self.settings.zero_padded_ids is None else str(self.settings.zero_padded_ids),
                placeholder="Zero-padded ID width",
                id="settings-zero-padded-ids",
            )
            yield Input(value=_bool_text(self.settings.auto_commit), placeholder="Auto-commit", id="settings-auto-commit")
            yield Input(
                value=_bool_text(self.settings.remote_operations),
                placeholder="Remote operations",
                id="settings-remote-operations",
            )
            yield Input(
                value=_bool_text(self.settings.check_active_branches),
                placeholder="Check active branches",
                id="settings-check-active-branches",
            )
            yield Input(
                value=str(self.settings.active_branch_days),
                placeholder="Active branch days",
                id="settings-active-branch-days",
            )
            yield TextArea("\n".join(self.settings.statuses), id="settings-statuses", compact=True)
            yield Label("", id="settings-error")
            yield Button("Save", id="settings-submit", variant="primary")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#settings-project-name", Input))

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        try:
            self.dismiss(
                SettingsInput(
                    project_name=_required_input(self.query_one("#settings-project-name", Input).value, "Project name"),
                    default_assignee=_optional_input(self.query_one("#settings-default-assignee", Input).value),
                    default_status=_required_input(self.query_one("#settings-default-status", Input).value, "Default status"),
                    date_format=_required_input(self.query_one("#settings-date-format", Input).value, "Date format"),
                    include_datetime_in_dates=_parse_bool_input(
                        self.query_one("#settings-include-datetime", Input).value,
                        "Include datetime in dates",
                    ),
                    default_port=_parse_int_input(
                        self.query_one("#settings-default-port", Input).value,
                        "Default browser port",
                        minimum=1,
                        maximum=65535,
                    ),
                    auto_open_browser=_parse_bool_input(
                        self.query_one("#settings-auto-open-browser", Input).value,
                        "Auto-open browser",
                    ),
                    zero_padded_ids=_parse_optional_nonnegative_int(
                        self.query_one("#settings-zero-padded-ids", Input).value,
                        "Zero-padded ID width",
                    ),
                    auto_commit=_parse_bool_input(self.query_one("#settings-auto-commit", Input).value, "Auto-commit"),
                    remote_operations=_parse_bool_input(
                        self.query_one("#settings-remote-operations", Input).value,
                        "Remote operations",
                    ),
                    check_active_branches=_parse_bool_input(
                        self.query_one("#settings-check-active-branches", Input).value,
                        "Check active branches",
                    ),
                    active_branch_days=_parse_int_input(
                        self.query_one("#settings-active-branch-days", Input).value,
                        "Active branch days",
                        minimum=1,
                    ),
                    statuses=parse_multivalue(self.query_one("#settings-statuses", TextArea).text),
                )
            )
        except ValueError as exc:
            self.query_one("#settings-error", Label).update(str(exc))


class ArchiveTaskDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, task_id: str, title: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="archive-dialog", classes="dialog"):
            yield Static(f"Archive {self.task_id} - {self.title}?", markup=False)
            yield Button("Archive", id="archive-submit", variant="warning")

    def key_enter(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "archive-submit":
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ChecklistToggleDialog(ModalScreen[ChecklistToggleInput | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, task: TaskView) -> None:
        super().__init__()
        self.task_view = task
        self.items = _checklist_dialog_items(task)
        self.index = _first_unchecked_item_index(self.items)

    def compose(self) -> ComposeResult:
        with Vertical(id="checklist-dialog", classes="dialog"):
            yield Static(f"Toggle checklist - {self.task_view.id}", classes="dialog-title", markup=False)
            yield Static(self._item_text(), id="checklist-item", markup=False)
            yield Button("Toggle", id="checklist-submit", variant="primary")

    def key_down(self) -> None:
        if self.items:
            self.index = min(self.index + 1, len(self.items) - 1)
            self.query_one("#checklist-item", Static).update(self._item_text())

    def key_up(self) -> None:
        if self.items:
            self.index = max(self.index - 1, 0)
            self.query_one("#checklist-item", Static).update(self._item_text())

    def key_enter(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "checklist-submit":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        if not self.items:
            self.dismiss(None)
            return
        checklist, index, item = self.items[self.index]
        self.dismiss(ChecklistToggleInput(checklist=checklist, index=index, checked=not item.checked))

    def _item_text(self) -> str:
        if not self.items:
            return "No checklist items"
        checklist, index, item = self.items[self.index]
        marker = "x" if item.checked else " "
        label = "Acceptance Criteria" if checklist == "AC" else "Definition of Done"
        item_id = f" #{item.item_id}" if item.item_id else ""
        return f"{self.index + 1}/{len(self.items)} {label} {index}\n- [{marker}]{item_id} {item.text}"


class EditorConfirmDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, task_id: str, path: Path) -> None:
        super().__init__()
        self.task_id = task_id
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="editor-dialog", classes="dialog"):
            yield Static(f"Open {self.task_id} in editor?\n{self.path}", markup=False)
            yield Button("Open", id="editor-submit", variant="primary")

    def key_enter(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "editor-submit":
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def parse_multivalue(value: str) -> tuple[str, ...]:
    normalized = value.replace(",", "\n")
    return tuple(part.strip() for part in normalized.splitlines() if part.strip())


def _optional_input(value: str) -> str | None:
    text = value.strip()
    return text or None


def _required_input(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _parse_bool_input(value: str, label: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{label} must be true or false")


def _parse_int_input(value: str, label: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value.strip(), 10)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return parsed


def _parse_optional_nonnegative_int(value: str, label: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    parsed = _parse_int_input(text, label, minimum=0)
    return parsed or None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _checklist_dialog_items(
    task: TaskView,
) -> tuple[tuple[ChecklistName, int, ChecklistItemView], ...]:
    items: list[tuple[ChecklistName, int, ChecklistItemView]] = []
    items.extend(("AC", index, item) for index, item in enumerate(task.acceptance_criteria, start=1))
    items.extend(("DOD", index, item) for index, item in enumerate(task.definition_of_done, start=1))
    return tuple(items)


def _first_unchecked_item_index(items: tuple[tuple[ChecklistName, int, ChecklistItemView], ...]) -> int:
    for index, (_checklist, _item_index, item) in enumerate(items):
        if not item.checked:
            return index
    return 0


def _task_markdown_preview(task: TaskView) -> str:
    lines = [
        f"# {task.id} - {task.title}",
        "",
        f"- Status: {task.status}",
        f"- Path: {task.path.as_posix()}",
    ]
    if task.priority:
        lines.append(f"- Priority: {task.priority}")
    if task.assignees:
        lines.append(f"- Assignees: {', '.join(task.assignees)}")
    if task.labels:
        lines.append(f"- Labels: {', '.join(task.labels)}")
    if task.milestone:
        lines.append(f"- Milestone: {task.milestone}")
    if task.dependencies:
        lines.append(f"- Dependencies: {', '.join(task.dependencies)}")
    if task.description:
        lines.extend(["", "## Description", "", task.description])
    if task.acceptance_criteria:
        lines.extend(["", "## Acceptance Criteria", ""])
        lines.extend(_preview_checklist_lines(task.acceptance_criteria))
    if task.definition_of_done:
        lines.extend(["", "## Definition of Done", ""])
        lines.extend(_preview_checklist_lines(task.definition_of_done))
    return "\n".join(lines)


def _preview_checklist_lines(items: tuple[ChecklistItemView, ...]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        marker = "x" if item.checked else " "
        item_id = f" #{item.item_id}" if item.item_id else f" #{index}"
        lines.append(f"- [{marker}]{item_id} {item.text}")
    return lines


def _search_result_text(results: tuple[SearchResultView, ...], selected_index: int) -> str:
    if not results:
        return "No matching tasks, documents, or decisions"
    lines: list[str] = []
    for index, result in enumerate(results):
        marker = ">" if index == selected_index else " "
        subtitle = f" [{result.subtitle}]" if result.subtitle else ""
        lines.append(f"{marker} {result.kind} {result.identifier}{subtitle} - {result.title}")
    return "\n".join(lines)
