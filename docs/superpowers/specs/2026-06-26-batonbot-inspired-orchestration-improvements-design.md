# BatonBot-Inspired Orchestration Improvements Design

Status: Approved for implementation planning
Date: 2026-06-26
Scope: `backlog-md-py` design only

## Summary

Add five BatonBot-inspired improvements to `backlog-md-py` as one staged
orchestration-substrate effort:

- first-class run history and audit summaries,
- exposed orchestration reports and safe workflow mutations,
- queue visibility in automation and human-facing surfaces,
- explicit task splitting and continuation helpers,
- stronger generated agent guidance and troubleshooting.

The feature keeps the existing product boundary intact. `backlog-md-py` remains
a portable Backlog.md-compatible coordination library, not an agent process
runner, LLM proxy, scheduler, or workflow executor.

## Goals

- Preserve Markdown files as the source of truth.
- Add deterministic audit history without storing raw transcripts in task files.
- Expose claim, release, transition, record-run, queue, and split primitives
  through core services, CLI, and MCP.
- Make browser and TUI features read from the same core orchestration model.
- Improve generated agent instructions for multi-agent coordination and failure
  recovery.
- Keep all orchestration metadata optional for normal Backlog.md users.

## Non-Goals

- No LLM proxy or OpenAI-compatible gateway.
- No built-in coding agent loop.
- No subprocess launching, pause, cancel, or process-tree management.
- No automatic task execution or automatic context-overflow splitting.
- No browser-only orchestration state.
- No unbounded logs, transcripts, or command output inside task Markdown files.

## Architecture

The design uses a service layer so workflow behavior does not accumulate in
`MutableRepository` or UI adapters.

### Core Modules

- `backlog_py.orchestration.models`
  - Existing policy and orchestration state dataclasses.
  - New run-history event dataclasses.
  - New queue category and split-result dataclasses.

- `backlog_py.orchestration.policy`
  - Load `backlog/orchestration.yml` when present.
  - Fall back to `OrchestrationPolicy.default()` when absent.
  - Validate policy shape before mutation commands are exposed to callers.

- `backlog_py.orchestration.history`
  - Parse, validate, render, and append `RUN_HISTORY` entries.
  - Enforce entry markers and size limits.
  - Detect idempotency matches and conflicts from prior events.

- `backlog_py.orchestration.service`
  - Own claim, release, transition, record-run, queue, and split semantics.
  - Re-read tasks inside project write locks.
  - Validate policy, version, lease, idempotency, and split constraints.
  - Call low-level repository helpers for frontmatter and section updates.

- `backlog_py.core.repository`
  - Remains responsible for safe Markdown file access and preservation.
  - Provides narrow helpers for replacing frontmatter values, appending owned
    sections, and creating ordinary tasks.
  - Does not own orchestration workflow policy.

### Adapters

- CLI commands are thin wrappers over `OrchestrationService`.
- MCP tools expose the same operations with JSON-safe schemas and responses.
- Browser and TUI initially display orchestration data read-only.
- Generated agent instructions document the CLI/MCP workflow and recovery
  paths.

## File Format

Current automation state stays in optional task frontmatter:

```yaml
orchestration:
  status_key: inprogress
  version: 4
  lease_owner: codex-agent-1
  lease_expires_at: "2026-06-26T19:00:00Z"
  correlation_id: "run-2026-06-26-001"
  idempotency_key: "transition-task-4-start"
  workspace:
    path: ".worktrees/task-4"
    branch: "codex/task-4"
  runner:
    kind: codex
    profile: default
  review:
    state: awaiting_approval
    reviewer: human
    attempts: 1
    max_attempts: 3
```

Run history uses a new owned Markdown section. Each event has explicit entry
markers plus a YAML metadata block, followed by optional Markdown summary text.

````markdown
## Run History
<!-- SECTION:RUN_HISTORY:BEGIN -->
<!-- RUN_HISTORY_ENTRY:BEGIN -->
```yaml
event_id: run-2026-06-26-001
type: transition
actor: codex-agent-1
timestamp: "2026-06-26T18:04:00Z"
idempotency_key: transition-task-4-start
from_status: todo
to_status: inprogress
result: accepted
files: []
verification: []
```
Claimed task for implementation.
<!-- RUN_HISTORY_ENTRY:END -->
<!-- SECTION:RUN_HISTORY:END -->
````

### Run-History Rules

- The outer section marker is `SECTION:RUN_HISTORY`.
- Each event is delimited by `RUN_HISTORY_ENTRY` markers.
- Event metadata is YAML with stable keys.
- Event body is optional Markdown for human-readable summary.
- Task files store summaries and pointers, not full raw logs.
- Entry summaries, file lists, verification lists, and metadata values are size
  capped.
- Initial caps should be named constants in `orchestration.history`:
  `MAX_RUN_HISTORY_SUMMARY_CHARS`, `MAX_RUN_HISTORY_METADATA_CHARS`,
  `MAX_RUN_HISTORY_FILES`, and `MAX_RUN_HISTORY_VERIFICATION_COMMANDS`.
- Malformed entries produce stable parse issues. Mutation commands refuse to
  append until the section is fixed. A force-append escape hatch is outside this
  design.
- Idempotency comparisons use a canonical fingerprint that excludes generated
  fields: `event_id`, `timestamp`, rendered Markdown formatting, and storage
  ordering. The fingerprint includes task ID, event type, actor, result, status
  transition fields, split mode and child-item payload when present, capped
  summary text, files, verification commands, and other caller-supplied metadata.
  Reusing a key with a different canonical fingerprint is a conflict.

### Version Rules

- `claim_task`, `release_task`, and `transition_task` increment
  `orchestration.version`.
- `record_run` does not increment version unless it also changes current
  orchestration state.
- `split_task` increments the parent task's orchestration version when it
  records the split event or changes parent orchestration metadata.
- Missing `orchestration.version` is treated as version `0` for
  `expected_version` checks. The first versioned mutation that creates
  orchestration metadata writes version `1`.
- All versioned mutations accept `expected_version` and fail closed on mismatch.
  When an idempotency key is provided, the service checks for a matching prior
  fingerprint first, inside the file lock. Matching replay returns the previous
  result without checking the stale `expected_version`; new mutations check
  `expected_version` before writing.
- `record_run` accepts an optional idempotency key. When provided, replaying the
  same run event returns the prior matching event without appending duplicate
  history. Reusing the same key for different run metadata raises
  `OrchestrationIdempotencyConflict`.
- `split_task` also accepts an optional idempotency key. Matching replay returns
  the previously created child or continuation task IDs and parent split event.
  Reusing the same key with different split payload, mode, inherited-dependency
  options, or parent task ID raises `OrchestrationIdempotencyConflict`.

## Queue Model

Queue state is derived, not separately stored.

Inputs:

- `orchestration.status_key`
- `orchestration.lease_owner`
- `orchestration.lease_expires_at`
- policy state flags
- task dependencies
- validation issues, including run-history parse issues
- existing `ordinal`
- existing task status and completed/archive location

Queue categories:

- `eligible`: claimable, valid, unlocked, dependencies complete.
- `blocked_by_dependencies`: claimable but dependency completion is missing.
- `claimed`: active lease is held by an actor.
- `stale_claim`: lease exists but has expired.
- `invalid`: orchestration metadata, run-history parsing, or policy validation
  failed.
- `terminal`: policy treats current orchestration state as terminal.
- `in_workflow`: valid task is in a non-claimable, non-terminal workflow state
  and is not blocked by dependencies or leases.

These categories power CLI/MCP reports and browser/TUI badges. Queue reports
default to active tasks only, including active files whose plain status has
become terminal. Completed-task files are included only with an explicit
`--include-completed`/`include_completed` option. Archive files are excluded in
the initial implementation unless repository archive listing support is added in
the same phase.

Category assignment must be deterministic. Apply this precedence order and
return the first matching category:

1. `invalid`
2. `terminal`
3. `claimed`
4. `stale_claim`
5. `blocked_by_dependencies`
6. `eligible`
7. `in_workflow`

For example, malformed orchestration metadata is always `invalid`, even if the
same task also has an expired lease. A terminal task with a lingering lease is
reported as `terminal`, with lease details included in diagnostics. Completed
or archived Backlog tasks, plus plain task statuses that normalize to `done` or
`complete`, map to `terminal` even when no `orchestration.status_key` exists.
Every task must receive exactly one category.

## Task Splitting

Splitting is explicit and Backlog-native.

Modes:

- `child`
  - Creates child tasks with `parent_task_id`.
  - May inherit parent dependencies.
  - Does not make children depend on the parent by default.

- `continuation`
  - Creates follow-up tasks that can depend on the parent or previous
    continuation.
  - Preserves sequence with `ordinal`.
  - Intended for work that cannot be completed in one agent/session context.

Parent behavior:

- Add a run-history split event.
- Optionally add implementation notes summarizing what moved to children.
- Preserve existing task status unless the caller explicitly transitions it.
- Require `expected_version` for mutation paths unless the caller supplies an
  idempotency key that replays an already recorded split.

Child task behavior:

- Use normal `MutableRepository.create_task`.
- Copy selected context into description, implementation plan, dependencies,
  `parent_task_id`, `ordinal`, and optional `orchestration.runner`.
- Reject generated circular dependencies.

## CLI Surface

Add an `orchestration` command group:

```bash
backlog-py orchestration status --plain
backlog-py orchestration status --json
backlog-py orchestration eligible --plain
backlog-py orchestration eligible --json
backlog-py orchestration claims --plain
backlog-py orchestration stale-leases --plain
backlog-py orchestration queue --json
backlog-py orchestration claim TASK-1 --actor codex --expected-version 0
backlog-py orchestration release TASK-1 --actor codex --expected-version 3 --reason "handoff"
backlog-py orchestration transition TASK-1 review --actor codex --expected-version 4 --reason "ready for review" --idempotency-key transition-task-1-review
backlog-py orchestration record-run TASK-1 --actor codex --result succeeded --summary "Implemented and verified." --idempotency-key run-task-1-verification
backlog-py orchestration split TASK-1 --mode child --expected-version 5 --idempotency-key split-task-1-parser-mcp --item "Add parser coverage" --item "Expose MCP schema"
```

Actor defaulting order:

1. explicit `--actor`,
2. MCP session/client identity when available,
3. `BACKLOG_ACTOR`,
4. local username plus hostname,
5. `"unknown"`.

Plain output is for humans. JSON output is the stable automation surface.

## MCP Surface

Expose matching tools:

- `orchestration_status`
- `orchestration_queue`
- `orchestration_eligible`
- `orchestration_claims`
- `orchestration_stale_leases`
- `orchestration_record_run`
- `orchestration_claim_task`
- `orchestration_release_task`
- `orchestration_transition_task`
- `orchestration_split_task`

Tool responses include:

- task ID and path,
- current orchestration version,
- effective queue category,
- run-history event IDs,
- validation issues,
- conflict details with current version/lease owner when applicable.

MCP tools should not expose browser-only concepts and should not launch agents.

## Browser And TUI

The first UI phase is read-only:

- task card orchestration badges,
- task detail run-history display,
- queue category filters,
- stale lease and invalid metadata indicators,
- no browser claim/transition buttons initially.

Queue reordering through `ordinal` is a later follow-up after read-only queue
visibility proves stable.

## Agent Instructions

Generated instruction files should add:

- check `project_status` and orchestration queue/eligible reports before
  write-heavy work,
- claim before work when orchestration metadata is enabled,
- record run summaries with changed files and verification commands,
- transition to `review` or `triage`, not directly to terminal states unless
  policy allows,
- troubleshoot MCP discovery, daemon health, stale leases, version conflicts,
  and malformed run history,
- use explicit CLI fallback commands when MCP is unavailable.

## Error Handling

Errors are typed and machine-readable:

- `OrchestrationPolicyError`
  - invalid or unreadable `backlog/orchestration.yml`.

- `OrchestrationValidationError`
  - task metadata is malformed or violates policy.

- `OrchestrationVersionConflict`
  - `expected_version` does not match current `orchestration.version`.

- `OrchestrationLeaseConflict`
  - active lease is held by another actor.

- `OrchestrationTransitionError`
  - requested transition is not allowed by policy.

- `OrchestrationIdempotencyConflict`
  - same idempotency key was reused for a different action.

- `RunHistoryParseError`
  - `RUN_HISTORY` exists but has malformed entry markers or metadata.

- `TaskSplitError`
  - split mode, dependency shape, or generated child tasks are invalid.

Mutation behavior:

- All writes use the existing project write lock.
- Versioned commands re-read task files inside the lock.
- Version conflicts fail closed and return current version.
- Idempotency replay returns the prior matching result without duplicating
  history.
- Browser/TUI display read-only errors and do not repair files.

## Phased Rollout

### Phase 1: Run History Foundation

- Add `OrchestrationRunEvent`.
- Add `RUN_HISTORY` parse/render/append helpers.
- Add entry markers and YAML metadata format.
- Add size caps and malformed-entry diagnostics.
- Add the initial `OrchestrationService` shell with `record_run`; later phases
  extend the same service rather than introducing a second behavior owner.
- Expose CLI/MCP `record-run`.
- Add browser task-detail read-only run-history payload.

### Phase 2: Policy And Workflow Mutations

- Add `backlog/orchestration.yml` loader.
- Add typed orchestration errors.
- Extend `OrchestrationService` with claim/release/transition helpers.
- Enforce expected version, leases, policy transitions, idempotency replay, and
  version increments.
- Expose CLI/MCP mutation tools.
- Extend orchestration reports with queue categories.

### Phase 3: Queue Visibility

- Add CLI/MCP queue reports.
- Add browser queue category payloads and card badges.
- Add TUI read-only queue filters or badges.
- Keep queue reorder out of this phase.

### Phase 4: Explicit Task Splitting

- Add split planning service.
- Support `child` and `continuation` modes.
- Create normal Backlog tasks with parent, dependency, ordinal, and context
  fields.
- Record split events in parent run history.
- Expose CLI/MCP split command/tool.

### Phase 5: Human And Agent Ergonomics

- Refresh generated agent instructions.
- Update integration examples.
- Add browser/TUI polish for run history and queue categories.
- Consider queue reorder as a separate small follow-up.

## Testing Plan

- Policy loading, default fallback, invalid policies, and custom transition
  graphs.
- `RUN_HISTORY` parse/render/append, malformed entry detection, size caps, and
  idempotency lookup.
- Claim/release/transition version increments, stale version rejection, lease
  conflicts, stale lease reclaim, missing-version-as-zero behavior, and
  idempotency replay.
- `child` and `continuation` split modes, dependency normalization, ordinal
  generation, parent history entries, and circular dependency prevention.
- MCP tools/list definitions, JSON-safe outputs, and SDK-free protocol calls.
- CLI plain/JSON output, exit codes, and actionable error messages.
- Browser service queue payloads and run-history task detail payloads.
- Agent instruction generated text.

Focused validation commands:

```bash
uv run --extra dev python -m pytest tests/test_orchestration.py -q
uv run --extra dev python -m pytest tests/test_mcp_resources.py tests/test_mcp_protocol_sdk_free.py -q
uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_agent_instructions.py -q
```

## Release Positioning

This feature should be documented as an orchestration substrate with audit
history, safe claims, queue visibility, task splitting, and agent guidance. It
must not be advertised as an agent runner.
