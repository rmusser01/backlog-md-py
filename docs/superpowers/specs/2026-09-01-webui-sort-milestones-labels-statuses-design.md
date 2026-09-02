# WebUI Sort, Milestone, Label, and Status Port Design

**Date:** 2026-09-01

**backlog-md-py baseline:** `1197fa2`

**Backlog.md audit baseline:** `1.50.1`, commit `e515400`

**Status:** Approved design

## Objective

Review the current Backlog.md WebUI against backlog-md-py, preserve backlog-md-py's dependency-free browser architecture, and deliver these first-priority capabilities:

1. Persistent per-column sorting.
2. Current and legacy milestone compatibility plus milestone WebUI workflows.
3. Task-label creation/editing/display plus multi-select board filtering.
4. Structured project-status creation, removal, and ordering.

All other current Backlog.md WebUI gaps remain ordered follow-ups. Markdown stays the source of truth, and existing CLI/MCP behavior remains compatible unless this document calls out an intentional additive change.

## Product and Interface Constraints

This is a product interface for developers and maintainers who triage a Markdown-backed board during normal project work. The interface should remain focused, practical, and quiet.

- Keep the existing Python server, server-rendered HTML, packaged CSS, and vanilla JavaScript.
- Do not add React, Bun, Node, a frontend bundler, a database, or a client state framework.
- Reuse the existing light/dark system theme, typography, spacing, and restrained accent.
- Target WCAG AA contrast, visible keyboard focus, native controls, and reduced-motion-safe behavior.
- Keep mutations recoverable and explicit. Do not hide repository state in browser-only persistence.
- Avoid a broad application-shell redesign.

Image generation is intentionally skipped. This work extends an established product-control vocabulary and needs no visual assets or mood exploration.

## Gap Analysis

### First-priority gaps

| Capability | Current Backlog.md | backlog-md-py today | Required outcome |
| --- | --- | --- | --- |
| Column sorting | Column menu persists priority and creation-date sorts through task ordinals. | Core reads and writes `ordinal`, but the browser has no sort control or bulk reorder operation. | Server-calculated persistent sort for priority and creation date, exposed in every board column. |
| Ordinal-aware movement | Reorder logic calculates midpoint ordinals and rebalances when needed. | Browser status drops change `status` but retain the source-column ordinal. | A cross-column browser drop appends with a target-column ordinal. Arbitrary positional movement remains deferred. |
| Milestone storage | Current files use `m-N` IDs, `title`, optional `due_date`, and a Description section. | Service writes legacy `name` files and treats current filenames and `readme.md` as legacy milestone names. | Dual-read, current-write behavior without automatic migration. |
| Milestone WebUI | Dedicated milestone management, active/archive state, due dates, task assignment, filters, and milestone-aware board lanes. | CLI/MCP milestone CRUD exists; browser task forms accept free text but there is no milestone API or management UI. | Active/archive management dialog, current-format creation, edit/archive/remove, assignment, resolved display, and filtering. Swimlanes remain deferred. |
| Task labels | Multi-select, URL-persisted any-match filtering and label-aware task controls. | Labels work in the repository, CLI, MCP, browser forms, and card badges; the board has no label filter. Repository multi-label filters use all-match semantics. | Browser-only any-match multi-select filtering while preserving repository/CLI semantics. |
| Status creation | Settings consumes configured statuses for default-status selection; the audited WebUI does not manage the status list itself. | Config and browser settings already write statuses through a raw multiline textarea. | User-requested structured add/remove/reorder controls with usage/default safeguards. This intentionally goes beyond the audited upstream WebUI. |

### Existing strengths to preserve

- `MutableRepository.create_task()` and `edit_task()` already validate and serialize numeric ordinals.
- `ReadOnlyRepository.list_tasks()` already places ordinal-bearing tasks first and sorts them ascending.
- Project write locks already wrap browser, CLI, and MCP mutations and perform one optional auto-commit after a successful operation.
- `MilestoneService` already provides containment checks, per-file atomic writes, rollback for task-reference edits, and CLI/MCP integrations.
- Browser writes already enforce loopback Host, same-origin, request-size, path-containment, and project-lock protections.
- Browser templates and assets are packaged and tested without a frontend build step.

## Source-path Trace

### Persistent sorting

Backlog.md:

1. `src/web/components/TaskColumn.tsx` exposes priority and creation-date sort actions and emits a column order.
2. `src/web/components/Board.tsx` sends the reorder request and reconciles changed tasks.
3. `src/web/lib/api.ts` posts to `/api/tasks/reorder`.
4. `src/server/index.ts` validates the request and calls the core.
5. `src/core/backlog.ts` resolves tasks, rejects branch-only mutations, calculates the moved ordinal, resolves conflicts, and writes changed tasks in bulk.
6. `src/core/reorder.ts` owns midpoint, block, and rebalance calculations.
7. `src/utils/task-sorting.ts` owns ID, priority, and ordinal comparators.
8. `src/web/lib/lanes.ts` applies ordinal-first board ordering.

backlog-md-py:

1. `src/backlog_py/browser/service.py::render_board_html()` and `_render_column()` render the static board.
2. `src/backlog_py/browser/assets/board.js` currently posts only status changes after column drops.
3. `src/backlog_py/browser/service.py::do_POST()` wraps task writes in `with_project_write_lock()`.
4. `src/backlog_py/core/repository.py::MutableRepository.edit_task()` can already write `ordinal` but updates one task and its `updated_date`.
5. `ReadOnlyRepository.list_tasks()`, `board()`, and `_task_record_sort_key()` already consume ordinals.
6. `_task_payload()` does not currently expose ordinal to the browser.

### Milestones

Backlog.md:

1. `src/web/components/MilestonesPage.tsx` manages active milestones, archives, due dates, task groupings, and filters.
2. `src/web/components/TaskDetailsModal.tsx` resolves stored milestone aliases to current IDs and display titles.
3. `src/web/lib/api.ts` exposes milestone list/create/update/remove/archive requests.
4. `src/server/index.ts` validates milestone requests and resolves assignment input.
5. `src/core/backlog.ts` coordinates milestone mutations and optional task-reference updates.
6. `src/file-system/operations.ts` allocates IDs across active/archive directories, writes current files, renames while preserving IDs, and archives files.
7. `src/markdown/parser.ts` parses `id`, `title`, `due_date`, and the Description section.
8. `src/utils/milestone-storage.ts` resolves ID, numeric ID, and unique-title aliases for storage.

backlog-md-py:

1. `src/backlog_py/core/milestones.py::MilestoneService` provides legacy add/list/rename/remove/archive behavior and rollback-backed task-reference updates.
2. `MilestoneRecord` exposes `name`, path, content, frontmatter, and archive state, but no current-format identity or due date.
3. `_load_milestone()` reads `name` or falls back to the filename, causing current `m-N - title.md` files and `readme.md` to be misidentified.
4. `src/backlog_py/cli/main.py` and `src/backlog_py/mcp/tools.py` already wrap the service in the project write lock.
5. Task milestone values are generic strings in `MutableRepository`.
6. `src/backlog_py/browser/templates/board.html` exposes free-text milestone fields, while `browser/service.py` has no milestone endpoints.

### Labels

Backlog.md:

1. `src/web/components/LabelFilterDropdown.tsx` provides checkbox-based multi-select behavior.
2. `src/web/components/Board.tsx` collects labels and applies case-insensitive any-match filtering.
3. `src/web/components/BoardPage.tsx` reads and writes repeated label URL parameters.
4. `src/utils/label-filter.ts` normalizes and collects label values.

backlog-md-py:

1. `MutableRepository.create_task()` and `edit_task()` already normalize label lists.
2. `ReadOnlyRepository.list_tasks(labels=...)` supports case-insensitive all-match filtering.
3. `_task_payload()`, `_render_task_meta()`, and task create/edit forms already carry labels.
4. The browser has no multi-label filter or URL contract.

### Statuses

Backlog.md:

1. `src/web/App.tsx` loads `/api/statuses` from config and supplies them to board/task views.
2. `src/web/components/Settings.tsx` uses the list to select `defaultStatus`; it does not provide status-list creation.
3. `src/file-system/operations.ts` reads and serializes the configured list.

backlog-md-py:

1. `src/backlog_py/core/models.py::BacklogConfig` and `src/backlog_py/storage/config.py` already load and write statuses.
2. `browser/service.py::_config_settings_payload()`, `_config_settings_from_payload()`, and `_statuses_setting()` expose the setting.
3. `board.html` and `board.js` currently treat statuses as newline-delimited textarea content.
4. The settings handler writes each submitted setting separately, so a multi-setting request is not one atomic config replacement.

## Design Decisions

### 1. Sorting belongs in the repository, while sort intent belongs in the request

Add one reusable repository operation for sorting all current working-tree tasks in a status. The browser sends semantic intent rather than a potentially filtered or stale list of task IDs:

```json
{"status":"In Progress","sort":"created","direction":"asc"}
```

Supported requests:

- `sort: "priority"`, with no direction.
- `sort: "created"`, with `direction: "asc"` or `"desc"`.

The operation loads the complete local column under the project lock, rejects invalid status/sort/direction values before writing, deterministically sorts, and assigns `1000, 2000, 3000, ...` ordinals. A sortable status is valid when it is configured or currently used by at least one local active task. This covers task-derived columns when `statuses` is absent or empty, plus visible legacy columns whose task status is no longer configured. An empty status that is neither configured nor used is rejected.

Priority order comes from an optional Backlog.md-compatible `priorities` config list. If absent or empty, use high, medium, low. Matching is normalized case-insensitively; unknown and missing priorities sort last. Equal priorities use natural task-ID order.

Creation timestamps accept the repository's generated `YYYY-MM-DD` and `YYYY-MM-DD HH:MM` forms plus ISO-compatible `YYYY-MM-DDTHH:MM`, optional seconds/fraction, and optional `Z` or numeric offset. Date-only and offset-less values are interpreted as UTC; offset-bearing values are normalized to UTC before comparison. Invalid and missing values sort last. Equal timestamps use natural task-ID order.

Do not implement a sort as repeated `edit_task()` calls. That would create misleading `updated_date` changes and could leave a partial batch. Instead:

1. Resolve safe local task paths.
2. Prepare and parse every updated source in memory.
3. Write each source with the existing atomic file replacement.
4. Track originals and roll back completed writes when a runtime write fails.
5. Invalidate repository caches once after the batch.

This is application-level rollback, not a claim of crash-safe multi-file transactions. A process or machine failure between file replacements can still leave a partial batch. Adding a journal is not justified for this feature.

Add a second repository helper for browser status movement. Because the current browser supports column-level, not positional, drops, append semantics are sufficient. A drop onto the task's existing status is a no-op. For a cross-column drop:

1. Load target tasks in their current rendered order, excluding the moved task.
2. Keep valid existing ordinals unchanged.
3. Materialize ordinal-less target tasks after the greatest valid target ordinal, in their existing deterministic order.
4. Give the moved task the next ordinal after every target task.
5. Apply the status change and any required ordinal materialization through the same rollback-backed batch writer used by sorting.

This ensures “append” really means the bottom of a column. Writing an ordinal only to the moved task would incorrectly place it above every ordinal-less target task because ordinal-bearing tasks currently sort first.

Pure ordinal materialization on existing target tasks preserves their `updated_date`. The moved task receives the new status and ordinal through the batch, and its `updated_date` changes exactly as it does in the existing status endpoint. After all writes succeed and caches are invalidated, load the moved task and run the existing `onStatusChange` callback. Preserve the current failure semantics: if that callback fails, the written status/ordinal changes remain, the error propagates, and optional auto-commit does not run. Do not roll back a successful file mutation solely because its post-write callback failed.

### 2. Milestones use stable optional identity without breaking legacy callers

Extend `MilestoneRecord` additively:

- Keep `name` as the display-name compatibility field used by existing CLI/MCP code.
- Add `id: str | None`.
- Add `title` as the current display title, equal to `name` for legacy records.
- Add `due_date: str | None`.
- Add a derived current/legacy format indicator.
- Preserve path, raw frontmatter, content, and archive state.

Format detection:

- A valid current record has a canonical `m-N` ID and a non-empty `title`.
- A `name` field identifies legacy format.
- `readme.md` is always ignored.
- Existing non-current files without `name` retain the filename fallback for backward compatibility.
- Malformed current-looking files are logged and skipped rather than turned into fake legacy milestones.

New milestones are always current format:

```yaml
---
id: m-9
title: Release 2
due_date: "2026-09-30 17:00"
---

## Description

Release scope.
```

ID allocation scans active and archived `m-*.md` files, using valid frontmatter IDs first and filename IDs as a collision fallback. The next ID is one greater than the maximum observed non-negative number. CLI, MCP, and browser callers already hold the project write lock around creation, so allocation and creation remain serialized.

Current renames preserve the ID, change the title, and rename the file. Legacy renames preserve legacy behavior. Unknown frontmatter and body sections are preserved unless their owned field is explicitly updated.

Milestone matching supports exact ID, numeric ID alias, exact path stem, and unique title/name. Mutations fail closed when more than one record matches. Active create/rename rejects title aliases that collide with another active title, ID, or numeric ID alias.

Browser milestone records expose a single-segment API `key`. Current records use their canonical `m-N` ID. Legacy records use `legacy-<token>`, where `<token>` is the legacy name encoded as UTF-8 base64url without padding. Endpoint decoding restores the exact logical name and never treats it as a filesystem path. This keeps legacy names containing `/`, `\\`, `%`, or non-ASCII characters out of route/path parsing while remaining deterministic and reversible with the standard library.

Task-reference rules:

- Current selections store the canonical `m-N` ID.
- Legacy selections store the legacy name.
- Existing task references are never migrated during reads or ordinary saves.
- Renaming a current milestone leaves canonical ID references unchanged.
- An explicit rename with task updates may convert unique old-title references to the canonical ID.
- Removing with `clear` recognizes canonical, numeric, and unique-title aliases.
- Archiving leaves task references intact.
- Archived milestones remain resolvable for display but are unavailable for new assignment.
- Unknown references remain visible and are preserved until explicitly cleared or replaced.

Due dates accept the current Backlog.md UTC datetime shapes: `YYYY-MM-DD HH:MM` or `YYYY-MM-DDTHH:MM`, optional seconds/fraction, and optional `Z` or numeric offset. Offset-bearing input is converted to UTC; input without an offset, including browser `datetime-local` values, is interpreted as UTC. Storage is normalized to `YYYY-MM-DD HH:MM`. Date-only and invalid values fail before writes.

Current milestone filenames use `m-N - <safe-title>.md`. `<safe-title>` removes `< > : " / \\ | ? *`, collapses whitespace to hyphens, lowercases the result, and truncates it to 50 characters, matching the audited upstream convention. If sanitization produces an empty value, use `milestone` rather than creating an empty filename suffix.

### 3. Browser filters are server-rendered view state

Use one GET filter form for queue state, milestone, and repeated labels:

```text
/?queueCategory=eligible&milestone=m-9&labels=frontend&labels=urgent
```

The browser service applies WebUI-specific case-insensitive any-match label filtering after building the unfiltered local board. This does not change `ReadOnlyRepository.list_tasks(labels=...)`, whose all-match behavior remains the CLI/MCP contract.

Available labels and milestone choices are collected from the unfiltered board, so one filter never erases another filter's choices. The revision hash remains based on unfiltered board state so external changes still trigger refreshes.

Milestone filtering resolves active milestones, referenced archived milestones, and unknown raw references. Task assignment selectors show only active milestones plus the task's exact existing raw value when it differs from the canonical assignment value, including an active milestone referenced by a unique title alias. Ordinary saves keep that raw option selected; a separate canonical active option lets the user explicitly convert it to the stable ID. Archived and unknown existing values remain preservable but cannot be newly assigned.

### 4. Status editing remains config-based and becomes safe

Replace the statuses textarea with an ordered list of rows, usage counts, Move up, Move down, and Remove controls, plus an Add status input. Existing names are not directly editable. A rename requires adding the replacement, moving tasks, and removing the old status.

Default status becomes a selector driven by the working list. The working list initializes from configured statuses when non-empty; otherwise it uses task-derived local active statuses in board order. In either case, append the current default status when it is not already present case-insensitively; with no configured or task-derived statuses, the list therefore contains only the current default. UI removal is disabled when the status is selected as default or used by a current active local task. The server repeats both checks using the submitted final statuses/default pair and current task files.

Partial settings requests retain their existing compatibility:

- When `statuses` is submitted, it must be non-empty and the submitted or current default must be a member.
- When only `defaultStatus` is submitted and configured statuses are non-empty, the new default must be a member.
- When only `defaultStatus` is submitted and statuses are absent or empty, any non-empty default remains valid.
- When neither field is submitted, status-pair validation does not run.

Add a storage helper that applies multiple validated config changes to one loaded raw mapping and performs one atomic config-file replacement. `set_config_value()` can continue to expose the single-setting API, while the browser uses the batch helper. Unknown config keys remain preserved.

Status duplicate and safety checks are case-insensitive, but stored task status and config display spelling remain exact.

## Browser API

### Sorting

- `POST /api/tasks/sort`
  - Input: `status`, `sort`, optional `direction`.
  - Output: status, sort mode, changed task IDs/count.
  - Errors: invalid request/status `400`; conflicting state `409`.

Existing `POST /api/tasks/<id>/status` changes to call ordinal-aware append behavior. `_task_payload()` adds `ordinal`.

### Milestones

- `GET /api/milestones`
  - Returns active and archived summaries with `key`, title, due date, description, format, path, and task-reference count. `key` is the canonical ID for current records and the path-safe deterministic `legacy-<base64url-name>` token described above for legacy records.
- `POST /api/milestones`
  - Creates current format from title, optional description, and optional due date.
- `POST /api/milestones/<key>/edit`
  - Updates supplied title, due date, or description. Omitted fields are preserved.
- `POST /api/milestones/<key>/archive`
  - Moves an active milestone to the archive and preserves task references.
- `POST /api/milestones/<key>/remove`
  - When references exist, requires explicit `taskHandling: "keep" | "clear"` and rejects omission with `409`. When no references exist, omission is accepted and is equivalent to `keep`.

The API deliberately follows the existing browser service's POST action-route style rather than adding isolated PUT/DELETE handling.

### Settings

`GET /api/settings/config` adds status usage information. `POST /api/settings/config` retains its external payload but validates the complete submitted statuses/default pair and writes all accepted settings once.

All mutation endpoints retain Host, Origin, request-size, path-containment, project-lock, and optional auto-commit behavior.

## WebUI Shape

### Board

- Add a small text-labeled Sort disclosure to each column with at least two tasks.
- Use ordinary buttons inside the native disclosure. Do not assign menu roles that imply unimplemented arrow-key behavior.
- Preserve the current filtered URL after sorting or task movement.
- Show mutation failures in a visible `aria-live` board status region.
- Disable a mutation button while its request is in flight.

### Filters and cards

- Replace the queue-only form with a unified queue, milestone, and labels GET form.
- Use a native disclosure containing label checkboxes for multi-select labels.
- Provide Apply and Clear filters controls.
- Show visible/total counts when filters are active and `No matching tasks` for filtered empty columns.
- Add one resolved milestone badge to cards.
- Continue showing at most two label badges, plus `+N` when more exist.
- Keep full label and resolved milestone values in task details.
- Keep label task fields as textareas with concise comma-or-line-separated guidance.

### Milestone management

Add a Milestones header action using one existing-style dialog. A separate routed page would introduce routing, while an inline board panel would compress the primary board; the established project dialog is the smaller fit.

The dialog contains:

- An inline new-milestone form.
- Active milestone list.
- Collapsed archived section.
- Single-column selected-milestone editor for title, due datetime, and description.
- ID, format, path, and referencing-task count metadata.
- Archive as the normal retirement action.
- Secondary Remove action with inline `keep`/`clear` confirmation and warning.
- Empty state with a direct create action.
- Read-only archived details.

Task-create milestone inputs contain active milestones only. Task-edit inputs contain active milestones plus the task's exact current value when necessary. A current value that resolves to an active milestone through a non-canonical title alias is rendered as `<title> (stored as <value>)`, alongside the canonical `<title> (m-N)` option. A value that resolves to an archived milestone is rendered as `<title> (archived)`; an unresolved value is rendered as `Unknown: <value>`. The raw option value remains the task's exact stored string, is enabled, and stays selected through an ordinary save until the user explicitly clears or replaces it. Options are de-duplicated by exact value, not logical milestone, so the explicit canonical migration choice remains available.

### Status editor

Project settings renders ordered status rows with usage count and accessible text labels for Move up, Move down, and Remove. Add status works with Enter or the Add button. Changes stay local until Save. Client validation provides immediate empty/duplicate/default/in-use feedback; server validation remains authoritative.

### Refresh, errors, and responsive behavior

The current SSE handler ignores a changed revision while any dialog is open. Replace that loss with a pending-revision value. When the final dialog closes, reload if a revision is pending.

- Sort errors appear in the board status region.
- Milestone and settings errors appear next to the affected form.
- Unexpected server failures return safe messages and retain detailed server logs.
- Dialogs receive a viewport height limit and internal scrolling on desktop and mobile.
- Filters wrap and status rows stack at the current narrow-screen breakpoint.
- New controls expose visible focus and do not rely on color alone.
- Full keyboard card movement remains deferred.

## Error Semantics

| Status | Meaning |
| --- | --- |
| `400` | Malformed JSON, unsupported sort/direction, invalid date, unknown configured status, or another invalid request. |
| `404` | Task or milestone no longer exists. |
| `409` | Duplicate/ambiguous milestone, referenced/default status removal, or another state conflict. |
| `500` | Unexpected failure. Log detail server-side and return a safe generic browser message. |

Repository operations validate every input they can before the first write. Runtime multi-file failures use best-effort rollback. Optional auto-commit happens only after the locked callback returns successfully.

## Implementation Slices

1. **Persistent sorting**
   - Optional `priorities` config read support.
   - Repository sort and target-column append operations.
   - Sort endpoint, ordinal payload, column UI, and focused tests.
2. **Milestone compatibility**
   - Dual-format record/parser, ID allocator, alias resolver, reference-safe mutations, and CLI/MCP compatibility tests.
3. **Milestones and labels in the browser**
   - Milestone endpoints/dialog/selectors/display.
   - Unified filters, any-match labels, card metadata, and pending revision handling.
4. **Structured statuses**
   - Single-write config helper, final-state validation, structured editor, and tests.

Each slice should be independently reviewable and keep the working tree passing before the next begins.

## Test Strategy

Write a failing test before each behavior change.

### Core tests

- Priority sorting with default and configured priority order.
- Created-date ascending/descending, invalid/missing dates, and deterministic task-ID ties.
- Ordinal assignment, no-op cases, rollback after injected write failure, cache invalidation, and `updated_date` preservation.
- Cross-column append ordinal, target-task timestamp preservation, moved-task timestamp update, and existing status-callback success/failure semantics.
- Current, legacy, BOM, readme, malformed, duplicate, and archived milestone reads.
- ID allocation across active/archive and filename fallback collision reservation.
- Current/legacy rename behavior, title/ID alias collisions, ambiguous lookup, due-date normalization, reference updates, and rollback.
- Batch config update preserves unknown keys and writes no partial accepted state on validation failure.
- Status duplicate/default/in-use safeguards.

### Browser tests

- Sort, milestone, and settings request validation.
- Same-origin rejection and project-lock operation names.
- Combined queue/milestone/repeated-label filtering and any-match semantics.
- Available filter choices remain based on unfiltered data.
- Milestone selectors preserve archived, unknown, and active-title-alias references while offering an explicit canonical-ID choice for the latter.
- Status controls, usage data, accessible names, error regions, pending revision behavior, and responsive CSS contracts.
- Package resources continue shipping in wheels/sdists.

### Final verification

- Focused tests after every slice.
- `uv run --extra dev python -m pytest tests -v`.
- `uv run --extra dev python -m ruff check src tests`.
- `uv run --extra dev python -m mypy` as an advisory comparison against the documented baseline.
- Real-browser desktop and narrow-screen checks, including keyboard navigation and system light/dark themes.
- Update compatibility inventory and browser-parity documentation from the previous `1.45.2` audit baseline to this audited `1.50.1` scope.

## Deferred Roadmap and Dependencies

1. **Branch provenance and read-only branch cards.** Foundation for safely exposing any mutation on branch snapshots.
2. **Arbitrary positional reorder, keyboard movement, and batch moves.** Builds on ordinal write primitives and branch provenance.
3. **Milestone swimlanes and advanced lifecycle.** Builds on canonical milestone IDs; adds progress, overdue grouping, and reassignment during removal.
4. **Full all-tasks table.** Builds on normalized browser payloads and URL filters; adds sortable ID/title/status/priority/ordinal/milestone/created columns.
5. **Config-managed priorities, labels, types, and projects across every interface.** Priority read support lands in slice 1, but creation/editing and consistent validation remain here.
6. **Additional task metadata editing.** Due date, type, project, dependencies, references, documentation, and modified files depend on config metadata parity.
7. **Browser document and decision mutations.** Current Python browser support is read-only; reuse existing core services before adding UI.
8. **Statistics, cleanup, and richer search.** Build after normalized filters and all-tasks data are available.
9. **Incremental board reconciliation.** Replace full-page reloads only after endpoint payloads and client state justify the added complexity.
10. **Expanded rich editing and browser release evidence.** Add richer Markdown behavior only with round-trip preservation tests, desktop/mobile screenshots, and end-to-end browser evidence.

## Acceptance Criteria

- Sorting priority or creation date persists after reload by changing only ordinals for every local task in the selected status.
- Filtered views cannot cause a partial-column sort.
- Configured, task-derived, and visible legacy status columns can be sorted when they contain local tasks.
- Browser status drops append below ordinal-bearing and ordinal-less target tasks; ordinal-only target rewrites preserve `updated_date`, while the moved task receives the normal status-change timestamp and callback behavior.
- Current Backlog.md milestones load with their real `m-N` IDs/titles, legacy Python milestones continue loading, and `readme.md` is absent.
- New milestone files use current format and never reuse an active or archived numeric ID.
- Current milestone renames preserve ID; task references follow the explicit update/remove policy.
- Ordinary task edits preserve resolved archived and unresolved milestone references.
- Browser users can create, edit, archive, remove, assign, display, and filter milestones.
- Browser users can filter by multiple task labels with any-match semantics and preserve filters in the URL.
- Browser users can add, remove, and reorder statuses; unsafe removals are rejected without config mutation.
- Partial settings updates retain their existing behavior when statuses are absent or empty.
- Existing CLI/MCP milestone `name`, path, content, and archive behavior remains available, with current-format fields added rather than replacing existing fields.
- No frontend runtime/build dependency is added.
- Focused and full blocking test/lint gates pass, with advisory type-check changes reported accurately.
