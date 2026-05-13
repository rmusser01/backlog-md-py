from datetime import datetime, timezone
from pathlib import Path

import yaml

from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.core.repository import TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.orchestration import (
    OrchestrationPolicy,
    list_active_claims,
    list_eligible_tasks,
    list_stale_leases,
    parse_orchestration,
    summarize_orchestration,
    validate_orchestration,
)


def _task_with_frontmatter(frontmatter: dict[str, object]) -> TaskRecord:
    source = _render_task_source(frontmatter)
    parsed = parse_task_markdown(source)
    return TaskRecord(
        id=str(frontmatter.get("id", "TASK-1")),
        title=str(frontmatter.get("title", "")),
        status=str(frontmatter.get("status", "To Do")),
        path=Path("task.md"),
        parsed=parsed,
    )


def _render_task_source(frontmatter: dict[str, object]) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{yaml_text}\n---\n"


def _repo_with_tasks(tmp_path: Path, tasks: dict[str, dict[str, object]]) -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: orchestration-test\n", encoding="utf-8")
    for task_id, metadata in tasks.items():
        frontmatter = {
            "id": task_id,
            "title": str(metadata.get("title", f"Task {task_id}")),
            "status": str(metadata.get("status", "To Do")),
        }
        for key, value in metadata.items():
            if key not in {"title", "status"}:
                frontmatter[key] = value
        filename = f"{task_id.lower()} - Task-{task_id}.md"
        (task_dir / filename).write_text(_render_task_source(frontmatter), encoding="utf-8")
    return repo


def _utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def test_parse_orchestration_returns_none_when_metadata_missing():
    task = _task_with_frontmatter({"id": "TASK-1", "title": "Plain", "status": "To Do"})

    assert parse_orchestration(task) is None


def test_parse_orchestration_preserves_known_and_unknown_fields():
    task = _task_with_frontmatter(
        {
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
                "review": {
                    "state": "awaiting_approval",
                    "reviewer": "human",
                    "attempts": 1,
                    "max_attempts": 3,
                },
                "custom": {"preserve": True},
            },
        }
    )

    state = parse_orchestration(task)

    assert state is not None
    assert state.status_key == "todo"
    assert state.version == 3
    assert state.lease_owner == "codex-agent-1"
    assert state.lease_expires_at == "2026-05-13T07:00:00Z"
    assert state.correlation_id == "run-1"
    assert state.idempotency_key == "claim-1"
    assert state.workspace is not None
    assert state.workspace.path == ".worktrees/task-1"
    assert state.workspace.branch == "codex/task-1"
    assert state.runner is not None
    assert state.runner.kind == "codex"
    assert state.runner.profile == "default"
    assert state.review is not None
    assert state.review.state == "awaiting_approval"
    assert state.review.reviewer == "human"
    assert state.review.attempts == 1
    assert state.review.max_attempts == 3
    assert state.extra == {"custom": {"preserve": True}}


def test_default_policy_accepts_expected_transitions():
    policy = OrchestrationPolicy.default()

    assert policy.can_transition("todo", "inprogress")
    assert policy.can_transition("inprogress", "review")
    assert policy.can_transition("review", "complete")
    assert not policy.can_transition("complete", "todo")


def test_validate_orchestration_reports_invalid_known_fields():
    task = _task_with_frontmatter(
        {
            "id": "TASK-1",
            "title": "Invalid",
            "status": "To Do",
            "orchestration": {
                "status_key": "missing",
                "version": -1,
                "lease_expires_at": "not-a-date",
                "review": {"attempts": 4, "max_attempts": 3},
            },
        }
    )

    issues = validate_orchestration(task, OrchestrationPolicy.default())

    assert {issue.code for issue in issues} >= {
        "unknown_status_key",
        "invalid_version",
        "invalid_lease_expires_at",
        "review_attempts_exceed_max",
    }


def test_list_eligible_tasks_blocks_active_leases_and_incomplete_dependencies(tmp_path):
    repo = _repo_with_tasks(
        tmp_path,
        {
            "TASK-1": {"status": "To Do"},
            "TASK-2": {"status": "To Do", "dependencies": ["TASK-1"]},
            "TASK-3": {
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent",
                    "lease_expires_at": "2099-01-01T00:00:00Z",
                },
            },
        },
    )

    eligible = list_eligible_tasks(ReadOnlyRepository.from_path(repo), now=_utc("2026-05-13T00:00:00Z"))

    assert [task.id for task in eligible] == ["TASK-1"]


def test_lease_reports_split_active_and_stale_claims(tmp_path):
    repo = _repo_with_tasks(
        tmp_path,
        {
            "TASK-1": {
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent-a",
                    "lease_expires_at": "2026-05-13T01:00:00Z",
                },
            },
            "TASK-2": {
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent-b",
                    "lease_expires_at": "2026-05-12T23:00:00Z",
                },
            },
        },
    )
    repository = ReadOnlyRepository.from_path(repo)
    now = _utc("2026-05-13T00:00:00Z")

    assert [task.id for task in list_active_claims(repository, now=now)] == ["TASK-1"]
    assert [task.id for task in list_stale_leases(repository, now=now)] == ["TASK-2"]


def test_summarize_orchestration_counts_effective_statuses(tmp_path):
    repo = _repo_with_tasks(
        tmp_path,
        {
            "TASK-1": {"status": "To Do"},
            "TASK-2": {"status": "In Progress"},
            "TASK-3": {"status": "To Do", "orchestration": {"status_key": "review"}},
        },
    )

    summary = summarize_orchestration(ReadOnlyRepository.from_path(repo), now=_utc("2026-05-13T00:00:00Z"))

    assert summary.by_status == {"inprogress": 1, "review": 1, "todo": 1}
    assert summary.eligible_count == 1
    assert summary.active_claim_count == 0
    assert summary.stale_lease_count == 0
