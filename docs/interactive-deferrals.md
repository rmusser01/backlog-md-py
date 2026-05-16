# Interactive And Automation Deferrals

This document records CLI/TUI, automation, and git behavior that is intentionally
outside the first Backlog.md Python agent cutover candidate. These items are not
ignored; they are deferred because local, deterministic, reviewable file
operations are the first compatibility target.

## Deferral Matrix

| Capability | Classification | Agent cutover impact | Decision and reason |
| --- | --- | --- | --- |
| Interactive board | Interactive TUI | Intentionally deferred | The cutover requires deterministic `board` output, not keyboard-driven task movement or terminal UI state. |
| Overview TUI | Interactive TUI | Intentionally deferred | A human dashboard can follow after the core inventory and mutation paths remain stable. |
| Interactive task view/editor | Interactive TUI | Intentionally deferred | Plain `task <id> --plain` output covers agents; task-detail keybindings and editor launch need terminal/editor integration tests. |
| Interactive search filters | Interactive TUI | Intentionally deferred | Deterministic search output covers agents; live filtering and refinement controls need terminal UI tests. |
| Editor launch | Interactive TUI | Intentionally deferred | Launching `$EDITOR` is environment-dependent and not needed for non-interactive agent workflows. |
| Extended display/TUI config effects | Human-facing config | Partially deferred | Config read/write is supported and browser `defaultPort`/`autoOpenBrowser` behavior is implemented; TUI behavior that consumes `defaultEditor` and date display settings follows the TUI milestone. |
| hook bypass | Git safety bypass | Rejected for first cutover | Bypassing hooks conflicts with repo safety policy and must not be implemented as part of agent cutover. |
| Remote operations | Git/network behavior | Intentionally deferred | Remote git behavior introduces network and credential effects that are unnecessary for local Backlog.md compatibility. |

## Required Before Enabling Deferred Behavior

Any future implementation of these features must provide:

- A dedicated Backlog task and implementation plan.
- Tests proving the feature is opt-in and does not run during normal CLI, MCP,
  or test execution.
- Clear documentation of environment variables, subprocess behavior, and failure
  handling for any process-launching behavior.
- A security review for any behavior that launches editors, bypasses hooks,
  performs auto-commit, or touches remotes.

## Current Runtime Policy

The Python clone keeps these features out of the first cutover path:

- Plain output is the compatibility contract for agents.
- ANSI color is implemented for non-plain task list/search/board output without
  changing `--plain` output.
- Interactive task/search flows remain deferred to human-facing parity work.
- `backlog config` now provides the guided config wizard for human operators.
- Browser `defaultPort` and `autoOpenBrowser` effects are implemented for the
  read-only browser service; remaining TUI effects of extended config keys stay
  deferred.
- `onStatusChange` is supported only when explicitly configured; task-level
  hooks override the project hook, and hook failures do not block status writes.
- `autoCommit` is opt-in and local-only. It runs after project write mutations
  when the project had no pre-existing git changes, uses fixed `git` argv
  without a shell, does not push or pull remotes, and does not bypass hooks.
- Remote operations are deferred.
- hook bypass is rejected for first cutover.
- Rich interactive UI behavior remains later human-operator convenience, not an
  agent blocker.
