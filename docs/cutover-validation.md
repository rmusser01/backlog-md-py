# Cutover Validation

Use this checklist before replacing a Node/Bun Backlog.md integration with
`backlog-md-py` in another repository. The goal is to prove the local-file,
agent-facing workflow without mutating live project data.

## Package Gate

Run the package validation gate from a clean checkout:

```bash
python -m pip install -e ".[dev,mcp]"
python -m pytest tests -v
python -m bandit -r src
python -m build
python -m twine check dist/*
```

The GitHub Actions package job also installs the built wheel and verifies the
optional MCP extra, but local validation should not depend on remote CI alone.

## Copied-Repository Smoke

Always run mutation smoke tests against a temporary copy of the target project,
not the live repository:

```bash
tmpdir="$(mktemp -d)"
cp -R /path/to/project "$tmpdir/project"

backlog-py --cwd "$tmpdir/project" task list --plain
backlog-py --cwd "$tmpdir/project" board
backlog-py --cwd "$tmpdir/project" board export Backlog.md --force --export-version v1.45.1
backlog-py --cwd "$tmpdir/project" task create "Cutover smoke dependency base" --id TASK-9998 --description "Created by backlog-md-py smoke." --plain
backlog-py --cwd "$tmpdir/project" task create "Cutover smoke task" --id TASK-9999 --description "Created by backlog-md-py smoke." --plan "1. Run copied-repo smoke." --milestone "Cutover" --ordinal 9999 --parent TASK-9998 --ref "https://example.com/cutover" --doc "docs/cutover.md" --modified-file "src/cutover.py" --notes "Initial copied-repo note." -a codex -l smoke,cutover --priority high --ac "Smoke task is visible" --dod "Copied-repo smoke reviewed" --dep 9998 --plain
backlog-py --cwd "$tmpdir/project" task edit TASK-9999 --title "Cutover renamed smoke task" --plan "1. Update copied-repo smoke." --append-plan "2. Verify copied-repo smoke." --milestone "Cutover verified" --ordinal 10000 --ref "src/smoke.py,docs/smoke.md" --doc "docs/verification.md" --modified-file "src/smoke.py,tests/test_smoke.py" --notes "Copied-repo replacement note." --append-notes "- Copied-repo smoke note." -a reviewer -l smoke,edited --priority medium --ac "Edited smoke criterion" --dod "Edited smoke verification" --remove-ac 1 --final-summary "Copied-repo smoke complete." --append-final-summary "Final smoke details appended." --plain
backlog-py --cwd "$tmpdir/project" search "Cutover smoke" --modified-file "src/smoke.py" --limit 5 --plain
backlog-py --cwd "$tmpdir/project" task archive TASK-9999 --plain
backlog-py --cwd "$tmpdir/project" task archive TASK-9998 --plain
backlog-py --cwd "$tmpdir/project" doc list
backlog-py --cwd "$tmpdir/project" milestone list
backlog-py --cwd "$tmpdir/project" config list
```

Review the copied repository diff before considering live use:

```bash
git -C "$tmpdir/project" diff -- backlog
```

The diff should be limited to the intended Backlog.md files and should preserve
unowned Markdown sections around edited task content.

## MCP Smoke

For agent integrations, verify the FastMCP adapter can be constructed from the
same environment that will run the server:

```bash
python - <<'PY'
from backlog_py.mcp.server import create_server, is_mcp_sdk_available

assert is_mcp_sdk_available()
server = create_server()
assert type(server).__name__ == "FastMCP"
print(type(server).__name__)
PY
```

For pure Python embedding, call the helper functions against the copied project:

```bash
python - <<'PY'
from pathlib import Path

from backlog_py.mcp import read_resource, task_search
from backlog_py.storage.project import discover_project

project = discover_project(Path.cwd(), explicit_cwd=Path("/path/to/copied/project"))
assert "Backlog.md" in read_resource("backlog://workflow/overview")
print(task_search(project, "Cutover smoke", limit=5))
PY
```

## Cutover Decision

Do not alias `backlog-py` to `backlog` or point agents at live mutation tools
until:

- the package gate passes locally,
- copied-repository smoke diffs are reviewed,
- MCP or subprocess integration is verified in the consuming project,
- browser UI, interactive TUI, hook, auto-commit, and remote-git deferrals are
  acceptable for that workflow.
