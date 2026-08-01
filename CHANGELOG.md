# Changelog

## Unreleased

### Fixed

- Task reads no longer follow a symlink out of the project. A file such as
  `backlog/tasks/leak.md` pointing anywhere readable was parsed and its contents
  surfaced as a task on the browser board, in `task list`, in search, and through
  the MCP read tools. Writes were already containment-checked; reads were not.
  A link that stays inside its bucket still works, so relocating the whole
  directory onto another volume is unaffected. (#155)

### Changed

- The interactive-editor flow is now shared by the CLI and the TUI
  (`backlog_py.core.editing`). It was implemented twice, and both copies had to
  be fixed for the same four data-loss defects independently. Behaviour on both
  surfaces is unchanged. (#154)

## 2.0.0 - 2026-08-01

This release fixes data-corruption, security, and performance defects found in a
full review of the 1.0.1 codebase. Several fixes necessarily change documented
behaviour, so it opens a new major line per `docs/stability-policy.md`.

### Breaking Changes

Each of these changes an interface covered by the stable support contract. Most
exist because the previous behaviour was unsafe.

- **`onStatusChange` in a task file's own frontmatter no longer runs.** Task
  markdown arrives from clones, branches, and pull requests, so a status change
  — including a board drag-and-drop — could execute attacker-authored shell.
  *Migration:* set `taskFrontmatterStatusCallbacks: true` in `backlog/config.yml`
  to restore it. Config-level `onStatusChange` is unaffected, and a command
  passed explicitly to `task create`/`task edit --on-status-change` still runs.

  Be clear about what this does and does not protect. `backlog/config.yml`
  arrives through the same channel a task file does, so a hostile *repository*
  can still set `onStatusChange` at config level, or flip this key to `true`.
  The gate defends against the common case — a pull request or branch that only
  touches `backlog/tasks/` — not against cloning and operating on a repository
  you do not trust. Treat a clone's `backlog/config.yml` the way you would treat
  any other executable content in it.
- **`daemon start`/`daemon run` reject a non-loopback `--host`.** The daemon
  serves the full MCP JSON-RPC surface, including every write tool, so binding a
  LAN address handed project write access to the network.
  *Migration:* pass `--allow-remote` to opt in deliberately.
- **The browser board requires an `Origin` header on mutating requests, and a
  loopback `Host` on every request.** Previously any local process could create,
  edit, and archive tasks, rewrite `config.yml`, and shut the service down with
  no credentials; and a DNS-rebinding page could read the entire backlog.
  *Migration:* non-browser clients must send `Origin` and a loopback `Host`.
  Browsers already do both.
- **MCP tools reject unknown arguments.** Schemas now set
  `additionalProperties: false` wherever the handler has a fixed signature, so
  an argument that was previously accepted and silently ignored returns `-32602`.
  *Migration:* remove extra fields; the schema is now authoritative.
- **MCP `resources/read` returns `-32002` for an unknown URI** instead of
  `-32602`, matching the MCP specification.
- **`search --type` with no supported values is a usage error (exit 2)** instead
  of exiting 0 having silently searched nothing.
- **`--modified-files a,b` requires every value**, matching every other list
  filter. It previously matched a task touching any one of them.
- **`orchestration record-run --plain` emits a tab-separated record.** Its plain
  output was previously identical to the default rendering.
- **Run history is capped at 50 entries per task.** Older entries are dropped
  behind a marker recording how many; a replay whose idempotency key has aged
  out returns a conflict rather than silently re-running the work.
- **Python helper:** the document, decision, draft, and milestone services now
  raise at construction when their directory escapes the project via a symlink.

### Added

- Add a compatibility extension that discovers H1-first documents without
  title frontmatter and teaches generated agent instructions the conditional,
  incident-backed lesson workflow.

### Changed

- **Behaviour change:** an `onStatusChange` command carried in a *task file's own
  frontmatter* is no longer executed by default. Task markdown travels in from
  clones, branches, and pull requests, so a status change (including a board
  drag-and-drop) could run attacker-authored shell. Set
  `taskFrontmatterStatusCallbacks: true` in `backlog/config.yml` to restore the
  previous behaviour. Config-level `onStatusChange` is unaffected, and a command
  passed explicitly to `task create`/`task edit --on-status-change` still runs.
- Automatic release tagging now waits for CI to succeed on the exact commit
  before tagging and publishing to PyPI, rather than racing it.
- `daemon start`/`daemon run` now reject a non-loopback `--host`. Pass
  `--allow-remote` to bind a LAN address deliberately; the daemon serves the
  full MCP JSON-RPC surface including every write tool.
- `search --type` with no supported values is now a usage error (exit 2) instead
  of silently searching nothing and exiting 0.
- `--plain` is now implemented on all ten orchestration commands, emitting
  tab-separated records for agent parsing. It was previously declared and
  ignored on five of them, and identical to the default on `record-run`, whose
  plain output has therefore changed shape.
- MCP tool schemas now set `additionalProperties: false` where the handler has a
  fixed signature, so an unknown argument is rejected rather than accepted and
  ignored. `resources/read` returns `-32002` for an unknown URI, and a
  non-project `project` argument returns a tool error pointing at
  `backlog://init-required` instead of a `-32603` internal error.
- `claim_task` derives its target status from the orchestration policy, so a
  policy whose working state is not `inprogress` can now be claimed at all.
- `record_run` rejects a state update that would persist invalid orchestration
  metadata, and run history is capped at 50 entries. A replay whose idempotency
  key has aged out returns a conflict rather than silently re-running the work.
- Task ids are no longer reissued from a file that failed to parse; allocation
  also scans filenames, so a corrupted `decision-9` leaves a gap rather than
  producing two files under one id.

### Fixed

- Keep frontmatterless documents frontmatterless for content-only updates and
  preserve original CRLF bytes during directory-only moves.
- Avoid rendering a duplicate document title in the browser when the body
  already begins with the displayed heading.

- Parse task files that begin with a UTF-8 BOM. A BOM previously hid the
  frontmatter delimiter, so the task parsed as frontmatter-less and the next edit
  prepended a *second* frontmatter block, demoting the real id, title, and status
  into the body and silently losing the task's status.
- Refuse to create a task whose id differs from an existing one only by
  zero-padding (`TASK-007` alongside `TASK-7`). Both forms resolve to the same
  task on lookup, so the two files left the original unaddressable and made edits
  land on the wrong file.
- Keep an edit to a section visible when the file contains a duplicated section
  block. Reader and writer disagreed on which block was authoritative, so the
  write succeeded on disk but read back empty everywhere.
- Allow dependencies and `parent_task_id` to reference completed tasks; a
  completed task no longer retroactively breaks later edits that depend on it.
  Circular-dependency detection now spans completed tasks too.
- Preserve CRLF line endings when writing task sections instead of emitting mixed
  endings into a CRLF file.
- Warn and pick a deterministic winner when two files claim the same task id,
  instead of silently hiding one behind an arbitrary ordering.
- Make `--modified-files` require every requested value, matching every other
  list filter, instead of matching any one of them.
- Search now matches non-Latin scripts. The tokenizer was ASCII-only, so CJK,
  Cyrillic, Greek, and Hebrew queries silently returned nothing and accented
  Latin was truncated; queries are also accent-insensitive now (`cafe` matches
  `Café`). Applies to the CLI, MCP `task_search`, the TUI, and the browser board.
- Anchor task-path containment checks on the backlog directory rather than a
  file's own parent, so a symlinked `tasks/` directory cannot escape the project,
  while a project legitimately reached through a symlink still works.
- Stop the TUI editor from holding the project write lock for the whole
  interactive session, which blocked every CLI, MCP, and browser write for as
  long as `$EDITOR` stayed open. The lock is now held only to apply the result,
  and an edit is refused rather than silently overwriting a change that landed
  while the editor was open.

### Performance

- A single `task create` on a 51-task project now runs 2 git subprocesses in
  ~40 ms instead of 450 in ~6.2 s. The worktree probe is memoized, per-file
  `git status`/`git log` calls are batched into one of each, and
  `MutableRepository` no longer discards its own cache on every read. Because
  this work ran while holding the project write lock, the old cost could exceed
  the 5-second lock timeout and surface as spurious `LockTimeoutError`s in other
  processes.
- `GIT_OPTIONAL_LOCKS=0` keeps read-only git calls from taking the index lock and
  colliding with the user's own git commands.

## 1.0.1 - 2026-07-03

### Added

- Add MCP `task_create(..., draft=False, ...)` support so task creation can
  create draft tasks with the same rich sections as the CLI `task create --draft`
  path.

### Fixed

- Stop reusing task IDs after a task is completed or cleaned up, and let
  completed tasks be viewed/edited/archived again (ID allocation and lookup now
  span the active, completed, and archived buckets).
- Make ID lookups zero-padding-insensitive so zero-padded drafts, decisions,
  and tasks are addressable.
- Skip (and warn about) an unparsable task file instead of making the whole
  repository unreadable, and reject content containing reserved section/
  run-history markers instead of silently corrupting files.
- Read only the local working tree for milestone/draft mutations so another git
  branch's snapshot can no longer overwrite local task content.
- Write task/config files without newline translation (no more `\r\r\n` on
  Windows) and store non-ASCII frontmatter as readable UTF-8.
- Harden the MCP and browser HTTP servers (Content-Length validation/caps,
  socket timeouts, constant-time token compare) and time-bound/non-interactive
  git subprocesses so read commands and the TUI can't hang.
- Enforce the orchestration policy in `record_run` status transitions, reject
  reserved markers in run-history text, and fix TUI crashes on markup-like or
  non-ASCII task content.
- Verify daemon endpoint ownership before reporting status or signalling a PID,
  so `daemon stop --force` can no longer kill an unrelated process after PID
  reuse.

### Changed

- MCP tool failures (task not found, invalid mutation, lock timeout, ...) now
  return an MCP tool error (`isError: true`) instead of a JSON-RPC `-32603`
  internal error, and no longer leak absolute filesystem paths.
- Releasing an orchestration task now returns it to the first claimable status
  (default `todo`) so it re-enters the work queue; the release response's
  `queueCategory` is `eligible` accordingly.
- `_is_done_status`/cleanup now match the whole normalized status against
  `{done, complete, completed}`, so statuses like "Not Done"/"Incomplete" are
  no longer treated as done (compound statuses such as "Done - Verified" are no
  longer matched by substring).
- Scope git auto-commit to the backlog directory, give the CLI clean error
  messages instead of tracebacks, and reject `task create`/`task edit` flags
  that the chosen subcommand cannot honor.
- Vendor Mermaid (v10.9.1, MIT) and serve it locally from the browser board so
  no third-party request is made by default. Override the source with
  `BACKLOG_PY_BROWSER_MERMAID_URL` (a `.mjs` URL loads as an ES module, any
  other URL as a classic script) or set it to an empty string to disable
  diagram rendering.
- Add a `verification: "self-declared"` field to the compatibility report and
  each of its items to make explicit that parity statuses are a maintained
  declaration rather than automated per-item verification. Existing report
  fields (including `agent_cutover_ready` and the release gates) are unchanged.
- Require a claimable status (or existing ownership) before `record_run` can
  acquire an orchestration lease, so a lease can no longer be granted on a
  non-claimable task outside `claim_task`'s rules.
- Signal "not found" with a dedicated `NotFoundError` so the MCP/CLI
  error-mapping layers no longer treat an accidental internal `KeyError` as a
  clean "not found" result.

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
