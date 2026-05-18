from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LockScope = Literal["project", "init-root", "out-of-scope"]


@dataclass(frozen=True)
class MutationSurface:
    """Known repository mutation surface that must be covered by shared locks."""

    name: str
    entrypoints: tuple[str, ...]
    lock_scope: LockScope
    rationale: str


MUTATION_SURFACES: tuple[MutationSurface, ...] = (
    MutationSurface(
        "init_project",
        ("backlog_py.cli.main",),
        "init-root",
        "Creates project config, directories, and optional agent instruction files before project discovery exists.",
    ),
    MutationSurface(
        "task_create",
        ("backlog_py.cli.main", "backlog_py.mcp.tools", "backlog_py.browser.service"),
        "project",
        "Creates active task markdown and allocates task IDs inside one project.",
    ),
    MutationSurface(
        "task_edit",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Rewrites active task markdown and may change task status metadata.",
    ),
    MutationSurface(
        "task_archive",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Moves active task markdown into the project archive tree.",
    ),
    MutationSurface(
        "task_complete",
        ("backlog_py.mcp.tools",),
        "project",
        "Moves Done task markdown into the project completed tree.",
    ),
    MutationSurface(
        "cleanup_complete_done",
        ("backlog_py.cli.main",),
        "project",
        "Bulk-moves Done/complete tasks from active task storage to completed storage.",
    ),
    MutationSurface(
        "draft_create",
        ("backlog_py.cli.main",),
        "project",
        "Creates draft task markdown and allocates draft IDs inside one project.",
    ),
    MutationSurface(
        "draft_promote",
        ("backlog_py.cli.main",),
        "project",
        "Moves draft task markdown into active task storage and allocates task IDs.",
    ),
    MutationSurface(
        "draft_demote",
        ("backlog_py.cli.main",),
        "project",
        "Moves active task markdown into draft storage and allocates draft IDs.",
    ),
    MutationSurface(
        "draft_archive",
        ("backlog_py.cli.main",),
        "project",
        "Moves draft task markdown into archived draft storage.",
    ),
    MutationSurface(
        "document_create",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Creates markdown documents under the project's docs tree.",
    ),
    MutationSurface(
        "document_update",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Rewrites or moves markdown documents under the project's docs tree.",
    ),
    MutationSurface(
        "decision_create",
        ("backlog_py.cli.main",),
        "project",
        "Creates decision records under the project's decisions tree.",
    ),
    MutationSurface(
        "milestone_add",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Creates milestone markdown files inside one project.",
    ),
    MutationSurface(
        "milestone_rename",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Renames milestone files and may rewrite matching task frontmatter.",
    ),
    MutationSurface(
        "milestone_remove",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Removes milestone files and may rewrite matching task frontmatter.",
    ),
    MutationSurface(
        "milestone_archive",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Moves milestone markdown into the project archive tree.",
    ),
    MutationSurface(
        "config_set",
        ("backlog_py.cli.main",),
        "project",
        "Rewrites project configuration values.",
    ),
    MutationSurface(
        "definition_of_done_defaults_upsert",
        ("backlog_py.cli.main", "backlog_py.mcp.tools"),
        "project",
        "Rewrites project Definition of Done defaults in configuration.",
    ),
    MutationSurface(
        "agents_update_instructions",
        ("backlog_py.cli.main",),
        "project",
        "Writes agent instruction files inside the project root.",
    ),
    MutationSurface(
        "board_export_file",
        ("backlog_py.cli.main",),
        "project",
        "Writes a generated board export file inside the project root.",
    ),
    MutationSurface(
        "board_export_readme",
        ("backlog_py.cli.main",),
        "project",
        "Rewrites the generated board section in README.md.",
    ),
)


def mutation_by_name(name: str) -> MutationSurface:
    """Return mutation metadata by stable operation name."""
    for surface in MUTATION_SURFACES:
        if surface.name == name:
            return surface
    raise KeyError(f"Unknown mutation surface: {name}")
