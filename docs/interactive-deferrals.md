# Interactive And Automation Deferrals

This document records CLI/TUI, automation, and git behavior that either remains
outside the first Backlog.md Python agent cutover candidate or has graduated
from that deferral list. Items are tracked here because local, deterministic,
reviewable file operations are the first compatibility target.

## Deferral Matrix

| Capability | Classification | Agent cutover impact | Decision and reason |
| --- | --- | --- | --- |
| Prompt-style board controls | Interactive CLI | Implemented | Interactive terminals can view, edit, or move tasks from `board`; non-interactive output remains deterministic. |
| Optional Textual Kanban board | Optional TUI extra | Implemented | `backlog-py tui` provides keyboard board navigation, task detail, global search, Markdown preview, create/edit/move/archive actions, checklist toggles, configured-editor launch, and board-local filters without making Textual a base dependency. |
| Overview TUI | Interactive TUI | Implemented | Interactive terminals render a project statistics dashboard from `overview`; non-interactive output remains deterministic. |
| Interactive task view/editor | Interactive TUI | Implemented | Non-plain `task <id>` renders a human task detail view and interactive terminals can press `E` to launch the configured editor under the project write lock. |
| Interactive search filters | Interactive TUI | Implemented | Non-plain `search` renders a human filter panel; interactive terminals can refine by status, priority, result type, or modified file while preserving `--plain`. |
| Editor launch | Interactive TUI | Implemented for task view and board | `defaultEditor`, `VISUAL`, or `EDITOR` is split into argv without a shell and receives the task file path. |
| Extended display/TUI config effects | Human-facing config | Implemented | Config read/write is supported, browser `defaultPort`/`autoOpenBrowser` behavior is implemented, task view consumes `defaultEditor`, and non-plain task detail respects `dateFormat` plus `includeDatetimeInDates` for Created/Updated display. |
| hook bypass | Git safety bypass | Implemented for opt-in auto-commit | `bypassGitHooks` only adds `--no-verify` to the local `autoCommit` commit argv when explicitly enabled; hooks still run by default. |
| Remote operations | Git/network behavior | Implemented as fetch-only plus read-only snapshots | When `remoteOperations` and `checkActiveBranches` are enabled, repository reads run a best-effort `git fetch --all --prune` to refresh remote-tracking refs, then load recent branch task snapshots without pulling, merging, pushing, checking out branches, or changing the working tree. |

## Required Before Enabling Deferred Behavior

Any future implementation of additional deferred features must provide:

- A dedicated Backlog task and implementation plan.
- Tests proving the feature is opt-in and does not run during normal CLI, MCP,
  or test execution.
- Clear documentation of environment variables, subprocess behavior, and failure
  handling for any remaining process-launching behavior.
- A security review for any behavior that bypasses hooks, performs new
  automation, or touches remotes.

## Current Runtime Policy

The Python clone keeps these features out of the first cutover path:

- Plain output is the compatibility contract for agents.
- ANSI color is implemented for non-plain task list/search/board output while
  preserving task/search `--plain` output.
- Interactive task view/editor is implemented for human operators without
  changing `task <id> --plain`.
- Interactive search filters are implemented for human operators without
  changing `search <query> --plain`.
- Prompt-style board view/edit/move controls are implemented for human
  operators without changing non-interactive `board` output.
- The optional Textual board is available through `backlog-py tui` after
  installing `backlog-md-py[tui]`. It is a human-facing workflow, not the
  automation contract for agents. It can edit selected-task title, status,
  description, and metadata, can render a read-only Markdown preview for the
  selected task, can search tasks/documents/decisions, and can toggle
  selected-task Acceptance Criteria and Definition of Done checklist items
  through the same safe mutation paths as CLI and MCP.
- Interactive overview dashboard output is implemented for human operators
  without changing non-interactive `overview` output.
- `backlog config` now provides the guided config wizard for human operators.
- Non-plain task detail now displays Created/Updated dates using the configured
  `dateFormat` and `includeDatetimeInDates` preferences while preserving raw
  `task <id> --plain` output.
- Browser `defaultPort` and `autoOpenBrowser` effects are implemented for the
  loopback browser service; the browser also exposes basic task creation,
  basic task editing, task archive confirmation, task detail inspection, and
  AC/DoD checklist state controls.
- `onStatusChange` is supported only when explicitly configured; task-level
  hooks override the project hook, and hook failures do not block status writes.
- `autoCommit` is opt-in and local-only. It runs after project write mutations
  when the project had no pre-existing git changes, uses fixed `git` argv
  without a shell, and does not push or pull remotes. Git hooks run by default;
  `bypassGitHooks: true` only adds `--no-verify` to this local auto-commit.
- Remote operations are implemented as best-effort fetch-only remote-tracking
  ref refreshes plus read-only task snapshots from recent active branches;
  `remoteOperations: false` keeps repository reads offline and limited to local
  branch refs.
- hook bypass is implemented only for explicit local auto-commit opt-in.
- Remaining rich interactive TUI settings are later human-operator convenience,
  not an agent blocker.
