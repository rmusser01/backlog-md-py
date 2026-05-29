# Changelog

## Unreleased

### Added

- Improve MCP project-discovery resources and stdio initialization guidance so
  agents can find project roots and workflow instructions without relying on an
  MCP SDK.
- Add explicit `backlog-py init --no-git` support for filesystem-only scratch
  projects while keeping the default init path Git-aware.
- Add deterministic fuzzy search matching for CLI, MCP, TUI, document, and
  decision search surfaces while preserving stable result ordering.
- Add a portable `backlog-py compat evidence-template` command and CI release
  evidence artifacts for browser readiness validation.

### Changed

- Record the post-TUI beta validation refresh and document isolated daemon
  state for MCP cutover smoke checks.
- Require browser release-evidence manifests to include schema version,
  generation date, upstream audit baseline, command provenance, freshness
  policy, and portable relative artifact paths.

## 0.2.0 - 2026-05-23 (Beta)

### Changed

- Promote `backlog-md-py` from alpha to beta package metadata.
- Document the beta supported contract and release gate in
  `docs/stability-policy.md`.
- Add a 0.2.0 beta release-readiness record.
- Route README and documentation index readers to the stability policy,
  changelog, and release-readiness evidence.
- Keep install guidance split between released PyPI packages and unreleased
  GitHub commits.
- Add an optional Textual Kanban board through `backlog-py tui` behind the
  `tui` extra.
- Add Vim-style TUI navigation aliases and direct adjacent-status task moves.
- Show TUI dependency status counts for done, open, and missing task
  dependencies.
- Add a TUI dependency shortcut to jump to the selected task's first visible
  dependency.
- Make repeated TUI dependency jumps cycle through additional visible
  dependencies for the same source task.
- Add a TUI dependent shortcut to jump to the first visible task that depends
  on the selection.
- Make repeated TUI dependent jumps cycle through additional visible dependents
  for the same source task.
- Add TUI dependency-navigation history with `backspace`.
- Reset TUI dependency-cycle state after manual task selection.
- Avoid duplicate TUI dependency-navigation history entries during repeated
  cycles.
- Clear the focused TUI filter with `escape` and return focus to the board.
- Add TUI selected-task metadata editing for title, status, description,
  priority, assignees, labels, milestone, and dependencies.
- Add a TUI read-only Markdown preview for the selected task.
- Add TUI global search across tasks, documents, and decisions.
- Add TUI safe project settings editing for the browser-compatible non-shell
  settings allowlist.
- Add TUI Definition of Done defaults editing.
- Add TUI Acceptance Criteria and Definition of Done checklist toggles.

### Validation

- Release candidates must pass the beta release gate in
  `docs/stability-policy.md` before tagging.
