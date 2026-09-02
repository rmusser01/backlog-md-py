from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from loguru import logger

from backlog_py.core import repository as repository_module
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, TaskMutationError
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.storage.project import discover_project


@pytest.fixture
def project(tmp_path: Path) -> BacklogProject:
    backlog_dir = tmp_path / "backlog"
    (backlog_dir / "tasks").mkdir(parents=True)
    (backlog_dir / "config.yml").write_text(
        "projectName: ordering\nstatuses:\n  - To Do\ndefaultStatus: To Do\n"
        "remoteOperations: false\ncheckActiveBranches: false\n",
        encoding="utf-8",
    )
    return discover_project(Path.cwd(), explicit_cwd=tmp_path)


def _write_task(
    project: BacklogProject,
    *,
    task_id: str,
    status: str = "To Do",
    priority: str | None = None,
    created_date: object | None = None,
    updated_date: object | None = None,
    ordinal: int | None = None,
) -> None:
    frontmatter: dict[str, object] = {
        "id": task_id,
        "title": task_id,
        "status": status,
    }
    if priority is not None:
        frontmatter["priority"] = priority
    if created_date is not None:
        frontmatter["created_date"] = created_date
    if updated_date is not None:
        frontmatter["updated_date"] = updated_date
    if ordinal is not None:
        frontmatter["ordinal"] = ordinal
    source = f"---\n{yaml.safe_dump(frontmatter, sort_keys=False).strip()}\n---\n"
    (project.backlog_dir / "tasks" / f"{task_id.lower()}.md").write_text(source, encoding="utf-8")


def _ordinals(project: BacklogProject, status: str) -> dict[str, object]:
    return {
        task.id: task.parsed.frontmatter.get("ordinal")
        for task in MutableRepository(project).list_tasks(status=status)
    }


def _task_sources(project: BacklogProject) -> dict[str, str]:
    task_dir = project.backlog_dir / "tasks"
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(task_dir.glob("*.md"))}


def _frontmatters(project: BacklogProject) -> dict[str, dict[str, object]]:
    return {
        task.id: dict(task.parsed.frontmatter)
        for task in MutableRepository(project).list_tasks(status="To Do")
    }


def _ordered_ids(project: BacklogProject) -> list[str]:
    return [task.id for task in MutableRepository(project).list_tasks(status="To Do")]


def _without(values: dict[str, object], key: str) -> dict[str, object]:
    return {name: value for name, value in values.items() if name != key}


def _fail_the_second_forward_write_once(monkeypatch: pytest.MonkeyPatch) -> None:
    atomic_write = repository_module._atomic_write_text
    calls = 0

    def fail_once(path: Path, content: str, base: Path | None = None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure")
        atomic_write(path, content, base=base)

    monkeypatch.setattr(repository_module, "_atomic_write_text", fail_once)


def _set_priorities(project: BacklogProject, priorities: list[str]) -> None:
    config = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    config["priorities"] = priorities
    project.config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_sort_tasks_by_default_priority_then_natural_id(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", priority="low", ordinal=4)
    _write_task(project, task_id="TASK-2", priority="HIGH", ordinal=3)
    _write_task(project, task_id="TASK-10", priority="medium", ordinal=2)
    _write_task(project, task_id="TASK-3", ordinal=1)

    result = MutableRepository(project).sort_tasks("To Do", sort="priority")

    assert result.task_ids == ("TASK-2", "TASK-10", "TASK-1", "TASK-3")
    assert result.changed_task_ids == ("TASK-2", "TASK-10", "TASK-1", "TASK-3")
    assert _ordinals(project, "To Do") == {
        "TASK-2": 1000,
        "TASK-10": 2000,
        "TASK-1": 3000,
        "TASK-3": 4000,
    }


def test_sort_tasks_uses_configured_priority_order_case_insensitively(project: BacklogProject) -> None:
    _set_priorities(project, ["urgent", "normal"])
    _write_task(project, task_id="TASK-1", priority="low")
    _write_task(project, task_id="TASK-2", priority="normal")
    _write_task(project, task_id="TASK-3", priority="URGENT")
    _write_task(project, task_id="TASK-4")

    result = MutableRepository(project).sort_tasks("To Do", sort="priority")

    assert result.task_ids == ("TASK-3", "TASK-2", "TASK-1", "TASK-4")


def test_sort_tasks_reports_only_ordinals_that_changed(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", priority="high", ordinal=1000)
    _write_task(project, task_id="TASK-2", priority="low", ordinal=7)

    result = MutableRepository(project).sort_tasks("To Do", sort="priority")

    assert result.task_ids == ("TASK-1", "TASK-2")
    assert result.changed_task_ids == ("TASK-2",)


def test_sort_rewrites_only_ordinals_and_preserves_updated_dates(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", priority="low", updated_date="2026-08-29")
    _write_task(project, task_id="TASK-2", priority="high", updated_date="2026-08-30")
    _write_task(project, task_id="TASK-3", priority="medium", updated_date="2026-08-31")
    before = _frontmatters(project)

    MutableRepository(project).sort_tasks("To Do", sort="priority")

    after = _frontmatters(project)
    assert [after[task_id]["ordinal"] for task_id in _ordered_ids(project)] == [1000, 2000, 3000]
    for task_id in before:
        assert after[task_id].get("updated_date") == before[task_id].get("updated_date")
        assert _without(after[task_id], "ordinal") == _without(before[task_id], "ordinal")


def test_sort_noop_performs_no_file_writes(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_task(project, task_id="TASK-1", priority="low")
    _write_task(project, task_id="TASK-2", priority="high")
    repository = MutableRepository(project)
    repository.sort_tasks("To Do", sort="priority")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("no-op sort attempted a write or cache invalidation")

    monkeypatch.setattr(repository_module, "_atomic_write_text", fail_if_called)
    monkeypatch.setattr(repository, "_invalidate_task_cache", fail_if_called)

    result = repository.sort_tasks("To Do", sort="priority")

    assert result.changed_task_ids == ()


def test_sort_semantic_noop_preserves_hand_formatted_source(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_path = project.backlog_dir / "tasks" / "task-1.md"
    source = "---\nid: TASK-1\ntitle: TASK-1\nstatus: To Do\npriority: high\nordinal: 1000 # keep\n---\n"
    task_path.write_text(source, encoding="utf-8")
    repository = MutableRepository(project)
    atomic_write = repository_module._atomic_write_text
    invalidate = repository._invalidate_task_cache
    writes: list[Path] = []
    invalidations = 0

    def record_write(path: Path, content: str, base: Path | None = None) -> None:
        writes.append(path)
        atomic_write(path, content, base=base)

    def record_invalidation() -> None:
        nonlocal invalidations
        invalidations += 1
        invalidate()

    monkeypatch.setattr(repository_module, "_atomic_write_text", record_write)
    monkeypatch.setattr(repository, "_invalidate_task_cache", record_invalidation)

    result = repository.sort_tasks("To Do", sort="priority")

    assert result.changed_task_ids == ()
    assert writes == []
    assert invalidations == 0
    assert task_path.read_text(encoding="utf-8") == source


def test_sort_rolls_back_completed_writes_after_runtime_failure(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_task(project, task_id="TASK-1", priority="low")
    _write_task(project, task_id="TASK-2", priority="high")
    _write_task(project, task_id="TASK-3", priority="medium")
    before = _task_sources(project)
    _fail_the_second_forward_write_once(monkeypatch)

    with pytest.raises(OSError, match="simulated write failure"):
        MutableRepository(project).sort_tasks("To Do", sort="priority")

    assert _task_sources(project) == before


def test_sort_continues_rollback_failures_without_hiding_forward_error(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_task(project, task_id="TASK-1", priority="high")
    _write_task(project, task_id="TASK-2", priority="medium")
    _write_task(project, task_id="TASK-3", priority="low")
    before = _task_sources(project)
    atomic_write = repository_module._atomic_write_text
    attempts: list[str] = []
    calls = 0

    def fail_forward_and_rollback(path: Path, content: str, base: Path | None = None) -> None:
        nonlocal calls
        calls += 1
        attempts.append(path.name)
        if calls == 3:
            raise OSError("forward failure")
        if calls == 4:
            raise OSError("rollback failure")
        atomic_write(path, content, base=base)

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    monkeypatch.setattr(repository_module, "_atomic_write_text", fail_forward_and_rollback)
    try:
        with pytest.raises(OSError, match="forward failure"):
            MutableRepository(project).sort_tasks("To Do", sort="priority")
    finally:
        logger.remove(sink_id)

    after = _task_sources(project)
    assert attempts == ["task-1.md", "task-2.md", "task-3.md", "task-2.md", "task-1.md"]
    assert after["task-1.md"] == before["task-1.md"]
    assert after["task-2.md"] != before["task-2.md"]
    assert any(
        "TASK-2" in message and "task-2.md" in message and "rollback failure" in message
        for message in messages
    )


def test_sort_invalidates_populated_caches_after_success(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", priority="low")
    _write_task(project, task_id="TASK-2", priority="high")
    _write_task(project, task_id="TASK-3", priority="medium")
    repository = MutableRepository(project)
    assert [task.id for task in repository.list_tasks(status="To Do")] == ["TASK-1", "TASK-2", "TASK-3"]

    repository.sort_tasks("To Do", sort="priority")

    assert [task.id for task in repository.list_tasks(status="To Do")] == ["TASK-2", "TASK-3", "TASK-1"]


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("asc", ["TASK-1", "TASK-2", "TASK-3", "TASK-4"]),
        ("desc", ["TASK-3", "TASK-2", "TASK-1", "TASK-4"]),
    ],
)
def test_sort_tasks_by_created_date_normalizes_supported_utc_forms(
    project: BacklogProject, direction: str, expected: list[str]
) -> None:
    _write_task(project, task_id="TASK-1", created_date=date(2026, 9, 1))
    _write_task(project, task_id="TASK-2", created_date="2026-09-01 05:00:00-07:00")
    _write_task(project, task_id="TASK-3", created_date="2026-09-01T13:00:00.5Z")
    _write_task(project, task_id="TASK-4")

    result = MutableRepository(project).sort_tasks("To Do", sort="created", direction=direction)

    assert list(result.task_ids) == expected


def test_sort_tasks_by_created_date_accepts_yaml_datetime_and_keeps_invalid_dates_last(
    project: BacklogProject,
) -> None:
    _write_task(project, task_id="TASK-1", created_date=date(2026, 9, 1))
    _write_task(project, task_id="TASK-2", created_date=datetime(2026, 9, 1, 1))
    _write_task(project, task_id="TASK-3", created_date="2026-09-01 02:00")
    _write_task(project, task_id="TASK-4", created_date="2026-09-01T03:00:00.25+00:00")
    _write_task(project, task_id="TASK-5", created_date="invalid")
    _write_task(project, task_id="TASK-6")

    ascending = MutableRepository(project).sort_tasks("To Do", sort="created", direction="asc")
    descending = MutableRepository(project).sort_tasks("To Do", sort="created", direction="desc")

    assert ascending.task_ids == ("TASK-1", "TASK-2", "TASK-3", "TASK-4", "TASK-5", "TASK-6")
    assert descending.task_ids == ("TASK-4", "TASK-3", "TASK-2", "TASK-1", "TASK-5", "TASK-6")


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sort_tasks_by_created_date_uses_natural_id_for_equivalent_instants(
    project: BacklogProject, direction: str
) -> None:
    _write_task(project, task_id="TASK-1", created_date="2026-09-01T12:00:00Z")
    _write_task(project, task_id="TASK-2", created_date="2026-09-01T05:00:00-07:00")
    _write_task(project, task_id="TASK-3", created_date="invalid")
    _write_task(project, task_id="TASK-4")

    result = MutableRepository(project).sort_tasks("To Do", sort="created", direction=direction)

    assert result.task_ids == ("TASK-1", "TASK-2", "TASK-3", "TASK-4")


def test_sort_tasks_by_created_date_keeps_boundary_offset_overflows_last(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", created_date="2026-09-01T12:00:00Z")
    _write_task(project, task_id="TASK-2", created_date="0001-01-01T00:00:00+14:00")
    _write_task(project, task_id="TASK-3", created_date="9999-12-31T23:59:59-14:00")
    _write_task(project, task_id="TASK-4")

    result = MutableRepository(project).sort_tasks("To Do", sort="created", direction="asc")

    assert result.task_ids == ("TASK-1", "TASK-2", "TASK-3", "TASK-4")


def test_sort_tasks_by_priority_uses_only_local_active_tasks(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-2", priority="low")
    _write_task(project, task_id="TASK-1", priority="high")
    completed_dir = project.backlog_dir / "completed"
    completed_dir.mkdir()
    (completed_dir / "task-0.md").write_text(
        "---\nid: TASK-0\ntitle: completed\nstatus: To Do\npriority: high\nordinal: 9\n---\n",
        encoding="utf-8",
    )

    result = MutableRepository(project).sort_tasks("To Do", sort="priority")

    assert result.task_ids == ("TASK-1", "TASK-2")
    assert parse_task_markdown((completed_dir / "task-0.md").read_text(encoding="utf-8")).frontmatter["ordinal"] == 9


@pytest.mark.parametrize(
    ("sort", "direction"),
    [("title", None), ("created", None), ("created", "sideways"), ("priority", "asc")],
)
def test_sort_tasks_rejects_unsupported_requests_without_writes(
    project: BacklogProject, sort: str, direction: str | None
) -> None:
    _write_task(project, task_id="TASK-1", priority="high", ordinal=7)
    before = _task_sources(project)

    with pytest.raises(TaskMutationError):
        MutableRepository(project).sort_tasks("To Do", sort=sort, direction=direction)

    assert _task_sources(project) == before


def test_sort_tasks_rejects_unknown_empty_status_without_writes(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", priority="high", ordinal=7)
    before = _task_sources(project)

    with pytest.raises(TaskMutationError):
        MutableRepository(project).sort_tasks("Later", sort="priority")

    assert _task_sources(project) == before


def test_sort_tasks_accepts_status_used_by_a_local_task_outside_config(project: BacklogProject) -> None:
    _write_task(project, task_id="TASK-1", status="Waiting", priority="high")

    result = MutableRepository(project).sort_tasks("Waiting", sort="priority")

    assert result.task_ids == ("TASK-1",)
    assert _ordinals(project, "Waiting") == {"TASK-1": 1000}
