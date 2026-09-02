# WebUI Sort, Milestones, Labels, and Statuses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent column sorting, current/legacy milestone compatibility and browser workflows, any-match label filtering, and safe structured status creation/reordering to `backlog-md-py` without changing its dependency-free WebUI architecture.

**Architecture:** Keep task ordering and milestone mutations in the existing core services, and expose only semantic operations through the loopback browser service. Continue server-rendering the board from packaged HTML/CSS/vanilla JavaScript resources; add no frontend runtime or build step. Perform multi-file task updates and multi-key config updates through prepare-first, atomic-per-file writers with best-effort rollback, then retain the existing project lock and auto-commit boundary.

**Tech Stack:** Python 3.11+, PyYAML, stdlib `http.server`, packaged HTML/CSS/vanilla JavaScript, pytest, Ruff, mypy (advisory), Bandit, and the existing `uv` workflow.

---

## Approved inputs and execution rules

- Design specification: `docs/superpowers/specs/2026-09-01-webui-sort-milestones-labels-statuses-design.md`.
- Worktree: `/Users/macbook-dev/Documents/GitHub/backlog-md-py/.worktrees/webui-sort-milestones-statuses`.
- Branch: `codex/webui-sort-milestones-statuses`.
- Baseline verification on 2026-09-01: `1132 passed, 5 skipped` from `uv run --extra dev python -m pytest tests -q`. The run emitted existing Click deprecation and pytest temporary-directory cleanup warnings; it had no failures.
- Before Task 1, apply `@superpowers:test-driven-development`. Do not write implementation before observing each focused test fail for the expected missing behavior.
- During Tasks 5, 10, 11, 12, and 15, apply `@impeccable` to preserve the approved quiet developer-tool interface, visible focus, keyboard-native controls, responsive layout, light/dark support, and reduced-motion compatibility.
- Before Task 16 completion claims, apply `@superpowers:verification-before-completion`.
- Keep `src/backlog_py/browser/service.py` and the packaged browser resources as the established architecture. Do not add a JavaScript framework, bundler, database, migration pass, or speculative generic reorder protocol.
- Commit only after the focused tests for that task pass. Do not amend earlier commits; later fixes receive their own commit.

## File responsibility map

| File | Responsibility in this change |
| --- | --- |
| `src/backlog_py/core/models.py` | Add optional read-only `priorities` config data. |
| `src/backlog_py/storage/config.py` | Read `priorities`; add one-write batch config mutation while retaining `set_config_value()`. |
| `src/backlog_py/core/repository.py` | Persistent sort, rollback-backed ordinal batches, and append-to-status movement with current hook semantics. |
| `src/backlog_py/core/milestones.py` | Dual-format records, current-format creation, ID allocation, dates, aliases, reference-safe mutations, and archive reads. |
| `src/backlog_py/mcp/tools.py` | Add current milestone fields without removing legacy response keys. |
| `src/backlog_py/browser/service.py` | Query filters, payload resolution, sort/milestone/settings endpoints, validation, and server-rendered controls. |
| `src/backlog_py/browser/templates/board.html` | Milestone dialog, selectors, live regions, and structured status editor host markup. |
| `src/backlog_py/browser/assets/board.js` | Sort/milestone/status actions, selector preservation, visible errors, and pending revision handling. |
| `src/backlog_py/browser/assets/board.css` | Small responsive styles for the new controls; no visual redesign. |
| `tests/test_config_storage.py` | Focused priority-read and batch-config atomicity tests. |
| `tests/test_task_ordering.py` | Focused repository sort/append/rollback/timestamp/hook tests. |
| `tests/test_milestones.py` | Current/legacy milestone format, alias, mutation, and compatibility tests. |
| `tests/test_browser_service.py` | Browser payload, route, filter, HTML/JS/CSS contract, lock, and security tests. |
| `tests/test_mcp_protocol_sdk_free.py` | Confirm legacy milestone MCP schema remains unchanged. |
| `src/backlog_py/compat/inventory.py` | Explicit implemented feature entries for the new browser capabilities. |
| `src/backlog_py/compat/report.py` | Move the audited WebUI feature baseline to Backlog.md 1.50.1. |
| `tests/test_compat_report.py`, `tests/test_cli_readonly.py` | Ratchet new inventory counts and baseline output. |
| `docs/webui-gap-analysis.md` | Durable code-path comparison, resolved gaps, compatibility notes, and ordered follow-up roadmap. |
| `docs/browser-parity.md`, `docs/upstream-feature-parity.md`, `docs/browser-release-validation.md`, `docs/stability-policy.md`, `CHANGELOG.md` | Document the 1.50.1 audit and new behavior without rewriting historical evidence. |

The four implementation slices share `browser/service.py`, `board.html`, `board.js`, and `board.css`, so they stay in one ordered plan. The dependency order is: persistent sort → core milestone compatibility → browser milestones/labels → structured statuses → compatibility documentation and full verification.

## Slice 1: persistent sorting and ordinal-aware movement

### Task 1: Read optional configured priority order

**Files:**
- Create: `tests/test_config_storage.py`
- Modify: `src/backlog_py/core/models.py:7-31`
- Modify: `src/backlog_py/storage/config.py:66-91`

- [ ] **Step 1: Write the failing priority-read tests**

```python
from pathlib import Path

from backlog_py.storage.config import load_config


def test_load_config_reads_optional_priorities(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\npriorities: [critical, high, normal]\n", encoding="utf-8")

    assert load_config(path).priorities == ["critical", "high", "normal"]


def test_load_config_defaults_priorities_to_none(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\n", encoding="utf-8")

    assert load_config(path).priorities is None
```

- [ ] **Step 2: Run the tests and confirm the missing-field failure**

Run: `uv run --extra dev python -m pytest tests/test_config_storage.py -q`

Expected: FAIL because `BacklogConfig` has no `priorities` attribute.

- [ ] **Step 3: Add read-only priority data to the normalized model**

Add at the end of `BacklogConfig` so any existing positional construction keeps its current argument meaning:

```python
priorities: list[str] | None = None
```

Add to `load_config()` without registering a writable CLI alias:

```python
priorities=_optional_string_list(raw.get("priorities")),
```

This deliberately reads only the upstream `priorities` key. Do not add it to `_KEY_ALIASES` or `_LIST_CONFIG_KEYS`; creation/editing of configured priorities remains a follow-up.

- [ ] **Step 4: Run the focused config tests**

Run: `uv run --extra dev python -m pytest tests/test_config_storage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the priority-read support**

```bash
git add src/backlog_py/core/models.py src/backlog_py/storage/config.py tests/test_config_storage.py
git commit -m "feat: read configured task priorities"
```

### Task 2: Define deterministic priority and creation-date ordering

**Files:**
- Create: `tests/test_task_ordering.py`
- Modify: `src/backlog_py/core/repository.py:1-18,487-828,1808-1841`

- [ ] **Step 1: Add a minimal local-project fixture and failing sort-semantic tests**

Create helpers in `tests/test_task_ordering.py` that write a `backlog/config.yml` and task files with caller-supplied `id`, `status`, `priority`, `created_date`, and `ordinal`. Add these tests:

```python
def test_sort_tasks_by_default_priority_then_natural_id(project) -> None:
    result = MutableRepository(project).sort_tasks("To Do", sort="priority")
    assert result.task_ids == ("TASK-2", "TASK-10", "TASK-1", "TASK-3")
    assert _ordinals(project, "To Do") == {
        "TASK-2": 1000,
        "TASK-10": 2000,
        "TASK-1": 3000,
        "TASK-3": 4000,
    }


def test_sort_tasks_uses_configured_priority_order_case_insensitively(project) -> None:
    # Configure [urgent, normal]; values URGENT and normal precede unknown/missing.
    result = MutableRepository(project).sort_tasks("To Do", sort="priority")
    assert result.task_ids == ("TASK-3", "TASK-2", "TASK-1", "TASK-4")


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("asc", ["TASK-1", "TASK-2", "TASK-3", "TASK-4"]),
        ("desc", ["TASK-3", "TASK-2", "TASK-1", "TASK-4"]),
    ],
)
def test_sort_tasks_by_created_date_normalizes_supported_utc_forms(project, direction, expected) -> None:
    # Cover date-only, space/T separators, seconds/fraction, Z/offset, and invalid/missing-last.
    result = MutableRepository(project).sort_tasks("To Do", sort="created", direction=direction)
    assert list(result.task_ids) == expected


@pytest.mark.parametrize(
    ("sort", "direction"),
    [("title", None), ("created", None), ("created", "sideways"), ("priority", "asc")],
)
def test_sort_tasks_rejects_unsupported_requests_without_writes(project, sort, direction) -> None:
    before = _task_sources(project)
    with pytest.raises(TaskMutationError):
        MutableRepository(project).sort_tasks("To Do", sort=sort, direction=direction)
    assert _task_sources(project) == before
```

Use values that prove equivalent instants compare equally (`2026-09-01T12:00:00Z` and `2026-09-01T05:00:00-07:00`) and then fall back to natural task ID. Invalid and missing dates must be last in both directions.

- [ ] **Step 2: Run only the new semantic tests**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py -q -k "sort_tasks_by or sort_tasks_uses or unsupported"`

Expected: FAIL because `MutableRepository.sort_tasks()` does not exist.

- [ ] **Step 3: Add narrow ordering helpers**

Add to `repository.py`:

```python
_DEFAULT_PRIORITIES = ("high", "medium", "low")


def _priority_order_key(task: TaskRecord, priorities: Sequence[str]) -> tuple[int, tuple[object, ...]]:
    raw = task.parsed.frontmatter.get("priority")
    priority = str(raw).strip().casefold() if raw is not None else ""
    order = {value.strip().casefold(): index for index, value in enumerate(priorities)}
    return order.get(priority, len(order)), _task_sort_key(task.id)


def _created_datetime(task: TaskRecord) -> datetime | None:
    raw = task.parsed.frontmatter.get("created_date")
    # Accept YAML date/datetime objects and the approved ISO-compatible strings.
    # Convert `Z` to `+00:00`, parse with datetime.fromisoformat(), interpret
    # naive/date-only values as UTC, and return an aware UTC datetime.
```

Add the small result type and public repository method with exact validation:

```python
@dataclass(frozen=True)
class TaskSortResult:
    task_ids: tuple[str, ...]
    changed_task_ids: tuple[str, ...]


def sort_tasks(self, status: str, *, sort: str, direction: str | None = None) -> TaskSortResult:
    # priority requires direction is None; created requires asc/desc.
    # Reload config from disk, use only this MutableRepository's local active tasks,
    # accept a configured status or a status used by a local task, and reject an
    # unknown empty status. Sort invalid/missing dates last regardless of direction.
```

For this task, the method may call a temporary `_assign_task_ordinals()` helper; Task 3 replaces it with the rollback-backed implementation. Return the complete ordered IDs in `task_ids` and the IDs actually rewritten in `changed_task_ids`, so the browser response is deterministic even on a no-op.

- [ ] **Step 4: Run the semantic tests**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py -q -k "sort_tasks_by or sort_tasks_uses or unsupported"`

Expected: PASS.

- [ ] **Step 5: Commit deterministic sort semantics**

```bash
git add src/backlog_py/core/repository.py tests/test_task_ordering.py
git commit -m "feat: define persistent task sort order"
```

### Task 3: Make ordinal batches prepare-first and rollback-backed

**Files:**
- Modify: `tests/test_task_ordering.py`
- Modify: `src/backlog_py/core/repository.py:487-828,1073-1105,1383-1401`

- [ ] **Step 1: Add failing batch-integrity tests**

```python
def test_sort_rewrites_only_ordinals_and_preserves_updated_dates(project) -> None:
    before = _frontmatters(project)
    MutableRepository(project).sort_tasks("To Do", sort="priority")
    after = _frontmatters(project)
    assert [after[task_id]["ordinal"] for task_id in _ordered_ids(project)] == [1000, 2000, 3000]
    for task_id in before:
        assert after[task_id].get("updated_date") == before[task_id].get("updated_date")
        assert _without(after[task_id], "ordinal") == _without(before[task_id], "ordinal")


def test_sort_noop_performs_no_file_writes(project, monkeypatch) -> None:
    MutableRepository(project).sort_tasks("To Do", sort="priority")
    monkeypatch.setattr(repository_module, "_atomic_write_text", _fail_if_called)
    result = MutableRepository(project).sort_tasks("To Do", sort="priority")
    assert result.changed_task_ids == ()


def test_sort_rolls_back_completed_writes_after_runtime_failure(project, monkeypatch) -> None:
    before = _task_sources(project)
    _fail_the_second_forward_write_once(monkeypatch)
    with pytest.raises(OSError, match="simulated write failure"):
        MutableRepository(project).sort_tasks("To Do", sort="priority")
    assert _task_sources(project) == before
```

Also assert a single `MutableRepository` instance observes the new order after successful cache invalidation.

- [ ] **Step 2: Run the batch tests and observe the integrity failures**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py -q -k "rewrites_only or noop or rolls_back or cache"`

Expected: at least one FAIL because the initial implementation is not yet prepare-first/rollback-backed.

- [ ] **Step 3: Implement the shared task-source batch writer**

Add a private immutable update record and writer:

```python
@dataclass(frozen=True)
class _TaskSourceUpdate:
    task_id: str
    path: Path
    original_source: str
    updated_source: str


def _write_task_source_batch(repository: MutableRepository, updates: Sequence[_TaskSourceUpdate]) -> list[str]:
    # Every source/path is parsed and containment-checked before the first write.
    # Skip byte-identical updates. Atomic-write each changed source with
    # base=repository.project.backlog_dir, recording
    # successes. On failure, restore successes in reverse order, warn on rollback
    # failure, invalidate once, and re-raise. On success, invalidate once and
    # return changed task IDs.
```

Build every ordinal source in memory with `_replace_frontmatter_values()` and `parse_task_markdown()` before calling the writer. Assign integer ordinals beginning at 1000 and continuing in increments of 1000. Do not call `edit_task()` and do not set `updated_date`.

Return `TaskSortResult(tuple(ordered_ids), tuple(changed_ids))`; do not create a general client-supplied reorder API.

- [ ] **Step 4: Run all ordering tests**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py -q`

Expected: PASS.

- [ ] **Step 5: Commit rollback-backed sorting**

```bash
git add src/backlog_py/core/repository.py tests/test_task_ordering.py
git commit -m "feat: persist task sort ordinals atomically"
```

### Task 4: Append cross-column browser moves correctly

**Files:**
- Modify: `tests/test_task_ordering.py`
- Modify: `src/backlog_py/core/repository.py:584-828`

- [ ] **Step 1: Add failing append, timestamp, rollback, and hook tests**

```python
def test_move_task_to_status_appends_after_ordinal_and_ordinal_less_tasks(project) -> None:
    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")
    assert moved.status == "Doing"
    assert _rendered_ids(project, "Doing") == ["TASK-2", "TASK-3", "TASK-1"]
    assert _frontmatter(project, "TASK-2")["ordinal"] == 4000  # existing valid ordinal unchanged
    assert _frontmatter(project, "TASK-3")["ordinal"] == 5000  # materialized
    assert _frontmatter(project, "TASK-1")["ordinal"] == 6000  # appended


def test_same_status_move_is_a_byte_identical_noop(project) -> None:
    before = _task_sources(project)
    MutableRepository(project).move_task_to_status("TASK-1", "To Do")
    assert _task_sources(project) == before


def test_move_preserves_target_dates_but_updates_moved_task_date(project, monkeypatch) -> None:
    monkeypatch.setattr(repository_module, "_current_task_timestamp", lambda _: "2026-09-01 12:00")
    MutableRepository(project).move_task_to_status("TASK-1", "Doing")
    assert _frontmatter(project, "TASK-2")["updated_date"] == "existing-target-date"
    assert _frontmatter(project, "TASK-1")["updated_date"] == "2026-09-01 12:00"


def test_move_runs_best_effort_status_hook_and_keeps_change_on_failure(project, monkeypatch) -> None:
    monkeypatch.setattr(repository_module, "execute_status_callback", _raising_callback)
    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")
    assert moved.status == "Doing"


@pytest.mark.parametrize("configured_statuses", [None, []])
def test_move_accepts_task_derived_target_when_statuses_absent_or_empty(
    project, configured_statuses
) -> None:
    _write_config_statuses(project, configured_statuses)
    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")
    assert moved.status == "Doing"


def test_move_accepts_default_only_empty_column(project) -> None:
    _write_config(project, statuses=[], default_status="Ready")
    moved = MutableRepository(project).move_task_to_status("TASK-1", "Ready")
    assert moved.status == "Ready"
```

Add an injected second-write failure test proving both the target ordinal materialization and moved task are rolled back. Add an invalid target-status test proving no write occurs. Cover a non-empty configured list that omits a status still used by an active local task; that task-derived column remains a valid target. At the lock boundary, add `test_locked_status_move_callback_failure_still_reaches_auto_commit`: monkeypatch `execute_status_callback` to raise, spy on `backlog_py.runtime.locks.maybe_auto_commit`, call `with_project_write_lock(project, "browser_task_status", lambda: MutableRepository(project).move_task_to_status(...))`, and assert the change succeeds and the auto-commit stage is invoked once. This directly exercises the new helper before Task 5 switches the HTTP route to it.

- [ ] **Step 2: Run only movement tests**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py -q -k "move or same_status"`

Expected: FAIL because `move_task_to_status()` does not exist.

- [ ] **Step 3: Implement append-to-status through the shared batch writer**

```python
def status_assignment_options(self) -> tuple[str, ...]:
    # Exact configured values in order, then exact default, then exact statuses
    # from local-only self.list_tasks(); deduplicate exact strings in first-seen order.


def move_task_to_status(self, task_id: str, status: str) -> TaskRecord:
    task = self.get_task(task_id)
    if task.status == status:
        return task
    local_tasks = self.list_tasks()
    if status not in self.status_assignment_options():
        raise TaskMutationError(f"Unknown status: {status}")
    targets = [candidate for candidate in local_tasks if candidate.status == status and candidate.id != task.id]
    # Preserve valid target ordinals. Starting after max(valid) or 0, materialize
    # ordinal-less targets in rendered order at +1000, then append the moved task.
    # Target-only updates contain ordinal alone. The moved update contains status,
    # ordinal, and the normal current updated_date. Prepare all sources, batch-write,
    # reload the moved task, then call _run_status_change_callback().
```

Treat absent and explicitly empty configured status lists alike. A move target is valid when its exact spelling is configured, is the exact current default, or is currently used by an active local task; this keeps every assignable default/task-derived column operable while rejecting invented and active-branch-only targets. Use the existing `_run_status_change_callback()` unchanged so command failures and callback exceptions remain best-effort. The project lock wrapper will therefore continue to auto-commit after the repository method returns.

- [ ] **Step 4: Run ordering and existing mutation callback tests**

Run: `uv run --extra dev python -m pytest tests/test_task_ordering.py tests/test_task_mutations.py -q -k "ordering or sort or move or status_change or callback_failure_still_reaches_auto_commit"`

Expected: PASS.

- [ ] **Step 5: Commit ordinal-aware movement**

```bash
git add src/backlog_py/core/repository.py tests/test_task_ordering.py
git commit -m "feat: append browser status moves by ordinal"
```

### Task 5: Expose sorting and ordinal movement in the browser

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py:178-207,388-474,474-656,1057-1090,1199-1224,1549-1585`
- Modify: `src/backlog_py/browser/templates/board.html:12-30`
- Modify: `src/backlog_py/browser/assets/board.js:1-135,1100-1157`
- Modify: `src/backlog_py/browser/assets/board.css:41-122,134-166,436-454`

- [ ] **Step 1: Add failing endpoint and payload tests**

Add these tests with the existing copied-repository/service helpers:

- `test_browser_task_payload_includes_ordinal`: write `ordinal: 4200`, GET `/api/board`, and assert the task payload contains `4200`.
- `test_browser_sort_endpoint_persists_full_column_under_project_lock`: create two same-status tasks with reverse creation order, POST the request below, assert `browser_task_sort`, then reload `/api/board` and assert both order and stored ordinals.
- `test_browser_sort_endpoint_ignores_current_board_filters`: POST to `/api/tasks/sort?labels=only-one-task` and assert every local task in the status receives its full-column order.
- `test_browser_sort_endpoint_rejects_invalid_requests_without_mutation`: parameterize non-object JSON, missing/blank status, unsupported sort, missing/bad created direction, and priority with a direction; assert 400 and byte-identical task sources.
- `test_browser_status_move_uses_ordinal_aware_append`: create one ordinal-bearing and one ordinal-less target task, move a third task, and assert both materialization and final append order.
- `test_active_branch_only_column_omits_sort_controls`: render at least two tasks in a status supplied only by active-branch snapshots and assert the read-only column remains visible without a Sort disclosure.
- `test_browser_sort_and_move_reject_cross_origin`: POST both routes from `https://example.com`, assert 403, and assert byte-identical task sources.
- `test_browser_sort_unexpected_failure_returns_safe_json_500`: monkeypatch the repository call to raise `OSError("private detail")`, assert 500 with `{"error": "Internal server error"}`, and assert the private message is absent.

For the successful sort, assert the lock operation is `browser_task_sort`, the response is exactly:

```python
{
    "status": "To Do",
    "sort": "created",
    "direction": "asc",
    "taskIds": ["TASK-2", "TASK-1"],
    "changedCount": 2,
}
```

`changedCount` is `len(result.changed_task_ids)`; `taskIds` is `list(result.task_ids)`. Keep the existing status endpoint response shape and lock name `browser_task_status`.

- [ ] **Step 2: Run the endpoint tests and verify the missing-route failures**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "sort_endpoint or includes_ordinal or ordinal_aware_append"`

Expected: FAIL with 404/missing `ordinal`/old append behavior.

- [ ] **Step 3: Add the semantic sort route and switch status movement**

In `service.py`:

```python
def _sort_request_from_payload(payload: object) -> tuple[str, str, str | None]:
    # Require a JSON object and non-empty status. Accept only priority/no direction
    # or created/asc|desc. Reject extra type shapes before locking.


if path == "/api/tasks/sort":
    # Enforce Origin, parse, lock as browser_task_sort, call sort_tasks(), return
    # status/sort/direction/taskIds/changedCount. Map request/task mutation errors
    # to 400; unexpected failures flow to the safe 500 handler.
```

Refactor `do_POST()` to match the existing safe GET boundary: keep the Host check, move the route body to `_handle_post()`, and catch/log unexpected exceptions once at the outer method before `_safe_send_error()`. Existing route-specific 400/403/404/409 behavior stays inside `_handle_post()`.

Change the existing status endpoint callback to `MutableRepository(self.server.project).move_task_to_status(task_id, status)`. Add `"ordinal": _task_ordinal(task)` to `_task_payload()` using a public property/helper rather than duplicating ordinal parsing in the browser module.

- [ ] **Step 4: Run all focused browser mutation tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "sort or status_move or task_payload or origin"`

Expected: PASS.

- [ ] **Step 5: Add failing HTML/JS/CSS contract tests**

Assert that a mutable column with at least two tasks renders native `<details>` with buttons for priority, oldest, and newest; a one-task or active-branch-only column does not render sort controls; the page contains `role="status" aria-live="polite"`; JS disables an in-flight sort button, posts semantic JSON, reports response errors visibly, and reloads via `window.location.reload()` so the query string remains; CSS has visible `:focus-visible` treatment and responsive sort/filter wrapping. Determine mutability from `MutableRepository(project, refresh_remote_refs=False).status_assignment_options()`, so sort controls are never offered for a status the local repository will reject.

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "sort_controls or board_status_region or responsive"`

Expected: FAIL because the controls are absent.

- [ ] **Step 6: Render and wire the narrow sort controls**

Pass the exact `status_assignment_options()` set into column rendering. Change `_render_column()` to emit controls only when `len(tasks) >= 2` and the column status is in that local-mutable set:

```html
<details class="column-sort">
  <summary>Sort</summary>
  <div class="column-sort-actions">
    <button type="button" data-sort="priority">Priority</button>
    <button type="button" data-sort="created" data-direction="asc">Oldest</button>
    <button type="button" data-sort="created" data-direction="desc">Newest</button>
  </div>
</details>
```

Add one board-level live region outside `<main>` and a `showBoardMessage(message)` helper. Each sort handler reads the nearest column's `data-status`, disables itself during `fetch("/api/tasks/sort")`, displays the server error string on failure, and reloads on success. Update drag/drop failure handling to use the same live region.

- [ ] **Step 7: Run the complete slice-1 tests and lint changed files**

Run:

```bash
uv run --extra dev python -m pytest tests/test_config_storage.py tests/test_task_ordering.py tests/test_browser_service.py -q
uv run --extra dev python -m ruff check src/backlog_py/core/models.py src/backlog_py/storage/config.py src/backlog_py/core/repository.py src/backlog_py/browser/service.py tests/test_config_storage.py tests/test_task_ordering.py tests/test_browser_service.py
```

Expected: PASS, then `All checks passed!`.

- [ ] **Step 8: Commit browser sorting**

```bash
git add src/backlog_py/browser/service.py src/backlog_py/browser/templates/board.html src/backlog_py/browser/assets/board.js src/backlog_py/browser/assets/board.css tests/test_browser_service.py
git commit -m "feat: add persistent browser column sorting"
```

## Slice 2: current/legacy milestone compatibility

### Task 6: Read current and legacy milestones without migration

**Files:**
- Modify: `tests/test_milestones.py`
- Modify: `src/backlog_py/core/milestones.py:1-78,201-251`

- [ ] **Step 1: Add failing dual-format read tests**

Cover all of these exact cases:

- `test_current_milestone_loads_id_title_due_date_and_description`: write `m-9 - release-2.md` using current frontmatter and assert every field shown below.
- `test_legacy_milestone_retains_name_path_content_and_frontmatter`: write `Alpha.md` with `name: Alpha`, custom frontmatter, and body, then assert old fields remain exact and `format == "legacy"`.
- `test_list_milestones_can_include_archived_records`: place one valid file in each directory and assert the default list is active-only while `include_archived=True` returns both with correct flags.
- `test_readme_is_ignored_case_insensitively_in_active_and_archive`: place `readme.md` and `README.md` in the two directories and assert neither appears.
- `test_malformed_current_looking_file_is_warned_and_skipped`: write numeric `m-N` filenames missing ID or title, plus a non-canonical filename such as `release.md` whose frontmatter contains `id: m-9` but lacks a valid title; capture Loguru warnings and assert each filename is named in the warning.
- `test_noncurrent_file_without_name_keeps_filename_fallback`: retain the existing filename-derived compatibility behavior.
- `test_current_and_legacy_read_tolerates_utf8_bom`: parameterize one file of each format and assert successful decoding.

For a current file, assert:

```python
assert record.id == "m-9"
assert record.title == record.name == "Release 2"
assert record.due_date == "2026-09-30 17:00"
assert record.format == "current"
assert record.description == "Release scope."
assert record.archived is False
```

- [ ] **Step 2: Run the read tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q -k "current_milestone or legacy_milestone or readme or malformed_current or include_archived or filename_fallback"`

Expected: FAIL because `MilestoneRecord` lacks current-format fields and archive listing.

- [ ] **Step 3: Extend the record and loader additively**

Use this shape while retaining every old field:

```python
@dataclass(frozen=True)
class MilestoneRecord:
    name: str
    path: Path
    path_relative: str
    content: str
    frontmatter: dict[str, Any]
    archived: bool = False
    id: str | None = None
    title: str = ""
    due_date: str | None = None
    format: str = "legacy"

    @property
    def description(self) -> str:
        return _description_from_body(self.content) if self.format == "current" else self.content
```

Detection rules:

- Ignore `readme.md` before attempting to parse.
- A current record requires a canonical lowercase-insensitive `m-<non-negative integer>` ID and non-empty string title; normalize the exposed ID to lowercase `m-N` while preserving raw frontmatter.
- A `name` field selects legacy format first. Otherwise, a canonical numeric `m-N` filename prefix or the presence of either current marker key (`id` or `title`) selects current intent. Current-intent content with an invalid/missing canonical ID or non-empty title raises `ValueError`, so the list method warns and skips it instead of producing a fake legacy milestone—even when the filename itself is non-canonical.
- Only content with neither explicit legacy `name` nor current intent retains `_name_from_filename()` fallback behavior.
- `list_milestones(include_archived=False)` preserves the old default. When true, append safely loaded archive records and sort deterministically by archive flag, numeric current ID, then casefolded title/name/path.
- Keep `content` as the complete body (minus surrounding whitespace). Extract only the first `## Description` section for the new `description` property.

- [ ] **Step 4: Run all milestone read tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q -k "list or read or current or legacy or malformed or readme or bom"`

Expected: PASS.

- [ ] **Step 5: Commit dual-format reads**

```bash
git add src/backlog_py/core/milestones.py tests/test_milestones.py
git commit -m "feat: read current and legacy milestones"
```

### Task 7: Create current-format milestones with stable IDs and normalized due dates

**Files:**
- Modify: `tests/test_milestones.py`
- Modify: `src/backlog_py/core/milestones.py:44-78,201-251`

- [ ] **Step 1: Add failing current-write tests**

Add the following tests:

- `test_add_milestone_writes_current_format_and_description_heading`: assert allocated ID/frontmatter, `## Description`, filename, and returned record.
- `test_id_allocator_scans_active_archive_frontmatter_and_filename_fallbacks`: use the exact active/archive/filename-only setup described below and assert `m-12`.
- `test_id_allocator_never_reuses_archived_id`: archive the highest ID, create again, and assert the next integer.
- `test_due_date_normalizes_utc_shapes`: parameterize `2026-09-30 17:00`, `2026-09-30T17:00:45.123Z`, and `2026-09-30T10:00-07:00`; each stores `2026-09-30 17:00`.
- `test_due_date_rejects_invalid_or_date_only_before_write`: parameterize `2026-09-01`, `not-a-date`, and `2026-13-01 10:00`; assert `MilestoneMutationError` and no new file.
- `test_current_filename_sanitizes_and_truncates_title`: include every forbidden character, excess whitespace, and a title longer than 50 sanitized characters; assert the exact safe basename and the `milestone` fallback for an all-forbidden title.

Use active `m-2`, archived `m-8`, a filename-only `m-11 - reserved.md`, and malformed current content to prove the next allocated ID is `m-12`.

- [ ] **Step 2: Run current-write tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q -k "add_milestone_writes_current or id_allocator or due_date or current_filename"`

Expected: FAIL because new milestones still use legacy `name` files.

- [ ] **Step 3: Implement current creation and date normalization**

Extend without breaking callers:

```python
def add_milestone(
    self,
    name: str,
    description: str = "",
    *,
    due_date: str | None = None,
) -> MilestoneRecord:
    title = _required_title(name)
    milestone_id = self._next_milestone_id()
    normalized_due_date = _normalize_due_date(due_date) if due_date else None
    frontmatter = {"id": milestone_id, "title": title}
    if normalized_due_date is not None:
        frontmatter["due_date"] = normalized_due_date
    source = _render_milestone(frontmatter, f"## Description\n\n{description.strip()}")
```

Implement `_next_milestone_id()` by scanning both directories, reserving valid frontmatter IDs and every filename ID matching `^m-(\d+)(?:\s+-|$)` even when the file is malformed. Implement the approved safe-title algorithm: remove `< > : " / \\ | ? *`, collapse whitespace to hyphens, lowercase, truncate to 50 characters, and fall back to `milestone`.

Implement `_normalize_due_date()` with `datetime.fromisoformat()` after translating terminal `Z`. Require both a date and time, interpret offset-less values as UTC, convert offset-bearing values to UTC, and store `%Y-%m-%d %H:%M`.

- [ ] **Step 4: Run current-write and existing add tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q -k "add or allocator or due_date or filename"`

Expected: PASS after updating old filename assertions from `Release-1.md` to the allocated current `m-N - release-1.md` form.

- [ ] **Step 5: Commit current-format creation**

```bash
git add src/backlog_py/core/milestones.py tests/test_milestones.py
git commit -m "feat: write stable current milestones"
```

### Task 8: Resolve aliases and mutate milestones/reference batches safely

**Files:**
- Modify: `tests/test_milestones.py`
- Modify: `src/backlog_py/core/milestones.py:79-199,201-251`

- [ ] **Step 1: Add failing resolver and mutation tests**

Add tests for:

- Current resolution by `m-9`, `9`, exact path stem, and unique title; legacy resolution by name/path stem.
- Ambiguous case-insensitive titles/names fail closed with `MilestoneConflictError`.
- Active create/rename rejects aliases colliding with another active title, ID, or numeric alias.
- Current edit preserves ID, unknown frontmatter, and body sections outside `## Description`; renaming changes the filename but not the ID.
- Legacy edit preserves `name` format and legacy filename behavior.
- Current rename leaves canonical task references unchanged; `update_tasks=True` converts unique old-title references to the canonical ID.
- Remove-with-clear recognizes canonical, numeric, path-stem, and unique-title aliases.
- Archive preserves references and returns an archived record.
- Injected milestone/task write and rename failures restore every source/path.

Representative assertions:

```python
edited = service.edit_milestone("9", title="Release Final", description="Updated", due_date="")
assert edited.id == "m-9"
assert edited.title == "Release Final"
assert edited.due_date is None
assert edited.frontmatter["custom"] == "preserved"
assert "## Risks\n\nKeep me" in edited.content

with pytest.raises(MilestoneConflictError, match="ambiguous"):
    service.resolve_milestone("Release")
```

- [ ] **Step 2: Run resolver/mutation tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q -k "resolve or alias or ambiguous or edit_milestone or current_rename or remove or archive or rollback"`

Expected: FAIL because resolution and additive editing are not implemented.

- [ ] **Step 3: Implement one resolver and one edit transaction**

Add:

```python
class MilestoneConflictError(MilestoneMutationError):
    """Raised for duplicate or ambiguous milestone state."""


_UNSET = object()


def resolve_milestone(self, reference: str, *, include_archived: bool = True) -> MilestoneRecord:
    # Match exact canonical ID, numeric alias, exact path stem, or unique
    # title/name case-insensitively. De-duplicate the same record across aliases.
    # Raise NotFoundError for zero and MilestoneConflictError for >1.


def edit_milestone(
    self,
    reference: str,
    *,
    title: str | None = None,
    description: str | None = None,
    due_date: str | None | object = _UNSET,
    update_tasks: bool = False,
) -> MilestoneRecord:
    # Resolve active only, validate collisions/date/target before writing, prepare
    # task-reference updates, render one milestone source, then perform the existing
    # rollback-backed write/task-update/unlink sequence.
```

Have `rename_milestone(old_name, new_name, update_tasks=flag)` delegate to `edit_milestone(old_name, title=new_name, update_tasks=flag)`. Current edits own only `title`, `due_date`, and the first Description section. Legacy title edits own `name`; description replaces the legacy body. Unknown frontmatter and current body sections remain byte-content-equivalent after YAML re-rendering.

Replace `_task_reference_updates(old_name, new_name)` with record-aware alias matching. Only aliases that uniquely resolve to that record are eligible. For current rename with updates, replace old-title aliases with `record.id`; canonical ID references remain byte-identical. For clear, remove all unique aliases of the selected record.

- [ ] **Step 4: Run all milestone tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py -q`

Expected: PASS.

- [ ] **Step 5: Commit safe milestone mutation**

```bash
git add src/backlog_py/core/milestones.py tests/test_milestones.py
git commit -m "feat: resolve and mutate milestone aliases safely"
```

### Task 9: Preserve CLI/MCP milestone compatibility

**Files:**
- Modify: `tests/test_milestones.py`
- Modify: `tests/test_mcp_protocol_sdk_free.py:320-335`
- Modify: `src/backlog_py/mcp/tools.py:625-670,976-986`

- [ ] **Step 1: Add failing additive-output compatibility tests**

Extend the existing MCP test:

```python
added = milestone_add(project, "Alpha", description="First")
assert added["name"] == added["title"] == "Alpha"
assert added["id"] == "m-1"
assert added["due_date"] is None
assert added["format"] == "current"
for legacy_key in ("path", "content", "frontmatter", "archived", "project_path"):
    assert legacy_key in added
```

Add a CLI sequence proving list/rename/archive/remove still accept display names after current-write creation. Keep the SDK-free schema assertions for `milestone_add(name, description)` and existing rename/remove flags unchanged.

- [ ] **Step 2: Run CLI/MCP milestone tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py tests/test_mcp_protocol_sdk_free.py -q -k "milestone"`

Expected: FAIL because `_milestone_detail()` lacks the additive fields or old name lookup no longer works.

- [ ] **Step 3: Add current fields without changing existing signatures**

Extend `_milestone_detail()` only:

```python
"id": milestone.id,
"title": milestone.title,
"due_date": milestone.due_date,
"format": milestone.format,
```

Do not remove or rename `name`, `path`, `content`, `frontmatter`, `archived`, or `project_path`. Do not add required MCP arguments in this slice.

- [ ] **Step 4: Run milestone plus MCP schema tests**

Run: `uv run --extra dev python -m pytest tests/test_milestones.py tests/test_mcp_protocol_sdk_free.py -q -k "milestone"`

Expected: PASS.

- [ ] **Step 5: Commit additive compatibility fields**

```bash
git add src/backlog_py/mcp/tools.py tests/test_milestones.py tests/test_mcp_protocol_sdk_free.py
git commit -m "feat: expose current milestone metadata additively"
```

## Slice 3: milestones and labels in the WebUI

### Task 10: Add safe milestone browser endpoints

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py:1-35,388-474,474-656,1495-1585`

- [ ] **Step 1: Add failing API-key and endpoint tests**

Test these routes under their exact lock operation names:

| Route | Lock operation | Expected success |
| --- | --- | --- |
| `GET /api/milestones` | none | An object whose `milestones` array contains active and archived records/counts |
| `POST /api/milestones` | `browser_milestone_create` | 201 and current-format record |
| `POST /api/milestones/<key>/edit` | `browser_milestone_edit` | supplied fields changed, omitted fields preserved |
| `POST /api/milestones/<key>/archive` | `browser_milestone_archive` | record moves to archive |
| `POST /api/milestones/<key>/remove` | `browser_milestone_remove` | no-ref omission accepted; refs require `keep` or `clear` |

Add unit-level key round trips:

```python
@pytest.mark.parametrize("name", ["Release / Windows", r"Release \\ Windows", "Café % ready"])
def test_legacy_milestone_api_key_is_one_safe_reversible_segment(name):
    key = _legacy_milestone_key(name)
    assert "/" not in key and "\\" not in key and "%" not in key
    assert _legacy_name_from_key(key) == name
```

Also cover malformed/non-canonical tokens, cross-origin POSTs, Host rejection, missing record 404, invalid date 400, duplicate/ambiguous 409, referenced-remove-without-policy 409, and no mutation on every rejected case.

- [ ] **Step 2: Run milestone endpoint tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "milestone_api or milestone_endpoint or milestone_key"`

Expected: FAIL with missing helpers/routes.

- [ ] **Step 3: Implement deterministic keys, payloads, and routes**

Use stdlib `base64.urlsafe_b64encode/decode`:

```python
def _milestone_api_key(record: MilestoneRecord) -> str:
    if record.id is not None:
        return record.id
    token = urlsafe_b64encode(record.name.encode("utf-8")).decode("ascii").rstrip("=")
    return f"legacy-{token}"


def _milestone_reference_from_key(key: str) -> str:
    # Return current m-N directly. For legacy-, restore padding, decode strict
    # UTF-8, require non-empty name, and require re-encoding to the same canonical
    # key. Raise ValueError for anything else. Never pass decoded text to a path API.
```

Payload fields are `key`, `id`, `title`, `name`, `dueDate`, `description`, `format`, `path`, `archived`, and `taskReferenceCount`. Count only local active task files and recognize the selected record's unique supported aliases.

Parse edit fields by presence, so omitted means preserve and empty `dueDate` means clear. On referenced remove, require `taskHandling in {"keep", "clear"}`; omission is allowed only when count is zero. Catch `MilestoneConflictError` as 409, `NotFoundError` as 404, and validation errors as 400.

- [ ] **Step 4: Run endpoint/security tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "milestone or origin or host"`

Expected: PASS.

- [ ] **Step 5: Commit milestone endpoints**

```bash
git add src/backlog_py/browser/service.py tests/test_browser_service.py
git commit -m "feat: add safe browser milestone endpoints"
```

### Task 11: Add unified queue/milestone/label filtering and resolved card metadata

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `tests/test_task_mutations.py`
- Modify: `src/backlog_py/core/repository.py:255-260,500-828,1677-1679`
- Modify: `src/backlog_py/browser/service.py:178-207,388-474,823-887,1057-1140,1199-1226,1407-1445,1479-1510`
- Modify: `src/backlog_py/browser/templates/board.html:24-60,81-98,292-305`
- Modify: `src/backlog_py/browser/assets/board.js:700-760,900-970`
- Modify: `src/backlog_py/browser/assets/board.css:47-71,96-134,436-454`

- [ ] **Step 1: Add failing filter and payload tests**

Cover:

- `test_board_combines_queue_milestone_and_any_match_repeated_labels`: create tasks that isolate each predicate and assert only tasks satisfying queue AND milestone AND at least one selected label remain.
- `test_filter_choices_come_from_unfiltered_board`: select a filter that hides a label/milestone and assert both remain in available choices.
- `test_filtering_does_not_change_revision`: compare unfiltered and several filtered revision hashes for the same files.
- `test_milestone_filter_resolves_current_title_numeric_and_archived_aliases`: assert each unique alias selects the same referenced tasks.
- `test_unknown_milestone_reference_remains_filterable`: assert a raw unknown reference appears as an option and filters exactly that raw value.
- `test_task_payload_exposes_resolved_milestone_display_and_ordinal`: assert the four new task fields for current, archived, and unknown references.
- `test_task_card_shows_milestone_and_label_overflow_badges`: use three labels and assert two named badges plus `+1` and one resolved milestone badge.
- `test_task_details_show_resolved_milestone_state_not_raw_id`: open detail payloads for active current, archived, and unknown references; assert the UI contract renders `Release 2`, `Release 1 (archived)`, and `Unknown: raw-value` rather than displaying `m-9` as the active current label.
- `test_browser_status_options_separate_rendered_and_assignable_columns`: parameterize absent, explicitly empty, and non-empty configured status lists; assert `payload["statuses"]` retains all rendered columns while `payload["assignableStatuses"]` is configured statuses + exact current default + exact local active task-derived/legacy-visible statuses.
- `test_create_and_edit_accept_every_exposed_status_option`: parameterize those same board shapes and send each exposed option through core create/edit, including a legacy task-derived status omitted from a non-empty config.
- `test_absent_and_empty_status_config_keep_open_core_assignment_compatibility`: prove both shapes accept the same explicit non-empty status through create/edit while the browser continues to present only finite configured/default/task-derived suggestions.
- `test_default_only_column_accepts_browser_drop`: configure no statuses and default `Ready`, assert the empty `Ready` column is rendered and assignable, then move a task there successfully.
- `test_active_branch_only_status_is_rendered_read_only`: add an active-branch snapshot whose status has no configured/default/local-active counterpart; assert the status remains in `statuses`/`columns` for visibility but is absent from `assignableStatuses`, task create/edit selectors, and droppable-column markup.

The payload contract adds while retaining current keys:

```python
{
    "assignableStatuses": ["To Do", "In Progress", "Done"],
    "queueCategoryFilter": "eligible",
    "milestoneFilter": "m-9",
    "labelFilters": ["frontend", "urgent"],
    "availableLabels": ["backend", "frontend", "urgent"],
    "milestoneChoices": [{"value": "m-9", "label": "Release 2", "archived": False}],
    "visibleTaskCount": 2,
    "totalTaskCount": 5,
}
```

Assert labels use case-insensitive any-match in the browser only; `ReadOnlyRepository.list_tasks(labels=["frontend", "urgent"])` must retain its all-match contract.

- [ ] **Step 2: Run filter tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_task_mutations.py -q -k "filter or milestone_display or label_overflow or task_details or status_options or status_config or default_only or branch_only"`

Expected: FAIL because only queue filtering exists.

- [ ] **Step 3: Extend board view state without changing repository filters**

Use:

```python
def build_board_payload(
    project: BacklogProject,
    *,
    queue_category_filter: str | None = None,
    milestone_filter: str | None = None,
    label_filters: Sequence[str] | None = None,
) -> dict[str, object]:
```

Load local/current board tasks as today, load milestone records once, create unfiltered task payloads once, derive available choices from that unfiltered set, and apply all filters afterward. A task passes labels when no labels are selected or at least one task label casefold-matches at least one selected label. Resolve milestone filters and task references against the same loaded milestone set; ambiguous resolution remains an unknown raw value rather than guessing.

Extend `_task_payload(..., milestones: Sequence[MilestoneRecord] | None = None)`. Board construction passes its already-loaded records; the single-task detail path loads once when the argument is omitted. This prevents per-task milestone directory scans while keeping existing callers simple.

Hash the unfiltered columns with all active filter values removed. Add `_query_values(query, "labels")` beside `_query_value()` and pass filters through both `/api/board` and page rendering.

Add task payload fields `ordinal`, `milestoneTitle`, `milestoneArchived`, and `milestoneUnknown`. Update `_render_task_meta()` to show one milestone badge, at most two label badges, and `+N` for additional labels.

Define one assignability predicate in `core/repository.py` and use it from `create_task()` and `edit_task()`: absent and explicitly empty configured lists are the same open compatibility mode and accept any non-empty explicit status; a non-empty configured list accepts exact configured values, the exact current default, and exact statuses from `self.list_tasks()` (active local task files only). This preserves existing unconfigured CLI/MCP assignment while making every configured/default/task-derived browser option valid. Reject blank values in every mode. Do not case-normalize stored task status spelling.

Reuse `MutableRepository.status_assignment_options()` from Task 4 as the single finite browser-option source. It returns exact configured values in order, then the exact current default when absent, then exact statuses from its local-only `self.list_tasks()` when absent. It deduplicates by exact string and never inspects active-branch snapshots. `build_board_payload()` retains `statuses` as the complete rendered board-key list, including read-only active-branch-only columns, and adds `assignableStatuses` from this helper. Materialize a missing exact default as an empty rendered column. Active-branch-only columns remain visible but are not task create/edit choices or drop targets.

- [ ] **Step 4: Replace the queue-only GET form and text milestone inputs**

Render one `<form class="board-filter" method="get">` with:

- Native queue and milestone `<select>` controls.
- A native `<details>` disclosure containing repeated `name="labels"` checkboxes.
- Apply and clear-link controls.
- `visibleTaskCount / totalTaskCount` text when filters are active.
- `No matching tasks` empty text for filtered columns; retain `No tasks` without filters.

Change create/edit milestone controls to `<select>`. Active current options use `m-N`; active legacy options use exact names. Render canonical active options server-side. In `openTaskEdit()`, insert an enabled exact-raw selected option if the stored value is absent. If it resolves to active current by title, keep both `Title (stored as raw)` and `Title (m-N)` options. If archived use `Title (archived)`; if unknown use `Unknown: raw`. Build task status options from `payload["assignableStatuses"]`, not only `project.config.statuses`, so configured, task-derived/legacy-visible, and default-only local columns remain selectable while active-branch-only columns stay read-only.

Render a stable assignable flag on each board column and attach drag/drop handlers only when its exact status occurs in `assignableStatuses`. A task may still be inspected in an active-branch-only column, but that column cannot become a browser mutation target.

Replace the task-details milestone assignment with one small text formatter:

```javascript
function milestoneDetailText(task) {
  if (!task.milestone) return "";
  if (task.milestoneUnknown) return `Unknown: ${task.milestone}`;
  const title = task.milestoneTitle || task.milestone;
  return task.milestoneArchived ? `${title} (archived)` : title;
}
```

`openTaskDetails()` passes this result to `setText("task-dialog-milestone", ...)`; task labels continue rendering their complete list.

Update create submission to send the fields already present in the form but currently omitted:

```javascript
priority: String(data.get("priority") || ""),
milestone: String(data.get("milestone") || ""),
assignees: metadataList(data.get("assignees")),
labels: metadataList(data.get("labels")),
```

Keep edit submission's existing labels/milestone behavior.

- [ ] **Step 5: Run browser filter/form tests and core label regression tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_task_mutations.py tests/test_cli_readonly.py -q -k "filter or label or milestone or task_create or status_option or status_config"`

Expected: PASS.

- [ ] **Step 6: Commit WebUI filtering and assignment**

```bash
git add src/backlog_py/core/repository.py src/backlog_py/browser/service.py src/backlog_py/browser/templates/board.html src/backlog_py/browser/assets/board.js src/backlog_py/browser/assets/board.css tests/test_browser_service.py tests/test_task_mutations.py
git commit -m "feat: filter and assign browser milestones and labels"
```

### Task 12: Add milestone management and lossless deferred refresh

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/templates/board.html:12-30,229-284`
- Modify: `src/backlog_py/browser/assets/board.js:1-135,260-380,700-830,1000-1157`
- Modify: `src/backlog_py/browser/assets/board.css:166-220,240-360,436-454`

- [ ] **Step 1: Add failing management-dialog and pending-refresh contract tests**

Assert stable IDs and visible behavior for:

- Header `Milestones` action and labeled dialog.
- Inline create form with title, UTC due datetime, and description.
- Active list, collapsed archived list, selected editor, ID/format/path/reference-count metadata.
- Edit, Archive, and secondary Remove controls.
- Remove policy choices `keep`/`clear` plus warning.
- Inline `role="status" aria-live="polite"` errors.
- Dialog max-height/internal scroll, narrow-screen single-column layout, visible focus, and no animation dependency.
- `pendingBoardRevision` capture while any dialog is open and reload after the final dialog emits `close`.
- Due-date adaptation between the API's normalized `YYYY-MM-DD HH:MM` value and `<input type="datetime-local">`'s `YYYY-MM-DDTHH:MM` value. Add `test_milestone_edit_datetime_local_round_trip_is_lossless`: GET a stored `2026-09-30 17:00`, submit the unchanged control value `2026-09-30T17:00`, then GET and assert the normalized API/storage value is still `2026-09-30 17:00`.

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "milestone_dialog or pending_revision or focus or responsive"`

Expected: FAIL because the UI is absent and revisions are currently dropped while dialogs are open.

- [ ] **Step 2: Add the static dialog structure**

Use existing dialog/list/form patterns and these stable IDs:

```text
milestones-open, milestones-dialog, milestone-create-form,
milestone-active-list, milestone-archived-list, milestone-editor,
milestone-edit-form, milestone-archive, milestone-remove,
milestone-remove-options, milestone-message
```

Keep archived details read-only. Hide the editor when no record is selected and render a direct create prompt for an empty list.

- [ ] **Step 3: Add minimal client state and endpoint actions**

Use one `milestones` array and one `selectedMilestoneKey`. Implement these named functions with single responsibilities:

- `loadMilestones()` fetches `/api/milestones`, replaces the array, preserves selection when possible, and calls both renderers.
- `renderMilestoneLists()` creates active and archived list buttons with text nodes and the documented empty states.
- `selectMilestone(key)` finds one record, fills metadata/form controls, and toggles active versus archived actions.
- `submitMilestoneCreate(event)` posts title/description/dueDate and selects the returned key after reload.
- `submitMilestoneEdit(event)` posts title/description/dueDate to the encoded selected key and reloads the list.
- `archiveSelectedMilestone()` posts an empty object to the encoded selected key and selects the archived response.
- `removeSelectedMilestone()` sends no policy when count is zero or the explicitly selected `keep`/`clear` policy when references exist, then clears selection and reloads.

Use one explicit adapter at the DOM boundary:

```javascript
function dueDateInputValue(value) {
  return value ? String(value).replace(" ", "T").slice(0, 16) : "";
}
```

`selectMilestone()` assigns this value to the datetime-local control. Create/edit submission sends the control's `YYYY-MM-DDTHH:MM` string unchanged; the server-side normalizer from Task 7 converts it back to the API/storage `YYYY-MM-DD HH:MM` form. Clearing the control sends the documented empty value.

All DOM text uses `textContent`; do not concatenate user content into `innerHTML`. Disable only the control whose request is active. Show server errors next to the affected form and refresh the list after successful mutation. Archive is the primary retirement action; Remove remains secondary and requires explicit policy only when `taskReferenceCount > 0`.

- [ ] **Step 4: Preserve revisions received behind dialogs**

Implement exactly one pending value:

```javascript
let pendingBoardRevision = "";

function handleBoardRevision(nextRevision) {
  if (!nextRevision || nextRevision === currentBoardRevision) return;
  if (hasOpenDialog()) {
    pendingBoardRevision = nextRevision;
    return;
  }
  window.location.reload();
}

document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("close", () => {
    if (pendingBoardRevision && !hasOpenDialog()) window.location.reload();
  });
});
```

- [ ] **Step 5: Run all milestone/browser tests and lint**

Run:

```bash
uv run --extra dev python -m pytest tests/test_milestones.py tests/test_browser_service.py tests/test_mcp_protocol_sdk_free.py -q
uv run --extra dev python -m ruff check src/backlog_py/core/milestones.py src/backlog_py/mcp/tools.py src/backlog_py/browser/service.py tests/test_milestones.py tests/test_browser_service.py tests/test_mcp_protocol_sdk_free.py
```

Expected: PASS, then `All checks passed!`.

- [ ] **Step 6: Commit milestone management and deferred refresh**

```bash
git add src/backlog_py/browser/templates/board.html src/backlog_py/browser/assets/board.js src/backlog_py/browser/assets/board.css tests/test_browser_service.py
git commit -m "feat: manage milestones from the browser"
```

## Slice 4: safe structured statuses

### Task 13: Batch multiple config changes into one atomic replacement

**Files:**
- Modify: `tests/test_config_storage.py`
- Modify: `src/backlog_py/storage/config.py:125-181,310-350`

- [ ] **Step 1: Add failing batch-config tests**

```python
def test_set_config_values_writes_once_and_preserves_unknown_keys(project, monkeypatch) -> None:
    writes = _record_atomic_writes(monkeypatch)
    config = set_config_values(project, {"statuses": "[Ready, Done]", "defaultStatus": "Ready"})
    assert len(writes) == 1
    assert config.statuses == ["Ready", "Done"]
    assert config.default_status == "Ready"
    assert yaml.safe_load(project.config_path.read_text())["custom"] == {"preserve": True}


def test_set_config_values_validates_every_value_before_writing(project, monkeypatch) -> None:
    before = project.config_path.read_bytes()
    writes = _record_atomic_writes(monkeypatch)
    with pytest.raises(ValueError):
        set_config_values(project, {"projectName": "Changed", "defaultPort": "0"})
    assert writes == []
    assert project.config_path.read_bytes() == before
```

Also test alias-key preservation and optional-value removal (`zeroPaddedIds`/`onStatusChange`) to prevent divergence from `set_config_value()`.

- [ ] **Step 2: Run the batch-config tests**

Run: `uv run --extra dev python -m pytest tests/test_config_storage.py -q -k "set_config_values"`

Expected: FAIL because `set_config_values()` does not exist.

- [ ] **Step 3: Extract one in-memory value applier and add the batch API**

```python
def _apply_config_value(raw: dict[Any, Any], key: str, value: str) -> tuple[str, Any]:
    # Move the existing normalization, read-only rejection, alias targeting,
    # optional removal, and display-value logic here. Mutate only `raw`.


def set_config_values(project: BacklogProject, updates: Mapping[str, str]) -> BacklogConfig:
    raw = _load_raw_config(project.config_path)
    for key, value in updates.items():
        _apply_config_value(raw, key, value)  # all parsing before disk write
    yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).strip()
    _atomic_write_text(project.config_path, f"{yaml_text}\n", base=project.root)
    return load_config(project.config_path)
```

Refactor `set_config_value()` to load once, call `_apply_config_value()`, write once, and return its existing tuple. Import `Mapping` from `collections.abc`. Do not make browser-specific status rules part of generic config storage.

- [ ] **Step 4: Run config and existing CLI/TUI config tests**

Run: `uv run --extra dev python -m pytest tests/test_config_storage.py tests/test_cli_readonly.py tests/test_definition_of_done.py tests/test_tui_data.py -q -k "config or settings or definition_of_done"`

Expected: PASS.

- [ ] **Step 5: Commit atomic batch config writes**

```bash
git add src/backlog_py/storage/config.py tests/test_config_storage.py
git commit -m "feat: batch config updates atomically"
```

### Task 14: Validate complete status settings and expose usage rows

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py:73-91,388-415,510-535,1680-1785`

- [ ] **Step 1: Add failing settings behavior tests**

Cover:

- GET preserves `settings.statuses` and adds `settings.statusRows` as ordered `{"name", "taskCount"}` objects.
- When configured statuses are absent/empty, rows derive from local active task order and append the current default case-insensitively if missing.
- With no configured/derived statuses, rows contain only the default.
- Submitted statuses are non-empty, trimmed, case-insensitively unique, and contain the submitted/current default.
- Removing a local in-use status or submitted default returns 409 with no config mutation.
- Only-default update with non-empty configured statuses must be a member.
- Only-default update with absent/empty statuses accepts any non-empty value.
- Neither field means status-pair validation does not run.
- A successful full request calls the config atomic writer once and refreshes `server.project`.

Use existing request helpers and monkeypatch `browser_service.set_config_values` to count one call.

- [ ] **Step 2: Run focused settings tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "status_rows or status_pair or config_settings_update"`

Expected: FAIL because usage rows, final-state validation, 409 mapping, and batch writing are absent.

- [ ] **Step 3: Add browser-only final-state validation**

Define:

```python
class _BrowserConflictError(ValueError):
    pass


def _status_rows(project: BacklogProject, config: BacklogConfig) -> list[dict[str, object]]:
    # Use configured non-empty list, else local active task-derived board order;
    # append default if absent case-insensitively; count current active local tasks.


def _validate_status_settings(
    project: BacklogProject,
    current: BacklogConfig,
    updates: Mapping[str, str],
) -> dict[str, str]:
    # Apply only the approved partial-request rules. Parse submitted statuses with
    # json.loads(updates["statuses"]) (the exact JSON string produced by
    # _statuses_setting), compare case-insensitively, and raise
    # _BrowserConflictError for default/in-use removal. When a default matches a
    # submitted/configured status case-insensitively, replace it in the returned
    # update mapping with that status's canonical display spelling.
```

Change `_config_settings_payload(config, *, project)` to add `statusRows`. Change the POST handler to parse → validate-and-normalize → call `set_config_values()` once inside `browser_config_settings_update` → replace `server.project`. Catch `_BrowserConflictError` before `ValueError` and return 409.

- [ ] **Step 4: Run browser and config tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_config_storage.py -q -k "config or status"`

Expected: PASS.

- [ ] **Step 5: Commit status-pair validation**

```bash
git add src/backlog_py/browser/service.py tests/test_browser_service.py
git commit -m "feat: validate browser status configuration"
```

### Task 15: Replace the raw status textarea with structured controls

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/templates/board.html:123-182`
- Modify: `src/backlog_py/browser/assets/board.js:1-20,782-809,1008-1043,1070-1075`
- Modify: `src/backlog_py/browser/assets/board.css:166-220,400-454`

- [ ] **Step 1: Add failing structured-editor contract tests**

Assert:

- Default status is a `<select>`.
- Status rows container and Add input/button are present; raw `textarea[name="statuses"]` is absent.
- JS renders usage counts and exact `Move <name> up`, `Move <name> down`, `Remove <name>` accessible labels.
- Add works on button click and Enter, duplicates are case-insensitively rejected, and changes remain client-local until Save.
- Move controls update the submitted order.
- Remove is disabled for default/in-use rows using server-provided counts; server errors still render inline.
- Settings/status rows stack at `max-width: 720px`, controls show visible focus, and no control relies only on color.

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "structured_status or general_settings_dialog or responsive"`

Expected: FAIL because the textarea and free-text default remain.

- [ ] **Step 2: Add the host markup and minimal state model**

Replace only the two status fields with:

```html
<label for="config-default-status">Default status</label>
<select id="config-default-status" name="defaultStatus" required></select>
<fieldset class="status-editor">
  <legend>Statuses</legend>
  <div id="config-status-rows"></div>
  <div class="status-add-row">
    <label for="config-status-add">Add status</label>
    <input id="config-status-add" autocomplete="off">
    <button type="button" id="config-status-add-button">Add</button>
  </div>
  <p id="config-status-message" role="status" aria-live="polite"></p>
</fieldset>
```

Use one array of `{name, taskCount}` copied from `settings.statusRows`. Render rows with DOM APIs/textContent. Rebuild the default selector from the working array while preserving its selection. Disable unsafe removes client-side but keep the server authoritative.

- [ ] **Step 3: Submit structured state and render inline errors**

Remove textarea parsing from `submitConfigSettings()` and send:

```javascript
statuses: statusRows.map((row) => row.name),
defaultStatus: String(form.elements.defaultStatus.value || ""),
```

On non-OK response, parse `{error}`, write `config-status-message`, and retain the dialog/state. Reload only after success.

- [ ] **Step 4: Run settings UI and endpoint tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_config_storage.py -q -k "settings or status or responsive"`

Expected: PASS.

- [ ] **Step 5: Commit structured status creation/reordering**

```bash
git add src/backlog_py/browser/templates/board.html src/backlog_py/browser/assets/board.js src/backlog_py/browser/assets/board.css tests/test_browser_service.py
git commit -m "feat: add structured browser status editor"
```

## Documentation, parity inventory, and verification

### Task 16: Publish the concrete gap analysis and audited parity status

**Files:**
- Create: `docs/webui-gap-analysis.md`
- Modify: `src/backlog_py/compat/inventory.py:176-329`
- Modify: `src/backlog_py/compat/report.py:13-22`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`
- Modify: `docs/browser-release-validation.md`
- Modify: `docs/stability-policy.md:32-40`
- Modify: `CHANGELOG.md:1-5`

- [ ] **Step 1: Add failing inventory/baseline ratchets**

Add four explicit browser inventory items:

```text
browser:persistent-column-sort
browser:milestone-management
browser:milestone-label-filters
browser:structured-status-editor
```

Update expected totals from 102 to 106 and browser totals from 24 to 28. Change the current report baseline assertions to:

```python
{"package": "backlog.md", "version": "1.50.1", "audit_date": "2026-09-01"}
```

Update the fresh evidence test helper to emit that current baseline. Retain at least one explicit mismatched-evidence test using 1.45.2. Do not change `tests/fixtures/oracle/manifest.yml`: it remains historical agent-critical golden evidence because this slice changes no CLI/MCP golden command surface.

Run: `uv run --extra dev python -m pytest tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py -q`

Expected: FAIL until inventory/report/docs expectations are updated.

- [ ] **Step 2: Update the current report and inventory**

Set `UPSTREAM_BASELINE` to Backlog.md 1.50.1 audited 2026-09-01. Add the four implemented browser entries with precise expected behavior. Update ratchet counts and CLI output assertions; do not mark deferred follow-ups as implemented.

- [ ] **Step 3: Write `docs/webui-gap-analysis.md`**

The document must include:

1. Audited source commits/versions and dates.
2. A code-path table for each delivered goal:
   - upstream React/API/server/core/storage path;
   - Python template/JS/service/core/storage path;
   - behavior and file-format compatibility differences;
   - implemented resolution and test evidence.
3. Explicit scope boundaries: local working-tree mutations only; read-only active-branch snapshots are not browser-mutable.
4. Delivered details for persistent sort, append ordinals, dual-read/current-write milestones, label any-match WebUI filtering, and structured statuses.
5. The ordered follow-up roadmap and dependencies from the approved spec:
   - branch provenance/selection;
   - positional, keyboard, and batch reorder;
   - milestone lanes/lifecycle expansion;
   - full all-tasks table;
   - config-managed priorities/labels/types/projects;
   - extra task metadata;
   - document/decision CRUD;
   - statistics/cleanup/search;
   - incremental board reconciliation;
   - richer editing and fresh release evidence.

State clearly that current files are never auto-migrated and legacy milestone task values survive ordinary saves.

- [ ] **Step 4: Update parity/release/stability docs without rewriting history**

- `docs/browser-parity.md`: current audited feature coverage is 1.50.1 and includes sort/milestones/labels/status editor; release readiness still requires fresh browser artifacts.
- `docs/upstream-feature-parity.md`: replace the old “packaging-only 1.45.2 delta” current section with the 1.50.1 audited feature comparison and link the new gap analysis.
- `docs/browser-release-validation.md`: examples generated now use 1.50.1/2026-09-01; retain the older historical validation narrative as historical.
- `docs/stability-policy.md`: distinguish the 1.50.1 current feature audit from the still-pinned 1.45.2 agent-critical oracle manifest.
- `release-evidence/browser-release-evidence.json`: leave unchanged so it truthfully remains historical/mismatched; do not relabel old evidence.
- `CHANGELOG.md`: add concise Unreleased Added/Changed bullets for the delivered features and current audit baseline.

- [ ] **Step 5: Run parity/document tests**

Run: `uv run --extra dev python -m pytest tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py tests/test_package_metadata.py -q`

Expected: PASS.

- [ ] **Step 6: Commit gap analysis and parity documentation**

```bash
git add CHANGELOG.md docs/webui-gap-analysis.md docs/browser-parity.md docs/upstream-feature-parity.md docs/browser-release-validation.md docs/stability-policy.md src/backlog_py/compat/inventory.py src/backlog_py/compat/report.py tests/test_compat_report.py tests/test_cli_readonly.py
git commit -m "docs: record Backlog.md 1.50.1 WebUI parity"
```

### Task 17: Run full automated and real-browser verification

**Files:**
- Modify only if verification finds a defect: the smallest responsible source/test file from Tasks 1-16
- Do not commit temporary screenshots, logs, build output, or generated virtual environments

- [ ] **Step 1: Run formatting and blocking static checks**

```bash
git diff --check
uv run --extra dev python -m ruff check src tests
uv run --extra dev python -m bandit -r src
```

Expected: no whitespace errors, `All checks passed!`, and no Bandit findings that fail the command. If a check fails, add a focused regression test where appropriate, make the smallest fix, rerun the focused test, and commit the fix separately.

- [ ] **Step 2: Run the full test suite**

Run: `uv run --extra dev python -m pytest tests -v`

Expected: all tests pass; compare skips and pre-existing warnings to the baseline (`1132 passed, 5 skipped`) and report the new exact totals.

- [ ] **Step 3: Run advisory type checking**

Run: `uv run --extra dev python -m mypy`

Expected: report the exact result against the documented baseline of 60 errors across 14 files. This is advisory, but fix newly introduced errors in touched code when they are local and unambiguous.

- [ ] **Step 4: Verify package resources in built artifacts**

```bash
uv run --extra dev python -m build --outdir /private/tmp/backlog-md-py-webui-dist
uv run --extra dev python -m twine check /private/tmp/backlog-md-py-webui-dist/*
```

Expected: wheel and sdist build successfully, Twine reports `PASSED`, and `tests/test_package_metadata.py` has already confirmed the packaged HTML/CSS/JS resources.

- [ ] **Step 5: Run current compatibility report**

Run: `uv run --extra dev backlog-py compat status --json`

Expected: 106 implemented, 0 deferred, current upstream baseline 1.50.1/2026-09-01, and browser release readiness remains evidence-gated unless new artifacts are attached.

- [ ] **Step 6: Perform real-browser desktop, mobile, keyboard, and theme checks**

Apply `@browser:control-in-app-browser` (or `@playwright` if terminal automation is preferred):

1. Create and run against a disposable copy, recording the returned directory so the repository fixture is never mutated:
   ```bash
   WEBUI_VERIFY_ROOT="$(mktemp -d /private/tmp/backlog-md-py-webui.XXXXXX)"
   cp -R tests/fixtures/repos/basic "$WEBUI_VERIFY_ROOT/project"
   uv run --extra dev backlog-py --cwd "$WEBUI_VERIFY_ROOT/project" browser --port 6421 --no-open
   ```
2. Open `http://127.0.0.1:6421/` at a desktop viewport and a narrow viewport near 390 px.
3. Verify priority/created sorting persists after reload, filter URL state remains, labels use any-match, current/legacy/archived milestone display is correct, milestone CRUD confirmations work, and a new empty status column appears after Save.
4. Navigate all new controls with Tab/Shift+Tab/Enter/Space; confirm visible focus and native disclosure/select behavior.
5. Check system light and dark themes and `prefers-reduced-motion`; no information may depend on color or animation.
6. Confirm an external task edit received while a dialog is open reloads only after the final dialog closes.
7. Save non-repository screenshots to `/private/tmp/backlog-md-py-webui-desktop.png` and `/private/tmp/backlog-md-py-webui-mobile.png` if useful for review; do not call them release evidence unless a proper manifest is created.
8. Stop the browser server when the checks finish. The temporary copied project may remain under `/private/tmp` for the OS to reclaim; do not delete or modify the repository fixture.

- [ ] **Step 7: Review the final diff and commit only genuine verification fixes**

```bash
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: only approved implementation, tests, and docs are tracked; `.venv`, temporary artifacts, and screenshots are absent. If verification required fixes, commit them with a precise `fix:` message. Otherwise make no empty commit.

- [ ] **Step 8: Request final code review**

Apply `@superpowers:requesting-code-review` to the complete `main...HEAD` diff. Resolve correctness, compatibility, security, accessibility, and test-coverage findings before claiming implementation complete.
