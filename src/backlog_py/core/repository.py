from __future__ import annotations

import os
import re
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from backlog_py.core.ids import format_child_task_id, format_numbered_id
from backlog_py.core.models import BacklogConfig, BacklogProject, ParsedTaskMarkdown
from backlog_py.core.status_callback import execute_status_callback
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.runtime.git import maybe_fetch_remote_refs
from backlog_py.search.simple import contains_query
from backlog_py.security.paths import PathContainmentError, assert_path_within_base
from backlog_py.storage.config import load_config
from backlog_py.storage.project import discover_project


_TASK_ID_RE = re.compile(r"^[A-Z]+-\d+(?:\.\d+)*$")
_CHECKLIST_LINE_RE = re.compile(r"^(?P<prefix>\s*[-*]\s+\[)[ xX](?P<suffix>\]\s+.*)$")
_NO_STATUS_CALLBACK_UPDATE = object()


@dataclass(frozen=True)
class TaskRecord:
    id: str
    title: str
    status: str
    path: Path
    parsed: ParsedTaskMarkdown

    @property
    def description(self) -> str:
        section = self.parsed.sections.get("DESCRIPTION")
        return "" if section is None else section.content.strip()

    @property
    def body(self) -> str:
        return self.parsed.body

    @property
    def raw_source(self) -> str:
        return self.parsed.raw_source


class ReadOnlyRepository:
    def __init__(self, project: BacklogProject) -> None:
        self.project = project
        self._remote_refs_refreshed = False

    @classmethod
    def from_path(cls, cwd: Path) -> "ReadOnlyRepository":
        return cls(discover_project(Path.cwd(), explicit_cwd=cwd))

    def list_tasks(
        self,
        *,
        status: str | None = None,
        assignee: str | Sequence[str] | None = None,
        labels: str | Sequence[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        parent_task_id: str | None = None,
    ) -> list[TaskRecord]:
        tasks = sorted(self._load_tasks(), key=_task_record_sort_key)
        return [
            task
            for task in tasks
            if _task_matches_filters(
                task,
                task_prefix=self.project.config.task_prefix,
                status=status,
                assignee=assignee,
                labels=labels,
                priority=priority,
                milestone=milestone,
                parent_task_id=parent_task_id,
            )
        ]

    def get_task(self, task_id: str) -> TaskRecord:
        normalized_id = task_id.casefold()
        try:
            normalized_lookup_id = _normalize_dependency_id(task_id, self.project.config.task_prefix).casefold()
        except TaskMutationError:
            normalized_lookup_id = normalized_id
        for task in self.list_tasks():
            if task.id.casefold() in {normalized_id, normalized_lookup_id}:
                return task
        raise KeyError(f"Task not found: {task_id}")

    def list_completed_tasks(self) -> list[TaskRecord]:
        return sorted(self._load_completed_tasks(), key=_task_record_sort_key)

    def search_tasks(
        self,
        query: str = "",
        *,
        status: str | None = None,
        assignee: str | Sequence[str] | None = None,
        labels: str | Sequence[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        parent_task_id: str | None = None,
        modified_files: str | Sequence[str] | None = None,
    ) -> list[TaskRecord]:
        tasks = [*self.list_tasks(), *self.list_completed_tasks()]
        return [
            task
            for task in tasks
            if _task_matches_filters(
                task,
                task_prefix=self.project.config.task_prefix,
                status=status,
                assignee=assignee,
                labels=labels,
                priority=priority,
                milestone=milestone,
                parent_task_id=parent_task_id,
            )
            if contains_query(_search_text(task), query)
            and _matches_modified_file_filters(task, modified_files)
        ]

    def board(self) -> "OrderedDict[str, list[TaskRecord]]":
        statuses = self.project.config.statuses or _statuses_from_tasks(self.list_tasks())
        board: OrderedDict[str, list[TaskRecord]] = OrderedDict((status, []) for status in statuses)
        for task in self.list_tasks():
            board.setdefault(task.status, []).append(task)
        return board

    def _load_tasks(self) -> list[TaskRecord]:
        self._ensure_remote_refs()
        task_dir = self.project.backlog_dir / "tasks"
        return _load_tasks_from_dir(task_dir)

    def _load_completed_tasks(self) -> list[TaskRecord]:
        self._ensure_remote_refs()
        completed_dir = self.project.backlog_dir / "completed"
        return _load_tasks_from_dir(completed_dir)

    def _ensure_remote_refs(self) -> None:
        if self._remote_refs_refreshed:
            return
        self._remote_refs_refreshed = True
        maybe_fetch_remote_refs(self.project)


def _load_tasks_from_dir(task_dir: Path) -> list[TaskRecord]:
    if not task_dir.is_dir():
        return []
    return [_load_task(path) for path in sorted(task_dir.glob("*.md"))]


class TaskMutationError(ValueError):
    """Raised when a task mutation request is invalid or unsupported."""


class MutableRepository(ReadOnlyRepository):
    @classmethod
    def from_path(cls, cwd: Path) -> "MutableRepository":
        return cls(discover_project(Path.cwd(), explicit_cwd=cwd))

    def create_task(
        self,
        *,
        title: str,
        task_id: str | None = None,
        status: str | None = None,
        description: str = "",
        plan: str = "",
        notes: str = "",
        final_summary: str = "",
        parent_task_id: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        definition_of_done: Sequence[str] | None = None,
        definition_of_done_add: Sequence[str] | None = None,
        disable_definition_of_done_defaults: bool = False,
        dependencies: Sequence[str] | None = None,
        assignees: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        ordinal: int | float | str | None = None,
        references: Sequence[str] | None = None,
        documentation: Sequence[str] | None = None,
        modified_files: Sequence[str] | None = None,
        on_status_change: str | bool | None = None,
    ) -> TaskRecord:
        current_config = load_config(self.project.config_path)
        normalized_on_status_change = _normalize_on_status_change_create(on_status_change)
        tasks = self.list_tasks()
        normalized_parent_task_id = _normalize_parent_task_id(parent_task_id, tasks, current_config.task_prefix)
        normalized_id = _normalize_task_id(
            task_id or (
                self._next_child_task_id(normalized_parent_task_id)
                if normalized_parent_task_id is not None
                else self._next_task_id(current_config)
            ),
            current_config.task_prefix,
        )
        if _task_exists(tasks, normalized_id):
            raise TaskMutationError(f"Task id already exists: {normalized_id}")
        normalized_dependencies = _normalize_dependency_ids(dependencies, current_config.task_prefix)
        _reject_missing_dependencies(normalized_dependencies, tasks)
        _reject_circular_dependencies(normalized_id, normalized_dependencies, tasks)
        task_status = status or current_config.default_status
        _reject_unknown_status(task_status, current_config.statuses)
        target = self._task_path(normalized_id, title)
        if target.exists():
            raise TaskMutationError(f"Task path already exists: {target.name}")
        task_definition_of_done = _definition_of_done_for_create(
            explicit=definition_of_done,
            additions=definition_of_done_add,
            defaults=current_config.definition_of_done,
            disable_defaults=disable_definition_of_done_defaults,
        )
        content = _new_task_source(
            task_id=normalized_id,
            title=title,
            status=task_status,
            description=description,
            plan=plan,
            notes=notes,
            final_summary=final_summary,
            acceptance_criteria=acceptance_criteria or (),
            definition_of_done=task_definition_of_done,
            dependencies=normalized_dependencies,
            assignees=_normalize_metadata_list(assignees),
            labels=_normalize_metadata_list(labels),
            priority=_normalize_optional_string(priority),
            milestone=_normalize_optional_string(milestone),
            ordinal=normalize_ordinal_value(ordinal),
            parent_task_id=normalized_parent_task_id,
            references=_normalize_metadata_list(references),
            documentation=_normalize_metadata_list(documentation),
            modified_files=_normalize_metadata_list(modified_files),
            on_status_change=normalized_on_status_change,
            created_date=_current_task_timestamp(),
        )
        parse_task_markdown(content)
        _atomic_write_text(target, content)
        return _load_task(target)

    def edit_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        plan: str | None = None,
        append_plan: Sequence[str] | None = None,
        clear_plan: bool = False,
        notes: str | None = None,
        append_notes: str | None = None,
        acceptance_criteria: Sequence[str] | None = None,
        acceptance_criteria_add: Sequence[str] | None = None,
        definition_of_done_add: Sequence[str] | None = None,
        final_summary: str | None = None,
        append_final_summary: Sequence[str] | None = None,
        clear_final_summary: bool = False,
        check_ac: Sequence[int] | None = None,
        check_dod: Sequence[int] | None = None,
        uncheck_ac: Sequence[int] | None = None,
        uncheck_dod: Sequence[int] | None = None,
        remove_ac: Sequence[int] | None = None,
        remove_dod: Sequence[int] | None = None,
        dependencies: Sequence[str] | None = None,
        assignees: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        ordinal: int | float | str | None = None,
        clear_milestone: bool = False,
        references: Sequence[str] | None = None,
        add_references: Sequence[str] | None = None,
        remove_references: Sequence[str] | None = None,
        documentation: Sequence[str] | None = None,
        add_documentation: Sequence[str] | None = None,
        remove_documentation: Sequence[str] | None = None,
        modified_files: Sequence[str] | None = None,
        status: str | None = None,
        on_status_change: str | bool | None = None,
    ) -> TaskRecord:
        if milestone is not None and clear_milestone:
            raise TaskMutationError("Cannot set and clear milestone in one edit")
        task = self.get_task(task_id)
        old_status = task.status
        original_source = task.raw_source
        on_status_change_update = _normalize_on_status_change_update(on_status_change)
        safe_current_path = _mutation_path(task.path.parent, task.path)
        target_path = safe_current_path
        if title is not None:
            target_path = self._task_path(task.id, title)
            if target_path != safe_current_path and target_path.exists():
                raise TaskMutationError(f"Task path already exists: {target_path.name}")
        normalized_dependencies = None
        if dependencies is not None:
            tasks = self.list_tasks()
            normalized_dependencies = _normalize_dependency_ids(dependencies, self.project.config.task_prefix)
            _reject_missing_dependencies(normalized_dependencies, tasks)
            _reject_circular_dependencies(task.id, normalized_dependencies, tasks)
        source = task.raw_source
        parsed = task.parsed
        if description is not None:
            source = _replace_section(source, parsed, "DESCRIPTION", _normalize_block(description))
            parsed = parse_task_markdown(source)
        if plan is not None:
            source = _replace_or_insert_section(source, parsed, "PLAN", _normalize_block(plan))
            parsed = parse_task_markdown(source)
        if append_plan:
            source = _append_to_structured_section(source, parsed, "PLAN", append_plan)
            parsed = parse_task_markdown(source)
        if clear_plan:
            source = _remove_section(source, parsed, "PLAN")
            parsed = parse_task_markdown(source)
        if notes is not None:
            source = _replace_section(source, parsed, "IMPLEMENTATION_NOTES", _normalize_block(notes))
            parsed = parse_task_markdown(source)
        if append_notes is not None:
            existing_notes = parsed.sections.get("IMPLEMENTATION_NOTES")
            existing_content = "" if existing_notes is None else existing_notes.content.rstrip()
            appended = _normalize_block(append_notes)
            notes_content = appended if not existing_content else f"{existing_content}\n{appended}"
            source = _replace_section(source, parsed, "IMPLEMENTATION_NOTES", notes_content)
            parsed = parse_task_markdown(source)
        if final_summary is not None:
            source = _replace_section(source, parsed, "FINAL_SUMMARY", _normalize_block(final_summary))
            parsed = parse_task_markdown(source)
        if append_final_summary:
            source = _append_to_section(source, parsed, "FINAL_SUMMARY", append_final_summary)
            parsed = parse_task_markdown(source)
        if clear_final_summary:
            source = _replace_section(source, parsed, "FINAL_SUMMARY", "")
            parsed = parse_task_markdown(source)
        if acceptance_criteria is not None:
            source = _replace_checklist_items(source, "AC", acceptance_criteria)
            parsed = parse_task_markdown(source)
        if check_ac:
            source = _set_checklist_indexes(source, parsed, "AC", check_ac, checked=True)
            parsed = parse_task_markdown(source)
        if check_dod:
            source = _set_checklist_indexes(source, parsed, "DOD", check_dod, checked=True)
            parsed = parse_task_markdown(source)
        if uncheck_ac:
            source = _set_checklist_indexes(source, parsed, "AC", uncheck_ac, checked=False)
            parsed = parse_task_markdown(source)
        if uncheck_dod:
            source = _set_checklist_indexes(source, parsed, "DOD", uncheck_dod, checked=False)
            parsed = parse_task_markdown(source)
        if remove_ac:
            source = _remove_checklist_indexes(source, parsed, "AC", remove_ac)
            parsed = parse_task_markdown(source)
        if remove_dod:
            source = _remove_checklist_indexes(source, parsed, "DOD", remove_dod)
            parsed = parse_task_markdown(source)
        if acceptance_criteria_add:
            source = _append_checklist_items(source, parsed, "AC", acceptance_criteria_add)
            parsed = parse_task_markdown(source)
        if definition_of_done_add:
            source = _append_checklist_items(source, parsed, "DOD", definition_of_done_add)
            parsed = parse_task_markdown(source)
        normalized_assignees = _normalize_metadata_list(assignees) if assignees is not None else None
        normalized_labels = _normalize_metadata_list(labels) if labels is not None else None
        normalized_priority = _normalize_optional_string(priority) if priority is not None else None
        normalized_milestone = _normalize_optional_string(milestone) if milestone is not None else None
        normalized_ordinal = normalize_ordinal_value(ordinal) if ordinal is not None else None
        normalized_references = _metadata_update(
            parsed.frontmatter,
            "references",
            replace=references,
            add=add_references,
            remove=remove_references,
        )
        normalized_documentation = _metadata_update(
            parsed.frontmatter,
            "documentation",
            replace=documentation,
            add=add_documentation,
            remove=remove_documentation,
        )
        normalized_modified_files = (
            _normalize_metadata_list(modified_files) if modified_files is not None else None
        )
        if (
            title is not None
            or status is not None
            or normalized_dependencies is not None
            or normalized_assignees is not None
            or normalized_labels is not None
            or normalized_priority is not None
            or normalized_milestone is not None
            or normalized_ordinal is not None
            or clear_milestone
            or normalized_references is not None
            or normalized_documentation is not None
            or normalized_modified_files is not None
            or on_status_change_update is not _NO_STATUS_CALLBACK_UPDATE
        ):
            updates: dict[str, object] = {}
            if title is not None:
                updates["title"] = title
            if status is not None:
                _reject_unknown_status(status, self.project.config.statuses)
                updates["status"] = status
            if normalized_dependencies is not None:
                updates["dependencies"] = normalized_dependencies
            if normalized_assignees is not None:
                updates["assignee"] = normalized_assignees
            if normalized_labels is not None:
                updates["labels"] = normalized_labels
            if normalized_priority is not None:
                updates["priority"] = normalized_priority
            if normalized_milestone is not None:
                updates["milestone"] = normalized_milestone
            if normalized_ordinal is not None:
                updates["ordinal"] = normalized_ordinal
            if clear_milestone:
                updates["milestone"] = None
            if normalized_references is not None:
                updates["references"] = normalized_references
            if normalized_documentation is not None:
                updates["documentation"] = normalized_documentation
            if normalized_modified_files is not None:
                updates["modified_files"] = normalized_modified_files
            if on_status_change_update is not _NO_STATUS_CALLBACK_UPDATE:
                updates["onStatusChange"] = on_status_change_update
            source = _replace_frontmatter_values(source, parsed, updates)
            parsed = parse_task_markdown(source)
        if source != original_source or target_path != safe_current_path:
            source = _replace_frontmatter_values(
                source,
                parsed,
                {"updated_date": _current_task_timestamp()},
            )
            parsed = parse_task_markdown(source)
        parse_task_markdown(source)
        _atomic_write_text(target_path, source)
        if target_path != safe_current_path:
            safe_current_path.unlink()
        updated_task = _load_task(target_path)
        if status is not None and updated_task.status != old_status:
            _run_status_change_callback(self.project, updated_task, old_status, updated_task.status)
        return updated_task

    def archive_task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        safe_current_path = _mutation_path(task.path.parent, task.path)
        archive_root = _mutation_path(self.project.backlog_dir, self.project.backlog_dir / "archive")
        archive_dir = _mutation_path(archive_root, archive_root / "tasks")
        archive_dir.mkdir(parents=True, exist_ok=True)
        target_path = _mutation_path(archive_dir, archive_dir / task.path.name)
        if target_path.exists():
            raise TaskMutationError(f"Archived task path already exists: {target_path.name}")
        os.replace(safe_current_path, target_path)
        return _load_task(target_path)

    def complete_task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if not _is_done_status(task.status):
            raise TaskMutationError(f'Task {task.id} is not Done. Set status to "Done" before completing it.')
        safe_current_path = _mutation_path(task.path.parent, task.path)
        completed_dir = _mutation_path(self.project.backlog_dir, self.project.backlog_dir / "completed")
        completed_dir.mkdir(parents=True, exist_ok=True)
        target_path = _mutation_path(completed_dir, completed_dir / task.path.name)
        if target_path.exists():
            raise TaskMutationError(f"Completed task path already exists: {target_path.name}")
        os.replace(safe_current_path, target_path)
        return _load_task(target_path)

    def _next_task_id(self, config: BacklogConfig | None = None) -> str:
        current_config = config or self.project.config
        max_id = 0
        prefix = current_config.task_prefix.upper()
        pattern = re.compile(rf"{re.escape(prefix)}-(\d+)", re.IGNORECASE)
        for task in self.list_tasks():
            match = pattern.fullmatch(task.id)
            if match is not None:
                max_id = max(max_id, int(match.group(1)))
        return format_numbered_id(f"{prefix}-", max_id + 1, current_config.zero_padded_ids)

    def _next_child_task_id(self, parent_task_id: str) -> str:
        max_id = 0
        prefix = f"{parent_task_id}."
        for task in self.list_tasks():
            if not task.id.upper().startswith(prefix):
                continue
            rest = task.id[len(prefix):]
            first_segment = rest.split(".", 1)[0]
            if first_segment.isdigit():
                max_id = max(max_id, int(first_segment))
        return format_child_task_id(parent_task_id, max_id + 1, self.project.config.zero_padded_ids)

    def _task_path(self, task_id: str, title: str) -> Path:
        task_dir = self.project.backlog_dir / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / f"{task_id.lower()} - {_slug_title(title)}.md"
        return _mutation_path(task_dir, path)


def _load_task(path: Path) -> TaskRecord:
    with path.open("r", encoding="utf-8", newline="") as task_file:
        parsed = parse_task_markdown(task_file.read())
    frontmatter = parsed.frontmatter
    task_id = str(frontmatter.get("id") or _id_from_filename(path))
    return TaskRecord(
        id=task_id,
        title=str(frontmatter.get("title") or ""),
        status=str(frontmatter.get("status") or "To Do"),
        path=path,
        parsed=parsed,
    )


def _task_matches_filters(
    task: TaskRecord,
    *,
    task_prefix: str,
    status: str | None,
    assignee: str | Sequence[str] | None,
    labels: str | Sequence[str] | None,
    priority: str | None,
    milestone: str | None,
    parent_task_id: str | None = None,
) -> bool:
    return (
        _matches_text(task.status, status)
        and _matches_frontmatter_values(task, "assignee", assignee)
        and _matches_frontmatter_values(task, "labels", labels)
        and _matches_frontmatter_text(task, "priority", priority)
        and _matches_frontmatter_text(task, "milestone", milestone)
        and _matches_parent_task_id(task, parent_task_id, task_prefix)
    )


def _matches_frontmatter_text(task: TaskRecord, key: str, requested: str | None) -> bool:
    value = task.parsed.frontmatter.get(key)
    return _matches_text(None if value is None else str(value), requested)


def _matches_parent_task_id(task: TaskRecord, requested: str | None, task_prefix: str) -> bool:
    if requested is None:
        return True
    parent = task.parsed.frontmatter.get("parent_task_id")
    return parent is not None and _task_ids_equal(str(parent), requested, task_prefix)


def _matches_text(actual: str | None, requested: str | None) -> bool:
    if requested is None:
        return True
    normalized_requested = requested.strip().casefold()
    if not normalized_requested:
        return True
    return actual is not None and actual.strip().casefold() == normalized_requested


def _matches_frontmatter_values(
    task: TaskRecord,
    key: str,
    requested: str | Sequence[str] | None,
) -> bool:
    requested_values = _normalize_filter_values(requested)
    if not requested_values:
        return True
    actual_values = {
        value.strip().casefold()
        for value in _frontmatter_string_list(task.parsed.frontmatter.get(key))
        if value.strip()
    }
    return all(value.strip().casefold() in actual_values for value in requested_values)


def _matches_modified_file_filters(
    task: TaskRecord,
    requested: str | Sequence[str] | None,
) -> bool:
    requested_values = _normalize_filter_values(requested)
    if not requested_values:
        return True
    modified_files = [
        value.strip().casefold()
        for value in _frontmatter_string_list(task.parsed.frontmatter.get("modified_files"))
        if value.strip()
    ]
    if not modified_files:
        return False
    return any(
        requested_value.strip().casefold() in modified_file
        for requested_value in requested_values
        for modified_file in modified_files
    )


def _normalize_filter_values(values: str | Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return _normalize_metadata_list([values])
    return _normalize_metadata_list(values)


def _new_task_source(
    *,
    task_id: str,
    title: str,
    status: str,
    description: str,
    plan: str,
    notes: str,
    final_summary: str,
    acceptance_criteria: Sequence[str],
    definition_of_done: Sequence[str],
    dependencies: Sequence[str],
    assignees: Sequence[str],
    labels: Sequence[str],
    priority: str | None,
    milestone: str | None,
    ordinal: int | float | None,
    parent_task_id: str | None,
    references: Sequence[str],
    documentation: Sequence[str],
    modified_files: Sequence[str],
    on_status_change: str | None,
    created_date: str | None = None,
) -> str:
    frontmatter: dict[str, object] = {
        "id": task_id,
        "title": title,
        "status": status,
    }
    if created_date:
        frontmatter["created_date"] = created_date
    if dependencies:
        frontmatter["dependencies"] = list(dependencies)
    if assignees:
        frontmatter["assignee"] = list(assignees)
    if labels:
        frontmatter["labels"] = list(labels)
    if priority:
        frontmatter["priority"] = priority
    if milestone:
        frontmatter["milestone"] = milestone
    if ordinal is not None:
        frontmatter["ordinal"] = ordinal
    if parent_task_id:
        frontmatter["parent_task_id"] = parent_task_id
    if references:
        frontmatter["references"] = list(references)
    if documentation:
        frontmatter["documentation"] = list(documentation)
    if modified_files:
        frontmatter["modified_files"] = list(modified_files)
    if on_status_change:
        frontmatter["onStatusChange"] = on_status_change
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return (
        f"---\n{yaml_text}\n---\n\n"
        "## Description\n\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\n"
        f"{_normalize_block(description)}\n"
        "<!-- SECTION:DESCRIPTION:END -->\n\n"
        "## Acceptance Criteria\n"
        "<!-- AC:BEGIN -->\n"
        f"{_render_checklist(acceptance_criteria)}"
        "<!-- AC:END -->\n\n"
        f"{_render_optional_section('PLAN', plan)}"
        "## Implementation Notes\n\n"
        "<!-- SECTION:IMPLEMENTATION_NOTES:BEGIN -->\n"
        f"{_normalize_block(notes)}\n"
        "<!-- SECTION:IMPLEMENTATION_NOTES:END -->\n\n"
        "## Final Summary\n\n"
        "<!-- SECTION:FINAL_SUMMARY:BEGIN -->\n"
        f"{_normalize_block(final_summary)}\n"
        "<!-- SECTION:FINAL_SUMMARY:END -->\n\n"
        "## Definition of Done\n"
        "<!-- DOD:BEGIN -->\n"
        f"{_render_checklist(definition_of_done)}"
        "<!-- DOD:END -->\n"
    )


def _atomic_write_text(path: Path, content: str) -> None:
    safe_path = _mutation_path(path.parent, path)
    temp_name: str | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=safe_path.parent,
        prefix=f".{safe_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_name = temp_file.name
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
    try:
        os.replace(temp_name, safe_path)
    except Exception:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
        raise


def _mutation_path(base: Path, candidate: Path) -> Path:
    try:
        return assert_path_within_base(base, candidate)
    except PathContainmentError as exc:
        raise TaskMutationError(str(exc)) from exc


def _replace_section(source: str, parsed: ParsedTaskMarkdown, name: str, content: str) -> str:
    section = parsed.sections.get(name)
    new_section = _render_section_markers(name, content)
    if section is not None:
        return source.replace(section.raw.rstrip("\r\n"), new_section, 1)
    return source.rstrip() + f"\n\n{_heading_for_section(name)}\n\n{new_section}\n"


def _replace_or_insert_section(source: str, parsed: ParsedTaskMarkdown, name: str, content: str) -> str:
    if name in parsed.sections:
        return _replace_section(source, parsed, name, content)
    return _insert_section(source, name, content)


def _append_to_structured_section(
    source: str,
    parsed: ParsedTaskMarkdown,
    name: str,
    items: Sequence[str],
) -> str:
    section = parsed.sections.get(name)
    existing_content = "" if section is None else section.content.rstrip()
    appended = "\n".join(_normalize_block(item) for item in items if _normalize_block(item))
    if not appended:
        return source
    content = appended if not existing_content else f"{existing_content}\n{appended}"
    return _replace_or_insert_section(source, parsed, name, content)


def _remove_section(source: str, parsed: ParsedTaskMarkdown, name: str) -> str:
    section = parsed.sections.get(name)
    if section is None:
        return source
    section_start = source.find(section.raw)
    if section_start == -1:
        return source
    heading = _heading_for_section(name)
    heading_start = source.rfind(heading, 0, section_start)
    remove_start = heading_start if heading_start != -1 else section_start
    remove_end = section_start + len(section.raw)
    while remove_end < len(source) and source[remove_end] in "\r\n":
        remove_end += 1
    before = source[:remove_start].rstrip()
    after = source[remove_end:].lstrip()
    if before and after:
        return f"{before}\n\n{after}"
    if before:
        return f"{before}\n"
    return after


def _insert_section(source: str, name: str, content: str) -> str:
    block = f"{_heading_for_section(name)}\n\n{_render_section_markers(name, content)}"
    if name == "PLAN":
        try:
            acceptance_block = _extract_marker_block(source, "AC")
        except TaskMutationError:
            acceptance_block = ""
        if acceptance_block:
            return source.replace(acceptance_block, f"{acceptance_block.rstrip()}\n\n{block}", 1)
    return source.rstrip() + f"\n\n{block}\n"


def _append_to_section(
    source: str,
    parsed: ParsedTaskMarkdown,
    name: str,
    items: Sequence[str],
) -> str:
    section = parsed.sections.get(name)
    existing_content = "" if section is None else section.content.rstrip()
    appended = "\n".join(_normalize_block(item) for item in items if _normalize_block(item))
    content = appended if not existing_content else f"{existing_content}\n{appended}"
    return _replace_section(source, parsed, name, content)


def _append_checklist_items(
    source: str,
    parsed: ParsedTaskMarkdown,
    marker: str,
    items: Sequence[str],
) -> str:
    normalized_items = [_normalize_block(item) for item in items if _normalize_block(item)]
    if not normalized_items:
        return source
    raw = _extract_marker_block(source, marker)
    lines = raw.splitlines(keepends=True)
    if not lines:
        raise TaskMutationError(f"Missing {marker} checklist section")
    newline = "\r\n" if "\r\n" in raw else "\n"
    prefix = "".join(lines[:-1])
    if prefix and not prefix.endswith(("\n", "\r\n")):
        prefix = f"{prefix}{newline}"
    start_index = len(parsed.checklists.get(marker, [])) + 1
    appended = "".join(
        f"- [ ] #{index} {item}{newline}"
        for index, item in enumerate(normalized_items, start=start_index)
    )
    return source.replace(raw, f"{prefix}{appended}{lines[-1]}", 1)


def _replace_checklist_items(
    source: str,
    marker: str,
    items: Sequence[str],
) -> str:
    normalized_items = [_normalize_block(item) for item in items if _normalize_block(item)]
    raw = _extract_marker_block(source, marker)
    newline = "\r\n" if "\r\n" in raw else "\n"
    rendered_items = _render_checklist(normalized_items)
    if newline == "\r\n":
        rendered_items = rendered_items.replace("\n", "\r\n")
    replacement = f"<!-- {marker}:BEGIN -->{newline}{rendered_items}<!-- {marker}:END -->"
    return source.replace(raw, replacement, 1)


def _remove_checklist_indexes(
    source: str,
    parsed: ParsedTaskMarkdown,
    marker: str,
    indexes: Sequence[int],
) -> str:
    _reject_checklist_indexes(marker, indexes, len(parsed.checklists.get(marker, [])))
    remove_indexes = set(indexes)
    raw = _extract_marker_block(source, marker)
    lines = raw.splitlines(keepends=True)
    item_number = 0
    rendered: list[str] = []
    for line in lines:
        raw_line = line.rstrip("\r\n")
        if _CHECKLIST_LINE_RE.match(raw_line):
            item_number += 1
            if item_number in remove_indexes:
                continue
        rendered.append(line)
    return source.replace(raw, "".join(rendered), 1)


def _set_checklist_indexes(
    source: str,
    parsed: ParsedTaskMarkdown,
    marker: str,
    indexes: Sequence[int],
    *,
    checked: bool,
) -> str:
    items = parsed.checklists.get(marker, [])
    _reject_checklist_indexes(marker, indexes, len(items))
    raw = _extract_marker_block(source, marker)
    lines = raw.splitlines(keepends=True)
    item_number = 0
    rendered: list[str] = []
    for line in lines:
        raw_line = line.rstrip("\r\n")
        if _CHECKLIST_LINE_RE.match(raw_line):
            item_number += 1
            if item_number in indexes:
                line = _set_checklist_line(line, checked=checked)
        rendered.append(line)
    return source.replace(raw, "".join(rendered), 1)


def _reject_checklist_indexes(marker: str, indexes: Sequence[int], item_count: int) -> None:
    for index in indexes:
        if index < 1 or index > item_count:
            raise TaskMutationError(f"{marker} checklist index {index} is out of range")


def _replace_frontmatter_values(
    source: str,
    parsed: ParsedTaskMarkdown,
    updates: dict[str, object],
) -> str:
    frontmatter = dict(parsed.frontmatter)
    for key, value in updates.items():
        if value is None:
            frontmatter.pop(key, None)
        else:
            frontmatter[key] = value
    newline = "\r\n" if "\r\n" in source else "\n"
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    yaml_text = yaml_text.replace("\n", newline)
    body = parsed.body
    return f"---{newline}{yaml_text}{newline}---{newline}{body}"


def _extract_marker_block(source: str, marker: str) -> str:
    pattern = re.compile(
        rf"<!-- {re.escape(marker)}:BEGIN -->.*?<!-- {re.escape(marker)}:END -->",
        re.DOTALL,
    )
    match = pattern.search(source)
    if match is None:
        raise TaskMutationError(f"Missing {marker} checklist section")
    return match.group(0)


def _set_checklist_line(line: str, *, checked: bool) -> str:
    raw_line = line.rstrip("\r\n")
    newline = line[len(raw_line):]
    match = _CHECKLIST_LINE_RE.match(raw_line)
    if match is None:
        return line
    mark = "x" if checked else " "
    return f"{match.group('prefix')}{mark}{match.group('suffix')}{newline}"


def _render_checklist(items: Sequence[str]) -> str:
    return "".join(f"- [ ] #{index} {item}\n" for index, item in enumerate(items, start=1))


def _render_optional_section(name: str, content: str) -> str:
    normalized = _normalize_block(content)
    if not normalized:
        return ""
    return f"{_heading_for_section(name)}\n\n{_render_section_markers(name, normalized)}\n\n"


def _render_section_markers(name: str, content: str) -> str:
    return (
        f"<!-- SECTION:{name}:BEGIN -->\n"
        f"{content}\n"
        f"<!-- SECTION:{name}:END -->"
    )


def _definition_of_done_for_create(
    *,
    explicit: Sequence[str] | None,
    additions: Sequence[str] | None,
    defaults: Sequence[str] | None,
    disable_defaults: bool,
) -> list[str]:
    if explicit is not None:
        return list(explicit)
    inherited = [] if disable_defaults else list(defaults or ())
    inherited.extend(additions or ())
    return inherited


def _normalize_block(content: str) -> str:
    return content.strip()


def _normalize_task_id(task_id: str, task_prefix: str = "task") -> str:
    candidate = task_id.strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", candidate):
        candidate = f"{task_prefix.upper()}-{candidate}"
    normalized = candidate.upper()
    if _TASK_ID_RE.fullmatch(normalized) is None:
        raise TaskMutationError(f"Invalid task id: {task_id}")
    return normalized


def _current_task_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _normalize_dependency_ids(dependencies: Sequence[str] | None, task_prefix: str = "task") -> list[str]:
    normalized: list[str] = []
    for raw_dependency in dependencies or ():
        for dependency in str(raw_dependency).split(","):
            trimmed = dependency.strip()
            if trimmed:
                normalized.append(_normalize_dependency_id(trimmed, task_prefix))
    return normalized


def _normalize_metadata_list(values: Sequence[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw_value in values or ():
        for value in str(raw_value).split(","):
            trimmed = value.strip()
            if trimmed:
                normalized.append(trimmed)
    return normalized


def _metadata_update(
    frontmatter: dict[str, object],
    key: str,
    *,
    replace: Sequence[str] | None,
    add: Sequence[str] | None,
    remove: Sequence[str] | None,
) -> list[str] | None:
    if replace is not None:
        return _normalize_metadata_list(replace)
    if add is None and remove is None:
        return None
    values = _frontmatter_string_list(frontmatter.get(key))
    remove_values = set(_normalize_metadata_list(remove))
    if remove_values:
        values = [value for value in values if value not in remove_values]
    for value in _normalize_metadata_list(add):
        if value not in values:
            values.append(value)
    return values


def _frontmatter_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_ordinal_value(value: int | float | str | None) -> int | float | None:
    """Return a YAML-safe non-negative ordinal value or reject invalid input."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TaskMutationError(f"Invalid ordinal: {value}. Must be a non-negative number.")
    if isinstance(value, int):
        number: int | float = value
    elif isinstance(value, float):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise TaskMutationError(f"Invalid ordinal: {value}. Must be a non-negative number.")
        try:
            number = int(text) if re.fullmatch(r"\d+", text) else float(text)
        except ValueError as exc:
            raise TaskMutationError(f"Invalid ordinal: {value}. Must be a non-negative number.") from exc
    else:
        raise TaskMutationError(f"Invalid ordinal: {value}. Must be a non-negative number.")
    if not isfinite(float(number)) or number < 0:
        raise TaskMutationError(f"Invalid ordinal: {value}. Must be a non-negative number.")
    return number


def _normalize_dependency_id(task_id: str, task_prefix: str = "task") -> str:
    candidate = task_id.strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", candidate):
        candidate = f"{task_prefix.upper()}-{candidate}"
    return _normalize_task_id(candidate, task_prefix)


def _normalize_parent_task_id(
    parent_task_id: str | None,
    tasks: Sequence[TaskRecord],
    task_prefix: str = "task",
) -> str | None:
    if parent_task_id is None:
        return None
    normalized = _normalize_dependency_id(parent_task_id, task_prefix)
    for task in tasks:
        if _task_ids_equal(task.id, normalized, task_prefix):
            return normalized
    raise TaskMutationError(f"Parent task not found: {normalized}")


def _task_ids_equal(left: str, right: str, task_prefix: str = "task") -> bool:
    try:
        return _normalize_dependency_id(left, task_prefix).casefold() == _normalize_dependency_id(
            right, task_prefix
        ).casefold()
    except TaskMutationError:
        return left.strip().casefold() == right.strip().casefold()


def _task_exists(tasks: Iterable[TaskRecord], task_id: str) -> bool:
    normalized_id = task_id.casefold()
    return any(task.id.casefold() == normalized_id for task in tasks)


def _reject_circular_dependencies(
    task_id: str,
    dependencies: Sequence[str],
    tasks: Sequence[TaskRecord],
) -> None:
    graph: dict[str, list[str]] = {}
    for task in tasks:
        raw_dependencies = task.parsed.frontmatter.get("dependencies") or []
        if isinstance(raw_dependencies, list):
            graph[task.id.upper()] = [str(dependency).upper() for dependency in raw_dependencies]
        else:
            graph[task.id.upper()] = []
    graph[task_id.upper()] = [dependency.upper() for dependency in dependencies]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(current: str) -> bool:
        if current in visiting:
            return True
        if current in visited:
            return False
        visiting.add(current)
        for dependency in graph.get(current, []):
            if visit(dependency):
                return True
        visiting.remove(current)
        visited.add(current)
        return False

    if visit(task_id.upper()):
        raise TaskMutationError(f"Circular dependency detected for {task_id}")


def _reject_missing_dependencies(dependencies: Sequence[str], tasks: Sequence[TaskRecord]) -> None:
    existing_ids = {task.id.upper() for task in tasks}
    for dependency in dependencies:
        if dependency.upper() not in existing_ids:
            raise TaskMutationError(f"Dependency not found: {dependency}")


def _reject_unknown_status(status: str, statuses: Sequence[str] | None) -> None:
    if statuses is not None and status not in statuses:
        raise TaskMutationError(f"Unknown status: {status}")


def _normalize_on_status_change_create(value: str | bool | None) -> str | None:
    normalized = _normalize_on_status_change_value(value)
    if normalized is _NO_STATUS_CALLBACK_UPDATE:
        return None
    return normalized


def _normalize_on_status_change_update(value: str | bool | None) -> str | None | object:
    if value is None:
        return _NO_STATUS_CALLBACK_UPDATE
    return _normalize_on_status_change_value(value)


def _normalize_on_status_change_value(value: str | bool | None) -> str | None | object:
    if value is None:
        return _NO_STATUS_CALLBACK_UPDATE
    if isinstance(value, bool):
        if value:
            raise TaskMutationError("onStatusChange must be a command string, not true")
        return None
    normalized = str(value).strip()
    if normalized.casefold() in {"", "false", "0", "no", "disabled", "(disabled)"}:
        return None
    return normalized


def _task_status_callback_command(task: TaskRecord, config: BacklogConfig) -> str | None:
    raw = task.parsed.frontmatter.get("onStatusChange")
    if raw is None:
        raw = task.parsed.frontmatter.get("on_status_change")
    if raw is not None:
        normalized = _normalize_on_status_change_value(raw if isinstance(raw, bool) else str(raw))
        if normalized is _NO_STATUS_CALLBACK_UPDATE:
            return None
        return normalized
    return config.on_status_change


def _run_status_change_callback(
    project: BacklogProject,
    task: TaskRecord,
    old_status: str,
    new_status: str,
) -> None:
    command = _task_status_callback_command(task, load_config(project.config_path))
    if not command:
        return
    try:
        execute_status_callback(
            command=command,
            task_id=task.id,
            old_status=old_status,
            new_status=new_status,
            task_title=task.title,
            cwd=project.root,
        )
    except Exception:
        # Upstream treats status callbacks as best-effort automation.
        return


def _is_done_status(status: str) -> bool:
    normalized_status = status.strip().casefold()
    return "done" in normalized_status or "complete" in normalized_status


def _slug_title(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", title.strip()).strip("-")
    return slug or "Task"


def _heading_for_section(name: str) -> str:
    headings = {
        "DESCRIPTION": "## Description",
        "PLAN": "## Implementation Plan",
        "IMPLEMENTATION_NOTES": "## Implementation Notes",
        "FINAL_SUMMARY": "## Final Summary",
    }
    return headings.get(name, f"## {name.title().replace('_', ' ')}")


def _id_from_filename(path: Path) -> str:
    stem = path.stem
    if " - " in stem:
        return stem.split(" - ", 1)[0].upper()
    return stem.upper()


def _search_text(task: TaskRecord) -> str:
    return "\n".join([task.id, task.title, task.status, task.raw_source])


def _statuses_from_tasks(tasks: Iterable[TaskRecord]) -> list[str]:
    statuses: list[str] = []
    for task in tasks:
        if task.status not in statuses:
            statuses.append(task.status)
    return statuses


def _task_sort_key(task_id: str) -> tuple[str, tuple[tuple[int, int | str], ...]]:
    prefix, _, suffix = task_id.partition("-")
    return prefix, tuple(_sort_segment(segment) for segment in suffix.replace(".", "-").split("-"))


def _task_record_sort_key(task: TaskRecord) -> tuple[int, float, tuple[str, tuple[tuple[int, int | str], ...]], str]:
    ordinal = _task_ordinal(task)
    if ordinal is None:
        return 1, 0.0, _task_sort_key(task.id), task.title
    return 0, float(ordinal), _task_sort_key(task.id), task.title


def _task_ordinal(task: TaskRecord) -> int | float | None:
    try:
        return normalize_ordinal_value(task.parsed.frontmatter.get("ordinal"))
    except TaskMutationError:
        return None


def _sort_segment(segment: str) -> tuple[int, int | str]:
    if segment.isdigit():
        return 0, int(segment)
    return 1, segment
