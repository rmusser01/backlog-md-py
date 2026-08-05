# README Newcomer Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `README.md` so a first-time reader who has never used Backlog.md can understand the concept, scan the features, and complete a full quick-start tutorial without leaving the README.

**Architecture:** Single-file, in-place rewrite of `README.md` per the approved spec. No code, packaging, doc, or test changes. All commands are copied verbatim from the current README or `docs/getting-started.md` (verified sources); badges are PyPI-sourced shields so they cannot go stale.

**Tech Stack:** Markdown, shields.io badges, repo dev tooling (`build`, `twine`) for PyPI-render validation.

**Spec:** `docs/superpowers/specs/2026-08-04-readme-newcomer-rewrite-design.md`

**Verified badge facts (checked against `pyproject.toml`):**
- Package name on PyPI: `backlog-md-py` (`pyproject.toml:6`)
- Python: `requires-python = ">=3.11"`; classifiers list 3.11–3.14 (`pyproject.toml:9,22-25`)
- License: **GPL-3.0-only** (`pyproject.toml:10`) — do NOT use an MIT badge
- Repo URL: `https://github.com/rmusser01/backlog-md-py` (`pyproject.toml:37`)

---

### Task 1: Rewrite README.md with the newcomer-focused structure

**Files:**
- Modify: `README.md` (full replacement of all 143 lines)

- [ ] **Step 1: Replace the entire README content**

Use the Write tool to overwrite `README.md` with exactly this content:

````markdown
# backlog-md-py

[![PyPI version](https://img.shields.io/pypi/v/backlog-md-py)](https://pypi.org/project/backlog-md-py/)
[![Python versions](https://img.shields.io/pypi/pyversions/backlog-md-py)](https://pypi.org/project/backlog-md-py/)
[![License: GPL-3.0-only](https://img.shields.io/pypi/l/backlog-md-py)](LICENSE)

`backlog-md-py` is a Python implementation of the local-file
[Backlog.md](https://github.com/MrLesk/Backlog.md) task workflow. It manages
project tasks as plain Markdown files and lets you work with them from the
CLI, Python code, AI agents over MCP, a browser board, or a terminal UI — no
Node or Bun runtime required. New here? The Quick Start below runs entirely in
a scratch directory, so nothing touches a real project until you point it at
one.

## What Is Backlog.md?

Backlog.md keeps a project's tasks, documents, decisions, and milestones as
plain Markdown files under a `backlog/` directory inside the project. The
Markdown files are the source of truth: they diff, review, and commit with
your code, so humans and AI agents can both read and update them without a
hosted service or database.

`backlog-md-py` implements that workflow as a standalone Python package. Use
it when you want Backlog.md-compatible project tracking from Python tooling or
local agents. For the upstream concept and ecosystem, see
[Backlog.md](https://github.com/MrLesk/Backlog.md).

## Features

| Interface | Entry point | What it's for |
| --- | --- | --- |
| CLI | `backlog-py --cwd /path/to/project ...` | Scriptable task, document, and board commands; a recommended automation surface. |
| Python module | `python -m backlog_py ...` | The CLI in module form, for environments without script shims. |
| Python helpers | `backlog_py.mcp`, `backlog_py.storage.project` | Call the same core services from your own Python code. |
| MCP stdio server | `backlog-py-mcp` | SDK-free MCP server so AI agents can manage the backlog. |
| Singleton daemon | `backlog-py daemon ensure` | One shared process for multi-agent setups. |
| Browser board | `backlog-py --cwd /path/to/project browser` | Human-facing kanban board in the browser. |
| Terminal UI (optional) | `backlog-py --cwd /path/to/project tui` | Keyboard-driven board in the terminal; requires the `tui` extra. |

The plain CLI and MCP tools are the recommended automation surfaces. The
browser board and TUI are human-facing project navigation surfaces.

## Quick Start

### 1. Install

```bash
python -m pip install backlog-md-py
```

Optional extras and unreleased installs:

```bash
python -m pip install "backlog-md-py[tui]"   # terminal UI
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"   # unreleased commits
```

### 2. Try it in a scratch project

Start in an empty scratch directory so nothing touches a real project:

```bash
mkdir -p /tmp/backlog-md-py-demo
backlog-py --cwd /tmp/backlog-md-py-demo init --defaults --no-git
backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain
backlog-py --cwd /tmp/backlog-md-py-demo task list --plain
backlog-py --cwd /tmp/backlog-md-py-demo task edit task-1 --notes "Edited in a scratch project." --plain
backlog-py --cwd /tmp/backlog-md-py-demo board
```

Then look inside `/tmp/backlog-md-py-demo/backlog/` — every record you just
created is a plain Markdown file you can read and diff.

### 3. Point at a real project

Start with read-only commands:

```bash
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task <id> --plain
backlog-py --cwd /path/to/project board
```

Then the human-facing surfaces:

```bash
backlog-py --cwd /path/to/project browser --port 6420 --no-open
backlog-py --cwd /path/to/project tui   # requires the tui extra
```

Before running mutation commands (`task create`, `task edit`, `task archive`,
and friends) against a real project, smoke-test on a copy and review the
diff — see [Safety and compatibility](#safety-and-compatibility).

### 4. Use it with AI agents (MCP)

Run the SDK-free MCP stdio server:

```bash
backlog-py-mcp
```

For multi-agent setups, run one shared daemon and let MCP clients connect
through it:

```bash
backlog-py daemon ensure
backlog-py daemon status --json
```

To generate Backlog.md instruction blocks for common agent files:

```bash
backlog-py --cwd /path/to/project agents --update-instructions
```

See [integration.md](docs/integration.md) for MCP client configuration and
[singleton-daemon.md](docs/singleton-daemon.md) for daemon lifecycle details.

## Safety And Compatibility

The supported 2.x contract is the behavior documented in the compatibility
inventory, stability policy, and parity docs. A few rules keep adoption safe:

- Markdown files under `backlog/` remain the source of truth; the daemon is a
  process-reuse and coordination layer, and the optional SQLite index is
  disposable read acceleration — neither is a separate database.
- Before live mutation in a consuming project, run copied-repository smoke
  tests and review the resulting diff. See
  [cutover-validation.md](docs/cutover-validation.md).
- Do not alias `backlog-py` to `backlog` unless the target project has made an
  explicit project cutover decision.

See the [stability policy](docs/stability-policy.md) for the full supported
contract and release gate.

## Documentation

Start with the [documentation index](docs/README.md). Common references:

- [Getting started](docs/getting-started.md)
- [Integration guide](docs/integration.md)
- [Architecture guide](docs/architecture.md)
- [Stability policy](docs/stability-policy.md)
- [Singleton daemon guide](docs/singleton-daemon.md)
- [Cutover validation checklist](docs/cutover-validation.md)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [Release process](RELEASE.md)

## Development

Use Python 3.11, 3.12, or 3.13. Create a local virtual environment with `uv`
and install editable development dependencies:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Run the focused agent-critical gate or the full test suite:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
uv run --extra dev python -m pytest tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow and
[architecture.md](docs/architecture.md) for the source layout.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
````

- [ ] **Step 2: Verify every relative link resolves**

Run:

```bash
cd /Users/macbook-dev/Documents/GitHub/backlog-md-py && \
grep -oE '\]\(([^)#]+)(#[^)]*)?\)' README.md | sed -E 's/\]\(([^)#]+)(#[^)]*)?\)/\1/' | \
grep -v '^https\?://' | sort -u | while read -r f; do [ -e "$f" ] || echo "MISSING: $f"; done
```

Expected: no output (no MISSING lines).

- [ ] **Step 3: Verify every command matches a verified source**

Confirm each CLI invocation in the new README appears in the old README (git)
or `docs/getting-started.md`:

```bash
cd /Users/macbook-dev/Documents/GitHub/backlog-md-py && \
for cmd in "init --defaults --no-git" "task create" "task list --plain" \
  "task edit task-1 --notes" "browser --port 6420 --no-open" \
  "backlog-py-mcp" "daemon ensure" "daemon status --json" \
  "agents --update-instructions" "task <id> --plain"; do
  grep -qF "$cmd" docs/getting-started.md || git show HEAD:README.md | grep -qF "$cmd" \
    && echo "OK: $cmd" || echo "UNVERIFIED: $cmd"
done
```

Expected: every line prints `OK:`.

- [ ] **Step 4: Validate PyPI rendering**

`readme` in `pyproject.toml` points at `README.md`, so a broken render would
break the PyPI page. Build and check:

```bash
cd /Users/macbook-dev/Documents/GitHub/backlog-md-py && \
uv run --extra dev python -m build --sdist --outdir /tmp/readme-check && \
uv run --extra dev python -m twine check /tmp/readme-check/*
```

Expected: `Checking ...: PASSED`. Clean up with `rm -rf /tmp/readme-check`.

### Task 2: Final review and commit

**Files:**
- Modify: `README.md`
- (already present, uncommitted) `docs/superpowers/specs/2026-08-04-readme-newcomer-rewrite-design.md`, `docs/superpowers/plans/2026-08-04-readme-newcomer-rewrite.md`

- [ ] **Step 1: Review the rendered diff**

```bash
cd /Users/macbook-dev/Documents/GitHub/backlog-md-py && git diff README.md
```

Read the diff top to bottom as a newcomer: concept clear, table scannable,
tutorial complete, safety present but not front-loaded. Fix anything unclear.

- [ ] **Step 2: Commit (requires explicit user confirmation first)**

Git mutations need the user's go-ahead. Once confirmed:

```bash
cd /Users/macbook-dev/Documents/GitHub/backlog-md-py && \
git add README.md docs/superpowers/specs/2026-08-04-readme-newcomer-rewrite-design.md docs/superpowers/plans/2026-08-04-readme-newcomer-rewrite.md && \
git commit -m "docs: rewrite README for first-time Backlog.md users"
```
