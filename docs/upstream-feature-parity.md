# Upstream Feature Parity

This document tracks the gap between `backlog-md-py` and the current upstream
Backlog.md feature set beyond the first local-file agent cutover gate.

## Current Upstream Baseline

- Upstream package: `backlog.md@1.50.1`
- Upstream source commit: `e515400`
- Audit date: 2026-09-01
- Sources: upstream WebUI, API server, core/storage code, `README.md`,
  `CLI-INSTRUCTIONS.md`, `ADVANCED-CONFIG.md`, and `package.json`.

The 1.50.1 audit covers an explicitly inventoried WebUI slice: sorting, ordinal
movement, milestone storage and lifecycle, milestone/label filtering, and
status settings. It is not an exhaustive audit of every 1.50.1 WebUI feature.
The delivered source trace, compatibility decisions, test evidence, scope
boundaries, and ten ordered follow-ups are recorded in
[webui-gap-analysis.md](webui-gap-analysis.md).

The agent-critical matrix remains focused on deterministic CLI, MCP, and file
format behavior. Full upstream parity additionally includes human-facing
browser, terminal UI, editor, shell integration, and git automation behavior.

## Implemented But Now Explicitly Tracked

The compatibility inventory now calls out these upstream surfaces separately
because they are visible feature-set commitments, not incidental options inside
larger task commands:

- MCP `backlog://init-required` project-initialization guidance for clients
  launched outside a Backlog.md project.
- MCP `project_status(project, recentLimit=5)` coordination summary for project
  paths, task counts, recent activity, and active write locks.
- MCP task editing exposes `clearPriority=False` so agents can clear priority
  metadata without replacing the task body manually.
- MCP task editing exposes `clearMilestone=False` in compatibility inventory,
  workflow guidance, and tools/list schema so agents can clear milestone
  metadata without relying on implicit `milestone=None` behavior.
- MCP task editing exposes `ordinal=None` in tools/list schema discovery for
  deterministic task ordering.
- MCP task editing exposes `milestone=None` in tools/list schema discovery for
  deterministic milestone reassignment.
- MCP task editing exposes `references=None` in tools/list schema discovery for
  deterministic task reference metadata replacement.
- MCP task editing exposes `addReferences=None` in tools/list schema discovery
  for deterministic task reference metadata appends.
- MCP task editing exposes `documentation=None` in tools/list schema discovery
  for deterministic task documentation metadata replacement.
- MCP task editing exposes `addDocumentation=None` in tools/list schema
  discovery for deterministic task documentation metadata appends.
- MCP tools/list schemas explicitly advertise task read filters, task mutation
  metadata/checklist/status controls, document query/update metadata fields, and
  milestone optional flags accepted by the SDK-free MCP handlers.
- MCP tools/list schemas explicitly advertise handler-accepted aliases,
  including snake_case task mutation fields and legacy task edit aliases such as
  `planSet`, `removeAc`, and `removeDod`.
- MCP task creation exposes `id=None` for deterministic agent-created task IDs,
  including tools/list schema discovery.
- MCP task creation exposes `status=None` in compatibility inventory, workflow
  guidance, and tools/list schema for deterministic initial task states.
- MCP task creation exposes `parentTaskId=None` in tools/list schema discovery
  for deterministic child task creation.
- MCP task creation exposes `milestone=None` in tools/list schema discovery for
  deterministic milestone assignment.
- MCP task creation exposes `ordinal=None` in tools/list schema discovery for
  deterministic task ordering.
- MCP task creation exposes `references=None` in tools/list schema discovery for
  deterministic task reference metadata.
- MCP task creation exposes `documentation=None` in tools/list schema discovery
  for deterministic task documentation metadata.
- MCP task creation exposes `modifiedFiles=None` in tools/list schema discovery
  for deterministic touched-file metadata.
- MCP task creation exposes `implementationPlan=None` in tools/list schema
  discovery for deterministic implementation planning metadata.
- MCP task creation exposes `finalSummary=None` in tools/list schema discovery
  for deterministic completion-summary metadata.
- Task creation with explicit IDs and implementation notes.
- Task create/edit description entry through `-d`, upstream-documented `--desc`,
  and implemented `--description` aliases.
- Task create/list parent selection through both upstream-documented `-p` and
  `--parent` aliases.
- Task list/create/edit milestone selection through both `-m` and `--milestone`
  aliases.
- Task list/create/edit assignee and label entry through `-a/--assignee` and
  `-l/--label` aliases.
- Task list/create/edit status filtering and mutation through both `-s` and
  `--status` aliases.
- Task create/edit acceptance-criteria entry through both `--ac` and
  implemented `--acceptance-criteria` aliases.
- Draft creation with upstream-compatible status option acceptance while drafts
  remain in Draft status, including long aliases for description, assignee,
  label, and status inputs.
- Decision creation status entry through both `-s` and `--status` aliases.
- Milestone mutation options for creation descriptions, rename task reference
  updates, and remove-time task milestone clearing.
- Document listing with optional query filtering.
- Document creation/update by explicit path and title, including `-p/--path`
  and `-t/--type` aliases.
- Task editing for title, status, description, dependency, acceptance criteria,
  Definition of Done additions through both `--dod` and
  `--definition-of-done-add`, plan replacement, plan append, and plan clearing.
- Task creation Definition-of-Done entry through `--definition-of-done`,
  `--dod`, and implemented `--definition-of-done-add`, plus disabling
  inherited defaults through both `--disable-definition-of-done-defaults` and
  `--no-dod-defaults`.
- Task create/edit dependency entry through both `--dep` and implemented
  `--dependency` aliases.
- Task editing for notes replacement, notes append, final summary replacement,
  final summary append, and final summary clearing.
- Task editing for acceptance-criteria and Definition-of-Done check state,
  uncheck state, and removal.
- Extended config get/set/list support for `defaultAssignee`, `dateFormat`,
  `includeDatetimeInDates`, `defaultEditor`, `defaultPort`,
  `autoOpenBrowser`, `onStatusChange`, and `zeroPaddedIds`.
- `zeroPaddedIds` generation for top-level task, child task, draft, document,
  and decision IDs.
- Init-time `--backlog-dir`, `--task-prefix`, `--config-location`, and
  `--agent-instructions`, read-only `taskPrefix` config listing, generated
  task/subtask IDs that respect `prefixes.task`, and `--no-git`
  filesystem-only setup while default init writes Git-aware read settings.
- ANSI-rich terminal rendering for non-plain task list, search, board,
  document, decision, and milestone summary output while preserving unstyled
  `--plain` output.
- Shell completion installer for bash, zsh, fish, and PowerShell using
  user-scoped completion paths for the `backlog-py` executable.
- `onStatusChange` shell command execution on status edits, including
  task-level override, upstream-compatible environment variables, and
  non-blocking failure handling.
- Task and draft creation now write upstream-compatible `created_date`
  frontmatter, and task edits write `updated_date` only when content or the
  task file path changes.
- `includeDatetimeInDates: false` writes date-only `created_date` and
  `updated_date` frontmatter for task and draft mutations.
- Upstream-style plain task and draft detail output with file path, status
  icon, created/updated dates, description, and checklist sections.
- Guided `backlog config` wizard for advanced settings and
  Definition-of-Done defaults.
- Non-plain `backlog task <id>` task detail view with `defaultEditor`/`VISUAL`/
  `EDITOR` launch from interactive terminals under the project write lock.
- Non-plain `backlog task <id>` task detail Created/Updated display that
  respects `dateFormat` and `includeDatetimeInDates`, while preserving raw
  `--plain` detail output.
- Non-plain `backlog search` interactive filter panel with status, priority,
  result-type, and modified-file refinement while preserving `--plain`.
- Deterministic fuzzy search ranking across tasks, documents, and decisions,
  including exact, substring, and ordered-subsequence matches while preserving
  stable tie ordering.
- Interactive `backlog board` view/edit/move controls while preserving
  deterministic non-interactive board output.
- Interactive `backlog overview` project statistics dashboard while preserving
  deterministic non-interactive overview output.
- Opt-in local `autoCommit` after project write mutations, with dirty-worktree
  protection, no remote push/pull behavior, hooks enabled by default, and
  explicit `bypassGitHooks` support that only adds `--no-verify` to the local
  auto-commit argv.
- Fetch-only remote operations: when `remoteOperations` and
  `checkActiveBranches` are enabled, repository reads refresh remote-tracking
  refs with `git fetch --all --prune` without pulling, merging, or pushing.
- Read-only active branch accuracy: when `checkActiveBranches` is enabled,
  repository reads include task snapshots from local branches, and from remote
  branches when `remoteOperations` is enabled, whose branch tip is within
  `activeBranchDays`. This uses `git for-each-ref`, `git ls-tree`, and
  `git show` without checking out branches.
- Loopback `backlog browser` service with `--port <port>`,
  `--no-open`, config-driven default port and auto-open behavior, health and
  board JSON endpoints, and a static board snapshot.
- Responsive browser board layout for narrow viewports, including stacked
  header/actions, single-column board flow, constrained dialogs, and mobile
  form actions.
- Browser drag-and-drop status movement backed by the project write lock and
  status validation. Cross-column moves append through persistent target-column
  ordinals, including materializing ordinal-less target tasks.
- Persistent per-column priority and created-date sorting backed by complete
  unfiltered local-column reads and deterministic task ordinals.
- Dual-read/current-write milestone compatibility: current `m-N` files and
  legacy `name` files are read without migration, new milestones use current
  format, and CLI/MCP legacy fields remain available additively.
- Browser milestone create/edit/archive/remove management, assignment,
  resolved display, and milestone filtering, including explicit keep/clear
  policy for referenced removal.
- Browser repeated-label filtering with case-insensitive any-match semantics,
  without changing the repository/CLI/MCP all-match label-filter contract.
- Structured browser status add/move/remove controls with usage/default
  safeguards and atomic multi-setting config replacement. This user-requested
  status-list editor goes beyond the audited upstream WebUI.
- Browser task detail endpoint and in-page dialog for task metadata,
  description, Acceptance Criteria, Definition of Done, and AC/DoD checklist
  state controls.
- Browser task detail safe Markdown rendering for description, Implementation
  Notes, and Final Summary, including Mermaid fenced blocks rendered by the
  client-side Mermaid loader with strict security settings and escaped fallback
  source text.
- Browser read-only document and decision list/detail endpoints and in-page
  dialogs, including safe Markdown rendering for document bodies and decision
  sections.
- Basic browser task creation through the loopback service and in-page form,
  backed by the project write lock.
- Basic browser task editing through the loopback service and in-page form for
  title, status, description, and Acceptance Criteria replacement, backed by
  the project write lock.
- Browser edit form replacement for assignees, labels, priority, and
  milestone metadata, backed by the project write lock.
- Browser edit form replacement for raw Markdown Implementation Notes and
  Final Summary sections, backed by the project write lock and existing safe
  detail rendering.
- Browser Markdown formatting toolbar and safe server-rendered preview mode for
  raw Markdown description, Implementation Notes, and Final Summary textareas.
- Browser Rich mode v1 for the supported Markdown subset in description,
  Implementation Notes, and Final Summary editors while keeping raw Markdown
  textareas as the source of truth.
- Browser task archiving through a confirmation dialog and locked loopback
  service endpoint.
- Browser task detail checklist controls for Acceptance Criteria and Definition
  of Done check/uncheck state, backed by the project write lock.
- Browser Definition of Done defaults settings dialog and loopback endpoint,
  backed by the project write lock and safe config writer.
- Browser general project settings dialog and loopback endpoint for safe
  non-shell config values, backed by the project write lock and safe config
  writer.
- Browser safe git automation settings dialog and loopback endpoint for
  `remoteOperations`, `checkActiveBranches`, `activeBranchDays`, and
  `autoCommit`; browser writes still reject `onStatusChange` and
  `bypassGitHooks`.
- Browser board live-refresh polling through a deterministic `/api/board`
  revision, allowing open pages to detect external CLI, MCP, or browser-tab
  task changes and reload when no dialog is open.
- Browser board live-refresh Server-Sent Events through `/api/board/events`,
  with `EventSource` reconnect behavior and `/api/board` polling fallback.
- Browser service shutdown transport policy: `/api/board/events` reports
  pending shutdown with a dedicated SSE event and the browser client closes the
  EventSource plus revision polling when shutdown starts.
- Browser service status and guarded local shutdown controls through
  `/api/service/status`, `/api/service/shutdown`, and an in-page Service
  dialog.
- Browser service request logging through a bounded body-free
  `/api/service/requests` endpoint and Service dialog request list.
- Browser service shutdown state through idempotent stop scheduling and
  `/api/service/status` shutdown metadata.

## Remaining Scope Decisions

| Area | Future or rejected behavior | Current decision |
| --- | --- | --- |
| Browser UI | branch selection/mutation, positional reorder, milestone lanes, all-tasks table, broader config/task metadata, document/decision writes, statistics/cleanup/search, incremental reconciliation, complex full-WYSIWYG Markdown round trips, shell-hook settings | Persistent sort, append movement, milestone lifecycle/assignment/filtering, multi-label filtering, structured statuses, responsive board, task CRUD/metadata/checklists, read-only documents/decisions, safe Markdown/Rich mode v1, safe settings, SSE/polling, and service controls are implemented. See the ordered dependencies in `webui-gap-analysis.md`; shell execution and hook bypass stay CLI-only. |
| Browser service | future non-SSE persistent transports if introduced | Custom port, no-open, foreground lifecycle, health, service status, guarded local shutdown, idempotent shutdown state, bounded request logging, board JSON with deterministic revisions, SSE revision events with polling fallback, SSE shutdown events with client transport teardown, task create/edit/archive/checklist/detail JSON, and static board snapshot are implemented; any future WebSocket or long-lived non-SSE transport needs its own explicit shutdown policy |
| Git automation | none currently tracked | Local auto-commit, explicit auto-commit hook bypass, fetch-only remote operations, and read-only active branch snapshots are implemented |

## Release Validation Gates

`backlog-py compat status` reports implemented feature coverage and release
validation separately. A `106/106` implemented inventory means every explicit
inventory entry is self-declared implemented; it does not mean exhaustive
Backlog.md 1.50.1 parity. Declaring the inventoried browser release scope ready
also requires browser release evidence. Use
`backlog-py compat evidence-template` to create the manifest scaffold, then run
`backlog-py compat status --release-evidence <manifest.json>` with the completed
fresh manifest format in `docs/browser-release-validation.md` to promote
externally generated release evidence into machine-readable readiness.

The compat report prints the audited upstream baseline independently from
release evidence, so users can see which upstream Backlog.md version the
feature inventory covers even when browser release evidence is missing or
stale. Release-evidence manifests also carry their own upstream baseline and
must match the current audited baseline before they can satisfy browser release
gates.

The current browser release gates are:

- Required: browser E2E coverage for rich edit flows before declaring the
  inventoried browser release scope ready.
- Required: desktop and mobile browser screenshots before declaring the
  inventoried browser release scope ready.
- Not applicable unless explicitly scoped later: complex Markdown full-WYSIWYG
  round-trip guarantees.
- Passed by policy: browser API does not expose shell-hook execution or
  hook-bypass settings.
- Passed for current transports: SSE/polling/shutdown service transport policy
  is documented.

## Recommended Work Order

1. Keep the current compatibility inventory pinned to the audited upstream
   feature release. Update the agent-critical oracle only when its CLI/MCP
   golden command surface changes; it intentionally remains historical at
   `backlog.md@1.45.2` for this WebUI-only audit.
2. For release packaging, attach a fresh browser release-evidence manifest with
   repo-relative artifact paths and run
   `backlog-py compat status --release-evidence <manifest.json>` before declaring
   the inventoried browser slice release-ready.
3. Keep any future shell-hook or browser-exposed automation behind separate
   security review; the CLI-only hook-bypass milestone is complete for
   explicit local auto-commit opt-in.
4. Treat complex full-WYSIWYG Markdown round-trip guarantees as a future
   milestone, not as part of the current audited browser release scope.
