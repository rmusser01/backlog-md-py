from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.runtime.locks import with_project_write_lock


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git executable is required")


def test_project_write_lock_auto_commits_clean_repo_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True)
    project = _project(repo, auto_commit=True)

    result = with_project_write_lock(
        project,
        "task_create",
        lambda: _write_task(repo, "task-2 - Auto-commit.md", "id: TASK-2\ntitle: Auto commit\n"),
    )

    assert result == "created"
    assert _git(repo, "log", "-1", "--format=%s") == "backlog: task_create"
    assert _status_entries(repo) == []


def test_project_write_lock_skips_auto_commit_when_repo_was_dirty(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True)
    project = _project(repo, auto_commit=True)
    (repo / "unrelated.txt").write_text("preexisting\n", encoding="utf-8")

    with_project_write_lock(
        project,
        "task_create",
        lambda: _write_task(repo, "task-2 - Dirty-repo.md", "id: TASK-2\ntitle: Dirty\n"),
    )

    assert _git(repo, "log", "-1", "--format=%s") == "initial"
    assert set(_status_entries(repo)) == {
        "?? unrelated.txt",
        "?? backlog/tasks/task-2 - Dirty-repo.md",
    }


def test_project_write_lock_does_not_bypass_git_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True, bypass_git_hooks=True)
    project = _project(repo, auto_commit=True, bypass_git_hooks=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with_project_write_lock(
        project,
        "task_create",
        lambda: _write_task(repo, "task-2 - Hooked.md", "id: TASK-2\ntitle: Hooked\n"),
    )

    assert _git(repo, "log", "-1", "--format=%s") == "initial"
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _status_entries(repo) == ["?? backlog/tasks/task-2 - Hooked.md"]


def test_project_write_lock_auto_commit_takes_effect_after_enabling(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=False)
    project = _project(repo, auto_commit=False)

    with_project_write_lock(
        project,
        "config_set",
        lambda: _write_config(repo, auto_commit=True),
    )
    with_project_write_lock(
        _project(repo, auto_commit=True),
        "task_create",
        lambda: _write_task(repo, "task-2 - Enabled.md", "id: TASK-2\ntitle: Enabled\n"),
    )

    assert _git(repo, "log", "-1", "--format=%s") == "backlog: task_create"
    assert _status_entries(repo) == []


def _git_backlog_repo(tmp_path: Path, *, auto_commit: bool, bypass_git_hooks: bool = False) -> Path:
    repo = tmp_path / "repo"
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=auto_commit, bypass_git_hooks=bypass_git_hooks)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _project(repo: Path, *, auto_commit: bool, bypass_git_hooks: bool = False) -> BacklogProject:
    return BacklogProject(
        root=repo,
        backlog_dir=repo / "backlog",
        config_path=repo / "backlog" / "config.yml",
        config=BacklogConfig(
            project_name="git-auto-commit",
            auto_commit=auto_commit,
            bypass_git_hooks=bypass_git_hooks,
        ),
    )


def _write_task(repo: Path, filename: str, content: str) -> str:
    (repo / "backlog" / "tasks" / filename).write_text(content, encoding="utf-8")
    return "created"


def _write_config(repo: Path, *, auto_commit: bool, bypass_git_hooks: bool = False) -> None:
    (repo / "backlog").mkdir(parents=True, exist_ok=True)
    (repo / "backlog" / "config.yml").write_text(
        "\n".join(
            [
                "projectName: git-auto-commit",
                f"autoCommit: {_yaml_bool(auto_commit)}",
                f"bypassGitHooks: {_yaml_bool(bypass_git_hooks)}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _status_entries(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [entry for entry in result.stdout.split("\0") if entry]
