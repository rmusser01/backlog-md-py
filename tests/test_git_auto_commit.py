from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.core.repository import ReadOnlyRepository
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


def test_repository_fetches_remote_refs_when_remote_operations_enabled(tmp_path):
    repo, remote_branch = _git_repo_with_unfetched_remote_branch(tmp_path, remote_operations=True)
    project = _project(repo, remote_operations=True, check_active_branches=True)

    assert remote_branch not in _remote_branches(repo)

    assert ReadOnlyRepository(project).list_tasks() == []

    assert remote_branch in _remote_branches(repo)


def test_repository_skips_fetch_when_remote_operations_disabled(tmp_path):
    repo, remote_branch = _git_repo_with_unfetched_remote_branch(tmp_path, remote_operations=False)
    project = _project(repo, remote_operations=False, check_active_branches=True)

    assert remote_branch not in _remote_branches(repo)

    assert ReadOnlyRepository(project).list_tasks() == []

    assert remote_branch not in _remote_branches(repo)


def test_repository_skips_fetch_when_active_branch_checks_disabled(tmp_path):
    repo, remote_branch = _git_repo_with_unfetched_remote_branch(tmp_path, remote_operations=True)
    project = _project(repo, remote_operations=True, check_active_branches=False)

    assert remote_branch not in _remote_branches(repo)

    assert ReadOnlyRepository(project).list_tasks() == []

    assert remote_branch not in _remote_branches(repo)


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


def _project(
    repo: Path,
    *,
    auto_commit: bool = False,
    bypass_git_hooks: bool = False,
    remote_operations: bool = True,
    check_active_branches: bool = True,
) -> BacklogProject:
    return BacklogProject(
        root=repo,
        backlog_dir=repo / "backlog",
        config_path=repo / "backlog" / "config.yml",
        config=BacklogConfig(
            project_name="git-auto-commit",
            auto_commit=auto_commit,
            bypass_git_hooks=bypass_git_hooks,
            remote_operations=remote_operations,
            check_active_branches=check_active_branches,
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


def _git_repo_with_unfetched_remote_branch(tmp_path: Path, *, remote_operations: bool) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    producer = tmp_path / "producer"
    repo = tmp_path / "repo"
    branch = "origin/remote-task"

    _git(tmp_path, "init", "--bare", str(remote))
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=False)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", str(remote), str(producer))
    _git(producer, "config", "user.email", "producer@example.com")
    _git(producer, "config", "user.name", "Producer")
    _git(producer, "checkout", "-b", "remote-task")
    (producer / "backlog" / "tasks").mkdir(parents=True, exist_ok=True)
    _write_task(producer, "task-2 - Remote.md", "id: TASK-2\ntitle: Remote task\n")
    _git(producer, "add", ".")
    _git(producer, "commit", "-m", "remote branch task")
    _git(producer, "push", "-u", "origin", "remote-task")

    _write_config(repo, auto_commit=False)
    if not remote_operations:
        _replace_config_line(repo, "remoteOperations: false")
    return repo, branch


def _replace_config_line(repo: Path, line: str) -> None:
    path = repo / "backlog" / "config.yml"
    text = path.read_text(encoding="utf-8")
    if "remoteOperations:" in text:
        updated = "\n".join(
            line if existing.startswith("remoteOperations:") else existing
            for existing in text.splitlines()
        )
    else:
        updated = f"{text.rstrip()}\n{line}"
    path.write_text(f"{updated.rstrip()}\n", encoding="utf-8")


def _remote_branches(repo: Path) -> set[str]:
    output = _git(repo, "branch", "-r", "--format=%(refname:short)")
    return {line.strip() for line in output.splitlines() if line.strip()}


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
