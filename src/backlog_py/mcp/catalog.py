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


def _string_array_schema(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "project_status",
        "Return read-only project coordination status for overlap checks.",
        _project_schema({"recentLimit": {"type": "integer"}}),
        tool_registry.project_status,
    ),
    ToolDefinition("task_board", "Return tasks grouped by board status.", _project_schema(), tool_registry.task_board),
    ToolDefinition("task_list", "List tasks in a Backlog.md project.", _project_schema(), tool_registry.task_list),
    ToolDefinition(
        "task_search",
        "Search tasks in a Backlog.md project.",
        _project_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}),
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
                "status": {"type": "string", "description": "Initial task status."},
                "parentTaskId": {"type": "string", "description": "Parent task ID for child task creation."},
                "milestone": {"type": "string", "description": "Initial task milestone."},
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
                "acceptanceCriteria": _string_array_schema(
                    "Acceptance criteria to add; alias for acceptanceCriteriaAdd."
                ),
                "acceptanceCriteriaAdd": _string_array_schema("Acceptance criteria to append."),
                "acceptanceCriteriaSet": _string_array_schema("Acceptance criteria to replace the section with."),
                "clearPriority": {"type": "boolean", "description": "Clear priority frontmatter."},
                "clearMilestone": {"type": "boolean", "description": "Clear milestone frontmatter."},
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
    ToolDefinition("document_list", "List or search documents.", _project_schema(), tool_registry.document_list),
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
            },
            required=("project", "path", "title", "content"),
        ),
        tool_registry.document_create,
    ),
    ToolDefinition(
        "document_update",
        "Update a project document.",
        _project_schema({"path_or_id": {"type": "string"}}, required=("project", "path_or_id")),
        tool_registry.document_update,
    ),
    ToolDefinition("milestone_list", "List project milestones.", _project_schema(), tool_registry.milestone_list),
    ToolDefinition(
        "milestone_add",
        "Create a milestone.",
        _project_schema({"name": {"type": "string"}}, required=("project", "name")),
        tool_registry.milestone_add,
    ),
    ToolDefinition(
        "milestone_rename",
        "Rename a milestone.",
        _project_schema(
            {"old_name": {"type": "string"}, "new_name": {"type": "string"}},
            required=("project", "old_name", "new_name"),
        ),
        tool_registry.milestone_rename,
    ),
    ToolDefinition(
        "milestone_remove",
        "Remove a milestone.",
        _project_schema({"name": {"type": "string"}}, required=("project", "name")),
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
