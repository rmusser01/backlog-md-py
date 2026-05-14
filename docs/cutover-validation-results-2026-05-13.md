# Cutover Validation Results - 2026-05-13

This document records the first completed local-file agent cutover validation
for `backlog-md-py`.

## Scope

Validated commit:

```text
299d2a7 Support board export parity
```

Validation covered the agent-critical CLI, Python helper, and MCP paths tracked
by `docs/agent-critical-parity.md`.

Browser UI, interactive TUI, hook execution, auto-commit, hook bypass, shell
completion installation, and remote git behavior remain deferred as documented
in `docs/browser-parity.md` and `docs/interactive-deferrals.md`.

## Package Gate

Commands run in a disposable worktree:

```bash
UV_CACHE_DIR=/private/tmp/backlog-md-py-uv-cache \
  uv run --python /Users/macbook-dev/.local/bin/python3.12 \
  --extra dev --extra mcp python -m pytest -q
```

Result:

```text
160 passed in 0.84s
```

```bash
UV_CACHE_DIR=/private/tmp/backlog-md-py-uv-cache \
  uv run --python /Users/macbook-dev/.local/bin/python3.12 \
  --extra dev --extra mcp python -m bandit -r src -q
```

Result: passed with no findings.

```bash
UV_CACHE_DIR=/private/tmp/backlog-md-py-uv-cache \
  uv run --python /Users/macbook-dev/.local/bin/python3.12 \
  --extra dev --extra mcp python -m build
```

Result:

```text
Successfully built backlog_md_py-0.1.0.tar.gz and backlog_md_py-0.1.0-py3-none-any.whl
```

```bash
UV_CACHE_DIR=/private/tmp/backlog-md-py-uv-cache \
  uv run --python /Users/macbook-dev/.local/bin/python3.12 \
  --extra dev --extra mcp python -m twine check dist/*
```

Result:

```text
Checking dist/backlog_md_py-0.1.0-py3-none-any.whl: PASSED
Checking dist/backlog_md_py-0.1.0.tar.gz: PASSED
```

## Copied-Repository Smoke

Smoke target:

```text
tests/fixtures/repos/basic copied to /private/tmp/backlog-md-py-smoke.KeY0Pf/project
```

Commands verified:

```bash
backlog-py --cwd "$copy" task list --plain
backlog-py --cwd "$copy" board
backlog-py --cwd "$copy" board export Backlog.md --force --export-version v1.45.1
backlog-py --cwd "$copy" task create "Cutover smoke dependency base" --id TASK-9998 --description "Created by backlog-md-py smoke." --plain
backlog-py --cwd "$copy" task create "Cutover smoke task" --id TASK-9999 --description "Created by backlog-md-py smoke." --plan "1. Run copied-repo smoke." --milestone "Cutover" --parent TASK-9998 --ref "https://example.com/cutover" --doc "docs/cutover.md" --modified-file "src/cutover.py" --notes "Initial copied-repo note." -a codex -l smoke,cutover --priority high --ac "Smoke task is visible" --dod "Copied-repo smoke reviewed" --dep 9998 --plain
backlog-py --cwd "$copy" task edit TASK-9999 --title "Cutover renamed smoke task" --plan "1. Update copied-repo smoke." --append-plan "2. Verify copied-repo smoke." --milestone "Cutover verified" --ref "src/smoke.py,docs/smoke.md" --doc "docs/verification.md" --modified-file "src/smoke.py,tests/test_smoke.py" --notes "Copied-repo replacement note." --append-notes "- Copied-repo smoke note." -a reviewer -l smoke,edited --priority medium --ac "Edited smoke criterion" --dod "Edited smoke verification" --remove-ac 1 --final-summary "Copied-repo smoke complete." --append-final-summary "Final smoke details appended." --plain
backlog-py --cwd "$copy" task archive TASK-9999 --plain
backlog-py --cwd "$copy" task archive TASK-9998 --plain
backlog-py --cwd "$copy" doc list
backlog-py --cwd "$copy" milestone list
backlog-py --cwd "$copy" config list
```

Result: all commands exited successfully.

Diff review:

- Root `Backlog.md` board export was generated in the copied repository.
- `backlog/archive/tasks/task-9998 - Cutover-smoke-dependency-base.md` was created.
- `backlog/archive/tasks/task-9999 - Cutover-renamed-smoke-task.md` was created.
- Existing fixture task and config files were unchanged.

The archived edited smoke task preserved structured sections for description,
acceptance criteria, implementation plan, implementation notes, final summary,
and Definition of Done.

## MCP Smoke

FastMCP adapter:

```bash
python -c "from backlog_py.mcp.server import create_server, is_mcp_sdk_available; assert is_mcp_sdk_available(); server = create_server(); assert type(server).__name__ == 'FastMCP'; print(type(server).__name__)"
```

Result:

```text
FastMCP
```

Pure helper smoke:

```bash
python -c "from pathlib import Path; from backlog_py.mcp import read_resource, task_search; from backlog_py.storage.project import discover_project; project = discover_project(Path.cwd(), explicit_cwd=Path('/private/tmp/backlog-md-py-smoke.KeY0Pf/project')); assert 'Backlog.md' in read_resource('backlog://workflow/overview'); results = task_search(project, 'parser', limit=5); assert results and results[0]['id'] == 'TASK-1'; print(results)"
```

Result: returned `TASK-1` from the copied fixture repository.

## Decision

The agent-critical local-file cutover gate passed for the validated commit.
Consumers can use `backlog-py` for the documented agent-facing CLI, Python
helper, and MCP workflows after running their own copied-repository smoke test
against the target project.

Do not treat this as full Backlog.md clone parity. Browser UI and interactive
features remain explicitly deferred.
