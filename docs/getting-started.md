# Getting Started

## What This Project Is

`backlog-md-py` is a standalone Python compatibility implementation for
Backlog.md local-file task workflows. It provides the `backlog-py` CLI, Python
helpers, SDK-free MCP stdio through `backlog-py-mcp`, and agent-oriented
integration paths without requiring a Node/Bun runtime.

It is not a hosted task service, and adopting it does not require replacing
upstream Backlog.md immediately. Treat live mutation as a project cutover
decision after local validation.

## Install

For released versions, install from PyPI:

```bash
python -m pip install backlog-md-py
```

For unreleased commits, install from GitHub:

```bash
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
```

## Try It In A Scratch Project

Start in an empty scratch directory so task creation and editing are safe:

```bash
mkdir -p /tmp/backlog-md-py-demo
backlog-py --cwd /tmp/backlog-md-py-demo init --defaults
backlog-py --cwd /tmp/backlog-md-py-demo task list --plain
backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain
backlog-py --cwd /tmp/backlog-md-py-demo task edit task-1 --notes "Edited in a scratch project." --plain
backlog-py --cwd /tmp/backlog-md-py-demo board
```

Use the same caution for copied repositories: try mutation examples in a
scratch project or a copied repository before running them against a live
project backlog.

## Point At An Existing Project

Use `--cwd` to point at the project that contains Backlog.md files. Start with
read-only commands:

```bash
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task <id> --plain
backlog-py --cwd /path/to/project board
```

Before live task creation, editing, archiving, or export operations, run a
copied-repository mutation smoke and review the resulting diff. The
[cutover validation checklist](cutover-validation.md) has a full copied-repo
sequence.

## Common CLI Commands

```bash
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task list --status "In Progress" --plain
backlog-py --cwd /path/to/project task <id> --plain
backlog-py --cwd /path/to/project search "release" --plain
backlog-py --cwd /path/to/project board
backlog-py compat status
backlog-py compat status --json
```

For the longer command catalog and Python helper examples, see
[integration.md](integration.md).

## Browser Board

Start the optional loopback browser board without opening a browser:

```bash
backlog-py --cwd /path/to/project browser --port 6420 --no-open
```

Browser release-readiness evidence is tracked separately from the first
agent-cutover gate. See
[browser-release-validation.md](browser-release-validation.md) for the release
evidence manifest.

## Optional Textual TUI

The Textual board is not part of the base install:

```bash
python -m pip install "backlog-md-py[tui]"
backlog-py --cwd /path/to/project tui
```

Use it for human board work when you want keyboard navigation, task detail,
dependency visibility, filters, create/edit/move/archive actions, and
configured-editor launch. Arrow keys move the selection, `h/j/k/l` provide
Vim-style aliases, and `shift+h` / `shift+l` move the selected task to the
adjacent status. Press `d` to jump to the selected task's first visible
dependency; repeat `d` to cycle through additional visible dependencies. Press
`shift+d` to jump to the first visible task that depends on the selection;
repeat `shift+d` to cycle through additional visible dependents. Press
`backspace` to return through dependency navigation history. Press `u` to edit
the selected task's title, status, description, and metadata. Press `x` to
toggle Acceptance Criteria or Definition of Done checklist items on the
selected task. The plain CLI and MCP tools remain the recommended automation
surfaces. When the filter is focused, `escape` clears it and returns focus to
the board.

## MCP And Multi-Agent Use

Run the SDK-free MCP stdio entry point directly:

```bash
backlog-py-mcp
```

For multi-agent setups, start one singleton daemon and use `backlog-py-mcp` as
the stdio shim:

```bash
backlog-py daemon ensure
backlog-py daemon status --json
```

Use [integration.md](integration.md) for MCP client configuration and
[singleton-daemon.md](singleton-daemon.md) for lifecycle, verification, and
rollback details.

## Compatibility Status

The compatibility report is read-only:

```bash
backlog-py compat status
backlog-py compat status --json
```

Use it as a quick inventory check before deeper project validation. It reports
agent cutover readiness separately from browser release readiness.

See [stability-policy.md](stability-policy.md) for the beta supported contract
and release gate.

## Mutation Safety Checklist

- Start with read-only commands against the target project.
- Run create/edit/archive examples in a scratch project first.
- Run copied-repository mutation smoke before live mutation.
- Review the copied repository diff before accepting the workflow.
- Do not alias `backlog-py` to `backlog` without an explicit project cutover
  decision.
- Do not run upstream Backlog.md mutation paths and `backlog-md-py` mutation
  paths against the same live project during migration.

## Next Steps

- [Integration guide](integration.md) for CLI, Python helper, and MCP details.
- [Stability policy](stability-policy.md) for the beta support contract.
- [Singleton daemon guide](singleton-daemon.md) for multi-agent process reuse.
- [Cutover validation checklist](cutover-validation.md) for migration gates.
- [Browser release validation](browser-release-validation.md) for browser
  evidence rules.
- [Contributing guide](../CONTRIBUTING.md) for local development and release
  validation.
