# Upstream Feature Parity

This document tracks the gap between `backlog-md-py` and the current upstream
Backlog.md feature set beyond the first local-file agent cutover gate.

## Current Upstream Baseline

- Upstream package: `backlog.md@1.45.1`
- Audit date: 2026-05-16
- Sources: upstream `README.md`, `CLI-INSTRUCTIONS.md`, `ADVANCED-CONFIG.md`,
  and `package.json`.

The agent-critical matrix remains focused on deterministic CLI, MCP, and file
format behavior. Full upstream parity additionally includes human-facing
browser, terminal UI, editor, shell integration, and git automation behavior.

## Implemented But Now Explicitly Tracked

The compatibility inventory now calls out these upstream surfaces separately
because they are visible feature-set commitments, not incidental options inside
larger task commands:

- Task creation with implementation notes.
- Task editing for notes replacement, notes append, final summary replacement,
  final summary append, and final summary clearing.
- Task editing for acceptance-criteria and Definition-of-Done check state,
  uncheck state, and removal.
- Extended config get/set/list support for `defaultAssignee`, `dateFormat`,
  `includeDatetimeInDates`, `defaultEditor`, `defaultPort`,
  `autoOpenBrowser`, `onStatusChange`, and `zeroPaddedIds`.
- `zeroPaddedIds` generation for top-level task, child task, draft, document,
  and decision IDs.
- Init-time `--task-prefix`, read-only `taskPrefix` config listing, and
  generated task/subtask IDs that respect `prefixes.task`.
- ANSI-rich terminal rendering for non-plain task list, search, board,
  document, decision, and milestone summary output while preserving unstyled
  `--plain` output.
- Shell completion installer for bash, zsh, fish, and PowerShell using
  user-scoped completion paths for the `backlog-py` executable.
- `onStatusChange` shell command execution on status edits, including
  task-level override, upstream-compatible environment variables, and
  non-blocking failure handling.
- Guided `backlog config` wizard for advanced settings and
  Definition-of-Done defaults.

## Remaining Full-Parity Work

| Area | Remaining upstream behavior | Current decision |
| --- | --- | --- |
| Browser UI | `backlog browser`, responsive Kanban, drag-and-drop status changes, rich Markdown editing, mermaid rendering, archive confirmations, settings, live updates | Deferred to browser milestone |
| Browser service | `backlog browser --port <port> --no-open`, service lifecycle, port collisions, logging, shutdown | Deferred to browser milestone |
| Terminal UI | Interactive board, overview TUI, interactive task detail, editor launch, interactive search filters, live filtering | Deferred behind deterministic plain output |
| Extended config effects | Browser/TUI behavior driven by `defaultPort`, `autoOpenBrowser`, `defaultEditor`, and date display preferences | Config read/write and zero-padded ID generation implemented; browser/TUI effects remain deferred |
| Git automation | remote operations, active-branch accuracy behavior, auto-commit, hook bypass | Remote and auto-commit deferred; hook bypass rejected for first cutover |

## Recommended Work Order

1. Keep the oracle manifest and compatibility inventory pinned to the audited
   upstream release before adding new runtime behavior.
2. Decide whether full parity requires the browser UI or whether the project
   should explicitly advertise a headless/agent-focused compatibility scope.
3. If browser parity is in scope, implement it separately from MCP/CLI runtime
   work and require end-to-end browser tests before claiming support.
4. Implement interactive terminal/editor flows only after plain output remains
   stable and covered.
5. Treat auto-commit, remotes, and hook bypass as separate security-sensitive
   milestones with explicit opt-in behavior.
