from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backlog_py.core.repository import MutableRepository
from backlog_py.orchestration import (
    OrchestrationActorContext,
    OrchestrationIdempotencyConflict,
    OrchestrationService,
    OrchestrationStateUpdate,
    OrchestrationValidationError,
    OrchestrationVersionConflict,
    RunHistoryParseError,
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
