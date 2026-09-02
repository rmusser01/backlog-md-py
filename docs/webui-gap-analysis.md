# WebUI Gap Analysis: Backlog.md 1.50.1

## Audit Scope

This comparison was audited on 2026-09-01 against Backlog.md `1.50.1` at
commit `e515400`. The `backlog-md-py` comparison started at commit `1197fa2`;
the delivered implementation described here was reviewed through `aba4799`.
The audit covers the WebUI, its loopback API, the core mutation paths used by
that API, and the Markdown/config formats those paths persist.

This is a feature-coverage audit, not fresh browser release evidence. The
checked-in agent-critical oracle and browser evidence remain historical
`1.45.2` records. A release that advertises full browser parity still needs a
new evidence manifest and desktop/mobile artifacts for the current baseline.

## Delivered First-Priority Gaps

| Goal | Backlog.md 1.50.1 path | `backlog-md-py` path | Compatibility difference and implemented resolution | Test evidence |
| --- | --- | --- | --- | --- |
| Persistent column sort | React: `src/web/components/TaskColumn.tsx`, `src/web/components/Board.tsx`; API: `src/web/lib/api.ts`; server/core: `src/server/index.ts`, `src/core/backlog.ts`, `src/core/reorder.ts`; ordering: `src/utils/task-sorting.ts`, `src/web/lib/lanes.ts`; storage: task frontmatter `ordinal` | Template/client: `src/backlog_py/browser/service.py::_render_column()`, `src/backlog_py/browser/assets/board.js`; endpoint: `browser/service.py::do_POST()` at `/api/tasks/sort`; core/storage: `MutableRepository.sort_tasks()`, `_assign_task_ordinals()`, `_write_task_source_batch()` | Upstream accepts reorder intent and supports broader positional ordering. Python now accepts semantic priority or created-date sort intent, always loads the complete local status column so a filtered view cannot cause a partial sort, and persists deterministic `1000`-spaced ordinals without changing `updated_date`. Priority order honors optional config; created dates normalize supported UTC forms; invalid/missing values sort last; task ID breaks ties. | `tests/test_task_ordering.py` covers ordering, no-ops, rollback, timestamps, cache invalidation, dates, and local-only scope. `tests/test_browser_service.py` covers request validation, locking, filtered views, controls, errors, and query preservation. |
| Cross-column append ordinals | React/API/server/core: `TaskColumn.tsx` -> `Board.tsx` -> `api.ts` -> `server/index.ts` -> `backlog.ts`; `src/core/reorder.ts` calculates midpoint/rebalance ordinals | Client/endpoint: `board.js` -> `/api/tasks/<id>/status`; core/storage: `MutableRepository.move_task_to_status()`, `_write_task_source_batch()` | Upstream supports arbitrary positions. The current Python board has column-level drops, so the minimal compatible behavior is append: existing valid target ordinals stay intact, ordinal-less target tasks are materialized after them, and the moved task receives the last ordinal. Ordinal-only rewrites preserve target timestamps; the moved task keeps normal status-change timestamp/callback semantics. Arbitrary positional, keyboard, and batch moves remain deferred. | `tests/test_task_ordering.py::test_move_task_to_status_appends_after_ordinal_and_ordinal_less_tasks` and adjacent edge/rollback/callback tests; `tests/test_browser_service.py::test_browser_status_move_uses_ordinal_aware_append`. |
| Milestone storage and lifecycle | React: `src/web/components/MilestonesPage.tsx`, `TaskDetailsModal.tsx`; API/server/core: `src/web/lib/api.ts`, `src/server/index.ts`, `src/core/backlog.ts`; storage/parser: `src/file-system/operations.ts`, `src/markdown/parser.ts`, `src/utils/milestone-storage.ts` | Template/client: `browser/templates/board.html`, `browser/assets/board.js`; API: `browser/service.py` `/api/milestones`; model/core/storage: `MilestoneRecord`, `MilestoneService`, `resolve_milestone_from_records()` in `core/milestones.py`; CLI/MCP adapters: `cli/main.py`, `mcp/tools.py`; files: `backlog/milestones` and `backlog/archive/milestones` | Backlog.md current files use canonical `m-N` IDs, `title`, optional normalized `due_date`, and `## Description`. Older Python files use `name`. Python now dual-reads both formats, ignores `readme.md`, writes only current format, allocates IDs across active/archive, preserves IDs on rename, resolves canonical/numeric/path/unique-title aliases, and exposes additive current fields without removing legacy CLI/MCP `name`, path, content, or archive behavior. Browser management supports create/edit/archive/remove, reference counts, explicit keep/clear removal policy, assignment, and read-only archived details. | `tests/test_milestones.py` covers current/legacy/BOM/malformed reads, allocation, aliases, date normalization, preservation, conflicts, containment, reference updates, and rollback. `tests/test_browser_service.py` covers milestone routes, safe keys, locks/origin, task policies, filters/selectors, dialog behavior, and datetime round trips. Direct CLI command coverage and imported MCP helper coverage are both in `tests/test_milestones.py`. |
| Milestone and label (task-tag) board filters | React: `src/web/components/LabelFilterDropdown.tsx`, `Board.tsx`, `BoardPage.tsx`; normalization: `src/utils/label-filter.ts`; task/milestone controls use `TaskDetailsModal.tsx` and `MilestonesPage.tsx` | Server render/payload: `browser/service.py::build_board_payload()`, `_render_board_filter()`, `_task_payload()`; client forms/details: `board.html`, `board.js`; core task label storage remains in `MutableRepository` | Both UIs provide repeated, URL-persisted label selection with case-insensitive any-match behavior. Python applies that rule only to the WebUI; repository/CLI/MCP multi-label filters intentionally keep their existing all-match contract. Filter choices and the revision hash come from the unfiltered board. Milestone filtering resolves active, archived, alias, and unknown stored references; task forms preserve legacy/archived/unknown raw values unless the user explicitly replaces them. Existing browser label create/edit fields and card/detail display remain the mutation path. | `tests/test_browser_service.py::test_board_combines_queue_milestone_and_any_match_repeated_labels` plus adjacent choice, alias, unknown-reference, card, detail, and exact-value preservation tests. |
| Structured statuses | Upstream: `src/web/App.tsx` loads `/api/statuses`; `src/web/components/Settings.tsx` consumes statuses for the default selector; `src/file-system/operations.ts` reads/writes config. The audited upstream UI does not create the status list. | Template/client: `browser/templates/board.html`, `browser/assets/board.js`; endpoint/payload: `browser/service.py` `/api/settings/config`, `_config_settings_payload()`, `_validate_status_settings()`; model/storage: `BacklogConfig`, `storage/config.py::set_config_values()` | This user-requested capability intentionally extends beyond the audited upstream UI. The raw textarea is replaced by ordered add/move/remove controls with usage counts. Duplicate/default/in-use safeguards use case-insensitive identity while preserving stored display spelling. The server validates the complete submitted status/default state against fresh local tasks and writes accepted multi-key config changes with one atomic replacement while retaining partial-request compatibility and unknown keys. | `tests/test_config_storage.py` covers one-write batching, validation-before-write, aliases, and unknown keys. `tests/test_browser_service.py` covers status usage, complete-pair validation, fresh snapshots, structured controls, Unicode casefold identity, keyboard focus, errors, and atomic endpoint behavior. |

## Compatibility and Data-Preservation Boundaries

- Browser mutations operate only on local working-tree task, milestone, and
  config files under the project write lock. Read-only task snapshots found on
  active local or remote branches may appear on the board, but they are not
  browser-mutable.
- Sorting is persistent Markdown state, not browser-local preference. The
  operation changes only task ordinals; it does not rewrite a filtered subset.
- Existing milestone files are never auto-migrated. Reads and edits preserve
  their current or legacy format; legacy edits keep legacy `name` frontmatter
  and filename behavior. Archive and remove only move or delete the selected
  file as explicit actions.
- Legacy, archived, title-alias, and unresolved milestone values already stored
  on tasks survive ordinary task saves. Canonical `m-N` assignment happens only
  when the user explicitly selects that value.
- Milestone batch mutations and ordinal batches use best-effort rollback for
  runtime failures. This is not a claim of crash-safe multi-file transactions.
- The WebUI remains server-rendered HTML plus packaged vanilla JavaScript and
  CSS. No frontend runtime, bundler, database, or client state framework was
  added.

## Ordered Second-Priority Roadmap

1. **Branch provenance and selection.** Complete provenance UX before exposing
   any mutation on branch snapshots; local working-tree mutation remains the
   safe boundary until then.
2. **Positional, keyboard, and batch reorder.** Build on the ordinal batch
   writer and branch provenance to add arbitrary placement and accessible card
   movement.
3. **Milestone lanes and lifecycle expansion.** Build on canonical milestone
   IDs to add swimlanes, progress/overdue grouping, and reassignment during
   removal.
4. **Full all-tasks table.** Reuse normalized browser payloads and URL filter
   state for sortable ID, title, status, priority, ordinal, milestone, and
   created-date columns.
5. **Config-managed priorities, labels, types, and projects.** Complete
   creation/editing and consistent validation across browser, CLI, and MCP;
   priority-order reads are already available.
6. **Additional task metadata.** Add due date, type, project, dependencies,
   references, documentation, and modified-file editing after the related
   config metadata is consistent.
7. **Document and decision CRUD.** Extend the currently read-only browser
   views by reusing the existing core services and write-lock policy.
8. **Statistics, cleanup, and richer search.** Build after normalized filters
   and the all-tasks data surface exist.
9. **Incremental board reconciliation.** Replace full reloads only when stable
   endpoint payloads and client state make that complexity worthwhile.
10. **Richer editing and fresh release evidence.** Expand Markdown behavior
    only with round-trip preservation tests, then capture current desktop and
    mobile screenshots plus end-to-end browser evidence before making a full
    browser-release claim.
