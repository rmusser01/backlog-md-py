from backlog_py.core.repository import TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.orchestration import parse_orchestration


def _task_with_frontmatter(frontmatter: dict[str, object]) -> TaskRecord:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, str):
            lines.append(f"{key}: {value}")
        else:
            import yaml

            rendered = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=False).strip()
            lines.extend(rendered.splitlines())
    lines.extend(["---", ""])
    source = "\n".join(lines)
    parsed = parse_task_markdown(source)
    return TaskRecord(
        id=str(frontmatter.get("id", "TASK-1")),
        title=str(frontmatter.get("title", "")),
        status=str(frontmatter.get("status", "To Do")),
        path=None,  # type: ignore[arg-type]
        parsed=parsed,
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
