"""Regression tests for inherited orchestration bugs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backlog_py.orchestration import (
    OrchestrationService,
    OrchestrationStateUpdate,
    OrchestrationTransitionError,
    parse_orchestration,
)
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
