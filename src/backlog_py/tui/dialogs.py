from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from backlog_py.tui.models import CreateTaskInput


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
