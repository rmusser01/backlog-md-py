# Documentation

Use this index to find the right level of detail for trying, integrating,
validating, or contributing to `backlog-md-py`.

## Try It

- [Getting started](getting-started.md): install the package, create a scratch
  project, run common read-only commands, start the browser board, and learn the
  basic mutation safety rules.

## Integrate Agents Or MCP

- [Integration guide](integration.md): CLI entry points, Python helper examples,
  SDK-free MCP stdio, and consumer validation notes.
- [Singleton daemon](singleton-daemon.md): process reuse for multi-agent setups,
  daemon lifecycle commands, Codex-style MCP configuration, and rollback paths.

## Validate Migration

- [Cutover validation](cutover-validation.md): package checks, copied-repository
  mutation smoke, MCP smoke, and the criteria for pointing a project at
  `backlog-md-py`.
- [Cutover validation result](cutover-validation-results-2026-05-13.md): the
  first completed agent-critical validation record.

## Check Release Status

- [Stability policy](stability-policy.md): the beta supported contract, change
  policy, compatibility baseline, and release gate.
- [Beta release readiness](release-readiness-beta-2026-05-23.md): the 0.2.0
  beta promotion checklist and validation record.
- [Post-TUI beta validation](release-readiness-post-tui-2026-05-29.md): the
  package, MCP/daemon, TUI-extra, compatibility, and copied-repository smoke
  refresh after optional TUI settings merged.
- [Changelog](../CHANGELOG.md): release notes by package version.

## Understand Parity Or Release Readiness

- [Agent-critical parity](agent-critical-parity.md): the local-file CLI, MCP,
  and helper surface required for the first agent cutover gate.
- [Upstream feature parity](upstream-feature-parity.md): the broader audited
  upstream Backlog.md feature inventory and current implementation state.
- [Browser parity](browser-parity.md): browser board requirements, implemented
  interactions, release-evidence gates, and future full-WYSIWYG scope.
- [Browser release validation](browser-release-validation.md): release evidence
  rules for browser readiness claims.

## Contribute Or Release

- [Contributing guide](../CONTRIBUTING.md): local development setup, validation
  commands, packaging checks, and release expectations.
