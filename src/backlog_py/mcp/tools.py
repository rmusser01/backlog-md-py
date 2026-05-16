from __future__ import annotations

from typing import Any, Callable, TypeVar

from backlog_py.core.models import BacklogProject
from backlog_py.core.documents import DocumentRecord, DocumentService
from backlog_py.core.milestones import MilestoneRecord, MilestoneService
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskRecord
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.storage.config import get_definition_of_done_defaults, load_config, replace_definition_of_done_defaults

T = TypeVar("T")


def task_search(
    project: BacklogProject,
    query: str = "",
    limit: int = 10,
    *,
    status: str | None = None,
    priority: str | None = None,
    modified_files: str | list[str] | None = None,
    modifiedFiles: str | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search tasks through the read-only repository and return JSON-safe rows."""
    if limit <= 0:
        return []
    file_filters = modified_files if modified_files is not None else modifiedFiles
    if not query.strip() and not _string_list(file_filters):
        return []
    repository = ReadOnlyRepository(project)
    tasks = repository.search_tasks(
        query,
        status=status,
        priority=priority,
        modified_files=file_filters,
    )
    return [_task_summary(project, task) for task in tasks[:limit]]


def task_list(
    project: BacklogProject,
    status: str | None = None,
    limit: int = 100,
    *,
    assignee: str | list[str] | None = None,
    labels: str | list[str] | None = None,
    priority: str | None = None,
    milestone: str | None = None,
    search: str | None = None,
    parent_task_id: str | None = None,
    parentTaskId: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks through the read-only repository and return JSON-safe rows."""
    if limit <= 0:
        return []
    parent_filter = parent_task_id if parent_task_id is not None else parentTaskId
    repository = ReadOnlyRepository(project)
    if search is None:
        tasks = repository.list_tasks(
            status=status,
            assignee=assignee,
            labels=labels,
            priority=priority,
            milestone=milestone,
            parent_task_id=parent_filter,
        )
    else:
        tasks = repository.search_tasks(
            search,
            status=status,
            assignee=assignee,
            labels=labels,
            priority=priority,
            milestone=milestone,
            parent_task_id=parent_filter,
        )
    return [_task_summary(project, task) for task in tasks[:limit]]


def task_board(project: BacklogProject) -> dict[str, list[dict[str, Any]]]:
    """Return the task board grouped by configured project statuses."""
    repository = ReadOnlyRepository(project)
    return {
        status: [_task_summary(project, task) for task in tasks]
        for status, tasks in repository.board().items()
    }


def task_view(project: BacklogProject, task_id: str) -> dict[str, Any]:
    """Return one task through the read-only repository as a JSON-safe mapping."""
    repository = ReadOnlyRepository(project)
    return _task_detail(project, repository.get_task(task_id))


def task_create(project: BacklogProject, **kwargs: Any) -> dict[str, Any]:
    """Create a task through the safe mutation repository."""
    def mutate() -> dict[str, Any]:
        task_id = _get_alias(kwargs, "task_id", "id")
        repository = MutableRepository(_fresh_project(project))
        task = repository.create_task(
            title=str(kwargs.get("title") or ""),
            task_id=None if task_id is None else str(task_id),
            status=_optional_string(_get_alias(kwargs, "status")),
            description=str(kwargs.get("description") or ""),
            plan=str(_get_alias(kwargs, "implementationPlan", "implementation_plan", "plan") or ""),
            notes=str(kwargs.get("notes") or ""),
            final_summary=str(_get_alias(kwargs, "finalSummary", "final_summary") or ""),
            acceptance_criteria=_optional_string_list(_get_alias(kwargs, "acceptanceCriteria", "acceptance_criteria")),
            definition_of_done=_optional_string_list(_get_alias(kwargs, "definitionOfDone", "definition_of_done")),
            definition_of_done_add=_optional_string_list(
                _get_alias(kwargs, "definitionOfDoneAdd", "definition_of_done_add")
            ),
            disable_definition_of_done_defaults=_coerce_bool(
                _get_alias(kwargs, "disableDefinitionOfDoneDefaults", "disable_definition_of_done_defaults")
            )
            or False,
            dependencies=_optional_string_list(_get_alias(kwargs, "dependencies")),
            assignees=_optional_string_list(_get_alias(kwargs, "assignee", "assignees")),
            labels=_optional_string_list(_get_alias(kwargs, "labels")),
            priority=_optional_string(_get_alias(kwargs, "priority")),
            milestone=_optional_string(_get_alias(kwargs, "milestone")),
            ordinal=_get_alias(kwargs, "ordinal"),
            parent_task_id=_optional_string(_get_alias(kwargs, "parentTaskId", "parent_task_id", "parent")),
            references=_optional_string_list(_get_alias(kwargs, "references")),
            documentation=_optional_string_list(_get_alias(kwargs, "documentation")),
            modified_files=_optional_string_list(_get_alias(kwargs, "modifiedFiles", "modified_files")),
            on_status_change=_optional_status_callback(_get_alias(kwargs, "onStatusChange", "on_status_change")),
        )
        return _task_detail(project, task)

    return _locked(project, "mcp_task_create", mutate)


def task_edit(project: BacklogProject, task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Edit supported task sections through the safe mutation repository."""
    def mutate() -> dict[str, Any]:
        repository = MutableRepository(project)
        task = repository.edit_task(
            task_id,
            title=_optional_string(kwargs.get("title")),
            description=_optional_string(kwargs.get("description")),
            plan=_optional_string(_get_alias(kwargs, "planSet", "implementationPlan", "implementation_plan", "plan")),
            append_plan=_optional_string_list(_get_alias(kwargs, "planAppend", "append_plan")),
            clear_plan=_coerce_bool(_get_alias(kwargs, "planClear", "clear_plan")) or False,
            notes=_optional_string(kwargs.get("notes")),
            append_notes=_optional_string(_get_alias(kwargs, "appendNotes", "append_notes")),
            acceptance_criteria=_optional_string_list(
                _get_alias(kwargs, "acceptanceCriteriaSet", "acceptance_criteria_set")
            ),
            acceptance_criteria_add=_combined_optional_string_list(
                kwargs,
                "acceptanceCriteriaAdd",
                "acceptance_criteria_add",
                "acceptanceCriteria",
                "acceptance_criteria",
            ),
            definition_of_done_add=_optional_string_list(
                _get_alias(kwargs, "definitionOfDoneAdd", "definition_of_done_add")
            ),
            final_summary=_optional_string(_get_alias(kwargs, "finalSummary", "final_summary")),
            append_final_summary=_optional_string_list(_get_alias(kwargs, "finalSummaryAppend", "append_final_summary")),
            clear_final_summary=_coerce_bool(_get_alias(kwargs, "finalSummaryClear", "clear_final_summary")) or False,
            check_ac=_int_list(_get_alias(kwargs, "checkAc", "check_ac")),
            check_dod=_int_list(_get_alias(kwargs, "checkDod", "check_dod")),
            uncheck_ac=_int_list(_get_alias(kwargs, "uncheckAc", "uncheck_ac")),
            uncheck_dod=_int_list(_get_alias(kwargs, "uncheckDod", "uncheck_dod")),
            remove_ac=_int_list(_get_alias(kwargs, "acceptanceCriteriaRemove", "removeAc", "remove_ac")),
            remove_dod=_int_list(_get_alias(kwargs, "definitionOfDoneRemove", "removeDod", "remove_dod")),
            dependencies=_string_list(kwargs.get("dependencies")) if "dependencies" in kwargs else None,
            assignees=_optional_string_list(_get_alias(kwargs, "assignee", "assignees")),
            labels=_optional_string_list(_get_alias(kwargs, "labels")),
            priority=_optional_string(_get_alias(kwargs, "priority")),
            milestone=_optional_string(_get_alias(kwargs, "milestone")) if "milestone" in kwargs else None,
            ordinal=_get_alias(kwargs, "ordinal"),
            clear_milestone=("milestone" in kwargs and kwargs.get("milestone") is None)
            or (_coerce_bool(_get_alias(kwargs, "clearMilestone", "clear_milestone")) or False),
            references=_optional_string_list(_get_alias(kwargs, "references")),
            add_references=_optional_string_list(_get_alias(kwargs, "addReferences", "add_references")),
            remove_references=_optional_string_list(_get_alias(kwargs, "removeReferences", "remove_references")),
            documentation=_optional_string_list(_get_alias(kwargs, "documentation")),
            add_documentation=_optional_string_list(_get_alias(kwargs, "addDocumentation", "add_documentation")),
            remove_documentation=_optional_string_list(_get_alias(kwargs, "removeDocumentation", "remove_documentation")),
            modified_files=_optional_string_list(_get_alias(kwargs, "modifiedFiles", "modified_files")),
            status=_optional_string(kwargs.get("status")),
            on_status_change=_optional_status_callback(_get_alias(kwargs, "onStatusChange", "on_status_change")),
        )
        return _task_detail(project, task)

    return _locked(project, "mcp_task_edit", mutate)


def task_archive(project: BacklogProject, task_id: str) -> dict[str, Any]:
    """Move one active task to backlog/archive/tasks."""
    return _locked(
        project,
        "mcp_task_archive",
        lambda: _task_detail(project, MutableRepository(project).archive_task(task_id)),
    )


def task_complete(project: BacklogProject, task_id: str) -> dict[str, Any]:
    """Move one Done task to backlog/completed."""
    return _locked(
        project,
        "mcp_task_complete",
        lambda: _task_detail(project, MutableRepository(project).complete_task(task_id)),
    )


def document_list(project: BacklogProject, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List or search documents through the safe document service."""
    if limit <= 0:
        return []
    service = DocumentService(project)
    documents = service.list_documents() if query is None else service.search_documents(query)
    return [_document_detail(project, document) for document in documents[:limit]]


def document_view(project: BacklogProject, path_or_id: str) -> dict[str, Any]:
    """Return one document by docs-relative path or frontmatter id."""
    return _document_detail(project, DocumentService(project).view_document(path_or_id))


def document_create(project: BacklogProject, **kwargs: Any) -> dict[str, Any]:
    """Create a document under backlog/docs."""
    return _locked(
        project,
        "mcp_document_create",
        lambda: _document_detail(
            project,
            DocumentService(project).create_document(
                str(kwargs.get("path") or ""),
                title=str(kwargs.get("title") or ""),
                content=str(kwargs.get("content") or ""),
                metadata=_dict_value(kwargs.get("metadata")),
            ),
        ),
    )


def document_update(project: BacklogProject, path_or_id: str, **kwargs: Any) -> dict[str, Any]:
    """Update a document while preserving omitted metadata."""
    return _locked(
        project,
        "mcp_document_update",
        lambda: _document_detail(
            project,
            DocumentService(project).update_document(
                path_or_id,
                title=_optional_string(kwargs.get("title")),
                content=_optional_string(kwargs.get("content")),
            ),
        ),
    )


def milestone_list(project: BacklogProject) -> list[dict[str, Any]]:
    """List active milestone files."""
    return [_milestone_detail(project, milestone) for milestone in MilestoneService(project).list_milestones()]


def milestone_add(project: BacklogProject, name: str, description: str = "") -> dict[str, Any]:
    """Create a milestone file."""
    return _locked(
        project,
        "mcp_milestone_add",
        lambda: _milestone_detail(project, MilestoneService(project).add_milestone(name, description=description)),
    )


def milestone_rename(
    project: BacklogProject,
    old_name: str,
    new_name: str,
    update_tasks: bool = False,
) -> dict[str, Any]:
    """Rename a milestone file and optionally update task references."""
    return _locked(
        project,
        "mcp_milestone_rename",
        lambda: _milestone_detail(
            project,
            MilestoneService(project).rename_milestone(old_name, new_name, update_tasks=update_tasks),
        ),
    )


def milestone_remove(project: BacklogProject, name: str, clear_tasks: bool = False) -> dict[str, Any]:
    """Remove a milestone file and optionally clear task references."""
    return _locked(
        project,
        "mcp_milestone_remove",
        lambda: _milestone_detail(project, MilestoneService(project).remove_milestone(name, clear_tasks=clear_tasks)),
    )


def milestone_archive(project: BacklogProject, name: str) -> dict[str, Any]:
    """Archive a milestone file."""
    return _locked(
        project,
        "mcp_milestone_archive",
        lambda: _milestone_detail(project, MilestoneService(project).archive_milestone(name)),
    )


def definition_of_done_defaults_get(project: BacklogProject) -> dict[str, list[str]]:
    """Return project-level Definition of Done default checklist items."""
    return {"items": get_definition_of_done_defaults(project)}


def definition_of_done_defaults_upsert(project: BacklogProject, items: list[str]) -> dict[str, list[str]]:
    """Replace project-level Definition of Done default checklist items."""
    def mutate() -> dict[str, list[str]]:
        config = replace_definition_of_done_defaults(project, items)
        return {"items": list(config.definition_of_done or [])}

    return _locked(project, "mcp_definition_of_done_defaults_upsert", mutate)


def _locked(project: BacklogProject, operation: str, fn: Callable[[], T]) -> T:
    return with_project_write_lock(project, operation, fn)


def _task_summary(project: BacklogProject, task: TaskRecord) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "description": task.description,
        "path": _relative_task_path(project, task),
    }
    references = _frontmatter_string_list(task, "references")
    documentation = _frontmatter_string_list(task, "documentation")
    modified_files = _frontmatter_string_list(task, "modified_files")
    milestone = task.parsed.frontmatter.get("milestone")
    ordinal = task.parsed.frontmatter.get("ordinal")
    parent_task_id = task.parsed.frontmatter.get("parent_task_id")
    if milestone:
        summary["milestone"] = str(milestone)
    if ordinal is not None:
        summary["ordinal"] = ordinal
    if parent_task_id:
        summary["parentTaskId"] = str(parent_task_id)
    if references:
        summary["references"] = references
    if documentation:
        summary["documentation"] = documentation
    if modified_files:
        summary["modifiedFiles"] = modified_files
    return summary


def _task_detail(project: BacklogProject, task: TaskRecord) -> dict[str, Any]:
    detail = _task_summary(project, task)
    detail["raw_source"] = task.raw_source
    return detail


def _document_detail(project: BacklogProject, document: DocumentRecord) -> dict[str, Any]:
    return {
        "id": document.id,
        "title": document.title,
        "path": document.path_relative,
        "content": document.content,
        "frontmatter": dict(document.frontmatter),
        "raw_source": document.raw_source,
        "project_path": document.path.relative_to(project.root).as_posix(),
    }


def _milestone_detail(project: BacklogProject, milestone: MilestoneRecord) -> dict[str, Any]:
    return {
        "name": milestone.name,
        "path": milestone.path_relative,
        "content": milestone.content,
        "frontmatter": dict(milestone.frontmatter),
        "archived": milestone.archived,
        "project_path": milestone.path.relative_to(project.root).as_posix(),
    }


def _relative_task_path(project: BacklogProject, task: TaskRecord) -> str:
    return task.path.relative_to(project.root).as_posix()


def _frontmatter_string_list(task: TaskRecord, key: str) -> list[str]:
    value = task.parsed.frontmatter.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _fresh_project(project: BacklogProject) -> BacklogProject:
    return BacklogProject(
        root=project.root,
        backlog_dir=project.backlog_dir,
        config_path=project.config_path,
        config=load_config(project.config_path),
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    return _coerce_bool(value)


def _optional_status_callback(value: Any) -> str | bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        if value:
            raise TypeError("onStatusChange must be a command string, not true")
        return value
    if isinstance(value, str):
        return value.strip()
    raise TypeError("onStatusChange must be a command string")


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    raise TypeError("Expected boolean value")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _string_list(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    return [int(item) for item in value]


def _dict_value(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("Expected mapping")
    return dict(value)


def _get_alias(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _combined_optional_string_list(mapping: dict[str, Any], *names: str) -> list[str] | None:
    values: list[str] = []
    found = False
    for name in names:
        if name in mapping:
            found = True
            values.extend(_string_list(mapping[name]))
    return values if found else None
