# Interactive And Automation Deferrals

This document records CLI/TUI, automation, and git behavior that either remains
outside the first Backlog.md Python agent cutover candidate or has graduated
from that deferral list. Items are tracked here because local, deterministic,
reviewable file operations are the first compatibility target.

## Deferral Matrix

| Capability | Classification | Agent cutover impact | Decision and reason |
| --- | --- | --- | --- |
| Interactive board | Interactive TUI | Implemented | Interactive terminals can view, edit, or move tasks from `board`; non-interactive output remains deterministic. |
| Overview TUI | Interactive TUI | Implemented | Interactive terminals render a project statistics dashboard from `overview`; non-interactive output remains deterministic. |
| Interactive task view/editor | Interactive TUI | Implemented | Non-plain `task <id>` renders a human task detail view and interactive terminals can press `E` to launch the configured editor under the project write lock. |
| Interactive search filters | Interactive TUI | Implemented | Non-plain `search` renders a human filter panel; interactive terminals can refine by status, priority, result type, or modified file while preserving `--plain`. |
| Editor launch | Interactive TUI | Implemented for task view and board | `defaultEditor`, `VISUAL`, or `EDITOR` is split into argv without a shell and receives the task file path. |
| Extended display/TUI config effects | Human-facing config | Partially deferred | Config read/write is supported and browser `defaultPort`/`autoOpenBrowser` behavior is implemented; task view consumes `defaultEditor`; date display preferences follow the remaining TUI milestone. |
| hook bypass | Git safety bypass | Rejected for first cutover | Bypassing hooks conflicts with repo safety policy and must not be implemented as part of agent cutover. |
| Remote operations | Git/network behavior | Implemented as fetch-only | When `remoteOperations` and `checkActiveBranches` are enabled, repository reads run a best-effort `git fetch --all --prune` to refresh remote-tracking refs without pulling, merging, pushing, or changing the working tree. |

## Required Before Enabling Deferred Behavior

Any future implementation of these features must provide:

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
- Interactive board view/edit/move controls are implemented for human operators
  without changing non-interactive `board` output.
- Interactive overview dashboard output is implemented for human operators
  without changing non-interactive `overview` output.
- `backlog config` now provides the guided config wizard for human operators.
- Browser `defaultPort` and `autoOpenBrowser` effects are implemented for the
  loopback browser service; the browser also exposes basic task creation,
  basic task editing, task archive confirmation, and read-only task detail
  inspection; remaining TUI effects of extended config keys stay deferred.
- `onStatusChange` is supported only when explicitly configured; task-level
  hooks override the project hook, and hook failures do not block status writes.
- `autoCommit` is opt-in and local-only. It runs after project write mutations
  when the project had no pre-existing git changes, uses fixed `git` argv
  without a shell, does not push or pull remotes, and does not bypass hooks.
- Remote operations are implemented as best-effort fetch-only remote-tracking
  ref refreshes for read-time branch accuracy; `remoteOperations: false` keeps
  repository reads offline.
- hook bypass is rejected for first cutover.
- Remaining rich interactive UI behavior is later human-operator convenience,
  not an agent blocker.
