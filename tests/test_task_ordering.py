from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from sys import float_info

import pytest
import yaml
from loguru import logger

from backlog_py.core import repository as repository_module
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, TaskMutationError
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.runtime import locks as locks_module
from backlog_py.runtime.locks import with_project_write_lock
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
    ordinal: int | float | str | None = None,
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


def _rendered_ids(project: BacklogProject, status: str) -> list[str]:
    return [task.id for task in MutableRepository(project).list_tasks(status=status)]


def _frontmatter(project: BacklogProject, task_id: str) -> dict[str, object]:
    return dict(MutableRepository(project).get_task(task_id).parsed.frontmatter)


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


def _write_config(
    project: BacklogProject,
    *,
    statuses: list[str] | None,
    default_status: str = "To Do",
    on_status_change: str | None = None,
) -> None:
    config = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    if statuses is None:
        config.pop("statuses", None)
    else:
        config["statuses"] = statuses
    config["defaultStatus"] = default_status
    if on_status_change is not None:
        config["onStatusChange"] = on_status_change
    project.config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


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


def test_move_task_to_status_appends_after_ordinal_and_ordinal_less_tasks(
    project: BacklogProject,
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1", ordinal=1000)
    _write_task(project, task_id="TASK-2", status="Doing", ordinal=4000)
    _write_task(project, task_id="TASK-3", status="Doing")

    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    assert moved.status == "Doing"
    assert _rendered_ids(project, "Doing") == ["TASK-2", "TASK-3", "TASK-1"]
    assert _frontmatter(project, "TASK-2")["ordinal"] == 4000
    assert _frontmatter(project, "TASK-3")["ordinal"] == 5000
    assert _frontmatter(project, "TASK-1")["ordinal"] == 6000


def test_move_task_to_status_appends_after_decimal_max_and_invalid_ordinal(
    project: BacklogProject,
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1")
    _write_task(project, task_id="TASK-2", status="Doing", ordinal=4500.5)
    _write_task(project, task_id="TASK-3", status="Doing", ordinal="later")

    MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    assert _frontmatter(project, "TASK-2")["ordinal"] == 4500.5
    assert _frontmatter(project, "TASK-3")["ordinal"] == 5500.5
    assert _frontmatter(project, "TASK-1")["ordinal"] == 6500.5


@pytest.mark.parametrize("large_ordinal", [1e20, 10**20, float_info.max])
def test_move_task_to_status_appends_after_large_number_and_invalid_ordinal(
    project: BacklogProject, large_ordinal: int | float
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1")
    _write_task(project, task_id="TASK-2", status="Doing", ordinal=large_ordinal)
    _write_task(project, task_id="TASK-3", status="Doing", ordinal="later")

    MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    target_ordinal = _frontmatter(project, "TASK-2")["ordinal"]
    materialized_ordinal = _frontmatter(project, "TASK-3")["ordinal"]
    moved_ordinal = _frontmatter(project, "TASK-1")["ordinal"]
    assert target_ordinal == large_ordinal
    assert materialized_ordinal > target_ordinal
    assert moved_ordinal > materialized_ordinal
    assert _rendered_ids(project, "Doing") == ["TASK-2", "TASK-3", "TASK-1"]


def test_same_status_move_is_a_byte_identical_noop(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_task(project, task_id="TASK-1", ordinal=1000)
    repository = MutableRepository(project)
    before = _task_sources(project)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("same-status move performed validation, a write, or a callback")

    monkeypatch.setattr(repository_module, "load_config", fail_if_called)
    monkeypatch.setattr(repository_module, "_atomic_write_text", fail_if_called)
    monkeypatch.setattr(repository_module, "execute_status_callback", fail_if_called)

    moved = repository.move_task_to_status("TASK-1", "To Do")

    assert moved.status == "To Do"
    assert _task_sources(project) == before


def test_move_preserves_target_dates_but_updates_moved_task_date(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1", priority="high", updated_date="old-source-date")
    _write_task(
        project,
        task_id="TASK-2",
        status="Doing",
        priority="low",
        updated_date="existing-target-date",
    )
    target_before = _frontmatter(project, "TASK-2")
    moved_before = _frontmatter(project, "TASK-1")
    monkeypatch.setattr(
        repository_module, "_current_task_timestamp", lambda _: "2026-09-01 12:00"
    )

    MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    target_after = _frontmatter(project, "TASK-2")
    moved_after = _frontmatter(project, "TASK-1")
    assert target_after["updated_date"] == "existing-target-date"
    assert _without(target_after, "ordinal") == _without(target_before, "ordinal")
    assert moved_after["updated_date"] == "2026-09-01 12:00"
    assert {
        key: value
        for key, value in moved_after.items()
        if key not in {"status", "ordinal", "updated_date"}
    } == {
        key: value
        for key, value in moved_before.items()
        if key not in {"status", "ordinal", "updated_date"}
    }


def test_move_runs_best_effort_status_hook_and_keeps_change_on_failure(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        project,
        statuses=["To Do", "Doing"],
        on_status_change="echo status changed",
    )
    _write_task(project, task_id="TASK-1")
    received: dict[str, object] = {}

    def fail_callback(**kwargs: object) -> None:
        received.update(kwargs)
        raise RuntimeError("callback failure")

    monkeypatch.setattr(repository_module, "execute_status_callback", fail_callback)

    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    assert moved.status == "Doing"
    assert received == {
        "command": "echo status changed",
        "task_id": "TASK-1",
        "old_status": "To Do",
        "new_status": "Doing",
        "task_title": "TASK-1",
        "cwd": project.root,
    }


@pytest.mark.parametrize("configured_statuses", [None, []])
def test_move_accepts_task_derived_target_when_statuses_absent_or_empty(
    project: BacklogProject, configured_statuses: list[str] | None
) -> None:
    _write_config(project, statuses=configured_statuses)
    _write_task(project, task_id="TASK-1")
    _write_task(project, task_id="TASK-2", status="Doing")

    moved = MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    assert moved.status == "Doing"


def test_move_accepts_default_only_empty_column(project: BacklogProject) -> None:
    _write_config(project, statuses=[], default_status="Ready")
    _write_task(project, task_id="TASK-1")

    moved = MutableRepository(project).move_task_to_status("TASK-1", "Ready")

    assert moved.status == "Ready"


def test_move_rolls_back_materialized_target_and_source_after_second_write_failure(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1")
    _write_task(project, task_id="TASK-2", status="Doing")
    before = _task_sources(project)
    _fail_the_second_forward_write_once(monkeypatch)

    with pytest.raises(OSError, match="simulated write failure"):
        MutableRepository(project).move_task_to_status("TASK-1", "Doing")

    assert _task_sources(project) == before


@pytest.mark.parametrize("status", ["", "   ", "Invented"])
def test_move_rejects_blank_or_invented_target_without_writes(
    project: BacklogProject, status: str
) -> None:
    _write_task(project, task_id="TASK-1")
    before = _task_sources(project)

    with pytest.raises(TaskMutationError, match="Unknown status"):
        MutableRepository(project).move_task_to_status("TASK-1", status)

    assert _task_sources(project) == before


def test_move_accepts_local_legacy_status_omitted_from_non_empty_config(
    project: BacklogProject,
) -> None:
    _write_config(project, statuses=["To Do", "Doing"])
    _write_task(project, task_id="TASK-1")
    _write_task(project, task_id="TASK-2", status="Legacy")

    moved = MutableRepository(project).move_task_to_status("TASK-1", "Legacy")

    assert moved.status == "Legacy"


def test_locked_status_move_callback_failure_still_reaches_auto_commit(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        project,
        statuses=["To Do", "Doing"],
        on_status_change="echo status changed",
    )
    _write_task(project, task_id="TASK-1")
    auto_commits: list[tuple[object, ...]] = []

    def raise_callback(**kwargs: object) -> None:
        raise RuntimeError("callback failure")

    def record_auto_commit(*args: object) -> None:
        auto_commits.append(args)

    monkeypatch.setattr(repository_module, "execute_status_callback", raise_callback)
    monkeypatch.setattr(locks_module, "maybe_auto_commit", record_auto_commit)

    moved = with_project_write_lock(
        project,
        "browser_task_status",
        lambda: MutableRepository(project).move_task_to_status("TASK-1", "Doing"),
    )

    assert moved.status == "Doing"
    assert len(auto_commits) == 1
    assert auto_commits[0][1] == "browser_task_status"


def test_move_status_assignment_options_preserve_exact_order_and_use_only_local_tasks(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(project, statuses=["Doing", "To Do", "Doing"], default_status="Ready")
    config = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    config["checkActiveBranches"] = True
    project.config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    _write_task(project, task_id="TASK-1", status="Legacy A", ordinal=3000)
    _write_task(project, task_id="TASK-2", status="Legacy B", ordinal=1000)
    _write_task(project, task_id="TASK-3", status="doing", ordinal=2000)
    _write_task(project, task_id="TASK-4", status="Legacy B")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("MutableRepository loaded active-branch snapshots")

    monkeypatch.setattr(repository_module, "list_active_branch_task_snapshots", fail_if_called)

    assert MutableRepository(project).status_assignment_options() == (
        "Doing",
        "To Do",
        "Ready",
        "Legacy B",
        "doing",
        "Legacy A",
    )
