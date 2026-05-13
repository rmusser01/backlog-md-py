# Integration Guide

`backlog-md-py` is intended to be consumed as a normal Python package by
projects that need local-file Backlog.md compatibility without a Node or Bun
runtime dependency.

The project is still experimental. Keep live-repository mutation behind review
until the agent-critical cutover matrix is complete for your workflow.

## Install From GitHub

Until package publishing is configured, install directly from the repository:

```bash
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
```

Install the optional MCP SDK adapter with:

```bash
python -m pip install "backlog-md-py[mcp] @ git+https://github.com/rmusser01/backlog-md-py.git"
```

For local development against a checkout:

```bash
python -m pip install -e ".[dev]"
```

## CLI Entry Points

The installed command is intentionally named `backlog-py` while compatibility is
still experimental:

```bash
backlog-py --help
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task list --status "In Progress" --priority high -a codex -l implementation --milestone "Release 1" --plain
backlog-py --cwd /path/to/project task create "Implementation task" --plan "1. Inspect current code." --final-summary "Initial PR summary." --milestone "Release 1" --ref "https://github.com/org/repo/issues/123" --doc "docs/design.md" --modified-file "src/api.py" -a codex -l implementation --priority high --ac "Behavior covered" --dod "Tests pass" --dep 1 --plain
backlog-py --cwd /path/to/project task edit TASK-2 --plan "1. Patch focused scope." --append-plan "2. Verify behavior." --milestone "Release 2" --ref "src/api.py,tests/test_api.py" --doc "docs/verification.md" --modified-file "src/api.py,tests/test_api.py" -a reviewer -l ready --priority medium --notes "Implementation details." --ac "Regression covered" --dod "Package check passes" --remove-ac 1 --append-final-summary "Ready for review." --plain
backlog-py --cwd /path/to/project task archive TASK-2 --plain
backlog-py --cwd /path/to/project search "query" --plain
backlog-py --cwd /path/to/project board
```

The module entry point is equivalent:

```bash
python -m backlog_py --cwd /path/to/project task list --plain
```

Do not alias this command to `backlog` for production use until the cutover gate
for your workflow is satisfied.

## Python API Use

The current public surface is conservative. Prefer the CLI for subprocess-based
agent integration, or call the pure helper functions directly when embedding in
a Python process:

```python
from pathlib import Path

from backlog_py.mcp import read_resource, task_archive, task_board, task_complete, task_edit, task_list, task_search, task_view
from backlog_py.storage.project import discover_project

project = discover_project(Path("/path/to/copied/project"))
print(read_resource("backlog://workflow/overview"))
print(task_board(project))
print(task_list(project, status="In Progress", assignee="codex", labels=["implementation"], priority="high", milestone="Release 1", search="release", limit=10))
print(task_search(project, "release", status="In Progress", priority="high", modified_files=["src/api.py"], limit=5))
print(task_view(project, "task-1"))
print(task_edit(project, "task-1", milestone="Release 2", planSet="1. Patch focused scope.", planAppend=["2. Verify behavior."], addReferences=["src/api.py"], addDocumentation=["docs/verification.md"], modifiedFiles=["src/api.py", "tests/test_api.py"], assignee=["reviewer"], labels=["ready"], priority="medium", notes="Implementation details.", finalSummaryAppend=["Ready for review."]))
print(task_complete(project, "task-1"))
print(task_archive(project, "task-1"))
```

Mutation helpers are available for implemented local-file operations, but callers
should run them against a temporary copy first when integrating a new workflow.

## MCP Server

The package exposes pure MCP-style helper functions without optional
dependencies. Installing the `mcp` extra also installs a FastMCP-backed stdio
server:

```bash
backlog-py-mcp
```

For agent integrations, use one of these patterns:

- Run `backlog-py-mcp` as an MCP stdio server after installing
  `backlog-md-py[mcp]`.
- Call the pure helper functions from Python.
- Wrap `backlog-py --cwd <project> ...` as a subprocess tool.

Every MCP tool takes a `project` argument containing the path to the Backlog.md
project or a directory inside it. This keeps the server stateless and avoids a
global mutable working directory.

## Validation For Consumers

Before switching a project to `backlog-md-py`, run at least:

```bash
python -m pytest tests/test_agent_critical_matrix.py -v
python -m pytest tests -v
python -m bandit -r src
```

Then run your own mutation smoke test against a copied repository, not against
the live project backlog. See `docs/cutover-validation.md` for a concrete
cutover checklist.
