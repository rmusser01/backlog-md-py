# BatonBot-Inspired Orchestration Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add run history, safe orchestration mutations, queue visibility, explicit task splitting, and stronger agent guidance while keeping Markdown files as the source of truth.

**Architecture:** Add focused orchestration modules for policy loading, run-history parsing, and service-owned mutations. Keep `MutableRepository` responsible only for safe Markdown access helpers; CLI, MCP, browser, TUI, and generated agent instructions remain thin adapters over the shared orchestration service and report model.

**Tech Stack:** Python dataclasses, PyYAML, Click CLI, SDK-free MCP protocol, existing browser/TUI services, pytest, project write locks in `backlog_py.runtime.locks`.

---

## Reference Documents

- Spec: `docs/superpowers/specs/2026-06-26-batonbot-inspired-orchestration-improvements-design.md`
- Existing orchestration substrate: `src/backlog_py/orchestration/models.py`, `src/backlog_py/orchestration/reports.py`
- Existing task mutation surface: `src/backlog_py/core/repository.py`, `src/backlog_py/runtime/mutations.py`
- Existing adapter surfaces: `src/backlog_py/cli/main.py`, `src/backlog_py/mcp/catalog.py`, `src/backlog_py/mcp/tools.py`, `src/backlog_py/browser/service.py`, `src/backlog_py/tui/data.py`, `src/backlog_py/core/agents.py`

## File Map

- Create: `src/backlog_py/orchestration/history.py`
  - Parse, render, append, validate, cap, and fingerprint `RUN_HISTORY` entries.
- Create: `src/backlog_py/orchestration/policy.py`
  - Load `backlog/orchestration.yml`, fall back to `OrchestrationPolicy.default()`, and validate policy shape.
- Create: `src/backlog_py/orchestration/service.py`
  - Own `record_run`, `claim_task`, `release_task`, `transition_task`, `queue`, and `split_task`.
  - Acquire project write locks for mutations, re-read tasks inside the lock, then call narrow repository helpers.
- Modify: `src/backlog_py/orchestration/models.py`
  - Add run-event, queue-category, queue-item, mutation-result, and split-result dataclasses plus typed errors.
- Modify: `src/backlog_py/orchestration/reports.py`
  - Preserve existing read-only helpers, but delegate richer queue categorization to shared model/service helpers.
- Modify: `src/backlog_py/orchestration/__init__.py`
  - Export new public orchestration models, helpers, and service.
- Modify: `src/backlog_py/core/repository.py`
  - Add narrow Markdown source/frontmatter helper methods needed by the service. Do not add workflow policy here.
- Modify: `src/backlog_py/runtime/mutations.py`
  - Register orchestration mutation surfaces for lock/auto-commit inventory.
- Modify: `src/backlog_py/cli/main.py`
  - Add `orchestration` command group and plain/JSON output.
- Modify: `src/backlog_py/mcp/catalog.py`, `src/backlog_py/mcp/tools.py`
  - Add orchestration MCP tools with JSON-safe schemas and results.
- Modify: `src/backlog_py/browser/service.py`, `src/backlog_py/browser/assets/board.js`, `src/backlog_py/browser/assets/board.css`, `src/backlog_py/browser/templates/board.html`
  - Add read-only queue badges and run-history payload/display.
- Modify: `src/backlog_py/tui/data.py`, `src/backlog_py/tui/models.py`, `src/backlog_py/tui/widgets.py`, `src/backlog_py/tui/screens.py`
  - Add read-only queue category data and display where the current TUI model supports it.
- Modify: `src/backlog_py/core/agents.py`
  - Add generated orchestration guidance and fallback commands.
- Tests:
  - Create `tests/test_orchestration_history.py`
  - Create `tests/test_orchestration_policy.py`
  - Create `tests/test_orchestration_service.py`
  - Create `tests/test_cli_orchestration.py`
  - Modify `tests/test_orchestration.py`
  - Modify `tests/test_mutation_inventory.py`
  - Modify `tests/test_mcp_protocol_sdk_free.py`
  - Modify `tests/test_mcp_tools_locking.py`
  - Modify `tests/test_browser_service.py`
  - Modify `tests/test_tui_data.py`
  - Modify `tests/test_agent_instructions.py`

## Cross-Cutting Rules

- Execute implementation in a clean dedicated worktree from the design/plan
  branch. If a target file is already dirty before a task begins, inspect it and
  treat the pre-existing diff as user-owned; do not stage unrelated hunks into
  orchestration commits.
- Use `superpowers:test-driven-development` before implementation tasks.
- Use the existing project write lock for every orchestration mutation. The service should own locking; CLI and MCP orchestration adapters should not wrap service calls in a second project lock.
- Treat missing `orchestration.version` as version `0`; the first versioned mutation writes version `1`.
- Check idempotency replay inside the lock before checking stale `expected_version`.
- Compute idempotency fingerprints from normalized caller input, excluding generated `event_id`, `timestamp`, Markdown formatting, and storage ordering.
- Keep queue reports read-only and derived; do not store queue state.
- Browser and TUI are read-only for this feature. Do not add claim/transition UI controls in this plan.

### Task 1: Repository Helpers And Mutation Inventory

**Files:**
- Modify: `src/backlog_py/core/repository.py`
- Modify: `src/backlog_py/runtime/mutations.py`
- Modify: `tests/test_task_mutations.py`
- Modify: `tests/test_mutation_inventory.py`

- [ ] **Step 1: Write failing repository-helper tests**

Add tests for a narrow helper that rewrites an existing task source without changing the task path:

```python
def test_replace_task_source_validates_and_preserves_path(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = MutableRepository.from_path(repo)
    task = repository.get_task("TASK-1")
    source = task.raw_source.replace("status: To Do", "status: In Progress")

    updated = repository.replace_task_source("TASK-1", source)

    assert updated.path == task.path
    assert updated.status == "In Progress"
```

Add a second test that malformed YAML is rejected and the original file remains unchanged.

Add a third test that changing frontmatter `id` is rejected so orchestration
helpers cannot accidentally replace one task with another task's source.

Add tests for a nested frontmatter helper:

```python
def test_replace_task_frontmatter_values_updates_nested_orchestration(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = MutableRepository.from_path(repo)

    updated = repository.replace_task_frontmatter_values(
        "TASK-1",
        {"orchestration": {"status_key": "inprogress", "version": 1}},
    )

    assert updated.parsed.frontmatter["orchestration"]["version"] == 1
```

This helper should replace explicit top-level values only; callers pass the full
nested `orchestration` mapping they want persisted.

- [ ] **Step 2: Run helper tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_task_mutations.py::test_replace_task_source_validates_and_preserves_path -q
```

Expected: fail because `replace_task_source` does not exist.

- [ ] **Step 3: Implement the repository helper**

Add `MutableRepository.replace_task_source(task_id: str, source: str) -> TaskRecord`.

Implementation requirements:

- Look up active task with `get_task`.
- Resolve the existing task path through `_mutation_path`.
- Parse `source` with `parse_task_markdown` before writing.
- Reject sources whose parsed frontmatter `id` does not match `task_id`.
- Write with `_atomic_write_text`.
- Return `_load_task` for the same path.
- Do not interpret orchestration policy or run-history content in this helper.

Also add `MutableRepository.replace_task_frontmatter_values(task_id: str, updates: dict[str, object]) -> TaskRecord`.

Implementation requirements:

- Use the existing private `_replace_frontmatter_values` function internally.
- Parse and write through `replace_task_source`.
- Accept full replacement values for nested keys such as `orchestration`.
- Do not implement deep-merge behavior in the repository helper; orchestration
  state merge rules belong in `OrchestrationService`.

- [ ] **Step 4: Add mutation inventory entries**

Add these entries to `MUTATION_SURFACES`:

```python
MutationSurface(
    "orchestration_record_run",
    ("backlog_py.cli.main", "backlog_py.mcp.tools"),
    "project",
    "Appends orchestration run history and may update orchestration frontmatter.",
)
```

Also add entries for `orchestration_claim`, `orchestration_release`, `orchestration_transition`, and `orchestration_split`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_task_mutations.py tests/test_mutation_inventory.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/backlog_py/core/repository.py src/backlog_py/runtime/mutations.py tests/test_task_mutations.py tests/test_mutation_inventory.py
git commit -m "feat: add orchestration markdown mutation helpers"
```

### Task 2: Run-History Model, Parser, Renderer, And Idempotency

**Files:**
- Create: `src/backlog_py/orchestration/history.py`
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Create: `tests/test_orchestration_history.py`

- [ ] **Step 1: Write failing parse/render tests**

Cover:

- empty task body returns no events and no issues,
- valid `SECTION:RUN_HISTORY` with one `RUN_HISTORY_ENTRY` parses,
- malformed markers produce stable parse issues,
- append creates the section when missing,
- append preserves existing Markdown outside the owned section,
- size caps truncate/reject according to named constants.

Example test shape:

```python
def test_append_run_history_creates_owned_section():
    source = "---\nid: TASK-1\ntitle: Task\nstatus: To Do\n---\n\n## Description\n\nBody\n"
    event = OrchestrationRunEvent(
        event_id="run-1",
        type="record_run",
        actor="codex",
        timestamp="2026-06-26T18:04:00Z",
        result="succeeded",
        summary="Implemented and verified.",
    )

    updated = append_run_history_entry(source, event)

    assert "<!-- SECTION:RUN_HISTORY:BEGIN -->" in updated
    assert "<!-- RUN_HISTORY_ENTRY:BEGIN -->" in updated
    assert "Implemented and verified." in updated
    assert "type: record_run" in updated
```

- [ ] **Step 2: Write failing idempotency tests**

Cover:

- matching key and matching canonical fingerprint returns the prior event,
- same key with different summary raises `OrchestrationIdempotencyConflict`,
- generated `event_id` and `timestamp` do not affect the fingerprint,
- rendered Markdown whitespace does not affect the fingerprint,
- rendered YAML uses the stable metadata key `type`, not `event_type`.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_history.py -q
```

Expected: import/function failures.

- [ ] **Step 4: Add run-history dataclasses and errors**

In `models.py`, add:

- `OrchestrationRunEvent`
- `RunHistoryParseIssue`
- `RunHistoryParseResult`
- `OrchestrationIdempotencyConflict`
- `RunHistoryParseError`

Use plain dataclasses and simple string fields so MCP JSON output stays straightforward.

- [ ] **Step 5: Implement `history.py`**

Add:

- constants `MAX_RUN_HISTORY_SUMMARY_CHARS`, `MAX_RUN_HISTORY_METADATA_CHARS`, `MAX_RUN_HISTORY_FILES`, `MAX_RUN_HISTORY_VERIFICATION_COMMANDS`,
- `parse_run_history(source: str) -> RunHistoryParseResult`,
- `render_run_history_entry(event: OrchestrationRunEvent) -> str`,
- `append_run_history_entry(source: str, event: OrchestrationRunEvent) -> str`,
- `canonical_event_fingerprint(event: OrchestrationRunEvent) -> str`,
- `find_idempotency_match(events, candidate)`.

Implementation notes:

- Use `yaml.safe_load` and `yaml.safe_dump(sort_keys=False, allow_unicode=False)`.
- Render the event type as YAML key `type`. If the Python dataclass uses a
  different attribute name internally, explicitly map it to/from `type` in the
  parser and renderer.
- Store event body as optional Markdown summary text after the YAML block.
- Do not store raw logs or unbounded command output.
- Return parse issues instead of raising for read paths.
- Raise typed errors only on mutation paths that must fail closed.

- [ ] **Step 6: Export public symbols**

Update `src/backlog_py/orchestration/__init__.py` with the new dataclasses, constants, and helpers that are intended for adapters/tests.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_history.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/backlog_py/orchestration/history.py src/backlog_py/orchestration/models.py src/backlog_py/orchestration/__init__.py tests/test_orchestration_history.py
git commit -m "feat: add orchestration run history"
```

### Task 3: Policy Loader And Typed Validation Errors

**Files:**
- Create: `src/backlog_py/orchestration/policy.py`
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Create: `tests/test_orchestration_policy.py`
- Modify: `tests/test_orchestration.py`

- [ ] **Step 1: Write failing policy-loader tests**

Cover:

- missing `backlog/orchestration.yml` returns `OrchestrationPolicy.default()`,
- valid custom states/transitions load,
- unknown transition target returns validation errors,
- unreadable or non-mapping YAML raises `OrchestrationPolicyError`,
- default lease TTL and review attempts are preserved when omitted.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_policy.py -q
```

Expected: fail because `policy.py` does not exist.

- [ ] **Step 3: Add typed errors**

In `models.py`, add:

- `OrchestrationPolicyError`
- `OrchestrationValidationError`
- `OrchestrationVersionConflict`
- `OrchestrationLeaseConflict`
- `OrchestrationTransitionError`
- `TaskSplitError`

Keep exception payloads simple: message plus optional `details: dict[str, object]`.

- [ ] **Step 4: Implement policy loading**

Add `load_orchestration_policy(project: BacklogProject) -> OrchestrationPolicy`.

Implementation requirements:

- Read `project.backlog_dir / "orchestration.yml"`.
- Return default policy when absent.
- Accept YAML shape:

```yaml
states:
  todo:
    claimable: true
  complete:
    terminal: true
transitions:
  todo: [inprogress]
  inprogress: [review, triage]
```

- Normalize state keys using the existing model normalization behavior.
- Run `validate_policy` before returning.
- Raise `OrchestrationPolicyError` with validation issue details when invalid.

- [ ] **Step 5: Export policy loader**

Update `__init__.py` to export `load_orchestration_policy` and typed errors.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_policy.py tests/test_orchestration.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/orchestration/policy.py src/backlog_py/orchestration/models.py src/backlog_py/orchestration/__init__.py tests/test_orchestration_policy.py tests/test_orchestration.py
git commit -m "feat: load orchestration policy"
```

### Task 4: Orchestration Service Foundation And `record_run`

**Files:**
- Create: `src/backlog_py/orchestration/service.py`
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Create: `tests/test_orchestration_service.py`

- [ ] **Step 1: Write failing service tests for `record_run`**

Cover:

- appends run history to a task,
- refuses malformed existing run history,
- idempotency replay returns the prior event and does not rewrite the file,
- idempotency conflict raises `OrchestrationIdempotencyConflict`,
- record-only run does not increment orchestration version,
- record-run with explicit orchestration status update increments version when
  expected version matches,
- stale expected version raises `OrchestrationVersionConflict`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py -q
```

Expected: fail because `service.py` does not exist.

- [ ] **Step 3: Add service result dataclasses**

In `models.py`, add:

- `OrchestrationMutationResult`
- `OrchestrationRecordRunRequest`
- `OrchestrationStateUpdate`
- `OrchestrationActorContext`

Keep these dataclasses serialization-friendly. Prefer `dict[str, object]` for adapter details.

- [ ] **Step 4: Implement service skeleton**

Add `OrchestrationService(project: BacklogProject, *, now: Callable[[], datetime] | None = None)`.

Service responsibilities:

- create `MutableRepository(project, refresh_remote_refs=False)` inside lock-held mutation callbacks,
- implement `record_run(task_id, *, actor, result, summary, files=(), verification=(), idempotency_key=None, expected_version=None, state_update=None)`,
- accept only this narrow state update payload for `record_run`: `status_key`,
  `lease_owner`, `lease_expires_at`, `correlation_id`, `review_state`,
  `reviewer`, `review_attempts`, and `review_max_attempts`,
- reject a non-`None` `state_update` unless `expected_version` is supplied,
- increment `orchestration.version` only when `state_update` changes persisted
  orchestration frontmatter,
- use `with_project_write_lock(project, "orchestration_record_run", mutate)`,
- re-read task inside the lock,
- parse policy and run history inside the lock,
- perform idempotency replay before expected-version checks,
- write through `MutableRepository.replace_task_source`.

- [ ] **Step 5: Implement actor defaulting helper**

Add a helper that resolves actor in this order:

1. explicit actor,
2. adapter-provided identity,
3. `BACKLOG_ACTOR`,
4. local username plus hostname,
5. `"unknown"`.

Test environment fallback with monkeypatching to keep tests deterministic.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py tests/test_orchestration_history.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/orchestration/service.py src/backlog_py/orchestration/models.py src/backlog_py/orchestration/__init__.py tests/test_orchestration_service.py
git commit -m "feat: add orchestration record-run service"
```

### Task 5: Queue Categorization And Read-Only Reports

**Files:**
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/reports.py`
- Modify: `src/backlog_py/orchestration/service.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_orchestration_service.py`

- [ ] **Step 1: Write failing queue category tests**

Cover every category:

- `invalid`,
- `terminal`,
- `claimed`,
- `stale_claim`,
- `blocked_by_dependencies`,
- `eligible`,
- `in_workflow`.

Add overlap tests for precedence:

- invalid plus expired lease returns `invalid`,
- terminal plus active lease returns `terminal`,
- claimable with incomplete dependency returns `blocked_by_dependencies`.

- [ ] **Step 2: Write failing queue scope tests**

Cover:

- default queue includes active tasks only,
- active task with plain `Done`/`complete` status maps to `terminal`,
- completed tasks are included only when `include_completed=True`,
- archive files are excluded.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration.py tests/test_orchestration_service.py -q
```

Expected: queue model/import failures.

- [ ] **Step 4: Add queue dataclasses**

In `models.py`, add:

- `QueueCategory` as a string enum or `Literal` alias with runtime validation,
- `OrchestrationQueueItem`,
- `OrchestrationQueueReport`.

Include task ID, path, title, current version, effective status, category, validation issues, dependency IDs, lease owner/expiry, and run-history issue summaries.

- [ ] **Step 5: Implement queue categorization**

In `reports.py` or `service.py`, implement a pure categorizer function:

```python
def categorize_task(task: TaskRecord, *, policy: OrchestrationPolicy, complete_task_ids: set[str], now: datetime, run_history_issues: Sequence[ValidationIssue]) -> OrchestrationQueueItem:
    ...
```

Apply precedence exactly:

1. `invalid`
2. `terminal`
3. `claimed`
4. `stale_claim`
5. `blocked_by_dependencies`
6. `eligible`
7. `in_workflow`

- [ ] **Step 6: Implement service queue report**

Add `OrchestrationService.queue(include_completed: bool = False)`.

Use `ReadOnlyRepository.list_tasks()` by default. Add completed tasks from `list_completed_tasks()` only when `include_completed=True`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration.py tests/test_orchestration_service.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/backlog_py/orchestration/models.py src/backlog_py/orchestration/reports.py src/backlog_py/orchestration/service.py src/backlog_py/orchestration/__init__.py tests/test_orchestration.py tests/test_orchestration_service.py
git commit -m "feat: add orchestration queue categories"
```

### Task 6: CLI And MCP `record_run`

**Files:**
- Modify: `src/backlog_py/cli/main.py`
- Modify: `src/backlog_py/mcp/catalog.py`
- Modify: `src/backlog_py/mcp/tools.py`
- Create: `tests/test_cli_orchestration.py`
- Modify: `tests/test_mcp_protocol_sdk_free.py`
- Modify: `tests/test_mcp_tools_locking.py`

- [ ] **Step 1: Write failing CLI tests**

Cover:

- `backlog-py orchestration record-run TASK-1 --actor codex --result succeeded --summary "done" --plain` prints the new event ID,
- `--json` returns task ID, version, event ID, run-history event IDs, and queue category,
- malformed task returns non-zero exit and actionable error text.

- [ ] **Step 2: Write failing MCP tests**

Cover:

- `orchestration_record_run` appears in `tools/list`,
- calling it appends run history,
- response is JSON-safe and includes task ID, task path, current orchestration
  version, effective queue category, run-history event IDs, validation issues,
  and conflict details where applicable,
- mutation inventory includes `orchestration_record_run`,
- orchestration MCP mutations do not call `backlog_py.mcp.tools._locked`; they
  rely on the service's project lock instead.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py -q
```

Expected: missing command/tool failures.

- [ ] **Step 4: Add CLI command group**

In `cli/main.py`, add `@main.group("orchestration")` and a `record-run` subcommand.

Options:

- `--actor`
- `--result`
- `--summary`
- `--file` multiple changed files
- `--verification` multiple commands
- `--idempotency-key`
- `--expected-version`
- `--status-key` optional orchestration status update
- `--lease-owner` optional orchestration lease owner update
- `--lease-expires-at` optional orchestration lease expiry update
- `--correlation-id` optional orchestration correlation ID update
- `--review-state` optional orchestration review state update
- `--reviewer` optional orchestration reviewer update
- `--review-attempts` optional orchestration review attempts update
- `--review-max-attempts` optional orchestration review max attempts update
- `--json`
- `--plain`

Plain output is human-readable. JSON output is the stable automation surface.

- [ ] **Step 5: Add MCP tool**

In `mcp/tools.py`, add `orchestration_record_run(project, taskId/task_id, ...)`.

In `mcp/catalog.py`, add the schema with camelCase and snake_case aliases where existing MCP tools use both. Include optional `stateUpdate`/`state_update` fields matching `OrchestrationStateUpdate`; require `expectedVersion`/`expected_version` whenever state update fields are provided.

Do not wrap orchestration MCP mutations in `_locked`; `OrchestrationService`
owns the project write lock. Double-locking can deadlock because the project
lock is cross-process and not reentrant.

All orchestration MCP tool responses should share a response builder that emits:

- `taskId`,
- `path`,
- `version`,
- `queueCategory`,
- `runHistoryEventIds`,
- `validationIssues`,
- `conflict` when the operation failed due to version, lease, transition, or
  idempotency conflict.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py tests/test_orchestration_service.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/cli/main.py src/backlog_py/mcp/catalog.py src/backlog_py/mcp/tools.py tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py
git commit -m "feat: expose orchestration record-run adapters"
```

### Task 7: Claim, Release, Transition Service And Adapters

**Files:**
- Modify: `src/backlog_py/orchestration/service.py`
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/cli/main.py`
- Modify: `src/backlog_py/mcp/catalog.py`
- Modify: `src/backlog_py/mcp/tools.py`
- Modify: `tests/test_orchestration_service.py`
- Modify: `tests/test_cli_orchestration.py`
- Modify: `tests/test_mcp_protocol_sdk_free.py`
- Modify: `tests/test_mcp_tools_locking.py`

- [ ] **Step 1: Write failing service mutation tests**

Cover:

- `claim_task` on missing version `0` writes version `1`, status `inprogress`, active lease, and run-history event,
- claim with stale expected version raises `OrchestrationVersionConflict`,
- claim with another active lease raises `OrchestrationLeaseConflict`,
- stale lease can be reclaimed,
- `release_task` clears lease and increments version,
- `transition_task` enforces policy transitions and increments version,
- idempotency replay happens before expected-version mismatch.

- [ ] **Step 2: Run service tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py -q
```

Expected: missing methods/failing behavior.

- [ ] **Step 3: Implement service methods**

Add:

- `claim_task(task_id, *, actor, expected_version, idempotency_key=None, lease_ttl_seconds=None, reason=None)`,
- `release_task(task_id, *, actor, expected_version, idempotency_key=None, reason=None)`,
- `transition_task(task_id, to_status, *, actor, expected_version, idempotency_key=None, reason=None)`.

Implementation requirements:

- Re-read inside lock.
- Validate policy and metadata before mutation.
- Treat missing version as `0`.
- Generate UTC ISO timestamps with `Z`.
- Record run-history entries for accepted mutations.
- Update `orchestration.status_key`, `version`, `lease_owner`, `lease_expires_at`, and `idempotency_key` as appropriate.

- [ ] **Step 4: Write failing CLI/MCP tests**

Cover command/tool existence and one happy path each for claim, release, transition, plus one conflict response.

MCP assertions must verify the common response contract for each tool: task ID,
path, current version, queue category, run-history event IDs, validation issues,
and conflict details where applicable.

- [ ] **Step 5: Implement CLI subcommands**

Add:

- `orchestration status`
- `orchestration eligible`
- `orchestration claims`
- `orchestration stale-leases`
- `orchestration queue`
- `orchestration claim`
- `orchestration release`
- `orchestration transition`

Options must include `--json`, `--plain`, `--actor`, `--expected-version`, `--idempotency-key`, `--reason`, and `--include-completed` where relevant.

- [ ] **Step 6: Implement MCP tools**

Add:

- `orchestration_status`
- `orchestration_queue`
- `orchestration_eligible`
- `orchestration_claims`
- `orchestration_stale_leases`
- `orchestration_claim_task`
- `orchestration_release_task`
- `orchestration_transition_task`

Return typed conflict details with current version and lease owner when applicable.

Use the shared orchestration MCP response builder from Task 6. Do not call
`_locked` around service mutations because the service owns locking.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/backlog_py/orchestration/service.py src/backlog_py/orchestration/models.py src/backlog_py/cli/main.py src/backlog_py/mcp/catalog.py src/backlog_py/mcp/tools.py tests/test_orchestration_service.py tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py
git commit -m "feat: add orchestration workflow mutations"
```

### Task 8: Explicit Task Splitting And Continuations

**Files:**
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/service.py`
- Modify: `src/backlog_py/cli/main.py`
- Modify: `src/backlog_py/mcp/catalog.py`
- Modify: `src/backlog_py/mcp/tools.py`
- Modify: `tests/test_orchestration_service.py`
- Modify: `tests/test_cli_orchestration.py`
- Modify: `tests/test_mcp_protocol_sdk_free.py`

- [ ] **Step 1: Write failing split service tests**

Cover:

- child mode creates child tasks with `parent_task_id`,
- continuation mode creates ordered follow-up tasks,
- parent run history gets a split event,
- parent status is preserved unless explicit transition is requested,
- generated circular dependencies are rejected,
- idempotency replay returns existing child IDs without duplicate creation,
- idempotency replay returns the existing parent split event ID,
- same idempotency key with different item payload raises `OrchestrationIdempotencyConflict`,
- same idempotency key with a different split mode raises `OrchestrationIdempotencyConflict`,
- same idempotency key with different inherited-dependency options raises
  `OrchestrationIdempotencyConflict`,
- same idempotency key with a different parent task ID raises
  `OrchestrationIdempotencyConflict`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py -q
```

Expected: missing split method/failing behavior.

- [ ] **Step 3: Add split request/result models**

In `models.py`, add:

- `TaskSplitMode`
- `TaskSplitItem`
- `TaskSplitRequest`
- `TaskSplitResult`

- [ ] **Step 4: Implement `split_task`**

Use normal `MutableRepository.create_task` inside the service lock.

Implementation requirements:

- Require `expected_version` for new splits.
- Allow idempotency replay before stale version checks.
- Store enough caller-supplied split metadata in the parent split event
  fingerprint to detect changed parent task ID, mode, item payload, dependency
  inheritance options, and other caller-supplied split options. Store created
  child or continuation task IDs on the parent split event for replay responses;
  they are generated outputs, not caller-supplied fingerprint inputs.
- On matching replay, return the previously created child or continuation IDs
  plus the parent split event ID without creating duplicate tasks.
- Create child tasks with `parent_task_id`.
- For continuation mode, set dependencies according to request options and preserve sequence with `ordinal`.
- Copy selected parent context into description/plan, but do not duplicate full raw history.
- Reject circular dependencies before writing any child task.
- Append parent split event and increment parent version.

- [ ] **Step 5: Add CLI and MCP split surfaces**

CLI:

```bash
backlog-py orchestration split TASK-1 --mode child --expected-version 5 --idempotency-key split-task-1 --item "Add parser coverage"
```

MCP:

- `orchestration_split_task`

Schemas should accept multiple items and dependency-inheritance options.

MCP split response must use the shared orchestration response contract and also
include created child or continuation task IDs plus the parent split event ID.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration_service.py tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/orchestration/models.py src/backlog_py/orchestration/service.py src/backlog_py/cli/main.py src/backlog_py/mcp/catalog.py src/backlog_py/mcp/tools.py tests/test_orchestration_service.py tests/test_cli_orchestration.py tests/test_mcp_protocol_sdk_free.py
git commit -m "feat: add orchestration task splitting"
```

### Task 9: Browser And TUI Read-Only Visibility

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Modify: `src/backlog_py/browser/assets/board.js`
- Modify: `src/backlog_py/browser/assets/board.css`
- Modify: `src/backlog_py/browser/templates/board.html`
- Modify: `src/backlog_py/tui/data.py`
- Modify: `src/backlog_py/tui/models.py`
- Modify: `src/backlog_py/tui/widgets.py`
- Modify: `src/backlog_py/tui/screens.py`
- Modify: `tests/test_browser_service.py`
- Modify: `tests/test_tui_data.py`

- [ ] **Step 1: Write failing browser payload tests**

Cover:

- task board payload includes queue category,
- task detail payload includes run-history events,
- malformed run history appears as read-only validation issues,
- queue category filters are exposed in the browser payload/API and can filter
  the task list without mutating task files,
- no browser endpoint mutates orchestration state.

- [ ] **Step 2: Write failing TUI data tests**

Cover queue category and queue category filter state in task view models. Keep
tests data-level if widget testing is brittle.

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_tui_data.py -q
```

Expected: missing payload fields.

- [ ] **Step 4: Implement browser read-only fields**

Use `OrchestrationService.queue()` and run-history parser for payloads.

UI requirements:

- Add small category badge on cards.
- Add task-detail run-history list.
- Add queue category filters to the read-only task list/board controls.
- Add stale lease and invalid metadata indicators.
- Do not add claim, release, or transition buttons.

- [ ] **Step 5: Implement TUI read-only fields**

Add queue category to TUI models and display where the existing task widgets can
show compact metadata without disrupting layout. Add read-only queue category
filter support if the current screen already supports filtering; otherwise add
the data model and a focused screen-level test for the filter behavior.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_tui_data.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/browser/service.py src/backlog_py/browser/assets/board.js src/backlog_py/browser/assets/board.css src/backlog_py/browser/templates/board.html src/backlog_py/tui/data.py src/backlog_py/tui/models.py src/backlog_py/tui/widgets.py src/backlog_py/tui/screens.py tests/test_browser_service.py tests/test_tui_data.py
git commit -m "feat: show orchestration queue state in UI"
```

### Task 10: Agent Instructions, Docs, And Final Verification

**Files:**
- Modify: `src/backlog_py/core/agents.py`
- Modify: `tests/test_agent_instructions.py`
- Modify: `docs/integration.md`
- Modify: `docs/README.md`
- Modify: `README.md` only if a short feature mention is warranted

- [ ] **Step 1: Write failing agent instruction tests**

Assert generated instructions mention:

- `project_status` and orchestration queue/eligible checks,
- claim before work when orchestration metadata is enabled,
- record run summaries with files and verification commands,
- transition to review/triage rather than direct terminal states unless policy allows,
- troubleshooting stale leases, version conflicts, malformed run history, MCP discovery, and daemon health,
- explicit CLI fallback commands.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_agent_instructions.py -q
```

Expected: missing instruction text.

- [ ] **Step 3: Update generated agent guidance**

Modify only the owned Backlog.md instruction section in `core/agents.py`. Keep text concise and operational.

- [ ] **Step 4: Update integration docs**

Add a concise orchestration section to `docs/integration.md` covering:

- CLI commands,
- MCP tools,
- run-history format,
- queue categories,
- mutation conflict semantics,
- non-goals: no agent runner, no LLM proxy.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_agent_instructions.py tests/test_package_metadata.py -q
```

Expected: pass.

- [ ] **Step 6: Run final targeted suite**

Run:

```bash
uv run --extra dev python -m pytest tests/test_orchestration.py tests/test_orchestration_history.py tests/test_orchestration_policy.py tests/test_orchestration_service.py tests/test_cli_orchestration.py -q
uv run --extra dev python -m pytest tests/test_mcp_resources.py tests/test_mcp_protocol_sdk_free.py tests/test_mcp_tools_locking.py -q
uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_tui_data.py tests/test_agent_instructions.py -q
```

Expected: all pass.

- [ ] **Step 7: Run repository checks**

Run:

```bash
git diff --check
uv run --extra dev python -m pytest -q
```

Expected: `git diff --check` has no output, and the full test suite passes.

- [ ] **Step 8: Commit**

```bash
git add src/backlog_py/core/agents.py tests/test_agent_instructions.py docs/integration.md docs/README.md README.md
git commit -m "docs: document orchestration coordination workflow"
```

## Execution Notes

- If the implementation becomes too large, stop after a complete phase with passing tests and ask whether to split the remaining phases into a follow-up branch.
- If browser or TUI layout changes become more than a compact badge/list display, defer polish to a separate UI task; this plan intentionally keeps UI read-only.
- If archive listing is needed for queue reports, add a repository helper and tests in the same task that enables archive inclusion. Otherwise keep archive excluded as specified.
- Do not change the existing LLM/agent boundary. This feature coordinates work; it does not run agents.
