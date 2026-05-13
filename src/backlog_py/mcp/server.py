from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from backlog_py.mcp.resources import read_resource
from backlog_py.mcp import tools as tool_registry
from backlog_py.storage.project import discover_project


def is_mcp_sdk_available() -> bool:
    """Return whether the optional MCP SDK can be imported in this environment."""
    return importlib.util.find_spec("mcp") is not None


def create_server(fastmcp_cls: Any | None = None) -> Any:
    """Create a FastMCP server registered with Backlog.py resources and tools."""
    server_cls = fastmcp_cls or _load_fastmcp()
    server = server_cls("backlog-md-py")
    _register_resources(server)
    _register_tools(server)
    return server


def main() -> None:
    """Start the MCP SDK stdio server."""
    create_server().run()


def _load_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP SDK is not installed. Install backlog-md-py[mcp] to run the MCP server."
        ) from exc
    return FastMCP


def _register_resources(server: Any) -> None:
    @server.resource("backlog://workflow/overview")
    def workflow_overview() -> str:
        """Return Backlog.py MCP workflow guidance."""
        return read_resource("backlog://workflow/overview")

    @server.resource("backlog://docs/task-workflow")
    def task_workflow() -> str:
        """Return the task workflow guidance alias."""
        return read_resource("backlog://docs/task-workflow")


def _register_tools(server: Any) -> None:
    @server.tool()
    def task_board(project: str) -> dict[str, list[dict[str, Any]]]:
        """Return tasks grouped by board status for a Backlog.md project."""
        return tool_registry.task_board(_project(project))

    @server.tool()
    def task_list(
        project: str,
        status: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List tasks in a Backlog.md project."""
        return tool_registry.task_list(
            _project(project),
            status=status,
            assignee=assignee,
            labels=labels,
            priority=priority,
            milestone=milestone,
            search=search,
            limit=limit,
        )

    @server.tool()
    def task_search(
        project: str,
        query: str = "",
        status: str | None = None,
        priority: str | None = None,
        modified_files: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search tasks in a Backlog.md project."""
        return tool_registry.task_search(
            _project(project),
            query=query,
            status=status,
            priority=priority,
            modified_files=modified_files,
            limit=limit,
        )

    @server.tool()
    def task_view(project: str, task_id: str) -> dict[str, Any]:
        """Return one task from a Backlog.md project."""
        return tool_registry.task_view(_project(project), task_id=task_id)

    @server.tool()
    def task_create(
        project: str,
        title: str,
        task_id: str | None = None,
        status: str | None = None,
        description: str = "",
        implementation_plan: str | None = None,
        notes: str = "",
        acceptance_criteria: list[str] | None = None,
        definition_of_done: list[str] | None = None,
        definition_of_done_add: list[str] | None = None,
        disable_definition_of_done_defaults: bool = False,
        dependencies: list[str] | None = None,
        assignee: list[str] | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        references: list[str] | None = None,
        documentation: list[str] | None = None,
        modified_files: list[str] | None = None,
        on_status_change: bool | None = None,
    ) -> dict[str, Any]:
        """Create a task in a Backlog.md project."""
        return tool_registry.task_create(
            _project(project),
            **_present(
                title=title,
                task_id=task_id,
                status=status,
                description=description,
                implementation_plan=implementation_plan,
                notes=notes,
                acceptance_criteria=acceptance_criteria,
                definition_of_done=definition_of_done,
                definition_of_done_add=definition_of_done_add,
                disable_definition_of_done_defaults=disable_definition_of_done_defaults,
                dependencies=dependencies,
                assignee=assignee,
                labels=labels,
                priority=priority,
                milestone=milestone,
                references=references,
                documentation=documentation,
                modifiedFiles=modified_files,
                on_status_change=on_status_change,
            ),
        )

    @server.tool()
    def task_edit(
        project: str,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        plan_set: str | None = None,
        plan_append: list[str] | None = None,
        plan_clear: bool = False,
        notes: str | None = None,
        append_notes: str | None = None,
        acceptance_criteria_add: list[str] | None = None,
        definition_of_done_add: list[str] | None = None,
        final_summary: str | None = None,
        append_final_summary: list[str] | None = None,
        clear_final_summary: bool = False,
        check_ac: list[int] | None = None,
        check_dod: list[int] | None = None,
        uncheck_ac: list[int] | None = None,
        uncheck_dod: list[int] | None = None,
        remove_ac: list[int] | None = None,
        remove_dod: list[int] | None = None,
        dependencies: list[str] | None = None,
        assignee: list[str] | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
        milestone: str | None = None,
        clear_milestone: bool = False,
        references: list[str] | None = None,
        add_references: list[str] | None = None,
        remove_references: list[str] | None = None,
        documentation: list[str] | None = None,
        add_documentation: list[str] | None = None,
        remove_documentation: list[str] | None = None,
        modified_files: list[str] | None = None,
        status: str | None = None,
        on_status_change: bool | None = None,
    ) -> dict[str, Any]:
        """Edit supported task fields in a Backlog.md project."""
        return tool_registry.task_edit(
            _project(project),
            task_id=task_id,
            **_present(
                title=title,
                description=description,
                planSet=plan_set,
                planAppend=plan_append,
                planClear=plan_clear,
                notes=notes,
                append_notes=append_notes,
                acceptance_criteria_add=acceptance_criteria_add,
                definition_of_done_add=definition_of_done_add,
                final_summary=final_summary,
                append_final_summary=append_final_summary,
                clear_final_summary=clear_final_summary,
                check_ac=check_ac,
                check_dod=check_dod,
                uncheck_ac=uncheck_ac,
                uncheck_dod=uncheck_dod,
                remove_ac=remove_ac,
                remove_dod=remove_dod,
                dependencies=dependencies,
                assignee=assignee,
                labels=labels,
                priority=priority,
                milestone=milestone,
                clear_milestone=clear_milestone,
                references=references,
                add_references=add_references,
                remove_references=remove_references,
                documentation=documentation,
                add_documentation=add_documentation,
                remove_documentation=remove_documentation,
                modifiedFiles=modified_files,
                status=status,
                on_status_change=on_status_change,
            ),
        )

    @server.tool()
    def task_archive(project: str, task_id: str) -> dict[str, Any]:
        """Move one active task to backlog/archive/tasks."""
        return tool_registry.task_archive(_project(project), task_id=task_id)

    @server.tool()
    def task_complete(project: str, task_id: str) -> dict[str, Any]:
        """Move one Done task to backlog/completed."""
        return tool_registry.task_complete(_project(project), task_id=task_id)

    @server.tool()
    def document_list(project: str, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List or search documents in a Backlog.md project."""
        return tool_registry.document_list(_project(project), query=query, limit=limit)

    @server.tool()
    def document_view(project: str, path_or_id: str) -> dict[str, Any]:
        """Return one document from a Backlog.md project."""
        return tool_registry.document_view(_project(project), path_or_id=path_or_id)

    @server.tool()
    def document_create(
        project: str,
        path: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a document in a Backlog.md project."""
        return tool_registry.document_create(
            _project(project),
            path=path,
            title=title,
            content=content,
            metadata=metadata,
        )

    @server.tool()
    def document_update(
        project: str,
        path_or_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Update a document in a Backlog.md project."""
        return tool_registry.document_update(
            _project(project),
            path_or_id=path_or_id,
            **_present(title=title, content=content),
        )

    @server.tool()
    def milestone_list(project: str) -> list[dict[str, Any]]:
        """List milestones in a Backlog.md project."""
        return tool_registry.milestone_list(_project(project))

    @server.tool()
    def milestone_add(project: str, name: str, description: str = "") -> dict[str, Any]:
        """Create a milestone in a Backlog.md project."""
        return tool_registry.milestone_add(_project(project), name=name, description=description)

    @server.tool()
    def milestone_rename(
        project: str,
        old_name: str,
        new_name: str,
        update_tasks: bool = False,
    ) -> dict[str, Any]:
        """Rename a milestone in a Backlog.md project."""
        return tool_registry.milestone_rename(
            _project(project),
            old_name=old_name,
            new_name=new_name,
            update_tasks=update_tasks,
        )

    @server.tool()
    def milestone_remove(project: str, name: str, clear_tasks: bool = False) -> dict[str, Any]:
        """Remove a milestone from a Backlog.md project."""
        return tool_registry.milestone_remove(_project(project), name=name, clear_tasks=clear_tasks)

    @server.tool()
    def milestone_archive(project: str, name: str) -> dict[str, Any]:
        """Archive a milestone in a Backlog.md project."""
        return tool_registry.milestone_archive(_project(project), name=name)

    @server.tool()
    def definition_of_done_defaults_get(project: str) -> dict[str, list[str]]:
        """Return project Definition of Done defaults."""
        return tool_registry.definition_of_done_defaults_get(_project(project))

    @server.tool()
    def definition_of_done_defaults_upsert(project: str, items: list[str]) -> dict[str, list[str]]:
        """Replace project Definition of Done defaults."""
        return tool_registry.definition_of_done_defaults_upsert(_project(project), items=items)


def _project(project: str):
    return discover_project(Path.cwd(), explicit_cwd=Path(project))


def _present(**values: Any) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None}
