from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

from backlog_py.core.ids import format_numbered_id, ids_equivalent
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import (
    MutableRepository,
    ReadOnlyRepository,
    TaskMutationError,
    TaskRecord,
    _atomic_write_text,
    _current_task_timestamp,
    _definition_of_done_for_create,
    _load_task,
    _mutation_path,
    _new_task_source,
    _normalize_dependency_ids,
    _normalize_metadata_list,
    _normalize_optional_string,
    _normalize_parent_task_id,
    _reject_missing_dependencies,
    _replace_frontmatter_values,
    _slug_title,
    normalize_ordinal_value,
)
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.storage.config import load_config


_DRAFT_ID_RE = re.compile(r"^draft-(\d+)$", re.IGNORECASE)


class DraftService:
    """Create and read Backlog.md draft task files."""

    def __init__(self, project: BacklogProject) -> None:
        self.project = project
        self.drafts_dir = project.backlog_dir / "drafts"

    def create_draft(
        self,
        *,
        title: str,
        draft_id: str | None = None,
        description: str = "",
        plan: str = "",
        notes: str = "",
        final_summary: str = "",
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
        parent_task_id: str | None = None,
        references: Sequence[str] | None = None,
        documentation: Sequence[str] | None = None,
        modified_files: Sequence[str] | None = None,
    ) -> TaskRecord:
        normalized_id = _normalize_draft_id(draft_id) if draft_id is not None else self._next_draft_id()
        if self._draft_exists(normalized_id):
            raise TaskMutationError(f"Draft id already exists: {normalized_id}")
        tasks = ReadOnlyRepository(self.project).list_tasks()
        current_config = load_config(self.project.config_path)
        normalized_parent_task_id = _normalize_parent_task_id(parent_task_id, tasks, current_config.task_prefix)
        normalized_dependencies = _normalize_dependency_ids(dependencies, current_config.task_prefix)
        _reject_missing_dependencies(normalized_dependencies, tasks)
        task_definition_of_done = _definition_of_done_for_create(
            explicit=definition_of_done,
            additions=definition_of_done_add,
            defaults=current_config.definition_of_done,
            disable_defaults=disable_definition_of_done_defaults,
        )
        target = self._draft_path(normalized_id, title)
        if target.exists():
            raise TaskMutationError(f"Draft path already exists: {target.name}")
        content = _new_task_source(
            task_id=normalized_id,
            title=title,
            status="Draft",
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
            on_status_change=None,
            created_date=_current_task_timestamp(current_config.include_datetime_in_dates),
        )
        parse_task_markdown(content)
        _atomic_write_text(target, content)
        return _load_task(target)

    def list_drafts(self) -> list[TaskRecord]:
        if not self.drafts_dir.is_dir():
            return []
        drafts = [self._load_draft(path) for path in sorted(self.drafts_dir.glob("*.md"))]
        return sorted(drafts, key=_draft_sort_key)

    def view_draft(self, draft_id: str) -> TaskRecord:
        normalized_id = _normalize_draft_id(draft_id)
        for draft in self.list_drafts():
            if ids_equivalent(draft.id, normalized_id):
                return draft
        raise KeyError(f"Draft not found: {draft_id}")

    def promote_draft(self, draft_id: str) -> TaskRecord:
        draft = self.view_draft(draft_id)
        repository = MutableRepository(self.project)
        task_id = repository._next_task_id()
        target = repository._task_path(task_id, draft.title)
        if target.exists():
            raise TaskMutationError(f"Task path already exists: {target.name}")
        current_config = load_config(self.project.config_path)
        source = _replace_frontmatter_values(
            draft.raw_source,
            draft.parsed,
            {"id": task_id, "status": current_config.default_status},
        )
        parse_task_markdown(source)
        _atomic_write_text(target, source)
        _mutation_path(self.drafts_dir, draft.path).unlink()
        return _load_task(target)

    def demote_task(self, task_id: str) -> TaskRecord:
        task = ReadOnlyRepository(self.project).get_task(task_id)
        draft_id = self._next_draft_id()
        target = self._draft_path(draft_id, task.title)
        if target.exists():
            raise TaskMutationError(f"Draft path already exists: {target.name}")
        source = _replace_frontmatter_values(
            task.raw_source,
            task.parsed,
            {"id": draft_id, "status": "Draft"},
        )
        parse_task_markdown(source)
        _atomic_write_text(target, source)
        _mutation_path(task.path.parent, task.path).unlink()
        return _load_task(target)

    def archive_draft(self, draft_id: str) -> TaskRecord:
        draft = self.view_draft(draft_id)
        safe_current_path = _mutation_path(self.drafts_dir, draft.path)
        archive_root = _mutation_path(self.project.backlog_dir, self.project.backlog_dir / "archive")
        archive_dir = _mutation_path(archive_root, archive_root / "drafts")
        archive_dir.mkdir(parents=True, exist_ok=True)
        target_path = _mutation_path(archive_dir, archive_dir / draft.path.name)
        if target_path.exists():
            raise TaskMutationError(f"Archived draft path already exists: {target_path.name}")
        os.replace(safe_current_path, target_path)
        return _load_task(target_path)

    def _next_draft_id(self) -> str:
        max_id = 0
        for draft in self.list_drafts():
            match = _DRAFT_ID_RE.fullmatch(draft.id)
            if match is not None:
                max_id = max(max_id, int(match.group(1)))
        return format_numbered_id("draft-", max_id + 1, self.project.config.zero_padded_ids)

    def _draft_exists(self, draft_id: str) -> bool:
        return any(ids_equivalent(draft.id, draft_id) for draft in self.list_drafts())

    def _draft_path(self, draft_id: str, title: str) -> Path:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        path = self.drafts_dir / f"{draft_id} - {_slug_title(title)}.md"
        return _mutation_path(self.drafts_dir, path)

    def _load_draft(self, path: Path) -> TaskRecord:
        return _load_task(_mutation_path(self.drafts_dir, path))


def _normalize_draft_id(draft_id: str) -> str:
    candidate = draft_id.strip()
    if candidate.isdigit():
        candidate = f"draft-{candidate}"
    match = _DRAFT_ID_RE.fullmatch(candidate)
    if match is None:
        raise TaskMutationError(f"Invalid draft id: {draft_id}")
    return f"draft-{int(match.group(1))}"


def _draft_sort_key(draft: TaskRecord) -> tuple[int, str]:
    match = _DRAFT_ID_RE.fullmatch(draft.id)
    if match is None:
        return (10**9, draft.id.casefold())
    return (int(match.group(1)), draft.id.casefold())
