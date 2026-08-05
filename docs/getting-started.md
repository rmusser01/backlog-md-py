# Getting Started

## What This Project Is

`backlog-md-py` is a standalone Python implementation of the Backlog.md
local-file task workflow. It keeps project tasks, documents, decisions, and
milestones as Markdown files under a project's `backlog/` directory, and
provides Python-native ways to read and mutate them.

The main entry points are:

- `backlog-py`: CLI and browser/TUI launcher.
- `python -m backlog_py`: module form of the CLI.
- `backlog-py-mcp`: SDK-free MCP stdio server.
- `backlog-py daemon ...`: optional singleton daemon for multi-agent process
  reuse.
- Python helper functions under `backlog_py.mcp` and project discovery helpers
  under `backlog_py.storage.project`.

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

Optional extras:

```bash
python -m pip install "backlog-md-py[tui]"
```

Use the TUI extra only when you want the Textual terminal board. MCP stdio and
the daemon are included in the base package.

## First Run In A Scratch Project

Start in an empty scratch directory. This lets you try reads and writes without
touching a real project backlog:

```bash
mkdir -p /tmp/backlog-md-py-demo
backlog-py --cwd /tmp/backlog-md-py-demo init --defaults
backlog-py --cwd /tmp/backlog-md-py-demo task list --plain
backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain
backlog-py --cwd /tmp/backlog-md-py-demo task edit task-1 --notes "Edited in a scratch project." --plain
backlog-py --cwd /tmp/backlog-md-py-demo board
```

Default initialization writes Git-aware but non-mutating settings for projects
that may use remote freshness and active branch reads:
`remoteOperations: true`, `checkActiveBranches: true`,
`activeBranchDays: 30`, and `autoCommit: false`. For a filesystem-only
scratch project, pass `--no-git`:

```bash
backlog-py --cwd /tmp/backlog-md-py-demo init --defaults --no-git
```

On an interactive terminal you can also omit `--defaults` to be prompted for
the project name, backlog directory, task prefix, config location, git
integration, and agent instruction files; any flags you pass become the prompt
defaults. Scripts and agents should keep using `--defaults`.

After the scratch run, inspect `/tmp/backlog-md-py-demo/backlog/` to see the
Markdown files that were created.

## Point At An Existing Project

Use `--cwd` to point at the project that contains Backlog.md files. Start with
read-only commands:

```bash
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task <id> --plain
backlog-py --cwd /path/to/project board
```

For mutation commands such as `task create`, `task edit`, `task archive`,
document updates, cleanup, or board export, copy the repository first and review
the diff after the smoke test:

```bash
cp -R /path/to/project /tmp/project-backlog-py-smoke
backlog-py --cwd /tmp/project-backlog-py-smoke task create "Smoke task" --plain
backlog-py --cwd /tmp/project-backlog-py-smoke task edit task-1 --notes "Smoke edit." --plain
git -C /tmp/project-backlog-py-smoke diff -- backlog
```

Use the [cutover validation checklist](cutover-validation.md) for the full
copied-repository sequence before live project writes.

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
global search, Markdown preview, dependency visibility, filters,
create/edit/move/archive actions, safe project settings, Definition of Done
defaults, and configured-editor launch. Arrow keys move the selection, `h/j/k/l`
provide Vim-style aliases, and `shift+h` / `shift+l` move the selected task to
the adjacent status. Press `d` to jump to the selected task's first visible
dependency; repeat `d` to cycle through additional visible dependencies. Press
`shift+d` to jump to the first visible task that depends on the selection;
repeat `shift+d` to cycle through additional visible dependents. Press
`backspace` to return through dependency navigation history. Press `p` to open
a read-only Markdown preview for the selected task. Press `u` to edit the
selected task's title, status, description, and metadata. Press `x` to toggle
Acceptance Criteria or Definition of Done checklist items on the selected task.
Press `s` to search tasks, documents, and decisions; task results can jump to
visible board cards. Press `c` to edit the same safe non-shell project settings
available in the browser settings dialog. Press `o` to edit project-level
Definition of Done defaults, one item per line, and `ctrl+s` to save that
multiline dialog.

The plain CLI and MCP tools remain the recommended automation surfaces. When
the filter is focused, `escape` clears it and returns focus to the board.

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

In multi-agent environments, the daemon prevents each client from launching its
own long-lived server process. The daemon is not a durable task database; it
coordinates requests and forwards them to the same Markdown-backed core
services used by the CLI.

To keep local agent guidance synchronized with the supported workflow, generate
Backlog.md instruction blocks for common agent files:

```bash
backlog-py --cwd /path/to/project agents --update-instructions
```

The generated instructions cover search-before-create, status and notes
updates, Acceptance Criteria and Definition of Done checkoff, Final Summary
expectations, MCP workflow resources, CLI fallback commands with explicit
`--cwd`, singleton daemon guidance, and the guardrail against manually editing
files under `backlog/`.

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

See [stability-policy.md](stability-policy.md) for the stable supported
contract and release gate.

## Mutation Safety Checklist

- Start with read-only commands against the target project.
- Run create/edit/archive examples in a scratch project first.
- Run copied-repository mutation smoke before live mutation.
- Review the copied repository diff before accepting the workflow.
- Keep Markdown task files as the source of truth.
- Do not alias `backlog-py` to `backlog` without an explicit project cutover
  decision.
- Do not run upstream Backlog.md mutation paths and `backlog-md-py` mutation
  paths against the same live project during migration.

## Next Steps

- [Integration guide](integration.md) for CLI, Python helper, and MCP details.
- [Architecture guide](architecture.md) for the source layout and runtime model.
- [Stability policy](stability-policy.md) for the stable support contract.
- [Singleton daemon guide](singleton-daemon.md) for multi-agent process reuse.
- [Cutover validation checklist](cutover-validation.md) for migration gates.
- [Browser release validation](browser-release-validation.md) for browser
  evidence rules.
- [Contributing guide](../CONTRIBUTING.md) for local development and release
  validation.
