# Orchestration Read-Only Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first orchestration substrate slice: optional task orchestration metadata parsing, policy validation, and read-only repository reports.

**Architecture:** Add a focused `backlog_py.orchestration` package that depends on existing parsed task records but does not alter task mutation behavior. Keep all write helpers out of scope; the package only interprets frontmatter, validates known fields, applies a default workflow policy, and reports eligible tasks, active claims, stale leases, and status summaries.

**Tech Stack:** Python 3.11+ dataclasses, `datetime`, existing `PyYAML`, existing `ReadOnlyRepository`/`TaskRecord`, pytest.

---

## File Structure

- Create `src/backlog_py/orchestration/__init__.py`
  - Public exports for models and read-only helper functions.
- Create `src/backlog_py/orchestration/models.py`
  - Dataclasses for orchestration state, policy, validation issues, and reports.
  - Default workflow policy and policy validation.
  - Metadata parsing and task validation helpers.
- Create `src/backlog_py/orchestration/reports.py`
  - Repository-level read-only reports: eligible tasks, stale leases, active claims, and summary.
  - Dependency gating against active and completed tasks.
- Create `tests/test_orchestration.py`
  - Unit tests for parsing, validation, policy defaults, eligibility, leases, and summaries.
- Modify `README.md`
  - Add a short section describing the optional orchestration substrate and first-slice boundaries.

## Task 1: Add Orchestration Models and Parser

**Files:**
- Create: `src/backlog_py/orchestration/models.py`
- Create: `src/backlog_py/orchestration/__init__.py`
- Test: `tests/test_orchestration.py`

- [x] **Step 1: Write failing parser tests**

Add tests that prove:

```python
def test_parse_orchestration_returns_none_when_metadata_missing():
    task = _task_with_frontmatter({"id": "TASK-1", "title": "Plain", "status": "To Do"})

    assert parse_orchestration(task) is None


def test_parse_orchestration_preserves_known_and_unknown_fields():
    task = _task_with_frontmatter({
        "id": "TASK-1",
        "title": "Claimed",
        "status": "To Do",
        "orchestration": {
            "status_key": "todo",
            "version": 3,
            "lease_owner": "codex-agent-1",
            "lease_expires_at": "2026-05-13T07:00:00Z",
            "correlation_id": "run-1",
            "idempotency_key": "claim-1",
            "workspace": {"path": ".worktrees/task-1", "branch": "codex/task-1"},
            "runner": {"kind": "codex", "profile": "default"},
            "review": {"state": "awaiting_approval", "reviewer": "human", "attempts": 1, "max_attempts": 3},
            "custom": {"preserve": True},
        },
    })

    state = parse_orchestration(task)

    assert state is not None
    assert state.status_key == "todo"
    assert state.version == 3
    assert state.workspace.path == ".worktrees/task-1"
    assert state.extra == {"custom": {"preserve": True}}
```

- [x] **Step 2: Run parser tests to verify failure**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: FAIL because `backlog_py.orchestration` does not exist.

- [x] **Step 3: Implement minimal dataclasses and parser**

Create frozen dataclasses:

```python
@dataclass(frozen=True)
class OrchestrationWorkspace:
    path: str | None = None
    branch: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationRunner:
    kind: str | None = None
    profile: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationReview:
    state: str | None = None
    reviewer: str | None = None
    attempts: int | None = None
    max_attempts: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationState:
    status_key: str | None = None
    version: int | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    workspace: OrchestrationWorkspace | None = None
    runner: OrchestrationRunner | None = None
    review: OrchestrationReview | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
```

Add `parse_orchestration(task_or_frontmatter: Any) -> OrchestrationState | None`.

- [x] **Step 4: Run parser tests**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: parser tests pass.

- [x] **Step 5: Commit parser slice**

```bash
git add src/backlog_py/orchestration tests/test_orchestration.py
git commit -m "feat: parse orchestration metadata"
```

## Task 2: Add Default Policy and Validation

**Files:**
- Modify: `src/backlog_py/orchestration/models.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Test: `tests/test_orchestration.py`

- [x] **Step 1: Write failing validation tests**

Add tests that prove:

```python
def test_default_policy_accepts_expected_transitions():
    policy = OrchestrationPolicy.default()

    assert policy.can_transition("todo", "inprogress")
    assert policy.can_transition("inprogress", "review")
    assert policy.can_transition("review", "complete")
    assert not policy.can_transition("complete", "todo")


def test_validate_orchestration_reports_invalid_known_fields():
    task = _task_with_frontmatter({
        "id": "TASK-1",
        "title": "Invalid",
        "status": "To Do",
        "orchestration": {
            "status_key": "missing",
            "version": -1,
            "lease_expires_at": "not-a-date",
            "review": {"attempts": 4, "max_attempts": 3},
        },
    })

    issues = validate_orchestration(task, OrchestrationPolicy.default())

    assert {issue.code for issue in issues} >= {
        "unknown_status_key",
        "invalid_version",
        "invalid_lease_expires_at",
        "review_attempts_exceed_max",
    }
```

- [x] **Step 2: Run validation tests to verify failure**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: FAIL because policy and validation are not implemented.

- [x] **Step 3: Implement policy and validation**

Add dataclasses:

```python
@dataclass(frozen=True)
class WorkflowStatePolicy:
    claimable: bool = False
    terminal: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


@dataclass(frozen=True)
class OrchestrationPolicy:
    states: dict[str, WorkflowStatePolicy]
    transitions: dict[str, tuple[str, ...]]
    default_review_max_attempts: int = 3
    default_lease_ttl_seconds: int = 3600
```

Implement:

- `OrchestrationPolicy.default()`
- `can_transition(from_status, to_status)`
- `is_claimable(status_key)`
- `is_terminal(status_key)`
- `validate_policy(policy)`
- `validate_orchestration(task_or_frontmatter, policy=None)`

- [x] **Step 4: Run validation tests**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: validation tests pass.

- [x] **Step 5: Commit validation slice**

```bash
git add src/backlog_py/orchestration tests/test_orchestration.py
git commit -m "feat: validate orchestration metadata"
```

## Task 3: Add Read-Only Repository Reports

**Files:**
- Create: `src/backlog_py/orchestration/reports.py`
- Modify: `src/backlog_py/orchestration/__init__.py`
- Test: `tests/test_orchestration.py`

- [x] **Step 1: Write failing report tests**

Add tests that prove:

```python
def test_list_eligible_tasks_blocks_active_leases_and_incomplete_dependencies(tmp_path):
    repo = _repo_with_tasks(tmp_path, {
        "TASK-1": {"status": "To Do"},
        "TASK-2": {"status": "To Do", "dependencies": ["TASK-1"]},
        "TASK-3": {"status": "To Do", "orchestration": {"status_key": "todo", "lease_owner": "agent", "lease_expires_at": "2099-01-01T00:00:00Z"}},
    })

    eligible = list_eligible_tasks(ReadOnlyRepository.from_path(repo), now=_utc("2026-05-13T00:00:00Z"))

    assert [task.id for task in eligible] == ["TASK-1"]


def test_lease_reports_split_active_and_stale_claims(tmp_path):
    repo = _repo_with_tasks(tmp_path, {
        "TASK-1": {"status": "To Do", "orchestration": {"status_key": "todo", "lease_owner": "agent-a", "lease_expires_at": "2026-05-13T01:00:00Z"}},
        "TASK-2": {"status": "To Do", "orchestration": {"status_key": "todo", "lease_owner": "agent-b", "lease_expires_at": "2026-05-12T23:00:00Z"}},
    })
    repository = ReadOnlyRepository.from_path(repo)
    now = _utc("2026-05-13T00:00:00Z")

    assert [task.id for task in list_active_claims(repository, now=now)] == ["TASK-1"]
    assert [task.id for task in list_stale_leases(repository, now=now)] == ["TASK-2"]
```

- [x] **Step 2: Run report tests to verify failure**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: FAIL because report helpers are not implemented.

- [x] **Step 3: Implement report helpers**

Implement:

- `list_eligible_tasks(repository, policy=None, now=None)`
- `list_active_claims(repository, now=None)`
- `list_stale_leases(repository, now=None)`
- `summarize_orchestration(repository, policy=None, now=None)`

Rules:

- Missing orchestration status falls back to normalized Backlog status: `To Do -> todo`, `In Progress -> inprogress`, `Done -> done`.
- A task is eligible only when its effective status is claimable, dependencies are complete, validation has no errors, and there is no active lease.
- Active lease means `lease_owner` is set and `lease_expires_at > now`.
- Stale lease means `lease_owner` is set and `lease_expires_at <= now`.
- Completed tasks count as dependency-complete if their Backlog status includes `done` or `complete`, or they live under `backlog/completed`.

- [x] **Step 4: Run report tests**

Run: `python -m pytest tests/test_orchestration.py -q`

Expected: report tests pass.

- [x] **Step 5: Commit reports slice**

```bash
git add src/backlog_py/orchestration tests/test_orchestration.py
git commit -m "feat: report orchestration readiness"
```

## Task 4: Document the First Slice

**Files:**
- Modify: `README.md`
- Test: existing focused tests

- [x] **Step 1: Add README note**

Add a concise section:

```markdown
## Optional Orchestration Metadata

`backlog-md-py` can parse optional `orchestration` frontmatter for agent or workflow coordinators. The first supported slice is read-only: parse metadata, validate it against the default workflow policy, and report eligible tasks, active claims, stale leases, and status summaries. The library does not launch agents or mutate orchestration state in this slice.
```

- [x] **Step 2: Run focused tests**

Run: `python -m pytest tests/test_orchestration.py tests/test_task_parser.py tests/test_readonly_repository.py -q`

Expected: all pass.

- [x] **Step 3: Commit docs slice**

```bash
git add README.md
git commit -m "docs: describe optional orchestration metadata"
```

## Task 5: Final Verification

**Files:**
- No new files unless verification reveals issues.

- [x] **Step 1: Run full test suite**

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 2: Run whitespace check**

Run: `git diff --check origin/main...HEAD`

Expected: no output.

- [x] **Step 3: Review branch diff**

Run: `git diff --stat origin/main...HEAD`

Expected: spec doc, plan doc, orchestration package, tests, and README only.

- [x] **Step 4: Final commit if needed**

If verification required fixes:

```bash
git add <fixed-files>
git commit -m "test: cover orchestration reports"
```

Otherwise no commit.
