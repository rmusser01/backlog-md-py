"""Regression tests for inherited orchestration bugs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backlog_py.orchestration import (
    MAX_RUN_HISTORY_ENTRIES,
    RUN_HISTORY_TRUNCATION_TYPE,
    OrchestrationIdempotencyConflict,
    OrchestrationService,
    OrchestrationStateUpdate,
    OrchestrationTransitionError,
    OrchestrationValidationError,
    TaskSplitItem,
    parse_orchestration,
    parse_run_history,
)
from backlog_py.runtime.mutations import mutation_by_name
from backlog_py.orchestration.policy import load_orchestration_policy
from backlog_py.core.repository import MutableRepository
from backlog_py.storage.project import discover_project


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: orch-fixes\n", encoding="utf-8")
    (task_dir / "task-1 - Example.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Example\n"
        "status: To Do\n"
        "orchestration:\n"
        "  status_key: todo\n"
        "  version: 0\n"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    return repo


def _service(repo: Path) -> OrchestrationService:
    return OrchestrationService(
        discover_project(repo),
        now=lambda: datetime(2026, 6, 26, 18, 4, tzinfo=timezone.utc),
    )


def _status_key(repo: Path) -> str:
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    return parse_orchestration(task).status_key


def _repo_with_orchestration(tmp_path: Path, orchestration_yaml: str, *, status: str = "To Do") -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: orch-fixes\n", encoding="utf-8")
    indented = "".join(f"  {line}\n" for line in orchestration_yaml.strip().splitlines())
    (task_dir / "task-1 - Example.md").write_text(
        f"---\nid: TASK-1\ntitle: Example\nstatus: {status}\norchestration:\n{indented}---\n\n"
        "## Description\n\nBody\n",
        encoding="utf-8",
    )
    return repo


# --- review follow-up: record_run must not acquire a lease outside claim rules

def test_record_run_cannot_acquire_lease_on_non_claimable_task(tmp_path):
    # 'review' is not claimable in the default policy and there is no active
    # lease, so record_run must not let an actor grant itself one (that would
    # bypass claim_task's is_claimable check).
    repo = _repo_with_orchestration(tmp_path, "status_key: review\nversion: 0", status="In Progress")

    with pytest.raises(OrchestrationTransitionError):
        _service(repo).record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="grab",
            expected_version=0,
            state_update=OrchestrationStateUpdate(lease_owner="agent"),
        )


def test_record_run_can_renew_own_active_lease(tmp_path):
    # The actor already holds the active lease, so updating its lease fields is
    # allowed even on a non-claimable status.
    repo = _repo_with_orchestration(
        tmp_path,
        "status_key: review\nversion: 0\nlease_owner: agent\nlease_expires_at: '2026-06-26T19:00:00Z'",
        status="In Progress",
    )

    result = _service(repo).record_run(
        "TASK-1",
        actor="agent",
        result="succeeded",
        summary="renew",
        expected_version=0,
        state_update=OrchestrationStateUpdate(lease_expires_at="2026-06-26T20:00:00Z"),
    )
    assert result.version == 1


# --- #18: record_run bypasses the policy state machine ----------------------

def test_record_run_rejects_illegal_status_jump(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(OrchestrationTransitionError):
        _service(repo).record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="jump",
            expected_version=0,
            state_update=OrchestrationStateUpdate(status_key="complete"),
        )
    assert _status_key(repo) == "todo", "illegal transition was persisted"


def test_record_run_rejects_unknown_status(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(OrchestrationTransitionError):
        _service(repo).record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="bogus",
            expected_version=0,
            state_update=OrchestrationStateUpdate(status_key="totally-not-a-real-status"),
        )
    assert _status_key(repo) == "todo"


def test_record_run_allows_legal_transition(tmp_path):
    repo = _repo(tmp_path)

    _service(repo).record_run(
        "TASK-1",
        actor="agent",
        result="succeeded",
        summary="start",
        expected_version=0,
        state_update=OrchestrationStateUpdate(status_key="inprogress"),
    )
    assert _status_key(repo) == "inprogress"


# --- orchestration critical #2: marker injection in run-history summary -----

def test_record_run_rejects_marker_in_summary(tmp_path):
    from backlog_py.orchestration import RunHistoryParseError

    repo = _repo(tmp_path)
    with pytest.raises((RunHistoryParseError, ValueError)):
        _service(repo).record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="done <!-- RUN_HISTORY_ENTRY:END --> injected",
        )

    # The task's run history must remain parseable and mutable afterwards.
    _service(repo).record_run("TASK-1", actor="agent", result="succeeded", summary="clean entry")


# --- #19: released task is stuck (not re-claimable) -------------------------

def test_task_can_be_reclaimed_after_release(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)

    claim = service.claim_task("TASK-1", actor="agent-a", expected_version=0)
    release = service.release_task("TASK-1", actor="agent-a", expected_version=claim.version)

    # After release the task must be back in a claimable state.
    policy = load_orchestration_policy(service.project)
    assert policy.is_claimable(_status_key(repo))

    # And another agent can actually claim it again.
    service.claim_task("TASK-1", actor="agent-b", expected_version=release.version)
    assert _status_key(repo) == "inprogress"


# --- #20: substring "complete" detection misclassifies statuses -------------

def test_is_complete_status_uses_exact_matching():
    from backlog_py.orchestration.reports import _is_complete_status

    assert _is_complete_status("Done")
    assert _is_complete_status("Completed")
    assert _is_complete_status("Done ✅")
    assert not _is_complete_status("Incomplete")
    assert not _is_complete_status("Abandoned")
    assert not _is_complete_status("Not Done")
    assert not _is_complete_status("In Progress")


# --- review #1: claim must derive its target status from the policy ---------

_CUSTOM_POLICY = "\n".join(
    [
        "states:",
        "  todo:",
        "    claimable: true",
        "  doing: {}",
        "  review: {}",
        "  done:",
        "    terminal: true",
        "transitions:",
        "  todo: [doing]",
        "  doing: [review, todo]",
        "  review: [done, doing]",
        "  done: []",
    ]
)


def _write_policy(repo: Path, policy_yaml: str) -> None:
    (repo / "backlog" / "orchestration.yml").write_text(f"{policy_yaml}\n", encoding="utf-8")


def _task_source(repo: Path) -> str:
    return MutableRepository.from_path(repo).get_task("TASK-1").raw_source


def test_claim_task_moves_to_policy_working_status_not_hardcoded_inprogress(tmp_path):
    repo = _repo_with_orchestration(tmp_path, "status_key: todo\nversion: 0")
    _write_policy(repo, _CUSTOM_POLICY)

    result = _service(repo).claim_task("TASK-1", actor="agent", expected_version=0)

    assert result.event.from_status == "todo"
    assert result.event.to_status == "doing"
    assert _status_key(repo) == "doing"


def test_claim_release_reclaim_round_trip_under_custom_policy(tmp_path):
    repo = _repo_with_orchestration(tmp_path, "status_key: todo\nversion: 0")
    _write_policy(repo, _CUSTOM_POLICY)
    service = _service(repo)

    claim = service.claim_task("TASK-1", actor="agent-a", expected_version=0)
    release = service.release_task("TASK-1", actor="agent-a", expected_version=claim.version)
    assert _status_key(repo) == "todo"

    service.claim_task("TASK-1", actor="agent-b", expected_version=release.version)
    assert _status_key(repo) == "doing"


# --- review #2: record_run must not persist invalid orchestration state -----

def test_record_run_rejects_state_update_that_would_persist_invalid_state(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as error:
        service.record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="bad lease",
            expected_version=0,
            state_update=OrchestrationStateUpdate(lease_owner="agent", lease_expires_at="not-a-timestamp"),
        )

    assert [issue["code"] for issue in error.value.details["issues"]] == ["invalid_lease_expires_at"]
    assert _task_source(repo) == before
    # The task must still be usable after the rejected update.
    assert service.claim_task("TASK-1", actor="agent", expected_version=0).version == 1


def test_record_run_rejects_lease_owner_without_expiry(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as error:
        service.record_run(
            "TASK-1",
            actor="agent",
            result="succeeded",
            summary="half a lease",
            expected_version=0,
            state_update=OrchestrationStateUpdate(lease_owner="agent"),
        )

    assert [issue["code"] for issue in error.value.details["issues"]] == ["missing_lease_expires_at"]
    assert _task_source(repo) == before


def test_record_run_can_still_repair_invalid_orchestration_state(tmp_path):
    repo = _repo_with_orchestration(tmp_path, "status_key: todo\nversion: 0\nlease_owner: agent")
    service = _service(repo)
    with pytest.raises(OrchestrationValidationError):
        service.claim_task("TASK-1", actor="agent", expected_version=0)

    result = service.record_run(
        "TASK-1",
        actor="agent",
        result="succeeded",
        summary="repair lease metadata",
        expected_version=0,
        state_update=OrchestrationStateUpdate(lease_expires_at="2026-06-26T19:00:00Z"),
    )

    assert result.version == 1
    # The repaired task accepts normal workflow mutations again.
    assert service.release_task("TASK-1", actor="agent", expected_version=1).version == 2


def test_record_run_without_state_update_still_journals_on_invalid_task(tmp_path):
    repo = _repo_with_orchestration(tmp_path, "status_key: todo\nversion: 0\nlease_owner: agent")

    result = _service(repo).record_run("TASK-1", actor="agent", result="failed", summary="cannot proceed")

    assert result.version == 0
    assert parse_run_history(_task_source(repo)).events[-1].summary == "cannot proceed"


# --- review #3: run history retention ---------------------------------------

def test_service_run_history_stays_capped_and_records_dropped_entries(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    total = MAX_RUN_HISTORY_ENTRIES + 3

    for index in range(total):
        service.record_run("TASK-1", actor="agent", result="succeeded", summary=f"run {index}")

    history = parse_run_history(_task_source(repo))
    assert history.issues == []
    assert len(history.events) == MAX_RUN_HISTORY_ENTRIES
    assert history.events[0].type == RUN_HISTORY_TRUNCATION_TYPE
    assert history.events[0].metadata["dropped_entries"] == str(total - MAX_RUN_HISTORY_ENTRIES + 1)
    assert history.events[1].summary == f"run {total - MAX_RUN_HISTORY_ENTRIES + 1}"
    assert history.events[-1].summary == f"run {total - 1}"


def test_service_replay_of_aged_out_idempotency_key_reports_conflict(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    service.record_run("TASK-1", actor="agent", result="succeeded", summary="run 0", idempotency_key="idem-0")
    for index in range(1, MAX_RUN_HISTORY_ENTRIES + 1):
        service.record_run("TASK-1", actor="agent", result="succeeded", summary=f"run {index}")

    with pytest.raises(OrchestrationIdempotencyConflict) as error:
        service.record_run("TASK-1", actor="agent", result="succeeded", summary="run 0", idempotency_key="idem-0")

    assert "idem-0" in str(error.value)


# --- review #4: split idempotency scan should not sweep the project ---------

def test_split_idempotency_replay_does_not_scan_completed_tasks(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    service = _service(repo)
    request = {
        "mode": "child",
        "actor": "agent",
        "idempotency_key": "split-1",
        "items": [TaskSplitItem(title="Child one")],
    }
    service.split_task("TASK-1", expected_version=0, **request)

    original = MutableRepository.list_completed_tasks
    calls: list[int] = []

    def counting_list_completed_tasks(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(MutableRepository, "list_completed_tasks", counting_list_completed_tasks)
    replay = service.split_task("TASK-1", expected_version=999, **request)

    assert replay.idempotent_replay
    assert calls == []


# --- review #5: mutation registry drift -------------------------------------

def test_service_lock_operations_resolve_in_mutation_registry(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    recorded: list[str] = []

    def recording_lock(project, operation, fn):
        recorded.append(operation)
        return fn()

    monkeypatch.setattr("backlog_py.orchestration.service.with_project_write_lock", recording_lock)
    service = _service(repo)
    service.record_run("TASK-1", actor="agent", result="succeeded", summary="journal")
    claim = service.claim_task("TASK-1", actor="agent", expected_version=0)
    transition = service.transition_task("TASK-1", "review", actor="agent", expected_version=claim.version)
    release = service.release_task("TASK-1", actor="agent", expected_version=transition.version)
    service.split_task(
        "TASK-1",
        mode="child",
        actor="agent",
        expected_version=release.version,
        items=[TaskSplitItem(title="Follow-up")],
    )

    assert set(recorded) == {
        "orchestration_record_run",
        "orchestration_claim_task",
        "orchestration_transition_task",
        "orchestration_release_task",
        "orchestration_split_task",
    }
    for operation in recorded:
        assert mutation_by_name(operation).lock_scope == "project"
