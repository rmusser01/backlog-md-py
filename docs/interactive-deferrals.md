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
| Advanced config wizard | Interactive TUI | Intentionally deferred | Non-interactive `config get`, `config set`, and DoD default helpers cover agents; the guided wizard is human-facing workflow parity. |
| Editor launch | Interactive TUI | Intentionally deferred | Launching `$EDITOR` is environment-dependent and not needed for non-interactive agent workflows. |
| Extended display/browser config effects | Human-facing config | Intentionally deferred | Config read/write is supported; browser and TUI behavior that consumes `defaultPort`, `autoOpenBrowser`, `defaultEditor`, and date display settings follows the browser/TUI milestones. |
| onStatusChange | Automation hook | Intentionally deferred | Hook execution can run arbitrary commands, so it remains disabled until a dedicated safety design and tests exist. |
| auto-commit | Git automation | Intentionally deferred | Automatic commits hide mutation boundaries from reviewers and are outside the first local-file compatibility gate. |
| hook bypass | Git safety bypass | Rejected for first cutover | Bypassing hooks conflicts with repo safety policy and must not be implemented as part of agent cutover. |
| Remote operations | Git/network behavior | Intentionally deferred | Remote git behavior introduces network and credential effects that are unnecessary for local Backlog.md compatibility. |

## Required Before Enabling Deferred Behavior

Any future implementation of these features must provide:

- A dedicated Backlog task and implementation plan.
- Tests proving the feature is opt-in and does not run during normal CLI, MCP,
  or test execution.
- Clear documentation of environment variables, subprocess behavior, and failure
  handling.
- A security review for any behavior that launches editors, runs hooks, bypasses
  hooks, performs auto-commit, or touches remotes.

## Current Runtime Policy

The Python clone keeps these features out of the first cutover path:

- Plain output is the compatibility contract for agents.
- ANSI color is implemented for non-plain task list/search/board output without
  changing `--plain` output.
- Interactive task/search/config flows remain deferred to human-facing parity work.
- Browser and TUI effects of extended config keys remain deferred.
- `onStatusChange` remains disabled by default.
- auto-commit and remote operations are deferred.
- hook bypass is rejected for first cutover.
- Rich interactive UI behavior remains later human-operator convenience, not an
  agent blocker.
