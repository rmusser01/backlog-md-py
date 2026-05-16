# Interactive And Automation Deferrals

This document records CLI/TUI, automation, and git behavior that either remains
outside the first Backlog.md Python agent cutover candidate or has graduated
from that deferral list. Items are tracked here because local, deterministic,
reviewable file operations are the first compatibility target.

## Deferral Matrix

| Capability | Classification | Agent cutover impact | Decision and reason |
| --- | --- | --- | --- |
| Interactive board | Interactive TUI | Intentionally deferred | The cutover requires deterministic `board` output, not keyboard-driven task movement or terminal UI state. |
| Overview TUI | Interactive TUI | Intentionally deferred | A human dashboard can follow after the core inventory and mutation paths remain stable. |
| Interactive task view/editor | Interactive TUI | Implemented | Non-plain `task <id>` renders a human task detail view and interactive terminals can press `E` to launch the configured editor under the project write lock. |
| Interactive search filters | Interactive TUI | Implemented | Non-plain `search` renders a human filter panel; interactive terminals can refine by status, priority, result type, or modified file while preserving `--plain`. |
| Editor launch | Interactive TUI | Implemented for task view | `defaultEditor`, `VISUAL`, or `EDITOR` is split into argv without a shell and receives the task file path. |
| Extended display/TUI config effects | Human-facing config | Partially deferred | Config read/write is supported and browser `defaultPort`/`autoOpenBrowser` behavior is implemented; task view consumes `defaultEditor`; date display preferences follow the remaining TUI milestone. |
| hook bypass | Git safety bypass | Rejected for first cutover | Bypassing hooks conflicts with repo safety policy and must not be implemented as part of agent cutover. |
| Remote operations | Git/network behavior | Intentionally deferred | Remote git behavior introduces network and credential effects that are unnecessary for local Backlog.md compatibility. |

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
- ANSI color is implemented for non-plain task list/search/board output without
  changing `--plain` output.
- Interactive task view/editor is implemented for human operators without
  changing `task <id> --plain`.
- Interactive search filters are implemented for human operators without
  changing `search <query> --plain`.
- Interactive board flow remains deferred to human-facing parity work.
- `backlog config` now provides the guided config wizard for human operators.
- Browser `defaultPort` and `autoOpenBrowser` effects are implemented for the
  loopback browser service; remaining TUI effects of extended config keys stay
  deferred.
- `onStatusChange` is supported only when explicitly configured; task-level
  hooks override the project hook, and hook failures do not block status writes.
- `autoCommit` is opt-in and local-only. It runs after project write mutations
  when the project had no pre-existing git changes, uses fixed `git` argv
  without a shell, does not push or pull remotes, and does not bypass hooks.
- Remote operations are deferred.
- hook bypass is rejected for first cutover.
- Remaining rich interactive UI behavior is later human-operator convenience,
  not an agent blocker.
