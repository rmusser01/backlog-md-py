from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backlog_py.core.models import BacklogProject
from backlog_py.mcp import tools as tool_registry
from backlog_py.mcp.resources import list_resource_uris, read_resource
from backlog_py.storage.project import discover_project


@dataclass(frozen=True)
class ResourceDefinition:
    """SDK-free MCP resource metadata."""

    uri: str
    name: str
    description: str
    mime_type: str = "text/markdown"


@dataclass(frozen=True)
class ToolDefinition:
    """SDK-free MCP tool metadata and Python handler."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]


RESOURCE_DEFINITIONS: tuple[ResourceDefinition, ...] = (
    ResourceDefinition(
        uri="backlog://workflow/overview",
        name="Backlog.md Python workflow overview",
        description="Overview of the Python Backlog.md MCP workflow and available helpers.",
    ),
    ResourceDefinition(
        uri="backlog://docs/task-workflow",
        name="Backlog.md task workflow",
        description="Task lifecycle guidance used by Backlog.md agents.",
    ),
    ResourceDefinition(
        uri="backlog://init-required",
        name="Backlog.md initialization required",
        description="Setup guidance for MCP sessions launched outside a Backlog.md project.",
    ),
)


def _project_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema_properties = {
        "project": {
            "type": "string",
            "description": "Backlog.md project root. Optional when the MCP server can discover the project.",
        }
    }
    if properties:
        schema_properties.update(properties)
    return {
        "type": "object",
        "properties": schema_properties,
        "required": [field for field in required if field != "project"],
        "additionalProperties": True,
    }


def _string_or_string_array_schema(description: str) -> dict[str, Any]:
    return {
        "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        "description": description,
    }


def _integer_or_integer_array_schema(description: str) -> dict[str, Any]:
    return {
        "oneOf": [{"type": "integer"}, {"type": "array", "items": {"type": "integer"}}],
        "description": description,
    }


def _metadata_schema(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description, "additionalProperties": True}


def _orchestration_state_update_schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "statusKey": {"type": "string"},
            "status_key": {"type": "string", "description": "Alias for statusKey."},
            "leaseOwner": {"type": "string"},
            "lease_owner": {"type": "string", "description": "Alias for leaseOwner."},
            "leaseExpiresAt": {"type": "string"},
            "lease_expires_at": {"type": "string", "description": "Alias for leaseExpiresAt."},
            "correlationId": {"type": "string"},
            "correlation_id": {"type": "string", "description": "Alias for correlationId."},
            "reviewState": {"type": "string"},
            "review_state": {"type": "string", "description": "Alias for reviewState."},
            "reviewer": {"type": "string"},
            "reviewAttempts": {"type": "integer"},
            "review_attempts": {"type": "integer", "description": "Alias for reviewAttempts."},
            "reviewMaxAttempts": {"type": "integer"},
            "review_max_attempts": {"type": "integer", "description": "Alias for reviewMaxAttempts."},
        },
        "additionalProperties": True,
    }


def _orchestration_record_run_schema() -> dict[str, Any]:
    schema = _project_schema(
        {
            "task_id": {"type": "string", "description": "Task ID to record against."},
            "taskId": {"type": "string", "description": "Alias for task_id."},
            "actor": {"type": "string", "description": "Agent or user recording this run."},
            "result": {"type": "string", "description": "Run result, such as succeeded or failed."},
            "summary": {"type": "string", "description": "Short run summary."},
            "files": _string_or_string_array_schema("Project-relative files changed by the run."),
            "verification": _string_or_string_array_schema("Verification commands or checks executed by the run."),
            "idempotencyKey": {"type": "string", "description": "Client-supplied idempotency key."},
            "idempotency_key": {"type": "string", "description": "Alias for idempotencyKey."},
            "expectedVersion": {"type": "integer", "description": "Expected orchestration state version."},
            "expected_version": {"type": "integer", "description": "Alias for expectedVersion."},
            "stateUpdate": _orchestration_state_update_schema("Optional orchestration state update."),
            "state_update": _orchestration_state_update_schema("Alias for stateUpdate."),
        },
        required=("project", "result"),
    )
    expected_version_requirement = {
        "anyOf": [
            {"required": ["expectedVersion"]},
            {"required": ["expected_version"]},
        ]
    }
    schema["anyOf"] = [
        {"required": ["task_id"]},
        {"required": ["taskId"]},
    ]
    schema["allOf"] = [
        {"anyOf": [{"not": {"required": ["stateUpdate"]}}, expected_version_requirement]},
        {"anyOf": [{"not": {"required": ["state_update"]}}, expected_version_requirement]},
    ]
    return schema


def _orchestration_report_schema() -> dict[str, Any]:
    return _project_schema(
        {
            "includeCompleted": {"type": "boolean", "description": "Include completed tasks."},
            "include_completed": {"type": "boolean", "description": "Alias for includeCompleted."},
        }
    )


def _orchestration_workflow_mutation_schema(*, transition: bool = False, claim: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "task_id": {"type": "string", "description": "Task ID to mutate."},
        "taskId": {"type": "string", "description": "Alias for task_id."},
        "actor": {"type": "string", "description": "Agent or user performing the mutation."},
        "expectedVersion": {"type": "integer", "description": "Expected orchestration state version."},
        "expected_version": {"type": "integer", "description": "Alias for expectedVersion."},
        "idempotencyKey": {"type": "string", "description": "Client-supplied idempotency key."},
        "idempotency_key": {"type": "string", "description": "Alias for idempotencyKey."},
        "reason": {"type": "string", "description": "Reason stored in run history."},
    }
    if transition:
        properties.update(
            {
                "toStatus": {"type": "string", "description": "Target orchestration status."},
                "to_status": {"type": "string", "description": "Alias for toStatus."},
            }
        )
    if claim:
        properties.update(
            {
                "leaseTtlSeconds": {"type": "integer", "description": "Lease TTL in seconds."},
                "lease_ttl_seconds": {"type": "integer", "description": "Alias for leaseTtlSeconds."},
            }
        )
    schema = _project_schema(properties, required=("project", "actor"))
    schema["anyOf"] = [{"required": ["task_id"]}, {"required": ["taskId"]}]
    schema["allOf"] = [
        {"anyOf": [{"required": ["expectedVersion"]}, {"required": ["expected_version"]}]},
    ]
    if transition:
        schema["allOf"].append({"anyOf": [{"required": ["toStatus"]}, {"required": ["to_status"]}]})
    return schema


def _orchestration_split_schema() -> dict[str, Any]:
    schema = _project_schema(
        {
            "task_id": {"type": "string", "description": "Task ID to split."},
            "taskId": {"type": "string", "description": "Alias for task_id."},
            "mode": {"type": "string", "enum": ["child", "continuation"], "description": "Split mode."},
            "items": {
                "type": "array",
                "description": "Generated task definitions.",
                "items": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "plan": {"type": "string"},
                                "implementationPlan": {"type": "string"},
                                "implementation_plan": {"type": "string"},
                            },
                            "required": ["title"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "actor": {"type": "string", "description": "Agent or user performing the split."},
            "expectedVersion": {"type": "integer", "description": "Expected orchestration state version."},
            "expected_version": {"type": "integer", "description": "Alias for expectedVersion."},
            "idempotencyKey": {"type": "string", "description": "Client-supplied idempotency key."},
            "idempotency_key": {"type": "string", "description": "Alias for idempotencyKey."},
            "inheritDependencies": {"type": "boolean", "description": "Copy parent dependencies to generated tasks."},
            "inherit_dependencies": {"type": "boolean", "description": "Alias for inheritDependencies."},
            "linkSequence": {"type": "boolean", "description": "Link continuation tasks in dependency order."},
            "link_sequence": {"type": "boolean", "description": "Alias for linkSequence."},
            "transitionToStatus": {"type": "string", "description": "Optional parent status after split."},
            "transition_to_status": {"type": "string", "description": "Alias for transitionToStatus."},
            "reason": {"type": "string", "description": "Reason stored in run history."},
        },
        required=("project", "mode", "items", "actor"),
    )
    schema["anyOf"] = [{"required": ["task_id"]}, {"required": ["taskId"]}]
    schema["allOf"] = [
        {"anyOf": [{"required": ["expectedVersion"]}, {"required": ["expected_version"]}]},
    ]
    return schema


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "project_status",
        "Return read-only project coordination status for overlap checks.",
        _project_schema(
            {
                "recentLimit": {"type": "integer"},
                "recent_limit": {"type": "integer", "description": "Alias for recentLimit."},
            }
        ),
        tool_registry.project_status,
    ),
    ToolDefinition("task_board", "Return tasks grouped by board status.", _project_schema(), tool_registry.task_board),
    ToolDefinition(
        "task_list",
        "List tasks in a Backlog.md project.",
        _project_schema(
            {
                "status": {"type": "string", "description": "Filter by task status."},
                "limit": {"type": "integer", "description": "Maximum number of tasks to return."},
                "assignee": _string_or_string_array_schema("Filter by assignee."),
                "labels": _string_or_string_array_schema("Filter by label."),
                "priority": {"type": "string", "description": "Filter by task priority."},
                "milestone": {"type": "string", "description": "Filter by milestone."},
                "parentTaskId": {"type": "string", "description": "Filter by parent task ID."},
                "parent_task_id": {"type": "string", "description": "Alias for parentTaskId."},
                "search": {"type": "string", "description": "Search query to apply while listing tasks."},
            }
        ),
        tool_registry.task_list,
    ),
    ToolDefinition(
        "task_search",
        "Search tasks in a Backlog.md project.",
        _project_schema(
            {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Maximum number of results to return."},
                "status": {"type": "string", "description": "Filter by task status."},
                "priority": {"type": "string", "description": "Filter by task priority."},
                "modified_files": _string_or_string_array_schema("Filter by modified file path."),
                "modifiedFiles": _string_or_string_array_schema("Filter by modified file path."),
            }
        ),
        tool_registry.task_search,
    ),
    ToolDefinition(
        "task_view",
        "Return one task from a Backlog.md project.",
        _project_schema({"task_id": {"type": "string"}}, required=("project", "task_id")),
        tool_registry.task_view,
    ),
    ToolDefinition(
        "task_create",
        "Create a task in a Backlog.md project.",
        _project_schema(
            {
                "title": {"type": "string"},
                "id": {"type": "string", "description": "Explicit task ID to create."},
                "task_id": {"type": "string", "description": "Alias for id."},
                "draft": {"type": "boolean", "description": "Create the task as a draft."},
                "status": {"type": "string", "description": "Initial task status."},
                "description": {"type": "string", "description": "Initial task description section."},
                "notes": {"type": "string", "description": "Initial implementation notes section."},
                "parentTaskId": {"type": "string", "description": "Parent task ID for child task creation."},
                "parent_task_id": {"type": "string", "description": "Alias for parentTaskId."},
                "parent": {"type": "string", "description": "Alias for parentTaskId."},
                "milestone": {"type": "string", "description": "Initial task milestone."},
                "ordinal": {"type": "integer", "description": "Task ordering ordinal."},
                "acceptanceCriteria": _string_or_string_array_schema("Acceptance criteria to store on the task."),
                "acceptance_criteria": _string_or_string_array_schema("Alias for acceptanceCriteria."),
                "definitionOfDone": _string_or_string_array_schema("Definition of Done items to store on the task."),
                "definition_of_done": _string_or_string_array_schema("Alias for definitionOfDone."),
                "definitionOfDoneAdd": _string_or_string_array_schema("Definition of Done items to append."),
                "definition_of_done_add": _string_or_string_array_schema("Alias for definitionOfDoneAdd."),
                "disableDefinitionOfDoneDefaults": {
                    "type": "boolean",
                    "description": "Disable project-level Definition of Done defaults for this task.",
                },
                "disable_definition_of_done_defaults": {
                    "type": "boolean",
                    "description": "Alias for disableDefinitionOfDoneDefaults.",
                },
                "dependencies": _string_or_string_array_schema("Task dependencies to store."),
                "assignee": _string_or_string_array_schema("Task assignee values to store."),
                "assignees": _string_or_string_array_schema("Alias for assignee."),
                "labels": _string_or_string_array_schema("Task labels to store."),
                "priority": {"type": "string", "description": "Initial task priority."},
                "references": _string_or_string_array_schema("References to store on the task."),
                "documentation": _string_or_string_array_schema("Documentation links to store on the task."),
                "modifiedFiles": _string_or_string_array_schema("Modified file paths to store on the task."),
                "modified_files": _string_or_string_array_schema("Alias for modifiedFiles."),
                "implementationPlan": {"type": "string", "description": "Initial implementation plan section."},
                "implementation_plan": {"type": "string", "description": "Alias for implementationPlan."},
                "plan": {"type": "string", "description": "Alias for implementationPlan."},
                "finalSummary": {"type": "string", "description": "Initial final summary section."},
                "final_summary": {"type": "string", "description": "Alias for finalSummary."},
                "onStatusChange": {
                    "oneOf": [{"type": "string"}, {"type": "boolean"}],
                    "description": "Status-change hook command, or false to disable it.",
                },
                "on_status_change": {
                    "oneOf": [{"type": "string"}, {"type": "boolean"}],
                    "description": "Alias for onStatusChange.",
                },
            },
            required=("project", "title"),
        ),
        tool_registry.task_create,
    ),
    ToolDefinition(
        "task_edit",
        "Edit supported task fields in a Backlog.md project.",
        _project_schema(
            {
                "task_id": {"type": "string"},
                "title": {"type": "string", "description": "Replacement task title."},
                "description": {"type": "string", "description": "Replacement task description section."},
                "implementationPlan": {"type": "string", "description": "Replacement implementation plan section."},
                "implementation_plan": {"type": "string", "description": "Alias for implementationPlan."},
                "planSet": {"type": "string", "description": "Alias for implementationPlan."},
                "plan": {"type": "string", "description": "Alias for implementationPlan."},
                "planAppend": _string_or_string_array_schema("Implementation plan lines to append."),
                "append_plan": _string_or_string_array_schema("Alias for planAppend."),
                "planClear": {"type": "boolean", "description": "Clear the implementation plan section."},
                "clear_plan": {"type": "boolean", "description": "Alias for planClear."},
                "notes": {"type": "string", "description": "Replacement implementation notes section."},
                "appendNotes": {"type": "string", "description": "Implementation notes text to append."},
                "append_notes": {"type": "string", "description": "Alias for appendNotes."},
                "acceptanceCriteria": _string_or_string_array_schema(
                    "Acceptance criteria to add; alias for acceptanceCriteriaAdd."
                ),
                "acceptance_criteria": _string_or_string_array_schema("Alias for acceptanceCriteria."),
                "acceptanceCriteriaAdd": _string_or_string_array_schema("Acceptance criteria to append."),
                "acceptance_criteria_add": _string_or_string_array_schema("Alias for acceptanceCriteriaAdd."),
                "acceptanceCriteriaSet": _string_or_string_array_schema(
                    "Acceptance criteria to replace the section with."
                ),
                "acceptance_criteria_set": _string_or_string_array_schema("Alias for acceptanceCriteriaSet."),
                "definitionOfDoneAdd": _string_or_string_array_schema("Definition of Done items to append."),
                "definition_of_done_add": _string_or_string_array_schema("Alias for definitionOfDoneAdd."),
                "finalSummary": {"type": "string", "description": "Replacement final summary section."},
                "final_summary": {"type": "string", "description": "Alias for finalSummary."},
                "finalSummaryAppend": _string_or_string_array_schema("Final summary lines to append."),
                "append_final_summary": _string_or_string_array_schema("Alias for finalSummaryAppend."),
                "finalSummaryClear": {"type": "boolean", "description": "Clear the final summary section."},
                "clear_final_summary": {"type": "boolean", "description": "Alias for finalSummaryClear."},
                "checkAc": _integer_or_integer_array_schema("Acceptance criteria indexes to mark complete."),
                "check_ac": _integer_or_integer_array_schema("Alias for checkAc."),
                "checkDod": _integer_or_integer_array_schema("Definition of Done indexes to mark complete."),
                "check_dod": _integer_or_integer_array_schema("Alias for checkDod."),
                "uncheckAc": _integer_or_integer_array_schema("Acceptance criteria indexes to mark incomplete."),
                "uncheck_ac": _integer_or_integer_array_schema("Alias for uncheckAc."),
                "uncheckDod": _integer_or_integer_array_schema("Definition of Done indexes to mark incomplete."),
                "uncheck_dod": _integer_or_integer_array_schema("Alias for uncheckDod."),
                "acceptanceCriteriaRemove": _integer_or_integer_array_schema("Acceptance criteria indexes to remove."),
                "removeAc": _integer_or_integer_array_schema("Alias for acceptanceCriteriaRemove."),
                "remove_ac": _integer_or_integer_array_schema("Alias for acceptanceCriteriaRemove."),
                "definitionOfDoneRemove": _integer_or_integer_array_schema("Definition of Done indexes to remove."),
                "removeDod": _integer_or_integer_array_schema("Alias for definitionOfDoneRemove."),
                "remove_dod": _integer_or_integer_array_schema("Alias for definitionOfDoneRemove."),
                "dependencies": _string_or_string_array_schema("Task dependencies to replace."),
                "assignee": _string_or_string_array_schema("Task assignee values to replace."),
                "assignees": _string_or_string_array_schema("Alias for assignee."),
                "labels": _string_or_string_array_schema("Task labels to replace."),
                "priority": {"type": "string", "description": "Task priority."},
                "clearPriority": {"type": "boolean", "description": "Clear priority frontmatter."},
                "clear_priority": {"type": "boolean", "description": "Alias for clearPriority."},
                "clearMilestone": {"type": "boolean", "description": "Clear milestone frontmatter."},
                "clear_milestone": {"type": "boolean", "description": "Alias for clearMilestone."},
                "ordinal": {"type": "integer", "description": "Task ordering ordinal."},
                "milestone": {"type": "string", "description": "Task milestone."},
                "references": _string_or_string_array_schema("References to replace on the task."),
                "addReferences": _string_or_string_array_schema("References to append to the task."),
                "add_references": _string_or_string_array_schema("Alias for addReferences."),
                "removeReferences": _string_or_string_array_schema("References to remove from the task."),
                "remove_references": _string_or_string_array_schema("Alias for removeReferences."),
                "documentation": _string_or_string_array_schema("Documentation links to replace on the task."),
                "addDocumentation": _string_or_string_array_schema("Documentation links to append to the task."),
                "add_documentation": _string_or_string_array_schema("Alias for addDocumentation."),
                "removeDocumentation": _string_or_string_array_schema("Documentation links to remove from the task."),
                "remove_documentation": _string_or_string_array_schema("Alias for removeDocumentation."),
                "modifiedFiles": _string_or_string_array_schema("Modified file paths to replace on the task."),
                "modified_files": _string_or_string_array_schema("Alias for modifiedFiles."),
                "status": {"type": "string", "description": "Task status."},
                "onStatusChange": {
                    "oneOf": [{"type": "string"}, {"type": "boolean"}],
                    "description": "Status-change hook command, or false to disable it.",
                },
                "on_status_change": {
                    "oneOf": [{"type": "string"}, {"type": "boolean"}],
                    "description": "Alias for onStatusChange.",
                },
            },
            required=("project", "task_id"),
        ),
        tool_registry.task_edit,
    ),
    ToolDefinition(
        "task_archive",
        "Move one active task to the project archive.",
        _project_schema({"task_id": {"type": "string"}}, required=("project", "task_id")),
        tool_registry.task_archive,
    ),
    ToolDefinition(
        "task_complete",
        "Move one Done task to completed storage.",
        _project_schema({"task_id": {"type": "string"}}, required=("project", "task_id")),
        tool_registry.task_complete,
    ),
    ToolDefinition(
        "orchestration_record_run",
        "Append a task run-history event and optionally update orchestration state.",
        _orchestration_record_run_schema(),
        tool_registry.orchestration_record_run,
    ),
    ToolDefinition(
        "orchestration_status",
        "Return orchestration queue status counts and items.",
        _orchestration_report_schema(),
        tool_registry.orchestration_status,
    ),
    ToolDefinition(
        "orchestration_queue",
        "Return orchestration queue items.",
        _orchestration_report_schema(),
        tool_registry.orchestration_queue,
    ),
    ToolDefinition(
        "orchestration_eligible",
        "Return claimable orchestration queue items.",
        _project_schema(),
        tool_registry.orchestration_eligible,
    ),
    ToolDefinition(
        "orchestration_claims",
        "Return active orchestration claims.",
        _project_schema(),
        tool_registry.orchestration_claims,
    ),
    ToolDefinition(
        "orchestration_stale_leases",
        "Return stale orchestration leases.",
        _project_schema(),
        tool_registry.orchestration_stale_leases,
    ),
    ToolDefinition(
        "orchestration_claim_task",
        "Claim a task for orchestration work.",
        _orchestration_workflow_mutation_schema(claim=True),
        tool_registry.orchestration_claim_task,
    ),
    ToolDefinition(
        "orchestration_release_task",
        "Release a task orchestration claim.",
        _orchestration_workflow_mutation_schema(),
        tool_registry.orchestration_release_task,
    ),
    ToolDefinition(
        "orchestration_transition_task",
        "Transition task orchestration status.",
        _orchestration_workflow_mutation_schema(transition=True),
        tool_registry.orchestration_transition_task,
    ),
    ToolDefinition(
        "orchestration_split_task",
        "Split a task into child or continuation tasks.",
        _orchestration_split_schema(),
        tool_registry.orchestration_split_task,
    ),
    ToolDefinition(
        "document_list",
        "List or search documents.",
        _project_schema(
            {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Maximum number of documents to return."},
            }
        ),
        tool_registry.document_list,
    ),
    ToolDefinition(
        "document_view",
        "Return one document by path or id.",
        _project_schema({"path_or_id": {"type": "string"}}, required=("project", "path_or_id")),
        tool_registry.document_view,
    ),
    ToolDefinition(
        "document_create",
        "Create a project document.",
        _project_schema(
            {
                "path": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "metadata": _metadata_schema("Frontmatter metadata to store on the document."),
            },
            required=("project", "path", "title", "content"),
        ),
        tool_registry.document_create,
    ),
    ToolDefinition(
        "document_update",
        "Update a project document.",
        _project_schema(
            {
                "path_or_id": {"type": "string"},
                "title": {"type": "string", "description": "Replacement document title."},
                "content": {"type": "string", "description": "Replacement document content."},
            },
            required=("project", "path_or_id"),
        ),
        tool_registry.document_update,
    ),
    ToolDefinition("milestone_list", "List project milestones.", _project_schema(), tool_registry.milestone_list),
    ToolDefinition(
        "milestone_add",
        "Create a milestone.",
        _project_schema(
            {
                "name": {"type": "string"},
                "description": {"type": "string", "description": "Initial milestone description."},
            },
            required=("project", "name"),
        ),
        tool_registry.milestone_add,
    ),
    ToolDefinition(
        "milestone_rename",
        "Rename a milestone.",
        _project_schema(
            {
                "old_name": {"type": "string"},
                "new_name": {"type": "string"},
                "update_tasks": {"type": "boolean", "description": "Update matching task milestone references."},
            },
            required=("project", "old_name", "new_name"),
        ),
        tool_registry.milestone_rename,
    ),
    ToolDefinition(
        "milestone_remove",
        "Remove a milestone.",
        _project_schema(
            {
                "name": {"type": "string"},
                "clear_tasks": {"type": "boolean", "description": "Clear matching task milestone references."},
            },
            required=("project", "name"),
        ),
        tool_registry.milestone_remove,
    ),
    ToolDefinition(
        "milestone_archive",
        "Archive a milestone.",
        _project_schema({"name": {"type": "string"}}, required=("project", "name")),
        tool_registry.milestone_archive,
    ),
    ToolDefinition(
        "definition_of_done_defaults_get",
        "Return project-level Definition of Done defaults.",
        _project_schema(),
        tool_registry.definition_of_done_defaults_get,
    ),
    ToolDefinition(
        "definition_of_done_defaults_upsert",
        "Replace project-level Definition of Done defaults.",
        _project_schema({"items": {"type": "array", "items": {"type": "string"}}}, required=("project", "items")),
        tool_registry.definition_of_done_defaults_upsert,
    ),
)


def list_resources() -> list[dict[str, str]]:
    """Return MCP resource metadata."""
    by_uri = {resource.uri: resource for resource in RESOURCE_DEFINITIONS}
    return [
        {
            "uri": uri,
            "name": by_uri[uri].name,
            "description": by_uri[uri].description,
            "mimeType": by_uri[uri].mime_type,
        }
        for uri in list_resource_uris()
    ]


def read_resource_content(uri: str) -> dict[str, str]:
    """Return one MCP resource content item."""
    by_uri = {resource.uri: resource for resource in RESOURCE_DEFINITIONS}
    definition = by_uri.get(uri)
    if definition is None:
        raise KeyError(f"Unsupported Backlog MCP resource: {uri}")
    return {
        "uri": uri,
        "mimeType": definition.mime_type,
        "text": read_resource(uri),
    }


def list_tools() -> list[dict[str, Any]]:
    """Return MCP tool metadata."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in TOOL_DEFINITIONS
    ]


def tool_by_name(name: str) -> ToolDefinition:
    """Return a tool definition by MCP tool name."""
    for tool in TOOL_DEFINITIONS:
        if tool.name == name:
            return tool
    raise KeyError(f"Unknown Backlog MCP tool: {name}")


def project_from_argument(project: str) -> BacklogProject:
    """Discover a Backlog project from a tool argument."""
    return discover_project(Path.cwd(), explicit_cwd=Path(project))
