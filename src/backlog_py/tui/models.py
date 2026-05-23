from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

from backlog_py.core.models import BacklogProject, ParsedTaskMarkdown
from backlog_py.core.repository import TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.security.paths import assert_path_within_base


BoardSourceName = Literal["local", "daemon"]


@dataclass(frozen=True)
class ChecklistItemView:
    item_id: str | None
    text: str
    checked: bool


@dataclass(frozen=True)
class TaskView:
    id: str
    title: str
    status: str
    description: str
    path: Path
    priority: str | None = None
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    milestone: str | None = None
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[ChecklistItemView, ...] = ()
    definition_of_done: tuple[ChecklistItemView, ...] = ()
    raw_source: str | None = None


@dataclass(frozen=True)
class BoardSnapshot:
    project_name: str
    project_root: Path
    statuses: tuple[str, ...]
    columns: Mapping[str, tuple[TaskView, ...]]
    source: BoardSourceName
    revision: str | None


@dataclass(frozen=True)
class FilterState:
    text: str = ""
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class SelectionState:
    task_id: str | None = None
    status: str | None = None
    row: int = 0


@dataclass(frozen=True)
class CreateTaskInput:
    title: str
    status: str | None = None
    priority: str | None = None
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    milestone: str | None = None
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    definition_of_done_add: tuple[str, ...] = ()
    description: str = ""


def checklist_items_from_parsed(parsed: ParsedTaskMarkdown, name: str) -> tuple[ChecklistItemView, ...]:
    return tuple(
        ChecklistItemView(item_id=item.item_id, text=item.text, checked=item.checked)
        for item in parsed.checklists.get(name, [])
    )


def task_view_from_record(project: BacklogProject, task: TaskRecord) -> TaskView:
    parsed = task.parsed
    frontmatter = parsed.frontmatter
    return TaskView(
        id=task.id,
        title=task.title,
        status=task.status,
        description=task.description,
        path=_project_relative_path(project, task.path),
        priority=_optional_string(frontmatter.get("priority")),
        assignees=_string_tuple(frontmatter.get("assignee")),
        labels=_string_tuple(frontmatter.get("labels")),
        milestone=_optional_string(frontmatter.get("milestone")),
        dependencies=_string_tuple(frontmatter.get("dependencies")),
        acceptance_criteria=checklist_items_from_parsed(parsed, "AC"),
        definition_of_done=checklist_items_from_parsed(parsed, "DOD"),
        raw_source=task.raw_source,
    )


def task_view_from_mcp_payload(project: BacklogProject, payload: Mapping[str, object]) -> TaskView:
    raw_source = _optional_string(payload.get("raw_source"))
    parsed = parse_task_markdown(raw_source) if raw_source is not None else None
    frontmatter = {} if parsed is None else parsed.frontmatter

    task_id = _required_string(payload.get("id", frontmatter.get("id")), "id")
    title = _optional_string(payload.get("title", frontmatter.get("title"))) or ""
    status = _optional_string(payload.get("status", frontmatter.get("status"))) or project.config.default_status
    description = _optional_string(payload.get("description"))
    if description is None:
        description = _description_from_parsed(parsed)
    path = _project_relative_path(project, _required_string(payload.get("path"), "path"))

    return TaskView(
        id=task_id,
        title=title,
        status=status,
        description=description,
        path=path,
        priority=_optional_string(payload.get("priority", frontmatter.get("priority"))),
        assignees=_string_tuple(_first_present(payload, "assignees", "assignee", default=frontmatter.get("assignee"))),
        labels=_string_tuple(payload.get("labels", frontmatter.get("labels"))),
        milestone=_optional_string(payload.get("milestone", frontmatter.get("milestone"))),
        dependencies=_string_tuple(payload.get("dependencies", frontmatter.get("dependencies"))),
        acceptance_criteria=_payload_checklist(payload.get("acceptance_criteria"))
        or _payload_checklist(payload.get("acceptanceCriteria"))
        or (() if parsed is None else checklist_items_from_parsed(parsed, "AC")),
        definition_of_done=_payload_checklist(payload.get("definition_of_done"))
        or _payload_checklist(payload.get("definitionOfDone"))
        or (() if parsed is None else checklist_items_from_parsed(parsed, "DOD")),
        raw_source=raw_source,
    )


def board_snapshot_from_local(
    project: BacklogProject,
    board: Mapping[str, Sequence[TaskRecord]],
    source: BoardSourceName = "local",
) -> BoardSnapshot:
    columns = {
        status: tuple(task_view_from_record(project, task) for task in tasks)
        for status, tasks in board.items()
    }
    return BoardSnapshot(
        project_name=project.config.project_name,
        project_root=project.root,
        statuses=tuple(board.keys()),
        columns=columns,
        source=source,
        revision=None,
    )


def filter_snapshot(snapshot: BoardSnapshot, filters: FilterState) -> BoardSnapshot:
    columns = {
        status: tuple(task for task in tasks if _matches_filters(task, filters))
        for status, tasks in snapshot.columns.items()
    }
    return BoardSnapshot(
        project_name=snapshot.project_name,
        project_root=snapshot.project_root,
        statuses=snapshot.statuses,
        columns=columns,
        source=snapshot.source,
        revision=snapshot.revision,
    )


def move_status_choices(project: BacklogProject, board_statuses: Sequence[str]) -> tuple[str, ...]:
    if project.config.statuses is not None:
        return tuple(project.config.statuses)
    return tuple(board_statuses)


def create_status_choices(project: BacklogProject, board_statuses: Sequence[str]) -> tuple[str, ...]:
    if project.config.statuses is not None:
        return tuple(project.config.statuses)
    statuses = list(board_statuses)
    if project.config.default_status not in statuses:
        statuses.append(project.config.default_status)
    return tuple(statuses)


def select_after_refresh(
    previous: BoardSnapshot,
    current: BoardSnapshot,
    old_selection: SelectionState,
) -> SelectionState:
    if old_selection.task_id:
        preserved = _find_task_selection(current, old_selection.task_id)
        if preserved is not None:
            return preserved

    if old_selection.status in current.columns:
        tasks = current.columns[old_selection.status]
        if tasks:
            row = _bounded_row(old_selection.row, len(tasks))
            return SelectionState(task_id=tasks[row].id, status=old_selection.status, row=row)

    if old_selection.status in previous.statuses:
        start = previous.statuses.index(old_selection.status)
    elif old_selection.status in current.statuses:
        start = current.statuses.index(old_selection.status)
    else:
        return _first_available_selection(current)

    for index in range(start + 1, len(current.statuses)):
        selection = _first_in_column(current, index)
        if selection is not None:
            return selection
    for index in range(start - 1, -1, -1):
        selection = _first_in_column(current, index)
        if selection is not None:
            return selection
    return SelectionState()


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return () if not text else (text,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(text for item in value if (text := _optional_string(item)) is not None)
    text = str(value).strip()
    return () if not text else (text,)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_checklist(value: object) -> tuple[ChecklistItemView, ...]:
    if value is None or isinstance(value, str):
        return ()
    if not isinstance(value, Sequence):
        return ()
    items: list[ChecklistItemView] = []
    for item in value:
        if isinstance(item, Mapping):
            text = _optional_string(item.get("text"))
            if text is None:
                continue
            item_id = _optional_string(item.get("item_id", item.get("id")))
            items.append(ChecklistItemView(item_id=item_id, text=text, checked=bool(item.get("checked", False))))
        else:
            text = _optional_string(item)
            if text is not None:
                items.append(ChecklistItemView(item_id=None, text=text, checked=False))
    return tuple(items)


def _matches_filters(task: TaskView, filters: FilterState) -> bool:
    return (
        _matches_text_filter(task, filters.text)
        and _matches_exact(task.status, filters.status)
        and _matches_exact(task.priority, filters.priority)
        and _matches_member(task.assignees, filters.assignee)
        and _matches_member(task.labels, filters.label)
    )


def _matches_text_filter(task: TaskView, requested: str) -> bool:
    needle = requested.strip().casefold()
    if not needle:
        return True
    return any(needle in value.casefold() for value in (task.id, task.title, task.description))


def _matches_exact(value: str | None, requested: str | None) -> bool:
    needle = _optional_string(requested)
    if needle is None:
        return True
    return value is not None and value.strip().casefold() == needle.casefold()


def _matches_member(values: Sequence[str], requested: str | None) -> bool:
    needle = _optional_string(requested)
    if needle is None:
        return True
    normalized_values = {value.strip().casefold() for value in values if value.strip()}
    return needle.casefold() in normalized_values


def _project_relative_path(project: BacklogProject, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project.root / candidate
    contained = assert_path_within_base(project.root, candidate)
    return contained.relative_to(project.root.resolve())


def _required_string(value: object, field: str) -> str:
    text = _optional_string(value)
    if text is None:
        raise ValueError(f"MCP task payload is missing required field: {field}")
    return text


def _description_from_parsed(parsed: ParsedTaskMarkdown | None) -> str:
    if parsed is None:
        return ""
    section = parsed.sections.get("DESCRIPTION")
    return "" if section is None else section.content.strip()


def _first_present(payload: Mapping[str, object], *keys: str, default: object = None) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _find_task_selection(snapshot: BoardSnapshot, task_id: str) -> SelectionState | None:
    normalized_id = task_id.casefold()
    for status in snapshot.statuses:
        for row, task in enumerate(snapshot.columns.get(status, ())):
            if task.id.casefold() == normalized_id:
                return SelectionState(task_id=task.id, status=status, row=row)
    return None


def _bounded_row(row: int, length: int) -> int:
    if length <= 0:
        return 0
    return max(0, min(row, length - 1))


def _first_in_column(snapshot: BoardSnapshot, index: int) -> SelectionState | None:
    status = snapshot.statuses[index]
    tasks = snapshot.columns.get(status, ())
    if not tasks:
        return None
    return SelectionState(task_id=tasks[0].id, status=status, row=0)


def _first_available_selection(snapshot: BoardSnapshot) -> SelectionState:
    for index in range(len(snapshot.statuses)):
        selection = _first_in_column(snapshot, index)
        if selection is not None:
            return selection
    return SelectionState()
