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
- Add shared Definition of Done defaults normalization for config, MCP, and TUI
  writes.
- Expand generated agent instruction blocks with search-before-create,
  task-lifecycle, MCP resource, CLI fallback, and singleton-daemon guidance.
- Add an opt-in disposable SQLite read index for task list, search, and board
  reads while keeping Markdown as the source of truth.

### Changed

- Record the post-TUI beta validation refresh and document isolated daemon
  state for MCP cutover smoke checks.
- Print the audited upstream Backlog.md baseline directly in `compat status`
  plain and JSON output.
- Show release-gate evidence errors in plain `compat status` output instead of
  requiring JSON output to diagnose invalid browser release evidence.
- Track init setup options explicitly in the compatibility inventory and parity
  docs.
- Track milestone mutation options explicitly in the compatibility inventory
  and parity docs.
- Track `draft create -s/--status` compatibility explicitly in the
  compatibility inventory and parity docs.
- Track `task create --id` explicitly in the compatibility inventory and
  parity docs.
- Track `task edit --title`, `task edit -s`, `task edit -d`, and
  `task edit --dep` explicitly in the compatibility inventory and parity docs.
- Track `task edit --append-plan` and `task edit --clear-plan` explicitly in
  the compatibility inventory and parity docs.
- Clarify that browser feature coverage is implemented for the audited baseline
  while full browser release readiness remains evidence-gated.
- Move browser board HTML, CSS, and JavaScript into package-managed template
  and asset resources while preserving the dependency-free, no-build browser
  service.
- Require browser release-evidence manifests to include schema version,
  generation date, upstream audit baseline, command provenance, freshness
  policy, and portable relative artifact paths.
- Reject non-string Definition of Done defaults without partially mutating
  project configuration.

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
