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
- Non-plain `backlog task <id>` task detail Created/Updated display that
  respects `dateFormat` and `includeDatetimeInDates`, while preserving raw
  `--plain` detail output.
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
- Responsive browser board layout for narrow viewports, including stacked
  header/actions, single-column board flow, constrained dialogs, and mobile
  form actions.
- Browser drag-and-drop status movement backed by the project write lock and
  status validation.
- Browser task detail endpoint and in-page dialog for task metadata,
  description, Acceptance Criteria, Definition of Done, and AC/DoD checklist
  state controls.
- Browser task detail safe Markdown rendering for description, Implementation
  Notes, and Final Summary, including Mermaid fenced blocks rendered by the
  client-side Mermaid loader with strict security settings and escaped fallback
  source text.
- Basic browser task creation through the loopback service and in-page form,
  backed by the project write lock.
- Basic browser task editing through the loopback service and in-page form for
  title, status, description, and Acceptance Criteria replacement, backed by
  the project write lock.
- Browser edit form replacement for assignees, labels, priority, and
  milestone metadata, backed by the project write lock.
- Browser edit form replacement for raw Markdown Implementation Notes and
  Final Summary sections, backed by the project write lock and existing safe
  detail rendering.
- Browser Markdown formatting toolbar for raw Markdown description,
  Implementation Notes, and Final Summary textareas.
- Browser task archiving through a confirmation dialog and locked loopback
  service endpoint.
- Browser task detail checklist controls for Acceptance Criteria and Definition
  of Done check/uncheck state, backed by the project write lock.
- Browser Definition of Done defaults settings dialog and loopback endpoint,
  backed by the project write lock and safe config writer.
- Browser general project settings dialog and loopback endpoint for safe
  non-shell config values, backed by the project write lock and safe config
  writer.
- Browser safe git automation settings dialog and loopback endpoint for
  `remoteOperations`, `checkActiveBranches`, `activeBranchDays`, and
  `autoCommit`; browser writes still reject `onStatusChange` and
  `bypassGitHooks`.
- Browser board live-refresh polling through a deterministic `/api/board`
  revision, allowing open pages to detect external CLI, MCP, or browser-tab
  task changes and reload when no dialog is open.
- Browser board live-refresh Server-Sent Events through `/api/board/events`,
  with `EventSource` reconnect behavior and `/api/board` polling fallback.
- Browser service shutdown transport policy: `/api/board/events` reports
  pending shutdown with a dedicated SSE event and the browser client closes the
  EventSource plus revision polling when shutdown starts.
- Browser service status and guarded local shutdown controls through
  `/api/service/status`, `/api/service/shutdown`, and an in-page Service
  dialog.
- Browser service request logging through a bounded body-free
  `/api/service/requests` endpoint and Service dialog request list.
- Browser service shutdown state through idempotent stop scheduling and
  `/api/service/status` shutdown metadata.

## Remaining Full-Parity Work

| Area | Remaining upstream behavior | Current decision |
| --- | --- | --- |
| Browser UI | full WYSIWYG Markdown editing, shell-hook settings | Basic board service, responsive narrow-viewport layout, drag-and-drop status movement, basic task creation/editing, metadata editing, raw Markdown Implementation Notes/Final Summary editing, Markdown toolbar controls for raw description/notes/summary textareas, archive confirmation, task detail dialogs with safe Markdown and Mermaid rendering, AC/DoD checklist state controls, DoD defaults settings, safe general settings, safe git automation settings, SSE live refresh with polling fallback, and service status/shutdown/logging dialog controls are implemented; full WYSIWYG editing remains deferred, and shell-hook execution plus hook-bypass settings stay CLI-only or explicitly deferred |
| Browser service | future non-SSE persistent transports if introduced | Custom port, no-open, foreground lifecycle, health, service status, guarded local shutdown, idempotent shutdown state, bounded request logging, board JSON with deterministic revisions, SSE revision events with polling fallback, SSE shutdown events with client transport teardown, task create/edit/archive/checklist/detail JSON, and static board snapshot are implemented; any future WebSocket or long-lived non-SSE transport needs its own explicit shutdown policy |
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
