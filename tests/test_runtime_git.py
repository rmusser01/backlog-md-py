"""Direct tests for the batched git snapshot helpers.

These cover the path-space and quoting seams: `git status`/`git log` report
paths relative to the *repository* root and C-quote non-ASCII names, while the
caller builds paths relative to the *project* root.
"""
from __future__ import annotations

import os
import subprocess
from math import inf
from pathlib import Path

import pytest

from backlog_py.core.init import init_project
from backlog_py.runtime.git import current_task_snapshot_timestamps


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(repo),
        },
    )


def _task(project, name: str) -> Path:
    path = project.backlog_dir / "tasks" / name
    path.write_text(
        "---\nid: TASK-1\ntitle: T\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n## Description\n\nx\n",
        encoding="utf-8",
    )
    return path


def test_timestamps_resolve_when_project_root_is_below_the_git_root(tmp_path: Path) -> None:
    """git reports repo-relative paths; the project may be a subdirectory."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    project = init_project(repo / "pkg").project
    path = _task(project, "task-1 - nested.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a committed, clean task in a nested project reported no timestamp"


def test_timestamps_resolve_for_non_ascii_filenames(tmp_path: Path) -> None:
    """core.quotePath C-quotes non-ASCII names in git log output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    project = init_project(repo).project
    path = _task(project, "task-1 - café.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a non-ASCII filename reported no timestamp"


def test_dirty_task_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    project = init_project(repo).project
    path = _task(project, "task-1 - dirty.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_untracked_task_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    project = init_project(repo).project
    path = _task(project, "task-1 - untracked.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_outside_a_worktree_reports_no_timestamp(tmp_path: Path) -> None:
    project = init_project(tmp_path / "plain", no_git=True).project
    path = _task(project, "task-1 - plain.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_repository_without_commits_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    project = init_project(repo).project
    path = _task(project, "task-1 - fresh.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_empty_input_is_handled(tmp_path: Path) -> None:
    project = init_project(tmp_path / "plain", no_git=True).project
    assert current_task_snapshot_timestamps(project, []) == {}
