"""Regression tests for inherited data-loss / correctness bugs.

Each test reproduces a concrete corruption or silent-failure scenario that was
verified against the shipped 1.0.0 code before the fix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backlog_py.core.init import init_project
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.storage.config import load_config


def _project(tmp_path: Path, **config_overrides: str) -> BacklogProject:
    project = init_project(tmp_path, no_git=True).project
    if config_overrides:
        text = project.config_path.read_text()
        for key, value in config_overrides.items():
            text += f"\n{key}: {value}\n"
        project.config_path.write_text(text)
        project = BacklogProject(
            root=project.root,
            backlog_dir=project.backlog_dir,
            config_path=project.config_path,
            config=load_config(project.config_path),
        )
    return project


# --- #1: task IDs reused after completion -----------------------------------

def test_next_task_id_does_not_reuse_completed_ids(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    first = repo.create_task(title="First")
    repo.edit_task(first.id, status="Done")
    repo.complete_task(first.id)

    second = repo.create_task(title="Second")

    assert second.id != first.id, "new task reused a completed task's id"


def test_task_id_collision_guard_covers_completed(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    first = repo.create_task(title="First")
    repo.edit_task(first.id, status="Done")
    repo.complete_task(first.id)

    from backlog_py.core.repository import TaskMutationError

    with pytest.raises(TaskMutationError, match="already exists"):
        repo.create_task(title="Clash", task_id=first.id)


# --- #2: completed tasks unaddressable --------------------------------------

def test_get_task_finds_completed_task(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    first = repo.create_task(title="First")
    repo.edit_task(first.id, status="Done")
    repo.complete_task(first.id)

    found = repo.get_task(first.id)
    assert found.id == first.id


# --- #5: reserved-marker injection silently corrupts task files -------------

def test_create_task_rejects_section_end_marker_in_description(tmp_path):
    from backlog_py.core.repository import TaskMutationError

    project = _project(tmp_path)
    repo = MutableRepository(project)
    with pytest.raises(TaskMutationError, match="reserved"):
        repo.create_task(
            title="Injected",
            description="part one\n<!-- SECTION:DESCRIPTION:END -->\npart two",
        )


def test_create_task_rejects_checklist_marker_in_ac(tmp_path):
    from backlog_py.core.repository import TaskMutationError

    project = _project(tmp_path)
    repo = MutableRepository(project)
    with pytest.raises(TaskMutationError, match="reserved"):
        repo.create_task(title="Injected", acceptance_criteria=["ok", "<!-- AC:END -->"])


def test_edit_task_rejects_marker_in_notes(tmp_path):
    from backlog_py.core.repository import TaskMutationError

    project = _project(tmp_path)
    repo = MutableRepository(project)
    task = repo.create_task(title="Clean")
    with pytest.raises(TaskMutationError, match="reserved"):
        repo.edit_task(task.id, notes="oops <!-- SECTION:IMPLEMENTATION_NOTES:BEGIN -->")

    # the on-disk file must be unchanged and still parseable
    assert repo.get_task(task.id).title == "Clean"


# --- #4: one malformed task file bricks the repository ----------------------

def test_malformed_task_file_does_not_brick_repository(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    good = repo.create_task(title="Good task")

    bad = project.backlog_dir / "tasks" / "task-999 - broken.md"
    bad.write_text(
        "---\nid: task-999\ntitle: Broken\nstatus: To Do\n---\n\n"
        "## Acceptance Criteria\n<!-- AC:BEGIN -->\n- [ ] never closed\n",
        encoding="utf-8",
    )

    ro = ReadOnlyRepository(project)
    ids = [task.id for task in ro.list_tasks()]
    assert good.id in ids, "malformed sibling file made the good task disappear"
    assert ro.get_task(good.id).id == good.id


# --- #3: zero-padded ids unaddressable --------------------------------------

def test_zero_padded_draft_is_addressable(tmp_path):
    from backlog_py.core.drafts import DraftService

    project = _project(tmp_path, zeroPaddedIds="3")
    drafts = DraftService(project)
    draft = drafts.create_draft(title="Draft one")
    assert draft.id == "draft-001"

    for lookup in ("draft-001", "draft-1"):
        assert drafts.view_draft(lookup).id == draft.id, f"lookup {lookup!r} failed"


def test_zero_padded_decision_is_addressable_by_number(tmp_path):
    from backlog_py.core.decisions import DecisionService

    project = _project(tmp_path, zeroPaddedIds="3")
    decisions = DecisionService(project)
    created = decisions.create_decision("Adopt X")
    assert created.id == "decision-001"

    for lookup in ("decision-001", "decision-1", "1"):
        assert decisions.view_decision(lookup).id == created.id, f"lookup {lookup!r} failed"


def test_zero_padded_task_is_addressable_unpadded(tmp_path):
    project = _project(tmp_path, zeroPaddedIds="3")
    repo = MutableRepository(project)
    task = repo.create_task(title="Padded")
    assert task.id == "TASK-001"

    for lookup in ("TASK-001", "TASK-1", "1"):
        assert repo.get_task(lookup).id == task.id, f"lookup {lookup!r} failed"


def test_editing_completed_task_keeps_it_in_completed(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    first = repo.create_task(title="First")
    repo.edit_task(first.id, status="Done")
    completed = repo.complete_task(first.id)
    assert completed.path.parent.name == "completed"

    edited = repo.edit_task(first.id, title="First renamed")

    assert edited.path.parent.name == "completed", "title edit moved a completed task back to tasks/"
    active = [p.name for p in (project.backlog_dir / "tasks").glob("*.md")]
    assert active == [], f"completed task leaked into active tasks: {active}"
