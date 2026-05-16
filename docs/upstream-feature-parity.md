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
- Opt-in local `autoCommit` after project write mutations, with dirty-worktree
  protection, no remote operations, and no hook bypass.
- Read-only loopback `backlog browser` service with `--port <port>`,
  `--no-open`, config-driven default port and auto-open behavior, health and
  board JSON endpoints, and a static board snapshot.
- Browser drag-and-drop status movement backed by the project write lock and
  status validation.

## Remaining Full-Parity Work

| Area | Remaining upstream behavior | Current decision |
| --- | --- | --- |
| Browser UI | responsive Kanban polish, rich Markdown editing, mermaid rendering, archive confirmations, settings, live updates | Basic board service and drag-and-drop status movement are implemented; richer browser UI remains deferred |
| Browser service | advanced service logging and live-update shutdown behavior | Custom port, no-open, foreground lifecycle, health, board JSON, and static board snapshot are implemented |
| Terminal UI | Interactive board, overview TUI, interactive task detail, editor launch, interactive search filters, live filtering | Deferred behind deterministic plain output |
| Extended config effects | TUI behavior driven by `defaultEditor` and date display preferences | Browser `defaultPort` and `autoOpenBrowser` effects are implemented; TUI effects remain deferred |
| Git automation | remote operations, active-branch accuracy behavior, hook bypass | Local auto-commit implemented; remote operations deferred; hook bypass rejected for first cutover |

## Recommended Work Order

1. Keep the oracle manifest and compatibility inventory pinned to the audited
   upstream release before adding new runtime behavior.
2. Decide whether full parity requires the browser UI or whether the project
   should explicitly advertise a headless/agent-focused compatibility scope.
3. If browser parity is in scope, implement it separately from MCP/CLI runtime
   work and require end-to-end browser tests before claiming support.
4. Implement interactive terminal/editor flows only after plain output remains
   stable and covered.
5. Treat remotes and hook bypass as separate security-sensitive milestones with
   explicit opt-in behavior.
