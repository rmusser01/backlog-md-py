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
