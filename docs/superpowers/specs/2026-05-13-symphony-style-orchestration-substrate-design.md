# Symphony-Style Orchestration Substrate Design

Status: Draft
Date: 2026-05-13
Scope: `backlog-md-py` design only

## Summary

`backlog-md-py` should remain a portable Python implementation of the Backlog.md file format, not a project-specific agent runner. The useful extension is an optional orchestration substrate: a compatibility-safe metadata layer and small set of validation/mutation helpers that let external systems coordinate agents, humans, reviews, and workspaces through normal Backlog.md task files.

This borrows three ideas:

- Symphony's manager/worker/reviewer framing and workspace isolation, without baking Symphony or Codex into the core library.
- `tldw_server` Kanban's workflow-control-plane safety model: canonical workflow state separate from visual placement, versioned transitions, leases, idempotency keys, and correlation IDs.
- `tldw_server` ACP orchestration's explicit task runs, reviewer loop, dependency gating, governance, audit, and workspace constraints.

The first implementation should be schema and read-only validation. Version-aware claim/transition helpers can follow once the metadata contract is stable.

## Evidence

External references:

- OpenAI Symphony blog: <https://openai.com/index/open-source-codex-orchestration-symphony/>
- OpenAI Symphony repository: <https://github.com/openai/symphony>

Local `tldw_server` anchors reviewed:

- `tldw_Server_API/app/core/Agent_Orchestration/models.py`
  - The default lifecycle is `todo -> inprogress -> review -> complete`.
  - Dependencies block execution until completed.
  - Tasks carry agent type, dependency ID, reviewer type, review attempts, success criteria, metadata, and run history.
- `Docs/User_Guides/WebUI_Extension/Kanban_Board_Guide.md`
  - Kanban cards expose workflow state separately from list placement.
  - State changes include `expected_version`, `idempotency_key`, and `correlation_id`.
- `tldw_Server_API/app/core/DB_Management/Kanban_DB.py`
  - Runtime workflow state stores lease owner, lease expiry, approval state, retry counters, version, and timestamps.
  - Workflow events are append-only and include idempotency/correlation data plus before/after snapshots.
- `Docs/Product/ACP_Agent_Orchestration_PRD.md`
  - The server owns orchestration state, governance, audit, scheduling, workspace constraints, completion signals, reviewer agents, and run history.
  - ACP explicitly does not replace MCP, Jobs, Scheduler, Workflows, or the Kanban board.

## Goals

- Preserve Backlog.md compatibility by making all orchestration fields optional.
- Keep old tools usable by storing orchestration data in frontmatter and named markdown sections they can ignore.
- Model work coordination generically enough for Codex, Symphony, `tldw_server`, local scripts, CI jobs, or human-only workflows.
- Provide clear read APIs for eligible tasks, stale leases, active claims, run history, and validation reports.
- Provide future safe mutation helpers for claims and state transitions with version checks, leases, idempotency, and audit notes.
- Keep orchestration state distinct from human-facing Backlog task status.

## Non-Goals

- No agent process launcher in the first slice.
- No daemon, scheduler, GitHub PR shepherd, CI poller, or workspace provisioning service in the core library.
- No `tldw_server`-specific APIs or schema requirements in the portable package.
- No mandatory orchestration metadata for ordinary Backlog.md users.
- No attempt to replicate Symphony's whole runtime. The library should expose task-state primitives that Symphony-like systems can use.

## Metadata Contract

Each task may include an optional `orchestration` object in frontmatter:

```yaml
orchestration:
  status_key: todo
  version: 3
  lease_owner: codex-agent-1
  lease_expires_at: "2026-05-13T07:00:00Z"
  correlation_id: "run-2026-05-13-001"
  idempotency_key: "transition-task-12-start"
  workspace:
    path: ".worktrees/task-12"
    branch: "codex/task-12"
  runner:
    kind: codex
    profile: default
  review:
    state: awaiting_approval
    reviewer: human
    attempts: 1
    max_attempts: 3
```

Compatibility rules:

- Unknown orchestration keys must round-trip unchanged.
- Missing orchestration metadata means the task behaves like a normal Backlog.md task.
- Invalid known fields should produce validation errors, not destructive cleanup.
- `status` remains the human-facing Backlog.md status.
- `orchestration.status_key` is the automation control state.

Recommended markdown sections:

- `## Orchestration Notes` for blocker evidence, human handoff details, policy exceptions, and review notes.
- `## Run History` for append-only human-readable run summaries. Structured run events can later be mirrored in frontmatter or sidecar storage if the file format needs to stay compact.

## Default Workflow

Default states:

```text
todo -> inprogress -> review -> complete
inprogress -> triage
review -> triage
review -> inprogress
review -> complete
triage -> todo
triage -> inprogress
```

Semantics:

- A task is eligible when all dependencies are complete and `orchestration.status_key` is claimable.
- Claiming a task sets `lease_owner`, `lease_expires_at`, increments `version`, and records a `correlation_id`.
- A transition requires expected version, idempotency key, actor, reason, and optional correlation ID.
- Successful agent work should normally land in `review`, not `complete`, when human or reviewer-agent approval is required.
- `triage` is for blocked, failed, ambiguous, or policy-exception states. It must preserve evidence and avoid infinite retry loops.
- A stale lease can be reclaimed only after lease expiry or an explicit force policy.

## Policy File

Projects may define orchestration policy in `backlog/orchestration.yml`:

```yaml
version: 1
states:
  todo:
    claimable: true
  inprogress:
    claimable: false
  review:
    terminal: false
  complete:
    terminal: true
  triage:
    claimable: false
transitions:
  todo:
    - inprogress
  inprogress:
    - review
    - triage
  review:
    - complete
    - inprogress
    - triage
  triage:
    - todo
    - inprogress
review:
  default_state: awaiting_approval
  max_attempts: 3
lease:
  default_ttl_seconds: 3600
```

If the file is absent, the default workflow above applies.

Policy constraints:

- Policy must be local to the Backlog.md project.
- Unknown policy keys should round-trip if the policy model supports rewrite.
- Validation should report unreachable states, missing terminal states, invalid transitions, invalid retry limits, and malformed lease settings.
- Library consumers may provide an in-memory policy object instead of a file path.

## API Shape

Read-only helpers for the first implementation:

- `parse_orchestration(task) -> OrchestrationState | None`
- `validate_orchestration(task, policy) -> list[ValidationIssue]`
- `list_eligible_tasks(repository, policy, now) -> list[Task]`
- `list_stale_leases(repository, now) -> list[Task]`
- `list_active_claims(repository, now) -> list[Task]`
- `summarize_orchestration(repository, policy, now) -> OrchestrationSummary`

Future mutation helpers:

- `claim_task(task_id, actor, lease_ttl, expected_version=None, correlation_id=None)`
- `release_task(task_id, actor, expected_version, reason, correlation_id=None)`
- `transition_task(task_id, to_status_key, actor, expected_version, idempotency_key, reason, correlation_id=None)`
- `record_run(task_id, run_summary, actor, correlation_id=None)`

Mutation rules:

- Re-read the current task before writing.
- Validate the transition against policy.
- Reject stale `expected_version`.
- Replay the same `idempotency_key` as a no-op when the prior completed transition matches.
- Use existing repository atomic-write and path-safety patterns.
- Preserve unknown frontmatter and markdown sections.
- Never launch an external process.

## Mappings

Symphony-style systems:

- Manager maps to policy and task selection.
- Worker maps to `runner.kind`, active claim, workspace info, and run history.
- Reviewer maps to `review.state`, reviewer identity, attempts, and transitions from `review`.
- Workspace isolation maps to optional `workspace.path` and `workspace.branch`.

`tldw_server` Kanban:

- `workflow_status_key` maps to `orchestration.status_key`.
- `version` maps directly to `orchestration.version`.
- `lease_owner` and `lease_expires_at` map directly.
- `idempotency_key` and `correlation_id` map directly.
- The Kanban event log maps to future structured run/event history plus `## Run History`.

`tldw_server` ACP:

- ACP workspace maps to optional `workspace`.
- Agent task dependency, reviewer type, max review attempts, and success criteria map to optional orchestration fields or project policy.
- Agent run history maps to `record_run` and `## Run History`.
- Governance/scheduling remains outside `backlog-md-py` and belongs to the consuming application.

## Safety Invariants

- Human-facing Backlog status and orchestration status must not be silently conflated.
- Completing automation state must not automatically archive or delete a task.
- A task with active non-expired lease should not be claimable by another actor unless policy allows forced reclaim.
- A task cannot become eligible while dependencies are incomplete.
- Review retries must have a bounded max attempt count.
- All write helpers must preserve unrelated user edits or reject on version conflict.
- Unknown fields must survive parse/write cycles.
- Validation must be usable without mutating files.

## Testing Plan

Schema and parsing:

- Round-trip tasks with no orchestration metadata.
- Round-trip tasks with full orchestration metadata.
- Preserve unknown keys in `orchestration`.
- Report invalid known fields with stable issue codes.

Policy:

- Load absent policy and apply defaults.
- Load custom policy and validate transition graph.
- Reject malformed state names, negative lease TTLs, and invalid retry limits.

Eligibility:

- Block tasks with incomplete dependencies.
- Exclude tasks with active leases.
- Include tasks with expired leases when policy allows reclaim.
- Separate Backlog status from orchestration status.

Mutation helpers once implemented:

- Reject stale version.
- Replay matching idempotency key.
- Reject conflicting idempotency key reuse.
- Preserve unrelated frontmatter and markdown sections.
- Smoke-test mutations on a copied repository fixture.

## Rollout Plan

1. Document this design and keep it separate from baseline parity work.
2. Add orchestration dataclasses or Pydantic models with parse/validate helpers only.
3. Add read-only reports for eligible tasks, stale leases, active claims, and status summaries.
4. Add optional CLI report commands if the API is stable.
5. Add version-aware mutation helpers.
6. Add optional CLI mutation commands behind explicit verbs such as `orchestration claim` and `orchestration transition`.
7. Add integration examples for Codex/Symphony-style runners and `tldw_server` Kanban/ACP mappings.

## Open Questions

- Should structured run history stay in task frontmatter, named markdown sections, or optional sidecar files?
- Should policy live only at `backlog/orchestration.yml`, or should callers be able to pass a policy file path through CLI/MCP config?
- Should leases use wall-clock ISO timestamps only, or also record monotonic duration metadata for local-only runners?
- Should review success criteria be a first-class orchestration field, or should it reuse Backlog acceptance criteria?
- How much MCP surface should expose orchestration helpers before mutation semantics are proven in the Python API?

## Recommended First Slice

Do the smallest durable thing first:

- Add an orchestration model that can parse optional frontmatter metadata and preserve unknown fields.
- Add validation issue types with stable machine-readable codes.
- Add a default policy object and optional policy file loader.
- Add read-only repository reports.
- Add tests for round-trip compatibility, validation, dependencies, lease expiry, and policy defaults.

Avoid claim/transition writes until the read-only contract is validated against real task files.
