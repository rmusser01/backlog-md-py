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

from backlog_py.mcp import read_resource, task_search, task_view
from backlog_py.storage.project import discover_project

project = discover_project(Path("/path/to/project"))
print(read_resource("backlog://workflow/overview"))
print(task_search(project, "release", limit=5))
print(task_view(project, "task-1"))
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
