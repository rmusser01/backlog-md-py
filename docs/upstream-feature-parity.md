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
- Task and draft creation now write upstream-compatible `created_date`
  frontmatter, and task edits write `updated_date` only when content or the
  task file path changes.
- `includeDatetimeInDates: false` writes date-only `created_date` and
  `updated_date` frontmatter for task and draft mutations.
- Upstream-style plain task and draft detail output with file path, status
  icon, created/updated dates, description, and checklist sections.
- Guided `backlog config` wizard for advanced settings and
  Definition-of-Done defaults.
- Non-plain `backlog task <id>` task detail view with `defaultEditor`/`VISUAL`/
  `EDITOR` launch from interactive terminals under the project write lock.
- Non-plain `backlog search` interactive filter panel with status, priority,
  result-type, and modified-file refinement while preserving `--plain`.
- Interactive `backlog board` view/edit/move controls while preserving
  deterministic non-interactive board output.
- Interactive `backlog overview` project statistics dashboard while preserving
  deterministic non-interactive overview output.
- Opt-in local `autoCommit` after project write mutations, with dirty-worktree
  protection, no remote push/pull behavior, and no hook bypass.
- Fetch-only remote operations: when `remoteOperations` and
  `checkActiveBranches` are enabled, repository reads refresh remote-tracking
  refs with `git fetch --all --prune` without pulling, merging, or pushing.
- Loopback `backlog browser` service with `--port <port>`,
  `--no-open`, config-driven default port and auto-open behavior, health and
  board JSON endpoints, and a static board snapshot.
- Browser drag-and-drop status movement backed by the project write lock and
  status validation.
- Browser task detail endpoint and in-page dialog for task metadata,
  description, Acceptance Criteria, Definition of Done, and AC/DoD checklist
  state controls.
- Browser task detail safe Markdown rendering for description, Implementation
  Notes, and Final Summary. Mermaid fenced blocks remain escaped code until a
  dedicated browser rendering milestone.
- Basic browser task creation through the loopback service and in-page form,
  backed by the project write lock.
- Basic browser task editing through the loopback service and in-page form for
  title, status, description, and Acceptance Criteria replacement, backed by
  the project write lock.
- Browser task archiving through a confirmation dialog and locked loopback
  service endpoint.
- Browser task detail checklist controls for Acceptance Criteria and Definition
  of Done check/uncheck state, backed by the project write lock.
- Browser Definition of Done defaults settings dialog and loopback endpoint,
  backed by the project write lock and safe config writer.
- Browser general project settings dialog and loopback endpoint for safe
  non-shell, non-git config values, backed by the project write lock and safe
  config writer.
- Browser board live-refresh polling through a deterministic `/api/board`
  revision, allowing open pages to detect external CLI, MCP, or browser-tab
  task changes and reload when no dialog is open.

## Remaining Full-Parity Work

| Area | Remaining upstream behavior | Current decision |
| --- | --- | --- |
| Browser UI | responsive Kanban polish, rich Markdown editing, executable mermaid rendering, git/shell settings, richer live-update transports | Basic board service, drag-and-drop status movement, basic task creation/editing, archive confirmation, task detail dialogs with safe Markdown rendering, AC/DoD checklist state controls, DoD defaults settings, safe general settings, and polling-based live refresh are implemented; richer browser editing and executable Mermaid rendering remain deferred, and git automation plus shell-hook settings stay CLI-only or explicitly deferred |
| Browser service | advanced service logging and shutdown behavior | Custom port, no-open, foreground lifecycle, health, board JSON with deterministic revisions, task create/edit/archive/checklist/detail JSON, static board snapshot, and polling refresh are implemented |
| Extended config effects | TUI behavior driven by date display preferences | Browser `defaultPort` and `autoOpenBrowser` effects, task-view `defaultEditor`, and `includeDatetimeInDates` timestamp precision are implemented; remaining TUI display effects are deferred |
| Git automation | active-branch accuracy behavior beyond remote ref freshness, hook bypass | Local auto-commit and fetch-only remote operations implemented; hook bypass rejected for first cutover |

## Recommended Work Order

1. Keep the oracle manifest and compatibility inventory pinned to the audited
   upstream release before adding new runtime behavior.
2. Decide whether full parity requires the browser UI or whether the project
   should explicitly advertise a headless/agent-focused compatibility scope.
3. If browser parity is in scope, implement it separately from MCP/CLI runtime
   work and require end-to-end browser tests before claiming support.
4. Treat remotes and hook bypass as separate security-sensitive milestones with
   explicit opt-in behavior.
