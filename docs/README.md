# Documentation

This index is organized by what you are trying to do with `backlog-md-py`.

## New Users

- [Getting started](getting-started.md): install the package, create a scratch
  project, run the first commands, and learn the mutation safety rules.
- [Integration guide](integration.md): command examples for the CLI, Python
  helpers, MCP stdio, browser board, TUI, and compatibility report.
- [Stability policy](stability-policy.md): what beta support means and what is
  covered by the supported contract.

## Agent And MCP Integrators

- [Integration guide](integration.md): CLI entry points, Python helper examples,
  SDK-free MCP stdio behavior, and consuming-project validation notes.
- [Singleton daemon](singleton-daemon.md): process reuse for multi-agent setups,
  daemon lifecycle commands, Codex-style MCP configuration, runtime files, and
  rollback paths.
- [Cutover validation](cutover-validation.md): copied-repository mutation smoke,
  MCP smoke, package checks, and the criteria for pointing a project at
  `backlog-md-py`.

## Contributors

- [Architecture guide](architecture.md): source layout, data flow, adapters,
  runtime state, compatibility inventory, and safety invariants.
- [Contributing guide](../CONTRIBUTING.md): local development setup, workflow,
  validation commands, packaging checks, and release expectations.
- [Agent-critical parity](agent-critical-parity.md): the CLI, MCP, helper, and
  local-file surface that protects agent cutover readiness.

## Maintainers And Parity Reviewers

- [Upstream feature parity](upstream-feature-parity.md): audited upstream
  Backlog.md feature inventory and current implementation state.
- [Browser parity](browser-parity.md): browser board requirements, implemented
  interactions, release-evidence gates, and future full-WYSIWYG scope.
- [Browser release validation](browser-release-validation.md): release evidence
  rules for browser readiness claims.
- [Cutover validation result](cutover-validation-results-2026-05-13.md): the
  first completed agent-critical validation record.
- [Beta release readiness](release-readiness-beta-2026-05-23.md): the 0.2.0
  beta promotion checklist and validation record.
- [Post-TUI beta validation](release-readiness-post-tui-2026-05-29.md): the
  package, MCP/daemon, TUI-extra, compatibility, and copied-repository smoke
  refresh after optional TUI settings merged.
- [Changelog](../CHANGELOG.md): release notes by package version.
