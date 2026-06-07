from __future__ import annotations


WORKFLOW_OVERVIEW_RESOURCE = """# Backlog.md Python MCP Workflow

This compatibility layer exposes Backlog.md read helpers and safe mutation
helpers implemented in Python. It does not shell out to the Node.js Backlog.md
CLI.

Supported resources:
- backlog://workflow/overview
- backlog://docs/task-workflow
- backlog://init-required

Supported tools:
- project_status(project, recentLimit=5)
- task_board(project)
- task_list(project, status=None, limit=100, *, assignee=None, labels=None, priority=None, milestone=None, parentTaskId=None, search=None)
- task_search(project, query="", limit=10, *, status=None, priority=None, modified_files=None)
- task_view(project, task_id)
- task_create(project, id=None, ordinal=None, milestone=None, parentTaskId=None, references=None, documentation=None, modifiedFiles=None, implementationPlan=None, finalSummary=None, **kwargs)
- task_edit(project, task_id, ordinal=None, milestone=None, clearPriority=False, clearMilestone=False, references=None, addReferences=None, documentation=None, addDocumentation=None, modifiedFiles=None, **kwargs)
- task_archive(project, task_id)
- task_complete(project, task_id)
- document_list(project, query=None, limit=100)
- document_view(project, path_or_id)
- document_create(project, **kwargs)
- document_update(project, path_or_id, **kwargs)
- milestone_list(project)
- milestone_add(project, name, description="")
- milestone_rename(project, old_name, new_name, update_tasks=False)
- milestone_remove(project, name, clear_tasks=False)
- milestone_archive(project, name)
- definition_of_done_defaults_get(project)
- definition_of_done_defaults_upsert(project, items)

All write-capable helpers must use the safe core services, path-containment
checks, and atomic file writes. Do not use this registry for shell execution.

If this MCP server is launched from a directory that is not inside a Backlog.md
project, read backlog://init-required before attempting mutations.
"""

TASK_WORKFLOW_RESOURCE = """# Backlog.md Task Workflow

Use this workflow before mutating repository files through Backlog.md helpers.

## Search before creating

Use task_search, task_list, or project_status to find existing related work.
Create a new task only when no existing task covers the same reviewable unit of
work.

## Task creation

Use task_create with a focused title, status, description, acceptance criteria,
implementation plan, references, documentation, and modified files when they are
known. Keep one task scoped to one reviewable unit.

## Task execution

Keep status and implementation notes current while work is active. Prefer MCP
tools or the backlog-py CLI over manual task-file edits. Record blockers,
verification commands, and notable design decisions as they happen.

## Task finalization

Before marking work complete, ensure acceptance criteria are checked, tests or
verification are recorded, documentation changes are linked, security checks are
recorded when code changed, and finalSummary explains what changed and why.
"""

INIT_REQUIRED_RESOURCE = """# Backlog.md Project Initialization Required

No Backlog.md config was found from the current MCP server directory.

Run a project initialization command from the target repository, for example:

```bash
backlog-py --cwd /path/to/project init "Project Name" --defaults
```

For filesystem-only projects that should not inspect Git branches or remotes,
use the documented no-git initialization mode when available. After
initialization, restart or reconfigure the MCP client so it launches from inside
the project, sets BACKLOG_CWD, or passes the explicit project argument.
"""

_RESOURCE_ALIASES = {
    "backlog://workflow/overview": "backlog://workflow/overview",
    "backlog://docs/task-workflow": "backlog://docs/task-workflow",
    "backlog://init-required": "backlog://init-required",
}

_RESOURCES = {
    "backlog://workflow/overview": WORKFLOW_OVERVIEW_RESOURCE,
    "backlog://docs/task-workflow": TASK_WORKFLOW_RESOURCE,
    "backlog://init-required": INIT_REQUIRED_RESOURCE,
}


def read_resource(uri: str) -> str:
    """Return static read-only MCP resource content for a supported URI."""
    canonical_uri = _RESOURCE_ALIASES.get(uri)
    if canonical_uri is None:
        raise KeyError(f"Unsupported Backlog MCP resource: {uri}")
    return _RESOURCES[canonical_uri]


def list_resource_uris() -> tuple[str, ...]:
    """Return supported MCP resource URIs, including aliases."""
    return tuple(_RESOURCE_ALIASES)
