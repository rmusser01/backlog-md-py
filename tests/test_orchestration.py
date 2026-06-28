from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.core.repository import TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.orchestration import (
    OrchestrationPolicy,
    OrchestrationValidationError,
    ValidationIssue,
    WorkflowStatePolicy,
    categorize_task,
    list_active_claims,
    list_eligible_tasks,
    list_stale_leases,
    parse_orchestration,
    summarize_orchestration,
    validate_orchestration,
    validate_policy,
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


def _queue_item(
    frontmatter: dict[str, object],
    *,
    complete_task_ids: set[str] | None = None,
    now: datetime | None = None,
    run_history_issues: tuple[ValidationIssue, ...] = (),
):
    return categorize_task(
        _task_with_frontmatter(frontmatter),
        policy=OrchestrationPolicy.default(),
        complete_task_ids=complete_task_ids or set(),
        now=now or _utc("2026-05-13T00:00:00Z"),
        run_history_issues=run_history_issues,
    )


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


def test_orchestration_validation_error_preserves_details():
    error = OrchestrationValidationError("invalid", details={"code": "example"})

    assert str(error) == "invalid"
    assert error.details == {"code": "example"}


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


def test_parse_orchestration_does_not_treat_bools_as_integer_fields():
    task = _task_with_frontmatter(
        {
            "id": "TASK-1",
            "title": "Bool values",
            "status": "To Do",
            "orchestration": {
                "version": True,
                "review": {"attempts": False, "max_attempts": True},
            },
        }
    )

    state = parse_orchestration(task)

    assert state is not None
    assert state.version is None
    assert state.review is not None
    assert state.review.attempts is None
    assert state.review.max_attempts is None


def test_validate_orchestration_requires_lease_expiry_when_owner_is_set():
    task = _task_with_frontmatter(
        {
            "id": "TASK-1",
            "title": "Missing expiry",
            "status": "To Do",
            "orchestration": {"status_key": "todo", "lease_owner": "agent-a"},
        }
    )

    issues = validate_orchestration(task, OrchestrationPolicy.default())

    assert {issue.code for issue in issues} >= {"missing_lease_expires_at"}


def test_eligible_tasks_exclude_claims_with_missing_or_invalid_lease_expiry(tmp_path):
    repo = _repo_with_tasks(
        tmp_path,
        {
            "TASK-1": {
                "status": "To Do",
                "orchestration": {"status_key": "todo", "lease_owner": "agent-a"},
            },
            "TASK-2": {
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent-b",
                    "lease_expires_at": "not-a-date",
                },
            },
        },
    )

    eligible = list_eligible_tasks(ReadOnlyRepository.from_path(repo), now=_utc("2026-05-13T00:00:00Z"))

    assert eligible == []


def test_validate_orchestration_reports_invalid_workspace_and_runner_shapes():
    task = _task_with_frontmatter(
        {
            "id": "TASK-1",
            "title": "Invalid workspace",
            "status": "To Do",
            "orchestration": {
                "workspace": "bad",
                "runner": [],
            },
        }
    )

    issues = validate_orchestration(task, OrchestrationPolicy.default())

    assert {issue.code for issue in issues} >= {"invalid_workspace", "invalid_runner"}


def test_validate_orchestration_reports_invalid_workspace_and_runner_known_fields():
    task = _task_with_frontmatter(
        {
            "id": "TASK-1",
            "title": "Invalid subfields",
            "status": "To Do",
            "orchestration": {
                "workspace": {"path": 42, "branch": False},
                "runner": {"kind": True, "profile": 7},
            },
        }
    )

    issues = validate_orchestration(task, OrchestrationPolicy.default())

    assert {issue.code for issue in issues} >= {
        "invalid_workspace_path",
        "invalid_workspace_branch",
        "invalid_runner_kind",
        "invalid_runner_profile",
    }


def test_validate_policy_reports_unreachable_states():
    policy = OrchestrationPolicy(
        states={
            "todo": WorkflowStatePolicy(claimable=True),
            "inprogress": WorkflowStatePolicy(),
            "complete": WorkflowStatePolicy(terminal=True),
            "orphan": WorkflowStatePolicy(),
        },
        transitions={
            "todo": ("inprogress",),
            "inprogress": ("complete",),
            "complete": (),
            "orphan": (),
        },
    )

    issues = validate_policy(policy)

    assert {issue.code for issue in issues} >= {"policy_unreachable_state"}


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


@pytest.mark.parametrize(
    ("frontmatter", "complete_task_ids", "run_history_issues", "expected_category"),
    [
        (
            {"id": "TASK-1", "title": "Invalid", "status": "To Do"},
            set(),
            (
                ValidationIssue(
                    code="run_history_entry_unterminated",
                    message="RUN_HISTORY entry has no matching end marker",
                    path="SECTION:RUN_HISTORY",
                ),
            ),
            "invalid",
        ),
        (
            {
                "id": "TASK-2",
                "title": "Terminal",
                "status": "To Do",
                "orchestration": {"status_key": "complete", "version": 4},
            },
            set(),
            (),
            "terminal",
        ),
        (
            {
                "id": "TASK-3",
                "title": "Claimed",
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent-a",
                    "lease_expires_at": "2026-05-13T01:00:00Z",
                },
            },
            set(),
            (),
            "claimed",
        ),
        (
            {
                "id": "TASK-4",
                "title": "Stale",
                "status": "To Do",
                "orchestration": {
                    "status_key": "todo",
                    "lease_owner": "agent-a",
                    "lease_expires_at": "2026-05-12T23:00:00Z",
                },
            },
            set(),
            (),
            "stale_claim",
        ),
        (
            {
                "id": "TASK-5",
                "title": "Blocked",
                "status": "To Do",
                "dependencies": ["TASK-99"],
            },
            set(),
            (),
            "blocked_by_dependencies",
        ),
        (
            {"id": "TASK-6", "title": "Eligible", "status": "To Do"},
            set(),
            (),
            "eligible",
        ),
        (
            {
                "id": "TASK-7",
                "title": "Workflow",
                "status": "In Progress",
                "orchestration": {"status_key": "inprogress"},
            },
            set(),
            (),
            "in_workflow",
        ),
    ],
)
def test_categorize_task_assigns_every_queue_category(
    frontmatter,
    complete_task_ids,
    run_history_issues,
    expected_category,
):
    item = _queue_item(
        frontmatter,
        complete_task_ids=complete_task_ids,
        run_history_issues=run_history_issues,
    )

    assert item.category == expected_category
    assert item.task_id == frontmatter["id"]
    assert item.title == frontmatter["title"]


def test_categorize_task_prefers_invalid_over_expired_lease():
    item = _queue_item(
        {
            "id": "TASK-1",
            "title": "Invalid stale claim",
            "status": "To Do",
            "orchestration": {
                "status_key": "missing",
                "lease_owner": "agent-a",
                "lease_expires_at": "2026-05-12T23:00:00Z",
            },
        }
    )

    assert item.category == "invalid"
    assert {issue.code for issue in item.validation_issues} >= {"unknown_status_key"}


def test_categorize_task_prefers_terminal_over_active_lease():
    item = _queue_item(
        {
            "id": "TASK-1",
            "title": "Terminal active claim",
            "status": "To Do",
            "orchestration": {
                "status_key": "complete",
                "lease_owner": "agent-a",
                "lease_expires_at": "2026-05-13T01:00:00Z",
            },
        }
    )

    assert item.category == "terminal"


def test_categorize_task_blocks_claimable_tasks_with_incomplete_dependencies():
    item = _queue_item(
        {
            "id": "TASK-1",
            "title": "Blocked eligible task",
            "status": "To Do",
            "dependencies": ["TASK-2"],
        }
    )

    assert item.category == "blocked_by_dependencies"
    assert item.dependency_ids == ["TASK-2"]
