from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest

from backlog_py.core.repository import MutableRepository, TaskMutationError
from backlog_py.orchestration import (
    OrchestrationActorContext,
    OrchestrationIdempotencyConflict,
    OrchestrationLeaseConflict,
    OrchestrationService,
    OrchestrationStateUpdate,
    OrchestrationTransitionError,
    OrchestrationValidationError,
    OrchestrationVersionConflict,
    RunHistoryParseError,
    TaskSplitError,
    TaskSplitItem,
    parse_run_history,
    parse_orchestration,
    resolve_orchestration_actor,
)
from backlog_py.storage.project import discover_project


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: service-test\n", encoding="utf-8")
    (task_dir / "task-1 - Example.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Example\n"
        "status: To Do\n"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    return repo


def _write_task(directory: Path, task_id: str, title: str, *, status: str = "To Do") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id.lower()} - {title.replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        f"id: {task_id}\n"
        f"title: {title}\n"
        f"status: {status}\n"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    return path


def _service(repo: Path) -> OrchestrationService:
    return OrchestrationService(
        discover_project(repo),
        now=lambda: datetime(2026, 6, 26, 18, 4, tzinfo=timezone.utc),
    )


def _task_source(repo: Path) -> str:
    return MutableRepository.from_path(repo).get_task("TASK-1").raw_source


def _task_path(repo: Path) -> Path:
    return repo / "backlog" / "tasks" / "task-1 - Example.md"


def _set_task_orchestration(repo: Path, orchestration_yaml: str) -> None:
    path = _task_path(repo)
    indented = "".join(f"  {line}\n" for line in dedent(orchestration_yaml).strip().splitlines())
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "status: To Do\n",
            f"status: To Do\norchestration:\n{indented}",
        ),
        encoding="utf-8",
    )


def test_record_run_appends_run_history_to_task(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)

    result = service.record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Implemented and verified.",
        files=["src/backlog_py/orchestration/service.py"],
        verification=["uv run --extra dev python -m pytest tests/test_orchestration_service.py -q"],
    )

    parsed = parse_run_history(_task_source(repo))
    assert parsed.issues == []
    assert [event.event_id for event in parsed.events] == [result.event.event_id]
    assert parsed.events[0].actor == "codex"
    assert parsed.events[0].summary == "Implemented and verified."
    assert result.task_id == "TASK-1"
    assert result.path == "backlog/tasks/task-1 - Example.md"
    assert result.version == 0
    assert not result.idempotent_replay


def test_record_run_rejects_empty_result_without_write(tmp_path):
    repo = _repo(tmp_path)
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as error:
        _service(repo).record_run("TASK-1", actor="codex", result=" ", summary="No result.")

    assert error.value.details == {"field": "result"}
    assert _task_source(repo) == before


def test_record_run_refuses_malformed_existing_run_history(tmp_path):
    repo = _repo(tmp_path)
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    malformed = (
        task.raw_source
        + "\n<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n"
    )
    task.path.write_text(malformed, encoding="utf-8")
    before = task.path.read_text(encoding="utf-8")

    with pytest.raises(RunHistoryParseError) as error:
        _service(repo).record_run("TASK-1", actor="codex", result="failed", summary="Could not continue.")

    assert error.value.code == "run_history_entry_unterminated"
    assert task.path.read_text(encoding="utf-8") == before


def test_record_run_idempotency_replay_returns_prior_event_without_rewrite(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    first = service.record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Done.",
        idempotency_key="run-task-1",
    )
    before = _task_source(repo)

    second = service.record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Done.",
        idempotency_key="run-task-1",
        expected_version=999,
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert _task_source(repo) == before


def test_record_run_idempotency_replay_survives_state_update_retry(tmp_path):
    repo = _repo(tmp_path)
    task_path = repo / "backlog" / "tasks" / "task-1 - Example.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: To Do\n",
            "status: To Do\n"
            "orchestration:\n"
            "  status_key: todo\n"
            "  version: 0\n",
        ),
        encoding="utf-8",
    )
    service = _service(repo)

    first = service.record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Claimed task.",
        idempotency_key="claim-task-1",
        expected_version=0,
        state_update=OrchestrationStateUpdate(status_key="inprogress"),
    )
    before = _task_source(repo)

    second = service.record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Claimed task.",
        idempotency_key="claim-task-1",
        expected_version=0,
        state_update=OrchestrationStateUpdate(status_key="inprogress"),
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert _task_source(repo) == before


def test_record_run_idempotency_conflict_raises(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    service.record_run("TASK-1", actor="codex", result="succeeded", summary="Done.", idempotency_key="run-task-1")

    with pytest.raises(OrchestrationIdempotencyConflict):
        service.record_run(
            "TASK-1",
            actor="codex",
            result="succeeded",
            summary="Different.",
            idempotency_key="run-task-1",
        )


def test_record_only_run_does_not_increment_or_create_orchestration_version(tmp_path):
    repo = _repo(tmp_path)

    _service(repo).record_run("TASK-1", actor="codex", result="succeeded", summary="Done.")

    task = MutableRepository.from_path(repo).get_task("TASK-1")
    assert "orchestration" not in task.parsed.frontmatter


def test_record_run_with_state_update_increments_version_when_expected_version_matches(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).record_run(
        "TASK-1",
        actor="codex",
        result="succeeded",
        summary="Started work.",
        expected_version=0,
        state_update=OrchestrationStateUpdate(
            status_key="inprogress",
            lease_owner="codex",
            correlation_id="run-1",
            review_state="not_started",
            reviewer="human",
            review_attempts=0,
            review_max_attempts=3,
        ),
    )

    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.version == 1
    assert orchestration.status_key == "inprogress"
    assert orchestration.lease_owner == "codex"
    assert orchestration.correlation_id == "run-1"
    assert orchestration.review is not None
    assert orchestration.review.state == "not_started"
    assert result.version == 1


def test_record_run_state_update_requires_expected_version(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(OrchestrationVersionConflict):
        _service(repo).record_run(
            "TASK-1",
            actor="codex",
            result="succeeded",
            summary="Started work.",
            state_update=OrchestrationStateUpdate(status_key="inprogress"),
        )


def test_record_run_stale_expected_version_raises(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(OrchestrationVersionConflict) as error:
        _service(repo).record_run(
            "TASK-1",
            actor="codex",
            result="succeeded",
            summary="Started work.",
            expected_version=2,
            state_update=OrchestrationStateUpdate(status_key="inprogress"),
        )

    assert error.value.details["expected_version"] == 2
    assert error.value.details["actual_version"] == 0


def test_claim_task_creates_inprogress_lease_from_missing_version(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).claim_task(
        "TASK-1",
        actor="codex",
        expected_version=0,
        idempotency_key="claim-task-1",
        lease_ttl_seconds=60,
        reason="Starting implementation.",
    )

    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.version == 1
    assert orchestration.status_key == "inprogress"
    assert orchestration.lease_owner == "codex"
    assert orchestration.lease_expires_at == "2026-06-26T18:05:00Z"
    assert orchestration.idempotency_key == "claim-task-1"
    history = parse_run_history(task.raw_source)
    assert history.issues == []
    assert len(history.events) == 1
    assert history.events[0].type == "claim_task"
    assert history.events[0].actor == "codex"
    assert history.events[0].timestamp == "2026-06-26T18:04:00Z"
    assert history.events[0].result == "succeeded"
    assert history.events[0].from_status == "todo"
    assert history.events[0].to_status == "inprogress"
    assert history.events[0].summary == "Starting implementation."
    assert result.version == 1
    assert result.event == history.events[0]


def test_claim_task_stale_expected_version_raises(tmp_path):
    repo = _repo(tmp_path)
    before = _task_source(repo)

    with pytest.raises(OrchestrationVersionConflict) as error:
        _service(repo).claim_task("TASK-1", actor="codex", expected_version=2)

    assert error.value.details["expected_version"] == 2
    assert error.value.details["actual_version"] == 0
    assert _task_source(repo) == before


def test_claim_task_rejects_active_lease_owned_by_another_actor(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: todo
        version: 3
        lease_owner: agent-a
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationLeaseConflict) as error:
        _service(repo).claim_task("TASK-1", actor="codex", expected_version=3)

    assert error.value.details["lease_owner"] == "agent-a"
    assert error.value.details["lease_expires_at"] == "2026-06-26T18:10:00Z"
    assert error.value.details["actual_version"] == 3
    assert _task_source(repo) == before


def test_claim_task_allows_stale_lease_reclaim(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: todo
        version: 4
        lease_owner: agent-a
        lease_expires_at: '2026-06-26T18:03:00Z'
        """,
    )

    result = _service(repo).claim_task(
        "TASK-1",
        actor="codex",
        expected_version=4,
        idempotency_key="reclaim-task-1",
        lease_ttl_seconds=120,
        reason="Expired lease.",
    )

    orchestration = parse_orchestration(MutableRepository.from_path(repo).get_task("TASK-1"))
    assert orchestration is not None
    assert orchestration.version == 5
    assert orchestration.status_key == "inprogress"
    assert orchestration.lease_owner == "codex"
    assert orchestration.lease_expires_at == "2026-06-26T18:06:00Z"
    assert orchestration.idempotency_key == "reclaim-task-1"
    assert result.version == 5


def test_release_task_clears_lease_and_increments_version(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 1
        lease_owner: codex
        lease_expires_at: '2026-06-26T18:10:00Z'
        idempotency_key: claim-task-1
        """,
    )

    result = _service(repo).release_task(
        "TASK-1",
        actor="codex",
        expected_version=1,
        idempotency_key="release-task-1",
        reason="Handing off.",
    )

    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.version == 2
    # Release returns the task to a claimable status so it re-enters the queue.
    assert orchestration.status_key == "todo"
    assert orchestration.lease_owner is None
    assert orchestration.lease_expires_at is None
    assert orchestration.idempotency_key == "release-task-1"
    history = parse_run_history(task.raw_source)
    assert history.events[0].type == "release_task"
    assert history.events[0].summary == "Handing off."
    assert result.version == 2


def test_transition_task_enforces_policy_and_increments_version(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 2
        lease_owner: codex
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )

    result = _service(repo).transition_task(
        "TASK-1",
        "review",
        actor="codex",
        expected_version=2,
        idempotency_key="transition-task-1",
        reason="Ready for review.",
    )

    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.version == 3
    assert orchestration.status_key == "review"
    assert orchestration.idempotency_key == "transition-task-1"
    history = parse_run_history(task.raw_source)
    assert history.events[0].type == "transition_task"
    assert history.events[0].from_status == "inprogress"
    assert history.events[0].to_status == "review"
    assert history.events[0].summary == "Ready for review."
    assert result.version == 3

    with pytest.raises(OrchestrationTransitionError):
        _service(repo).transition_task("TASK-1", "todo", actor="codex", expected_version=3)


def test_mutation_idempotency_replay_precedes_expected_version_check(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    first = service.claim_task(
        "TASK-1",
        actor="codex",
        expected_version=0,
        idempotency_key="claim-replay",
        lease_ttl_seconds=60,
        reason="Starting implementation.",
    )
    before = _task_source(repo)

    second = service.claim_task(
        "TASK-1",
        actor="codex",
        expected_version=999,
        idempotency_key="claim-replay",
        lease_ttl_seconds=60,
        reason="Starting implementation.",
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert second.version == first.version
    assert _task_source(repo) == before


def test_transition_task_idempotency_replay_precedes_policy_validation(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 2
        lease_owner: codex
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )
    service = _service(repo)
    first = service.transition_task(
        "TASK-1",
        "review",
        actor="codex",
        expected_version=2,
        idempotency_key="transition-replay",
        reason="Ready for review.",
    )
    before = _task_source(repo)

    second = service.transition_task(
        "TASK-1",
        "review",
        actor="codex",
        expected_version=999,
        idempotency_key="transition-replay",
        reason="Ready for review.",
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert _task_source(repo) == before


def test_release_task_idempotency_replay_precedes_expected_version_check(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 1
        lease_owner: codex
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )
    service = _service(repo)
    first = service.release_task(
        "TASK-1",
        actor="codex",
        expected_version=1,
        idempotency_key="release-replay",
        reason="Handing off.",
    )
    before = _task_source(repo)

    second = service.release_task(
        "TASK-1",
        actor="codex",
        expected_version=999,
        idempotency_key="release-replay",
        reason="Handing off.",
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert _task_source(repo) == before


def test_claim_task_idempotency_replay_does_not_depend_on_generated_lease_expiry(tmp_path):
    repo = _repo(tmp_path)
    times = [
        datetime(2026, 6, 26, 18, 4, tzinfo=timezone.utc),
        datetime(2026, 6, 26, 18, 4, tzinfo=timezone.utc),
        datetime(2026, 6, 26, 18, 4, tzinfo=timezone.utc),
        datetime(2026, 6, 26, 18, 5, tzinfo=timezone.utc),
        datetime(2026, 6, 26, 18, 5, tzinfo=timezone.utc),
        datetime(2026, 6, 26, 18, 5, tzinfo=timezone.utc),
    ]

    def now() -> datetime:
        return times.pop(0) if times else datetime(2026, 6, 26, 18, 5, tzinfo=timezone.utc)

    service = OrchestrationService(discover_project(repo), now=now)
    first = service.claim_task(
        "TASK-1",
        actor="codex",
        expected_version=0,
        idempotency_key="claim-replay-moving-clock",
        lease_ttl_seconds=60,
        reason="Starting implementation.",
    )
    before = _task_source(repo)

    second = service.claim_task(
        "TASK-1",
        actor="codex",
        expected_version=999,
        idempotency_key="claim-replay-moving-clock",
        lease_ttl_seconds=60,
        reason="Starting implementation.",
    )

    assert second.idempotent_replay
    assert second.event == first.event
    assert _task_source(repo) == before


def test_release_task_rejects_active_lease_owned_by_another_actor(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 1
        lease_owner: agent-a
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationLeaseConflict) as error:
        _service(repo).release_task("TASK-1", actor="agent-b", expected_version=1)

    assert error.value.details["lease_owner"] == "agent-a"
    assert error.value.details["actual_version"] == 1
    assert _task_source(repo) == before


def test_transition_task_rejects_active_lease_owned_by_another_actor(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 1
        lease_owner: agent-a
        lease_expires_at: '2026-06-26T18:10:00Z'
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationLeaseConflict) as error:
        _service(repo).transition_task("TASK-1", "review", actor="agent-b", expected_version=1)

    assert error.value.details["lease_owner"] == "agent-a"
    assert error.value.details["actual_version"] == 1
    assert _task_source(repo) == before


def test_claim_task_rejects_terminal_state(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: complete
        version: 1
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationTransitionError) as error:
        _service(repo).claim_task("TASK-1", actor="codex", expected_version=1)

    assert error.value.details["from_status"] == "complete"
    assert error.value.details["to_status"] == "inprogress"
    assert _task_source(repo) == before


def test_release_and_transition_reject_invalid_orchestration_metadata(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: inprogress
        version: 1
        lease_owner: codex
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as release_error:
        _service(repo).release_task("TASK-1", actor="codex", expected_version=1)
    with pytest.raises(OrchestrationValidationError) as transition_error:
        _service(repo).transition_task("TASK-1", "review", actor="codex", expected_version=1)

    assert [issue["code"] for issue in release_error.value.details["issues"]] == ["missing_lease_expires_at"]
    assert [issue["code"] for issue in transition_error.value.details["issues"]] == ["missing_lease_expires_at"]
    assert _task_source(repo) == before


def test_claim_task_rejects_zero_lease_ttl(tmp_path):
    repo = _repo(tmp_path)
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as error:
        _service(repo).claim_task("TASK-1", actor="codex", expected_version=0, lease_ttl_seconds=0)

    assert error.value.details == {"field": "lease_ttl_seconds"}
    assert _task_source(repo) == before


def test_claim_task_rejects_invalid_orchestration_metadata(tmp_path):
    repo = _repo(tmp_path)
    _set_task_orchestration(
        repo,
        """
        status_key: todo
        version: 1
        lease_owner: agent-a
        """,
    )
    before = _task_source(repo)

    with pytest.raises(OrchestrationValidationError) as error:
        _service(repo).claim_task("TASK-1", actor="codex", expected_version=1)

    issue_codes = [issue["code"] for issue in error.value.details["issues"]]
    assert issue_codes == ["missing_lease_expires_at"]
    assert _task_source(repo) == before


def test_resolve_orchestration_actor_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("BACKLOG_ACTOR", "env-agent")

    assert resolve_orchestration_actor() == "env-agent"


def test_resolve_orchestration_actor_prefers_adapter_identity_over_environment(monkeypatch):
    monkeypatch.setenv("BACKLOG_ACTOR", "env-agent")

    actor = resolve_orchestration_actor(context=OrchestrationActorContext(adapter_identity="mcp-agent"))

    assert actor == "mcp-agent"


def test_resolve_orchestration_actor_falls_back_to_user_and_host(monkeypatch):
    monkeypatch.delenv("BACKLOG_ACTOR", raising=False)
    monkeypatch.setattr("backlog_py.orchestration.service.getpass.getuser", lambda: "dev")
    monkeypatch.setattr("backlog_py.orchestration.service.socket.gethostname", lambda: "host")

    assert resolve_orchestration_actor() == "dev@host"


def test_queue_defaults_to_active_tasks_and_excludes_completed_and_archive(tmp_path):
    repo = _repo(tmp_path)
    _write_task(repo / "backlog" / "completed", "TASK-2", "Completed", status="Done")
    _write_task(repo / "backlog" / "archive" / "tasks", "TASK-3", "Archived")

    report = _service(repo).queue()

    assert [item.task_id for item in report.items] == ["TASK-1"]
    assert report.items[0].path == "backlog/tasks/task-1 - Example.md"


def test_queue_include_completed_adds_completed_tasks_as_terminal(tmp_path):
    repo = _repo(tmp_path)
    _write_task(repo / "backlog" / "completed", "TASK-2", "Completed", status="Done")

    report = _service(repo).queue(include_completed=True)

    categories = {item.task_id: item.category for item in report.items}
    assert categories == {"TASK-1": "eligible", "TASK-2": "terminal"}
    assert report.by_category == {"eligible": 1, "terminal": 1}


def test_queue_active_plain_done_status_maps_to_terminal(tmp_path):
    repo = _repo(tmp_path)
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    task.path.write_text(task.raw_source.replace("status: To Do", "status: Done"), encoding="utf-8")

    report = _service(repo).queue()

    assert report.items[0].category == "terminal"


def test_queue_include_completed_returns_stable_global_order(tmp_path):
    repo = _repo(tmp_path)
    _write_task(repo / "backlog" / "completed", "TASK-0", "Earlier completed", status="Done")

    report = _service(repo).queue(include_completed=True)

    assert [item.task_id for item in report.items] == ["TASK-0", "TASK-1"]


def test_queue_orders_dotted_task_ids_naturally(tmp_path):
    repo = _repo(tmp_path)
    _write_task(repo / "backlog" / "tasks", "TASK-1.10", "Later child")
    _write_task(repo / "backlog" / "tasks", "TASK-1.2", "Earlier child")

    report = _service(repo).queue()

    assert [item.task_id for item in report.items] == ["TASK-1", "TASK-1.2", "TASK-1.10"]


def test_queue_reports_malformed_run_history_as_invalid_without_mutating(tmp_path):
    repo = _repo(tmp_path)
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    malformed = (
        task.raw_source
        + "\n<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n"
    )
    task.path.write_text(malformed, encoding="utf-8")

    report = _service(repo).queue()

    assert report.by_category == {"invalid": 1}
    assert report.items[0].category == "invalid"
    assert [issue.code for issue in report.items[0].run_history_issues] == ["run_history_entry_unterminated"]
    assert task.path.read_text(encoding="utf-8") == malformed


def test_split_task_child_mode_creates_children_and_parent_event(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-task-1",
        items=[
            TaskSplitItem(title="Add parser coverage", description="Cover parser cases."),
            TaskSplitItem(title="Update docs", plan="- [ ] Document split workflow"),
        ],
        reason="Split into focused child tasks.",
    )

    repository = MutableRepository.from_path(repo)
    parent = repository.get_task("TASK-1")
    orchestration = parse_orchestration(parent)
    assert orchestration is not None
    assert orchestration.version == 1
    assert orchestration.status_key is None
    assert parent.parsed.frontmatter["status"] == "To Do"
    assert result.version == 1
    assert result.created_task_ids == ["TASK-1.1", "TASK-1.2"]
    assert result.parent_event_id == result.event.event_id
    first_child = repository.get_task("TASK-1.1")
    second_child = repository.get_task("TASK-1.2")
    assert first_child.parsed.frontmatter["parent_task_id"] == "TASK-1"
    assert second_child.parsed.frontmatter["parent_task_id"] == "TASK-1"
    assert first_child.title == "Add parser coverage"
    assert "Cover parser cases." in first_child.raw_source
    history = parse_run_history(parent.raw_source)
    assert history.issues == []
    assert len(history.events) == 1
    assert history.events[0].type == "split_task"
    assert history.events[0].split_mode == "child"
    assert history.events[0].metadata["created_task_ids"] == '["TASK-1.1","TASK-1.2"]'


def test_split_task_continuation_mode_creates_ordered_followups(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).split_task(
        "TASK-1",
        mode="continuation",
        actor="codex",
        expected_version=0,
        idempotency_key="continue-task-1",
        items=[
            TaskSplitItem(title="Investigate edge case"),
            TaskSplitItem(title="Implement follow-up"),
        ],
        inherit_dependencies=False,
        link_sequence=True,
    )

    repository = MutableRepository.from_path(repo)
    first = repository.get_task(result.created_task_ids[0])
    second = repository.get_task(result.created_task_ids[1])
    assert first.parsed.frontmatter.get("parent_task_id") is None
    assert first.parsed.frontmatter["dependencies"] == ["TASK-1"]
    assert first.parsed.frontmatter["ordinal"] == 1
    assert second.parsed.frontmatter["dependencies"] == [first.id]
    assert second.parsed.frontmatter["ordinal"] == 2
    assert parse_run_history(repository.get_task("TASK-1").raw_source).events[0].split_mode == "continuation"


def test_split_task_transition_updates_parent_status_when_requested(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-and-start",
        items=[TaskSplitItem(title="Extract helper")],
        transition_to_status="inprogress",
    )

    orchestration = parse_orchestration(MutableRepository.from_path(repo).get_task("TASK-1"))
    assert orchestration is not None
    assert orchestration.status_key == "inprogress"
    assert orchestration.version == 1
    assert result.event.to_status == "inprogress"


def test_split_task_rejects_circular_inherited_dependency_without_writes(tmp_path):
    repo = _repo(tmp_path)
    task_path = _task_path(repo)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: To Do\n",
            "status: To Do\n"
            "dependencies:\n"
            "  - TASK-1\n",
        ),
        encoding="utf-8",
    )
    before_tasks = [task.id for task in MutableRepository.from_path(repo).list_tasks()]
    before_source = _task_source(repo)

    with pytest.raises(TaskSplitError) as error:
        _service(repo).split_task(
            "TASK-1",
            mode="child",
            actor="codex",
            expected_version=0,
            idempotency_key="bad-split",
            items=[TaskSplitItem(title="Would loop")],
        )

    assert error.value.details["task_id"] == "TASK-1"
    assert [task.id for task in MutableRepository.from_path(repo).list_tasks()] == before_tasks
    assert _task_source(repo) == before_source


def test_split_task_idempotency_replay_returns_existing_children_without_duplicates(tmp_path):
    repo = _repo(tmp_path)
    service = _service(repo)
    first = service.split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-replay",
        items=[TaskSplitItem(title="Child one"), TaskSplitItem(title="Child two")],
    )
    before_tasks = [task.id for task in MutableRepository.from_path(repo).list_tasks()]
    before_source = _task_source(repo)

    second = service.split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=999,
        idempotency_key="split-replay",
        items=[TaskSplitItem(title="Child one"), TaskSplitItem(title="Child two")],
    )

    assert second.idempotent_replay
    assert second.created_task_ids == first.created_task_ids
    assert second.parent_event_id == first.parent_event_id
    assert second.event == first.event
    assert [task.id for task in MutableRepository.from_path(repo).list_tasks()] == before_tasks
    assert _task_source(repo) == before_source


def test_split_task_rolls_back_children_when_later_child_create_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    original_create_task = MutableRepository.create_task
    call_count = 0

    def flaky_create_task(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise TaskMutationError("simulated child create failure")
        return original_create_task(self, *args, **kwargs)

    monkeypatch.setattr(MutableRepository, "create_task", flaky_create_task)

    with pytest.raises(TaskSplitError):
        _service(repo).split_task(
            "TASK-1",
            mode="child",
            actor="codex",
            expected_version=0,
            idempotency_key="split-rollback",
            items=[TaskSplitItem(title="Child one"), TaskSplitItem(title="Child two")],
        )

    assert [task.id for task in MutableRepository.from_path(repo).list_tasks()] == ["TASK-1"]
    assert parse_run_history(_task_source(repo)).events == []


def test_split_task_rolls_back_children_when_parent_update_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    def failing_replace_task_source(self, *args, **kwargs):
        raise TaskMutationError("simulated parent update failure")

    monkeypatch.setattr(MutableRepository, "replace_task_source", failing_replace_task_source)

    with pytest.raises(TaskSplitError):
        _service(repo).split_task(
            "TASK-1",
            mode="child",
            actor="codex",
            expected_version=0,
            idempotency_key="split-parent-rollback",
            items=[TaskSplitItem(title="Child one"), TaskSplitItem(title="Child two")],
        )

    assert [task.id for task in MutableRepository.from_path(repo).list_tasks()] == ["TASK-1"]
    assert parse_run_history(_task_source(repo)).events == []


def test_split_task_accepts_large_item_payload_by_hashing_idempotency_metadata(tmp_path):
    repo = _repo(tmp_path)

    result = _service(repo).split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-large-item",
        items=[TaskSplitItem(title="Large child", description="x" * 1500, plan="- [ ] " + "y" * 1500)],
    )

    assert result.created_task_ids == ["TASK-1.1"]
    assert "split_items_hash" in result.event.metadata
    assert len(result.event.metadata["split_items_hash"]) == 64
    assert "split_items" not in result.event.metadata


@pytest.mark.parametrize(
    "kwargs",
    [
        {"items": [TaskSplitItem(title="Different child")]},
        {"mode": "continuation"},
        {"inherit_dependencies": False},
        {"link_sequence": False},
    ],
)
def test_split_task_idempotency_conflict_when_payload_changes(tmp_path, kwargs):
    repo = _repo(tmp_path)
    service = _service(repo)
    service.split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-conflict",
        items=[TaskSplitItem(title="Child one")],
        inherit_dependencies=True,
        link_sequence=True,
    )

    request = {
        "mode": "child",
        "actor": "codex",
        "expected_version": 999,
        "idempotency_key": "split-conflict",
        "items": [TaskSplitItem(title="Child one")],
        "inherit_dependencies": True,
        "link_sequence": True,
    }
    request.update(kwargs)
    with pytest.raises(OrchestrationIdempotencyConflict):
        service.split_task("TASK-1", **request)


def test_split_task_idempotency_conflict_when_parent_task_changes(tmp_path):
    repo = _repo(tmp_path)
    _write_task(repo / "backlog" / "tasks", "TASK-2", "Second task")
    service = _service(repo)
    service.split_task(
        "TASK-1",
        mode="child",
        actor="codex",
        expected_version=0,
        idempotency_key="split-parent-conflict",
        items=[TaskSplitItem(title="Child one")],
    )

    with pytest.raises(OrchestrationIdempotencyConflict):
        service.split_task(
            "TASK-2",
            mode="child",
            actor="codex",
            expected_version=0,
            idempotency_key="split-parent-conflict",
            items=[TaskSplitItem(title="Child one")],
        )
