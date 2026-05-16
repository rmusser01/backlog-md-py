# Integration Guide

`backlog-md-py` is intended to be consumed as a normal Python package by
projects that need local-file Backlog.md compatibility without a Node or Bun
runtime dependency.

The project is still experimental, but the first agent-critical local-file
cutover validation passed on 2026-05-13. Keep live-repository mutation behind
copied-repository smoke tests and review for each consuming project.

## Install From GitHub

Until package publishing is configured, install directly from the repository:

```bash
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
```

The `backlog-py-mcp` stdio entry point is installed by default and does not
require the Python MCP SDK.

For local development against a checkout:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## CLI Entry Points

The installed command is intentionally named `backlog-py` while compatibility is
still experimental:

```bash
backlog-py --help
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task list --status "In Progress" --priority high -a codex -l implementation --milestone "Release 1" --parent TASK-1 --plain
backlog-py --cwd /path/to/project task create "Implementation task" -d "Implementation notes." -s "To Do" --plan "1. Inspect current code." --final-summary "Initial PR summary." --parent TASK-1 --milestone "Release 1" --ordinal 1000 --ref "https://github.com/org/repo/issues/123" --doc "docs/design.md" --modified-file "src/api.py" -a codex -l implementation --priority high --ac "Behavior covered" --dod "Tests pass" --no-dod-defaults --dep 1 --plain
backlog-py --cwd /path/to/project task edit TASK-2 --plan "1. Patch focused scope." --append-plan "2. Verify behavior." --milestone "Release 2" --ordinal 2000 --ref "src/api.py,tests/test_api.py" --doc "docs/verification.md" --modified-file "src/api.py,tests/test_api.py" -a reviewer -l ready --priority medium --notes "Implementation details." --ac "Regression covered" --dod "Package check passes" --remove-ac 1 --append-final-summary "Ready for review." --plain
backlog-py --cwd /path/to/project task archive TASK-2 --plain
backlog-py --cwd /path/to/project cleanup
backlog-py --cwd /path/to/project doc create "Setup Guide" --path guides --type guide --tags setup,runbook --content "Install and smoke-test the integration."
backlog-py --cwd /path/to/project doc update doc-1 --title "Setup Handbook" --path runbooks --type runbook --tags setup,verified --content "Updated runbook body."
backlog-py --cwd /path/to/project decision create "Use PostgreSQL for primary database" -s accepted
backlog-py --cwd /path/to/project search "query" --plain
backlog-py --cwd /path/to/project search "query" --type document --plain
backlog-py --cwd /path/to/project search "query" --modified-file "src/api.py" --limit 5 --plain
backlog-py --cwd /path/to/project board
backlog-py --cwd /path/to/project board export Backlog.md --force --export-version v1.45.1
backlog-py --cwd /path/to/project board export --readme --export-version v1.45.1
```

The compatibility report is read-only and does not need a project path:

```bash
backlog-py compat status
backlog-py compat status --json
```

Unfiltered `search` output includes matching tasks, documents, and decisions.
Use `--type task`, `--type document`, or `--type decision` to narrow result
classes. Task-specific filters such as `--status`, `--priority`, and
`--modified-file` keep default search restricted to tasks.

The module entry point is equivalent:

```bash
python -m backlog_py --cwd /path/to/project task list --plain
```

Do not alias this command to `backlog` for production use until the cutover gate
for your target project is satisfied and the aliasing decision is explicit.

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
print(task_edit(project, "task-1", ordinal=2000, milestone="Release 2", planSet="1. Patch focused scope.", planAppend=["2. Verify behavior."], addReferences=["src/api.py"], addDocumentation=["docs/verification.md"], modifiedFiles=["src/api.py", "tests/test_api.py"], assignee=["reviewer"], labels=["ready"], priority="medium", notes="Implementation details.", finalSummaryAppend=["Ready for review."]))
print(task_complete(project, "task-1"))
print(task_archive(project, "task-1"))
```

Mutation helpers are available for implemented local-file operations, but callers
should run them against a temporary copy first when integrating a new workflow.

## MCP Server

The package exposes pure MCP-style helper functions and an SDK-free MCP stdio
server without optional dependencies:

```bash
backlog-py-mcp
```

For agent integrations, use one of these patterns:

- Start `backlog-py daemon ensure`, then run `backlog-py-mcp` as the stdio
  compatibility shim. The shim forwards to the singleton daemon when a healthy
  runtime record exists.
- Run `backlog-py-mcp` without a daemon for direct SDK-free stdio mode. This is
  the rollback path, but each client process handles its own requests.
- Call the pure helper functions from Python.
- Wrap `backlog-py --cwd <project> ...` as a subprocess tool.

Every MCP tool takes a `project` argument containing the path to the Backlog.md
project or a directory inside it. This keeps the server stateless and avoids a
global mutable working directory.

See `docs/singleton-daemon.md` for the full daemon lifecycle, local Codex config
shape, and process verification steps.

## Validation For Consumers

For a quick machine-readable inventory check before deeper validation:

```bash
backlog-py compat status --json
```

Before switching a project to `backlog-md-py`, run at least:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src
```

Then run your own mutation smoke test against a copied repository, not against
the live project backlog. See `docs/cutover-validation.md` for a concrete
cutover checklist and `docs/cutover-validation-results-2026-05-13.md` for the
first completed validation record.
