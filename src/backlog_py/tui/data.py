from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from backlog_py.core.decisions import DecisionService
from backlog_py.core.documents import DocumentService
from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.daemon.lifecycle import DaemonNotRunningError, daemon_status
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.security.paths import assert_path_within_base
from backlog_py.storage.config import (
    get_definition_of_done_defaults,
    load_config,
    normalize_definition_of_done_defaults,
    replace_definition_of_done_defaults,
    set_config_value,
)
from backlog_py.tui.models import (
    BoardSnapshot,
    BoardSourceName,
    CreateTaskInput,
    DefinitionOfDoneDefaultsInput,
    DefinitionOfDoneDefaultsView,
    EditTaskInput,
    SearchResultView,
    SettingsInput,
    SettingsView,
    TaskView,
    board_snapshot_from_local,
    task_view_from_mcp_payload,
    task_view_from_record,
)


class BoardDataSource(Protocol):
    project: BacklogProject
    source_name: BoardSourceName

    def load_board(self) -> BoardSnapshot:
        """Load the current board snapshot."""

    def search(self, query: str, limit: int = 20) -> tuple[SearchResultView, ...]:
        """Search project tasks, documents, and decisions for TUI display."""

    def load_settings(self) -> SettingsView:
        """Load editable safe project settings for TUI display."""

    def update_settings(self, input: SettingsInput) -> SettingsView:
        """Persist editable safe project settings and return refreshed values."""

    def load_definition_of_done_defaults(self) -> DefinitionOfDoneDefaultsView:
        """Load project-level Definition of Done defaults for TUI display."""

    def update_definition_of_done_defaults(
        self,
        input: DefinitionOfDoneDefaultsInput,
    ) -> DefinitionOfDoneDefaultsView:
        """Persist project-level Definition of Done defaults and return refreshed values."""

    def create_task(self, input: CreateTaskInput) -> TaskView:
        """Create a task and return the normalized task view."""

    def move_task(self, task_id: str, status: str) -> TaskView:
        """Move a task to a new status and return the normalized task view."""

    def edit_task(self, task_id: str, input: EditTaskInput) -> TaskView:
        """Edit selected task metadata and return the normalized task view."""

    def archive_task(self, task_id: str) -> TaskView:
        """Archive a task and return the normalized task view."""

    def set_checklist_item(self, task_id: str, checklist: str, index: int, *, checked: bool) -> TaskView:
        """Set a selected checklist item check state and return the normalized task view."""

    def task_path(self, task_id: str) -> Path:
        """Return a validated absolute local task path."""


@dataclass
class LocalBoardDataSource:
    project: BacklogProject
    source_name: BoardSourceName = "local"

    def load_board(self) -> BoardSnapshot:
        repository = ReadOnlyRepository(self.project)
        return board_snapshot_from_local(self.project, repository.board(), source="local")

    def search(self, query: str, limit: int = 20) -> tuple[SearchResultView, ...]:
        return _search_project(self.project, query, limit=limit)

    def load_settings(self) -> SettingsView:
        return settings_view_from_config(load_config(self.project.config_path))

    def update_settings(self, input: SettingsInput) -> SettingsView:
        def mutate() -> SettingsView:
            settings = _update_project_settings(self.project, input)
            self.project = _refresh_project_config(self.project)
            return settings

        return with_project_write_lock(self.project, "tui_config_settings_update", mutate)

    def load_definition_of_done_defaults(self) -> DefinitionOfDoneDefaultsView:
        return DefinitionOfDoneDefaultsView(items=tuple(get_definition_of_done_defaults(self.project)))

    def update_definition_of_done_defaults(
        self,
        input: DefinitionOfDoneDefaultsInput,
    ) -> DefinitionOfDoneDefaultsView:
        def mutate() -> DefinitionOfDoneDefaultsView:
            defaults = _update_definition_of_done_defaults(self.project, input)
            self.project = _refresh_project_config(self.project)
            return defaults

        return with_project_write_lock(self.project, "tui_dod_defaults_update", mutate)

    def create_task(self, input: CreateTaskInput) -> TaskView:
        def mutate() -> TaskView:
            task = MutableRepository(self.project).create_task(
                title=input.title,
                status=input.status,
                description=input.description,
                acceptance_criteria=input.acceptance_criteria,
                definition_of_done_add=input.definition_of_done_add,
                dependencies=input.dependencies,
                assignees=input.assignees,
                labels=input.labels,
                priority=input.priority,
                milestone=input.milestone,
            )
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_create", mutate)

    def move_task(self, task_id: str, status: str) -> TaskView:
        def mutate() -> TaskView:
            task = MutableRepository(self.project).edit_task(task_id, status=status)
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_move", mutate)

    def edit_task(self, task_id: str, input: EditTaskInput) -> TaskView:
        def mutate() -> TaskView:
            task = MutableRepository(self.project).edit_task(task_id, **_local_edit_arguments(input))
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_edit", mutate)

    def archive_task(self, task_id: str) -> TaskView:
        def mutate() -> TaskView:
            task = MutableRepository(self.project).archive_task(task_id)
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_archive", mutate)

    def set_checklist_item(self, task_id: str, checklist: str, index: int, *, checked: bool) -> TaskView:
        def mutate() -> TaskView:
            arguments = _checklist_edit_arguments(checklist, index, checked=checked)
            task = MutableRepository(self.project).edit_task(task_id, **arguments)
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_checklist", mutate)

    def task_path(self, task_id: str) -> Path:
        task = ReadOnlyRepository(self.project).get_task(task_id)
        return assert_path_within_base(self.project.root, task.path)


class DaemonReadError(RuntimeError):
    """Raised when daemon board reads fail."""


class DaemonMutationError(RuntimeError):
    """Raised when daemon mutations fail."""


@dataclass(frozen=True)
class DaemonMcpClient:
    endpoint: str
    token: str
    timeout: float = 30.0

    def __post_init__(self) -> None:
        _validate_loopback_http_endpoint(self.endpoint)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # Endpoint is validated to loopback HTTP before this request is built.
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
            result = json.loads(response.read().decode("utf-8"))
        return _extract_tool_result(result)


@dataclass
class DaemonBoardDataSource:
    project: BacklogProject
    endpoint: str
    token: str
    source_name: BoardSourceName = "daemon"

    def __post_init__(self) -> None:
        _validate_loopback_http_endpoint(self.endpoint)

    def load_board(self) -> BoardSnapshot:
        client = self._client()
        try:
            board = client.call_tool("task_board", {"project": str(self.project.root)})
            if not isinstance(board, Mapping):
                raise TypeError("Daemon task_board returned a non-mapping payload")
            board_by_status = {str(status): rows for status, rows in board.items()}
            columns: dict[str, tuple[TaskView, ...]] = {}
            for status in _ordered_board_statuses(self.project, board_by_status):
                rows = board_by_status.get(status, [])
                if not isinstance(rows, list):
                    raise TypeError(f"Daemon task_board column {status!r} is not a list")
                columns[status] = tuple(
                    task_view_from_mcp_payload(
                        self.project,
                        client.call_tool(
                            "task_view",
                            {"project": str(self.project.root), "task_id": _task_id_from_row(row)},
                        ),
                    )
                    for row in rows
                )
            return BoardSnapshot(
                project_name=self.project.config.project_name,
                project_root=self.project.root,
                statuses=tuple(columns),
                columns=columns,
                source="daemon",
                revision=None,
            )
        except Exception as exc:
            raise DaemonReadError(str(exc)) from exc

    def search(self, query: str, limit: int = 20) -> tuple[SearchResultView, ...]:
        return _search_project(self.project, query, limit=limit)

    def load_settings(self) -> SettingsView:
        return settings_view_from_config(load_config(self.project.config_path))

    def update_settings(self, input: SettingsInput) -> SettingsView:
        def mutate() -> SettingsView:
            settings = _update_project_settings(self.project, input)
            self.project = _refresh_project_config(self.project)
            return settings

        return with_project_write_lock(self.project, "tui_config_settings_update", mutate)

    def load_definition_of_done_defaults(self) -> DefinitionOfDoneDefaultsView:
        return DefinitionOfDoneDefaultsView(items=tuple(get_definition_of_done_defaults(self.project)))

    def update_definition_of_done_defaults(
        self,
        input: DefinitionOfDoneDefaultsInput,
    ) -> DefinitionOfDoneDefaultsView:
        def mutate() -> DefinitionOfDoneDefaultsView:
            defaults = _update_definition_of_done_defaults(self.project, input)
            self.project = _refresh_project_config(self.project)
            return defaults

        return with_project_write_lock(self.project, "tui_dod_defaults_update", mutate)

    def create_task(self, input: CreateTaskInput) -> TaskView:
        arguments: dict[str, Any] = {
            "project": str(self.project.root),
            "title": input.title,
            "status": input.status,
            "description": input.description,
            "priority": input.priority,
            "assignees": list(input.assignees),
            "labels": list(input.labels),
            "milestone": input.milestone,
            "dependencies": list(input.dependencies),
            "acceptanceCriteria": list(input.acceptance_criteria),
            "definitionOfDoneAdd": list(input.definition_of_done_add),
        }
        return self._mutate("task_create", arguments)

    def move_task(self, task_id: str, status: str) -> TaskView:
        return self._mutate("task_edit", {"project": str(self.project.root), "task_id": task_id, "status": status})

    def edit_task(self, task_id: str, input: EditTaskInput) -> TaskView:
        arguments = {
            "project": str(self.project.root),
            "task_id": task_id,
            **_daemon_edit_arguments(input),
        }
        return self._mutate("task_edit", arguments)

    def archive_task(self, task_id: str) -> TaskView:
        return self._mutate("task_archive", {"project": str(self.project.root), "task_id": task_id})

    def set_checklist_item(self, task_id: str, checklist: str, index: int, *, checked: bool) -> TaskView:
        arguments = {
            "project": str(self.project.root),
            "task_id": task_id,
            **_daemon_checklist_edit_arguments(checklist, index, checked=checked),
        }
        return self._mutate("task_edit", arguments)

    def task_path(self, task_id: str) -> Path:
        client = self._client()
        try:
            payload = client.call_tool("task_view", {"project": str(self.project.root), "task_id": task_id})
            view = task_view_from_mcp_payload(self.project, payload)
            return assert_path_within_base(self.project.root, self.project.root / view.path)
        except Exception as exc:
            raise DaemonReadError(str(exc)) from exc

    def _mutate(self, tool_name: str, arguments: Mapping[str, Any]) -> TaskView:
        try:
            return task_view_from_mcp_payload(self.project, self._client().call_tool(tool_name, arguments))
        except Exception as exc:
            raise DaemonMutationError(str(exc)) from exc

    def _client(self) -> DaemonMcpClient:
        return DaemonMcpClient(self.endpoint, self.token)


def create_board_data_source(project: BacklogProject) -> BoardDataSource:
    try:
        status = daemon_status()
    except DaemonNotRunningError:
        return LocalBoardDataSource(project)
    return DaemonBoardDataSource(project, endpoint=status.record.endpoint, token=status.record.token)


def _search_project(project: BacklogProject, query: str, *, limit: int) -> tuple[SearchResultView, ...]:
    text = query.strip()
    if not text or limit <= 0:
        return ()

    results: list[SearchResultView] = []

    repository = ReadOnlyRepository(
        project,
        refresh_remote_refs=False,
        include_active_branch_snapshots=False,
    )
    for task in repository.search_tasks(text):
        results.append(
            SearchResultView(
                kind="task",
                identifier=task.id,
                title=task.title,
                subtitle=task.status,
                task_id=task.id,
            )
        )
        if len(results) >= limit:
            return tuple(results)

    for document in DocumentService(project).search_documents(text):
        results.append(
            SearchResultView(
                kind="document",
                identifier=document.path_relative,
                title=document.title or document.path_relative,
                subtitle=document.id or "",
            )
        )
        if len(results) >= limit:
            return tuple(results)

    for decision in DecisionService(project).search_decisions(text):
        results.append(
            SearchResultView(
                kind="decision",
                identifier=decision.id,
                title=decision.title or decision.id,
                subtitle=decision.status,
            )
        )
        if len(results) >= limit:
            return tuple(results)

    return tuple(results)


def settings_view_from_config(config: BacklogConfig) -> SettingsView:
    return SettingsView(
        project_name=config.project_name,
        default_assignee=config.default_assignee,
        default_status=config.default_status,
        date_format=config.date_format,
        include_datetime_in_dates=config.include_datetime_in_dates,
        default_port=config.default_port,
        auto_open_browser=config.auto_open_browser,
        zero_padded_ids=config.zero_padded_ids,
        auto_commit=config.auto_commit,
        remote_operations=config.remote_operations,
        check_active_branches=config.check_active_branches,
        active_branch_days=config.active_branch_days,
        statuses=tuple(config.statuses or ()),
    )


def _update_project_settings(project: BacklogProject, input: SettingsInput) -> SettingsView:
    _validate_settings_input(input)
    for key, value in _settings_update_values(input):
        set_config_value(project, key, value)
    return settings_view_from_config(load_config(project.config_path))


def _update_definition_of_done_defaults(
    project: BacklogProject,
    input: DefinitionOfDoneDefaultsInput,
) -> DefinitionOfDoneDefaultsView:
    items = normalize_definition_of_done_defaults(list(input.items))
    config = replace_definition_of_done_defaults(project, list(items))
    return DefinitionOfDoneDefaultsView(items=tuple(config.definition_of_done or ()))


def _settings_update_values(input: SettingsInput) -> tuple[tuple[str, str], ...]:
    return (
        ("projectName", input.project_name.strip()),
        ("defaultAssignee", "" if input.default_assignee is None else input.default_assignee.strip()),
        ("defaultStatus", input.default_status.strip()),
        ("dateFormat", input.date_format.strip()),
        ("includeDatetimeInDates", _bool_text(input.include_datetime_in_dates)),
        ("defaultPort", str(input.default_port)),
        ("autoOpenBrowser", _bool_text(input.auto_open_browser)),
        ("zeroPaddedIds", str(input.zero_padded_ids or 0)),
        ("autoCommit", _bool_text(input.auto_commit)),
        ("remoteOperations", _bool_text(input.remote_operations)),
        ("checkActiveBranches", _bool_text(input.check_active_branches)),
        ("activeBranchDays", str(input.active_branch_days)),
        ("statuses", json.dumps([status.strip() for status in input.statuses])),
    )


def _validate_settings_input(input: SettingsInput) -> None:
    _require_non_empty_string(input.project_name, "Project name")
    _require_optional_string(input.default_assignee, "Default assignee")
    _require_non_empty_string(input.default_status, "Default status")
    _require_non_empty_string(input.date_format, "Date format")
    _require_bool(input.include_datetime_in_dates, "Include datetime in dates")
    _require_int(input.default_port, "Default browser port", minimum=1, maximum=65535)
    _require_bool(input.auto_open_browser, "Auto-open browser")
    if input.zero_padded_ids is not None:
        _require_int(input.zero_padded_ids, "Zero-padded ID width", minimum=0)
    _require_bool(input.auto_commit, "Auto-commit")
    _require_bool(input.remote_operations, "Remote operations")
    _require_bool(input.check_active_branches, "Check active branches")
    _require_int(input.active_branch_days, "Active branch days", minimum=1)
    if not input.statuses:
        raise ValueError("Statuses must include at least one status")
    for status in input.statuses:
        _require_non_empty_string(status, "Statuses")


def _require_non_empty_string(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")


def _require_optional_string(value: object, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} must be a string")


def _require_bool(value: object, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")


def _require_int(value: object, label: str, *, minimum: int | None = None, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}")


def _refresh_project_config(project: BacklogProject) -> BacklogProject:
    return BacklogProject(
        root=project.root,
        backlog_dir=project.backlog_dir,
        config_path=project.config_path,
        config=load_config(project.config_path),
    )


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _extract_tool_result(response: object) -> Any:
    if not isinstance(response, Mapping):
        raise RuntimeError("Daemon response was not a JSON object")
    if "error" in response:
        error = response["error"]
        if isinstance(error, Mapping):
            raise RuntimeError(str(error.get("message") or "Daemon tool call failed"))
        raise RuntimeError("Daemon tool call failed")

    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("Daemon response did not include a tool result")
    content = result.get("content")
    if result.get("isError") is True:
        raise RuntimeError(_tool_content_text(content) or "Daemon tool call failed")
    text = _tool_content_text(content)
    if text is None:
        raise RuntimeError("Daemon tool result did not include text content")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Daemon tool result text must contain valid JSON") from exc


def _tool_content_text(content: object) -> str | None:
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, Mapping):
        return None
    text = first.get("text")
    return text if isinstance(text, str) else None


def _task_id_from_row(row: object) -> str:
    if not isinstance(row, Mapping):
        raise TypeError("Daemon task_board row is not a mapping")
    task_id = row.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise TypeError("Daemon task_board row is missing task id")
    return task_id


def _ordered_board_statuses(project: BacklogProject, board: Mapping[str, object]) -> tuple[str, ...]:
    configured = project.config.statuses
    if configured is None:
        return tuple(board)
    ordered = list(configured)
    ordered.extend(status for status in board if status not in configured)
    return tuple(ordered)


def _validate_loopback_http_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "http" or parsed.hostname not in loopback_hosts:
        raise ValueError("Daemon TUI access requires a loopback HTTP endpoint")


def _checklist_edit_arguments(checklist: str, index: int, *, checked: bool) -> dict[str, list[int]]:
    _validate_checklist_target(checklist, index)
    if checklist == "AC":
        return {"check_ac" if checked else "uncheck_ac": [index]}
    return {"check_dod" if checked else "uncheck_dod": [index]}


def _daemon_checklist_edit_arguments(checklist: str, index: int, *, checked: bool) -> dict[str, list[int]]:
    _validate_checklist_target(checklist, index)
    if checklist == "AC":
        return {"checkAc" if checked else "uncheckAc": [index]}
    return {"checkDod" if checked else "uncheckDod": [index]}


def _local_edit_arguments(input: EditTaskInput) -> dict[str, object]:
    arguments: dict[str, object] = {
        "title": input.title,
        "status": input.status,
        "description": input.description,
        "assignees": input.assignees,
        "labels": input.labels,
        "dependencies": input.dependencies,
    }
    if input.clear_priority:
        arguments["clear_priority"] = True
    elif input.priority is not None:
        arguments["priority"] = input.priority
    if input.clear_milestone:
        arguments["clear_milestone"] = True
    elif input.milestone is not None:
        arguments["milestone"] = input.milestone
    return arguments


def _daemon_edit_arguments(input: EditTaskInput) -> dict[str, object]:
    arguments: dict[str, object] = {
        "title": input.title,
        "status": input.status,
        "description": input.description,
        "assignees": list(input.assignees),
        "labels": list(input.labels),
        "dependencies": list(input.dependencies),
    }
    if input.clear_priority:
        arguments["clearPriority"] = True
    elif input.priority is not None:
        arguments["priority"] = input.priority
    if input.clear_milestone:
        arguments["milestone"] = None
    elif input.milestone is not None:
        arguments["milestone"] = input.milestone
    return arguments


def _validate_checklist_target(checklist: str, index: int) -> None:
    if checklist not in {"AC", "DOD"}:
        raise ValueError("Checklist must be AC or DOD")
    if index < 1:
        raise ValueError("Checklist index must be greater than zero")
