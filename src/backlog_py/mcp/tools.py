from __future__ import annotations

from collections import Counter
from typing import Any, Callable, TypeVar

from backlog_py.core.documents import DocumentRecord, DocumentService
from backlog_py.core.drafts import DraftService
from backlog_py.core.milestones import MilestoneRecord, MilestoneService
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskRecord
from backlog_py.orchestration import (
    OrchestrationIdempotencyConflict,
    OrchestrationMutationResult,
    OrchestrationQueueItem,
    OrchestrationService,
    OrchestrationStateUpdate,
    RunHistoryParseError,
    TaskSplitItem,
    ValidationIssue,
    parse_run_history,
)
from backlog_py.orchestration.models import OrchestrationError
from backlog_py.runtime.locks import list_runtime_locks, with_project_write_lock
from backlog_py.storage.config import get_definition_of_done_defaults, load_config, replace_definition_of_done_defaults

T = TypeVar("T")


class McpArgumentError(TypeError):
    """Raised when an MCP tool argument is missing, malformed, or of the wrong type.

    Only this exception is treated as a client error (-32602). A plain TypeError
    escaping a handler is a server bug and must stay a -32603, so tool helpers
    must never signal bad caller input with a bare TypeError. It subclasses
    TypeError so direct Python callers that already catch TypeError from these
    helpers keep working.
    """


def project_status(project: BacklogProject, recent_limit: int = 5, recentLimit: int | None = None) -> dict[str, Any]:
    """Return read-only project coordination status for multi-agent overlap checks."""
    limit = _int_argument(recentLimit if recentLimit is not None else recent_limit, "recentLimit", default=5)
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    active_tasks = repository.list_tasks()
    completed_tasks = repository.list_completed_tasks()
    tasks = [*active_tasks, *completed_tasks]
    by_status = Counter(task.status for task in tasks)
    project_root = project.root.resolve()
    return {
        "projectRoot": str(project_root),
        "backlogDir": str(project.backlog_dir.resolve()),
        "configPath": str(project.config_path.resolve()),
        "taskCounts": {
            "active": len(active_tasks),
            "completed": len(completed_tasks),
            "total": len(tasks),
            "byStatus": dict(sorted(by_status.items())),
        },
        "recentActivity": _recent_activity(project, tasks, limit=max(limit, 0)),
        "locks": [
            lock
            for lock in list_runtime_locks()
            if lock.get("kind") == "project" and lock.get("project_root") == str(project_root)
        ],
    }


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
    limit_value = _int_argument(limit, "limit", default=10)
    if limit_value <= 0:
        return []
    file_filters = modified_files if modified_files is not None else modifiedFiles
    if not query.strip() and not _string_list(file_filters):
        return []
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    tasks = repository.search_tasks(
        query,
        status=status,
        priority=priority,
        modified_files=file_filters,
    )
    return [_task_summary(project, task) for task in tasks[:limit_value]]


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
    limit_value = _int_argument(limit, "limit", default=100)
    if limit_value <= 0:
        return []
    parent_filter = parent_task_id if parent_task_id is not None else parentTaskId
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
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
    return [_task_summary(project, task) for task in tasks[:limit_value]]


def task_board(project: BacklogProject) -> dict[str, list[dict[str, Any]]]:
    """Return the task board grouped by configured project statuses."""
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    return {
        status: [_task_summary(project, task) for task in tasks]
        for status, tasks in repository.board().items()
    }


def task_view(project: BacklogProject, task_id: str) -> dict[str, Any]:
    """Return one task through the read-only repository as a JSON-safe mapping."""
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    return _task_detail(project, repository.get_task(task_id))


def task_create(project: BacklogProject, **kwargs: Any) -> dict[str, Any]:
    """Create a task through the safe mutation repository."""
    def mutate() -> dict[str, Any]:
        task_id = _get_alias(kwargs, "task_id", "id")
        fresh_project = _fresh_project(project)
        title = _required_mcp_string(_get_alias(kwargs, "title"), "title")
        description = str(kwargs.get("description") or "")
        plan = str(_get_alias(kwargs, "implementationPlan", "implementation_plan", "plan") or "")
        notes = str(kwargs.get("notes") or "")
        final_summary = str(_get_alias(kwargs, "finalSummary", "final_summary") or "")
        acceptance_criteria = _optional_string_list(_get_alias(kwargs, "acceptanceCriteria", "acceptance_criteria"))
        definition_of_done = _optional_string_list(_get_alias(kwargs, "definitionOfDone", "definition_of_done"))
        definition_of_done_add = _optional_string_list(
            _get_alias(kwargs, "definitionOfDoneAdd", "definition_of_done_add")
        )
        disable_definition_of_done_defaults = (
            _coerce_bool(_get_alias(kwargs, "disableDefinitionOfDoneDefaults", "disable_definition_of_done_defaults"))
            or False
        )
        dependencies = _optional_string_list(_get_alias(kwargs, "dependencies"))
        assignees = _optional_string_list(_get_alias(kwargs, "assignee", "assignees"))
        labels = _optional_string_list(_get_alias(kwargs, "labels"))
        priority = _optional_string(_get_alias(kwargs, "priority"))
        milestone = _optional_string(_get_alias(kwargs, "milestone"))
        ordinal = _get_alias(kwargs, "ordinal")
        parent_task_id = _optional_string(_get_alias(kwargs, "parentTaskId", "parent_task_id", "parent"))
        references = _optional_string_list(_get_alias(kwargs, "references"))
        documentation = _optional_string_list(_get_alias(kwargs, "documentation"))
        modified_files = _optional_string_list(_get_alias(kwargs, "modifiedFiles", "modified_files"))
        if _coerce_bool(_get_alias(kwargs, "draft")):
            task = DraftService(fresh_project).create_draft(
                title=title,
                draft_id=None if task_id is None else str(task_id),
                description=description,
                plan=plan,
                notes=notes,
                final_summary=final_summary,
                acceptance_criteria=acceptance_criteria,
                definition_of_done=definition_of_done,
                definition_of_done_add=definition_of_done_add,
                disable_definition_of_done_defaults=disable_definition_of_done_defaults,
                dependencies=dependencies,
                assignees=assignees,
                labels=labels,
                priority=priority,
                milestone=milestone,
                ordinal=ordinal,
                parent_task_id=parent_task_id,
                references=references,
                documentation=documentation,
                modified_files=modified_files,
            )
        else:
            repository = MutableRepository(fresh_project)
            task = repository.create_task(
                title=title,
                task_id=None if task_id is None else str(task_id),
                status=_optional_string(_get_alias(kwargs, "status")),
                description=description,
                plan=plan,
                notes=notes,
                final_summary=final_summary,
                acceptance_criteria=acceptance_criteria,
                definition_of_done=definition_of_done,
                definition_of_done_add=definition_of_done_add,
                disable_definition_of_done_defaults=disable_definition_of_done_defaults,
                dependencies=dependencies,
                assignees=assignees,
                labels=labels,
                priority=priority,
                milestone=milestone,
                ordinal=ordinal,
                parent_task_id=parent_task_id,
                references=references,
                documentation=documentation,
                modified_files=modified_files,
                on_status_change=_optional_status_callback(_get_alias(kwargs, "onStatusChange", "on_status_change")),
            )
        return _task_detail(project, task)

    return _locked(project, "mcp_task_create", mutate)


def task_edit(project: BacklogProject, task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Edit supported task sections through the safe mutation repository."""
    def mutate() -> dict[str, Any]:
        # Reload config like task_create: the long-lived daemon holds one
        # BacklogProject per request, and status validation must see the
        # statuses that are on disk now.
        repository = MutableRepository(_fresh_project(project))
        priority = _optional_string(_get_alias(kwargs, "priority"))
        clear_priority = ("priority" in kwargs and kwargs.get("priority") is None) or (
            _coerce_bool(_get_alias(kwargs, "clearPriority", "clear_priority")) or False
        )
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
            check_ac=_int_list(_get_alias(kwargs, "checkAc", "check_ac"), "checkAc"),
            check_dod=_int_list(_get_alias(kwargs, "checkDod", "check_dod"), "checkDod"),
            uncheck_ac=_int_list(_get_alias(kwargs, "uncheckAc", "uncheck_ac"), "uncheckAc"),
            uncheck_dod=_int_list(_get_alias(kwargs, "uncheckDod", "uncheck_dod"), "uncheckDod"),
            remove_ac=_int_list(
                _get_alias(kwargs, "acceptanceCriteriaRemove", "removeAc", "remove_ac"),
                "acceptanceCriteriaRemove",
            ),
            remove_dod=_int_list(
                _get_alias(kwargs, "definitionOfDoneRemove", "removeDod", "remove_dod"),
                "definitionOfDoneRemove",
            ),
            dependencies=_string_list(kwargs.get("dependencies")) if "dependencies" in kwargs else None,
            assignees=_optional_string_list(_get_alias(kwargs, "assignee", "assignees")),
            labels=_optional_string_list(_get_alias(kwargs, "labels")),
            priority=priority,
            clear_priority=clear_priority,
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


def orchestration_record_run(
    project: BacklogProject,
    task_id: str | None = None,
    taskId: str | None = None,
    actor: str | None = None,
    result: str | None = None,
    summary: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Append a run-history event through the orchestration service."""
    task_identifier = task_id if task_id is not None else taskId
    task_identifier_value = _required_mcp_string(task_identifier, "task_id")
    result_value = _required_mcp_string(result, "result")
    actor_value = _optional_mcp_string(actor, "actor")
    summary_value = _optional_mcp_string(summary, "summary") or ""

    idempotency_key = _optional_string(_get_alias(kwargs, "idempotencyKey", "idempotency_key"))
    expected_version = _optional_int_value(_get_alias(kwargs, "expectedVersion", "expected_version"), "expectedVersion")
    state_update = _orchestration_state_update_from_mapping(_get_alias(kwargs, "stateUpdate", "state_update"))
    return _orchestration_mutation(
        project,
        task_identifier_value,
        lambda: OrchestrationService(project).record_run(
            task_identifier_value,
            actor=actor_value,
            result=result_value,
            summary=summary_value,
            files=_string_list(kwargs.get("files")),
            verification=_string_list(kwargs.get("verification")),
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            state_update=state_update,
        ),
    )


def orchestration_status(
    project: BacklogProject,
    include_completed: bool = False,
    includeCompleted: bool | None = None,
) -> dict[str, Any]:
    """Return orchestration queue status counts and items."""
    include = includeCompleted if includeCompleted is not None else include_completed
    report = OrchestrationService(project).queue(include_completed=include)
    return _orchestration_queue_report_payload(report)


def orchestration_queue(
    project: BacklogProject,
    include_completed: bool = False,
    includeCompleted: bool | None = None,
) -> dict[str, Any]:
    """Return orchestration queue items."""
    include = includeCompleted if includeCompleted is not None else include_completed
    report = OrchestrationService(project).queue(include_completed=include)
    return _orchestration_queue_report_payload(report)


def orchestration_eligible(project: BacklogProject) -> dict[str, Any]:
    """Return claimable orchestration queue items."""
    return _orchestration_items_payload(project, "eligible")


def orchestration_claims(project: BacklogProject) -> dict[str, Any]:
    """Return actively claimed orchestration queue items."""
    return _orchestration_items_payload(project, "claimed")


def orchestration_stale_leases(project: BacklogProject) -> dict[str, Any]:
    """Return stale orchestration lease queue items."""
    return _orchestration_items_payload(project, "stale_claim")


def orchestration_claim_task(
    project: BacklogProject,
    task_id: str | None = None,
    taskId: str | None = None,
    actor: str | None = None,
    expectedVersion: int | None = None,
    expected_version: int | None = None,
    idempotencyKey: str | None = None,
    idempotency_key: str | None = None,
    leaseTtlSeconds: int | None = None,
    lease_ttl_seconds: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Claim a task through the orchestration service."""
    task_identifier = _required_mcp_string(task_id if task_id is not None else taskId, "task_id")
    actor_value = _required_mcp_string(actor, "actor")
    expected = _required_int_value(
        expectedVersion if expectedVersion is not None else expected_version,
        "expectedVersion",
    )
    ttl = _optional_int_value(leaseTtlSeconds if leaseTtlSeconds is not None else lease_ttl_seconds, "leaseTtlSeconds")
    return _orchestration_mutation(
        project,
        task_identifier,
        lambda: OrchestrationService(project).claim_task(
            task_identifier,
            actor=actor_value,
            expected_version=expected,
            idempotency_key=idempotencyKey if idempotencyKey is not None else idempotency_key,
            lease_ttl_seconds=ttl,
            reason=_optional_mcp_string(reason, "reason"),
        ),
    )


def orchestration_release_task(
    project: BacklogProject,
    task_id: str | None = None,
    taskId: str | None = None,
    actor: str | None = None,
    expectedVersion: int | None = None,
    expected_version: int | None = None,
    idempotencyKey: str | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Release a task claim through the orchestration service."""
    task_identifier = _required_mcp_string(task_id if task_id is not None else taskId, "task_id")
    actor_value = _required_mcp_string(actor, "actor")
    expected = _required_int_value(
        expectedVersion if expectedVersion is not None else expected_version,
        "expectedVersion",
    )
    return _orchestration_mutation(
        project,
        task_identifier,
        lambda: OrchestrationService(project).release_task(
            task_identifier,
            actor=actor_value,
            expected_version=expected,
            idempotency_key=idempotencyKey if idempotencyKey is not None else idempotency_key,
            reason=_optional_mcp_string(reason, "reason"),
        ),
    )


def orchestration_transition_task(
    project: BacklogProject,
    task_id: str | None = None,
    taskId: str | None = None,
    toStatus: str | None = None,
    to_status: str | None = None,
    actor: str | None = None,
    expectedVersion: int | None = None,
    expected_version: int | None = None,
    idempotencyKey: str | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Transition a task through the orchestration service."""
    task_identifier = _required_mcp_string(task_id if task_id is not None else taskId, "task_id")
    to_status_value = _required_mcp_string(toStatus if toStatus is not None else to_status, "toStatus")
    actor_value = _required_mcp_string(actor, "actor")
    expected = _required_int_value(
        expectedVersion if expectedVersion is not None else expected_version,
        "expectedVersion",
    )
    return _orchestration_mutation(
        project,
        task_identifier,
        lambda: OrchestrationService(project).transition_task(
            task_identifier,
            to_status_value,
            actor=actor_value,
            expected_version=expected,
            idempotency_key=idempotencyKey if idempotencyKey is not None else idempotency_key,
            reason=_optional_mcp_string(reason, "reason"),
        ),
    )


def orchestration_split_task(
    project: BacklogProject,
    task_id: str | None = None,
    taskId: str | None = None,
    mode: str | None = None,
    items: Any = None,
    actor: str | None = None,
    expectedVersion: int | None = None,
    expected_version: int | None = None,
    idempotencyKey: str | None = None,
    idempotency_key: str | None = None,
    inheritDependencies: bool | None = None,
    inherit_dependencies: bool | None = None,
    linkSequence: bool | None = None,
    link_sequence: bool | None = None,
    transitionToStatus: str | None = None,
    transition_to_status: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Split a task into child or continuation tasks through the orchestration service."""
    task_identifier = _required_mcp_string(task_id if task_id is not None else taskId, "task_id")
    mode_value = _required_mcp_string(mode, "mode")
    actor_value = _required_mcp_string(actor, "actor")
    expected = _required_int_value(
        expectedVersion if expectedVersion is not None else expected_version,
        "expectedVersion",
    )
    inherit = _optional_bool(inheritDependencies if inheritDependencies is not None else inherit_dependencies)
    sequence = _optional_bool(linkSequence if linkSequence is not None else link_sequence)
    split_items = _task_split_items(items)
    return _orchestration_mutation(
        project,
        task_identifier,
        lambda: OrchestrationService(project).split_task(
            task_identifier,
            mode=mode_value,
            actor=actor_value,
            expected_version=expected,
            idempotency_key=idempotencyKey if idempotencyKey is not None else idempotency_key,
            items=split_items,
            inherit_dependencies=True if inherit is None else inherit,
            link_sequence=True if sequence is None else sequence,
            transition_to_status=_optional_mcp_string(
                transitionToStatus if transitionToStatus is not None else transition_to_status,
                "transitionToStatus",
            ),
            reason=_optional_mcp_string(reason, "reason"),
        ),
    )


def document_list(project: BacklogProject, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List or search documents through the safe document service."""
    limit_value = _int_argument(limit, "limit", default=100)
    if limit_value <= 0:
        return []
    service = DocumentService(project)
    documents = service.list_documents() if query is None else service.search_documents(query)
    return [_document_detail(project, document) for document in documents[:limit_value]]


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
                _required_mcp_string(kwargs.get("path"), "path"),
                title=_required_mcp_string(kwargs.get("title"), "title"),
                content=str(kwargs.get("content") or ""),
                metadata=_dict_value(kwargs.get("metadata"), "metadata"),
            ),
        ),
    )


def document_update(project: BacklogProject, path_or_id: str, **kwargs: Any) -> dict[str, Any]:
    """Update a document's title, body, docs-relative directory, and metadata."""
    metadata = _document_update_metadata(
        _dict_value(kwargs.get("metadata"), "metadata"),
        _optional_mcp_string(_get_alias(kwargs, "type"), "type"),
        _get_alias(kwargs, "tags"),
    )
    directory = _optional_mcp_string(_get_alias(kwargs, "directory", "path"), "directory")
    return _locked(
        project,
        "mcp_document_update",
        lambda: _document_detail(
            project,
            DocumentService(project).update_document(
                path_or_id,
                title=_optional_string(kwargs.get("title")),
                content=_optional_string(kwargs.get("content")),
                directory=directory,
                metadata=metadata,
            ),
        ),
    )


def _document_update_metadata(
    metadata: dict[str, Any] | None,
    document_type: str | None,
    tags: Any,
) -> dict[str, Any] | None:
    """Merge explicit metadata with the CLI's --type/--tags shorthand.

    The service deletes a frontmatter key whose value is None, so an explicit
    {"key": null} still removes it. Shorthand fields mirror the CLI: they are
    applied only when they carry a value.
    """
    merged = dict(metadata or {})
    if document_type is not None:
        merged["type"] = document_type
    tag_values = _csv_string_list(tags)
    if tag_values is not None:
        merged["tags"] = tag_values
    return merged or None


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


def _orchestration_mutation(
    project: BacklogProject,
    task_id: str,
    mutate: Callable[[], Any],
) -> dict[str, Any]:
    """Run one orchestration mutation and classify its failures consistently.

    Every orchestration tool classifies the same failures the same way: an
    idempotency clash or any other OrchestrationError (including
    OrchestrationValidationError) becomes a success-with-conflict payload, and
    an unparsable run history becomes a validation issue on the payload.
    """
    try:
        mutation = mutate()
    except OrchestrationIdempotencyConflict as exc:
        return _orchestration_record_run_response(
            project,
            task_id,
            conflict=_orchestration_idempotency_conflict_payload(exc),
        )
    except RunHistoryParseError as exc:
        return _orchestration_record_run_response(
            project,
            task_id,
            validation_issue=ValidationIssue(
                code=exc.code,
                message=exc.message,
                path=exc.location or "run_history",
            ),
        )
    except OrchestrationError as exc:
        return _orchestration_record_run_response(
            project,
            task_id,
            conflict=_orchestration_error_conflict_payload(exc),
        )
    return _orchestration_record_run_response(project, task_id, result=mutation)


def _orchestration_record_run_response(
    project: BacklogProject,
    task_id: str,
    *,
    result: OrchestrationMutationResult | None = None,
    conflict: dict[str, Any] | None = None,
    validation_issue: ValidationIssue | None = None,
) -> dict[str, Any]:
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    task = repository.get_task(task_id)
    history = parse_run_history(task.raw_source)
    queue_item = _orchestration_queue_item(project, task.id)
    issues = list(queue_item.validation_issues if queue_item is not None else [])
    if validation_issue is not None:
        _append_unique_validation_issue(issues, validation_issue)
    elif queue_item is None:
        issues.extend(
            ValidationIssue(
                code=issue.code,
                message=issue.message,
                path=issue.location or "run_history",
            )
            for issue in history.issues
        )

    payload: dict[str, Any] = {
        "taskId": task.id,
        "path": queue_item.path if queue_item is not None else _relative_task_path(project, task),
        "version": queue_item.version if queue_item is not None else (result.version if result is not None else 0),
        "eventId": result.event.event_id if result is not None else None,
        "runHistoryEventIds": [event.event_id for event in history.events],
        "queueCategory": queue_item.category if queue_item is not None else None,
        "validationIssues": _validation_issue_payloads(issues),
    }
    if conflict is not None:
        payload["conflict"] = conflict
    created_task_ids = getattr(result, "created_task_ids", None)
    if created_task_ids is not None and result is not None:
        payload["createdTaskIds"] = list(created_task_ids)
        payload["parentEventId"] = getattr(result, "parent_event_id", result.event.event_id)
        payload["splitMode"] = result.event.split_mode
    return payload


def _orchestration_queue_item(project: BacklogProject, task_id: str) -> OrchestrationQueueItem | None:
    report = OrchestrationService(project).queue(include_completed=True)
    normalized = task_id.casefold()
    for item in report.items:
        if item.task_id.casefold() == normalized:
            return item
    return None


def _orchestration_queue_report_payload(report: Any) -> dict[str, Any]:
    return {
        "byCategory": dict(report.by_category),
        "items": [_orchestration_queue_item_payload(item) for item in report.items],
    }


def _orchestration_items_payload(project: BacklogProject, category: str) -> dict[str, Any]:
    report = OrchestrationService(project).queue(include_completed=True)
    return {
        "items": [_orchestration_queue_item_payload(item) for item in report.items if item.category == category],
    }


def _orchestration_queue_item_payload(item: OrchestrationQueueItem) -> dict[str, Any]:
    return {
        "taskId": item.task_id,
        "path": item.path,
        "title": item.title,
        "version": item.version,
        "effectiveStatus": item.effective_status,
        "queueCategory": item.category,
        "validationIssues": _validation_issue_payloads(item.validation_issues),
        "dependencyIds": list(item.dependency_ids),
        "leaseOwner": item.lease_owner,
        "leaseExpiresAt": item.lease_expires_at,
    }


def _orchestration_state_update_from_mapping(value: Any) -> OrchestrationStateUpdate | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise McpArgumentError("stateUpdate must be an object")
    fields = {
        "status_key": _optional_string(_get_alias(value, "statusKey", "status_key")),
        "lease_owner": _optional_string(_get_alias(value, "leaseOwner", "lease_owner")),
        "lease_expires_at": _optional_string(_get_alias(value, "leaseExpiresAt", "lease_expires_at")),
        "correlation_id": _optional_string(_get_alias(value, "correlationId", "correlation_id")),
        "review_state": _optional_string(_get_alias(value, "reviewState", "review_state")),
        "reviewer": _optional_string(_get_alias(value, "reviewer")),
        "review_attempts": _optional_int_value(_get_alias(value, "reviewAttempts", "review_attempts"), "reviewAttempts"),
        "review_max_attempts": _optional_int_value(
            _get_alias(value, "reviewMaxAttempts", "review_max_attempts"),
            "reviewMaxAttempts",
        ),
    }
    if all(field is None for field in fields.values()):
        return None
    return OrchestrationStateUpdate(**fields)


def _task_split_items(value: Any) -> tuple[TaskSplitItem, ...]:
    if value is None:
        raise McpArgumentError("items must be an array")
    if not isinstance(value, list):
        raise McpArgumentError("items must be an array")
    items: list[TaskSplitItem] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            items.append(TaskSplitItem(title=item))
            continue
        if not isinstance(item, dict):
            raise McpArgumentError(f"items[{index}] must be a string or object")
        title = _required_mcp_string(_get_alias(item, "title"), f"items[{index}].title")
        items.append(
            TaskSplitItem(
                title=title,
                description=_optional_mcp_string(_get_alias(item, "description"), f"items[{index}].description") or "",
                plan=_optional_mcp_string(
                    _get_alias(item, "plan", "implementationPlan", "implementation_plan"),
                    f"items[{index}].plan",
                )
                or "",
            )
        )
    return tuple(items)


def _orchestration_error_conflict_payload(error: OrchestrationError) -> dict[str, Any]:
    return {
        "type": error.__class__.__name__,
        "message": str(error),
        "details": dict(error.details),
    }


def _orchestration_idempotency_conflict_payload(error: OrchestrationIdempotencyConflict) -> dict[str, Any]:
    return {
        "type": error.__class__.__name__,
        "message": str(error),
        "details": {"idempotencyKey": error.idempotency_key},
    }


def _validation_issue_payloads(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [
        {
            "code": issue.code,
            "message": issue.message,
            "path": issue.path,
            "severity": issue.severity,
        }
        for issue in issues
    ]


def _append_unique_validation_issue(issues: list[ValidationIssue], issue: ValidationIssue) -> None:
    key = (issue.code, issue.message, issue.path, issue.severity)
    if all((existing.code, existing.message, existing.path, existing.severity) != key for existing in issues):
        issues.append(issue)


def _locked(project: BacklogProject, operation: str, fn: Callable[[], T]) -> T:
    return with_project_write_lock(project, operation, fn)


def _recent_activity(project: BacklogProject, tasks: list[TaskRecord], *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    activity = [_activity_summary(project, task) for task in tasks]
    activity.sort(
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return activity[:limit]


def _activity_summary(project: BacklogProject, task: TaskRecord) -> dict[str, Any]:
    summary = _task_summary(project, task)
    timestamp, timestamp_field = _task_activity_timestamp(task)
    summary["timestamp"] = timestamp
    summary["timestampField"] = timestamp_field
    return summary


def _task_activity_timestamp(task: TaskRecord) -> tuple[str | None, str | None]:
    updated = task.parsed.frontmatter.get("updated_date")
    if updated is not None:
        return str(updated), "updated_date"
    created = task.parsed.frontmatter.get("created_date")
    if created is not None:
        return str(created), "created_date"
    return None, None


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
        "id": milestone.id,
        "name": milestone.name,
        "title": milestone.title,
        "due_date": milestone.due_date,
        "format": milestone.format,
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


def _required_mcp_string(value: Any, field: str) -> str:
    text = _optional_mcp_string(value, field)
    if text is None or not text.strip():
        raise McpArgumentError(f"{field} must be a non-empty string")
    return text


def _optional_mcp_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpArgumentError(f"{field} must be a string")
    return value


def _optional_bool(value: Any) -> bool | None:
    return _coerce_bool(value)


def _optional_status_callback(value: Any) -> str | bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        if value:
            raise McpArgumentError("onStatusChange must be a command string, not true")
        return value
    if isinstance(value, str):
        return value.strip()
    raise McpArgumentError("onStatusChange must be a command string")


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
    raise McpArgumentError("Expected boolean value")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise McpArgumentError("Expected a string or an array of strings")
    return [str(item) for item in value]


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _string_list(value)


def _csv_string_list(value: Any) -> list[str] | None:
    """Split a string, comma-separated string, or array into trimmed values."""
    if value is None:
        return None
    values = [item.strip() for entry in _string_list(value) for item in entry.split(",")]
    normalized = [item for item in values if item]
    return normalized or None


def _int_list(value: Any, field: str = "value") -> list[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return [_required_int_value(value, field)]
    return [_required_int_value(item, field) for item in value]


def _int_argument(value: Any, field: str, *, default: int) -> int:
    """Coerce an optional numeric tool argument, falling back to its default."""
    parsed = _optional_int_value(value, field)
    return default if parsed is None else parsed


def _optional_int_value(value: Any, field: str = "value") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise McpArgumentError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise McpArgumentError(f"{field} must be an integer") from exc


def _required_int_value(value: Any, field: str) -> int:
    parsed = _optional_int_value(value, field)
    if parsed is None:
        raise McpArgumentError(f"{field} must be an integer")
    return parsed


def _dict_value(value: Any, field: str = "value") -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise McpArgumentError(f"{field} must be an object")
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
