# Changelog

## Unreleased

### Changed

- Vendor Mermaid (v10.9.1, MIT) and serve it locally from the browser board so
  no third-party request is made by default. Override the source with
  `BACKLOG_PY_BROWSER_MERMAID_URL` (a `.mjs` URL loads as an ES module, any
  other URL as a classic script) or set it to an empty string to disable
  diagram rendering.
- Add a `verification: "self-declared"` field to the compatibility report and
  each of its items to make explicit that parity statuses are a maintained
  declaration rather than automated per-item verification. Existing report
  fields (including `agent_cutover_ready` and the release gates) are unchanged.

## 1.0.0 - 2026-06-27 (Stable)

### Changed

- Promote `backlog-md-py` to the stable 1.0 support contract for the audited
  local-file CLI, Python helper, MCP, daemon, browser-board, and TUI surfaces.
- Keep the disposable SQLite read index outside the stable API while preserving
  Markdown files as the authoritative task source.
- Clarify that full browser parity claims still require fresh browser release
  evidence even when audited browser feature coverage is implemented.
- Attach fresh browser release evidence for rich-edit round trip and
  desktop/mobile screenshot coverage.

## 0.3.0 - 2026-06-20 (Beta)

### Added

- Improve MCP project-discovery resources and stdio initialization guidance so
  agents can find project roots and workflow instructions without relying on an
  MCP SDK.
- Add a release tag and publish checklist covering version, validation, tag,
  GitHub Release, PyPI, and rollback gates.
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

- Refresh the audited upstream compatibility baseline to `backlog.md@1.45.2`
  and document that the upstream delta is packaging-only for the Python clone's
  audited CLI/MCP/browser surfaces.
- Track MCP `backlog://init-required` explicitly in the compatibility inventory
  and parity docs.
- Track MCP `project_status(project, recentLimit=5)` explicitly in the
  compatibility inventory and parity docs.
- Track MCP `task_edit(..., clearPriority=False, ...)` explicitly in the
  compatibility inventory and parity docs.
- Track MCP `task_edit(..., clearMilestone=False, ...)` explicitly in the
  compatibility inventory, workflow resource, tools/list schema, and parity docs.
- Advertise MCP `task_edit` `ordinal` support in the tools/list schema.
- Advertise MCP `task_edit` `milestone` support in the tools/list schema.
- Advertise MCP `task_edit` `references` support in the tools/list schema.
- Advertise MCP `task_edit` `addReferences` support in the tools/list schema.
- Advertise MCP `task_edit` `documentation` support in the tools/list schema.
- Advertise MCP `task_edit` `addDocumentation` support in the tools/list schema.
- Expand SDK-free MCP tools/list schemas for task filters, task mutation
  metadata/checklist/status controls, document optional fields, and milestone
  optional flags.
- Advertise MCP tools/list schemas for handler-accepted aliases, including
  snake_case task fields and legacy task edit aliases.
- Track MCP `task_create(..., id=None, ...)` explicitly in the compatibility
  inventory, workflow resource, and parity docs.
- Advertise MCP `task_create` explicit `id` support in the tools/list schema.
- Track MCP `task_create(..., status=None, ...)` explicitly in the compatibility
  inventory, workflow resource, tools/list schema, and parity docs.
- Advertise MCP `task_create` `parentTaskId` support in the tools/list schema.
- Advertise MCP `task_create` `milestone` support in the tools/list schema.
- Advertise MCP `task_create` `ordinal` support in the tools/list schema.
- Advertise MCP `task_create` `references` support in the tools/list schema.
- Advertise MCP `task_create` `documentation` support in the tools/list schema.
- Advertise MCP `task_create` `modifiedFiles` support in the tools/list schema.
- Advertise MCP `task_create` `implementationPlan` support in the tools/list schema.
- Advertise MCP `task_create` `finalSummary` support in the tools/list schema.
- Track task create/list `-p` parent alias explicitly in the compatibility
  inventory and parity docs.
- Track task create/edit `--dependency` alias explicitly in the compatibility
  inventory and parity docs.
- Track task create Definition-of-Done long options explicitly in the
  compatibility inventory and parity docs.
- Track task list/create/edit `-m` milestone alias explicitly in the
  compatibility inventory and parity docs.
- Track task list/create/edit `--assignee` and `--label` aliases explicitly in
  the compatibility inventory and parity docs.
- Track task list/create/edit `-s/--status` aliases explicitly in the
  compatibility inventory and parity docs.
- Track task create/edit `--desc` description alias explicitly in the
  compatibility inventory and parity docs.
- Track task create/edit `--description` description alias explicitly in the
  compatibility inventory and parity docs.
- Track task create/edit `--acceptance-criteria` alias explicitly in the
  compatibility inventory and parity docs.
- Track task edit `--definition-of-done-add` alias explicitly in the
  compatibility inventory and parity docs.
- Track document `-p/--path` and `-t/--type` aliases explicitly in the
  compatibility inventory and parity docs.
- Track `task edit --dod` explicitly in the compatibility inventory and parity
  docs.
- Record the post-TUI beta validation refresh and document isolated daemon
  state for MCP cutover smoke checks.
- Print the audited upstream Backlog.md baseline directly in `compat status`
  plain and JSON output.
- Show release-gate evidence errors in plain `compat status` output instead of
  requiring JSON output to diagnose invalid browser release evidence.
- Track `doc create --title` explicitly in the compatibility inventory and
  parity docs.
- Track `doc list [query]` explicitly in the compatibility inventory and
  parity docs.
- Track init setup options explicitly in the compatibility inventory and parity
  docs.
- Track milestone mutation options explicitly in the compatibility inventory
  and parity docs.
- Track `draft create -s/--status` compatibility explicitly in the
  compatibility inventory and parity docs.
- Track `draft create` long aliases for description, assignee, label, and
  status explicitly in the compatibility inventory and parity docs.
- Track `decision create --status` explicitly in the compatibility inventory
  and parity docs.
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

### Fixed

- Preserve intentionally empty task Description sections in CLI and browser
  task detail views after notes-only edits, instead of falling back to the full
  sectioned Markdown body.

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
