from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
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


def test_auto_commit_does_not_stage_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True)
    project = _project(repo, auto_commit=True)

    def mutate() -> str:
        # A file written outside backlog/ during the locked operation (e.g. an
        # editor session or a status-change hook) must not be swept into the
        # auto-commit.
        (repo / "unrelated.txt").write_text("side effect\n", encoding="utf-8")
        return _write_task(repo, "task-2 - Scoped.md", "id: TASK-2\ntitle: Scoped\n")

    with_project_write_lock(project, "task_create", mutate)

    assert _git(repo, "log", "-1", "--format=%s") == "backlog: task_create"
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert any("task-2" in name for name in committed), committed
    assert "unrelated.txt" not in committed, committed
    assert "?? unrelated.txt" in _status_entries(repo)


def test_project_write_lock_runs_git_hooks_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True, bypass_git_hooks=False)
    project = _project(repo, auto_commit=True, bypass_git_hooks=False)
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


def test_project_write_lock_bypasses_git_hooks_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _git_backlog_repo(tmp_path, auto_commit=True, bypass_git_hooks=True)
    project = _project(repo, auto_commit=True, bypass_git_hooks=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with_project_write_lock(
        project,
        "task_create",
        lambda: _write_task(repo, "task-2 - Hook-bypass.md", "id: TASK-2\ntitle: Hook bypass\n"),
    )

    assert _git(repo, "log", "-1", "--format=%s") == "backlog: task_create"
    assert _status_entries(repo) == []


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

    tasks = ReadOnlyRepository(project).list_tasks()

    assert remote_branch in _remote_branches(repo)
    assert [task.id for task in tasks] == ["TASK-2"]


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


def test_readonly_repository_prefers_recent_active_branch_task_state(tmp_path):
    repo = _git_repo_with_recent_branch_status_update(tmp_path)
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=30)

    board = ReadOnlyRepository(project).board()

    assert [task.id for task in board.get("To Do", [])] == []
    assert [(task.id, task.status) for task in board["Done"]] == [("TASK-1", "Done")]


def test_readonly_repository_ignores_branch_state_outside_active_window(tmp_path):
    repo = _git_repo_with_recent_branch_status_update(tmp_path)
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=0)

    board = ReadOnlyRepository(project).board()

    assert [(task.id, task.status) for task in board["To Do"]] == [("TASK-1", "To Do")]
    assert [task.id for task in board.get("Done", [])] == []


def test_unrelated_worktree_changes_do_not_disable_active_branch_accuracy(tmp_path):
    repo = _git_repo_with_recent_branch_status_update(tmp_path)
    (repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=30)

    board = ReadOnlyRepository(project).board()

    assert [task.id for task in board.get("To Do", [])] == []
    assert [(task.id, task.status) for task in board["Done"]] == [("TASK-1", "Done")]


def test_recent_unrelated_branch_commit_does_not_resurrect_stale_task_state(tmp_path):
    repo = _git_repo_with_stale_branch_task_and_recent_unrelated_commit(tmp_path)
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=30)

    board = ReadOnlyRepository(project).board()

    assert [(task.id, task.status) for task in board["In Progress"]] == [("TASK-1", "In Progress")]
    assert [task.id for task in board.get("To Do", [])] == []


def test_recent_current_unrelated_commit_does_not_hide_newer_branch_task_state(tmp_path):
    repo = _git_repo_with_current_unrelated_commit_after_branch_task_update(tmp_path)
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=30)

    board = ReadOnlyRepository(project).board()

    assert [task.id for task in board.get("To Do", [])] == []
    assert [(task.id, task.status) for task in board["Done"]] == [("TASK-1", "Done")]


def test_mutable_repository_does_not_expose_branch_only_tasks(tmp_path):
    repo = _git_repo_with_branch_only_task(tmp_path)
    project = _project(repo, remote_operations=False, check_active_branches=True, active_branch_days=30)

    assert [(task.id, task.title) for task in ReadOnlyRepository(project).list_tasks()] == [
        ("TASK-2", "Branch Only")
    ]
    assert MutableRepository(project).list_tasks() == []


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
    active_branch_days: int = 30,
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
            active_branch_days=active_branch_days,
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


def _git_repo_with_recent_branch_status_update(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=False)
    _write_task(
        repo,
        "task-1 - Cross Branch.md",
        "\n".join(
            [
                "---",
                "id: TASK-1",
                "title: Cross Branch",
                "status: To Do",
                "assignee: []",
                "created_date: '2026-05-20'",
                "labels: []",
                "dependencies: []",
                "---",
                "",
            ]
        ),
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main task", env=_recent_git_date_env(0))
    _git(repo, "checkout", "-b", "feature/status-done")
    task_path = repo / "backlog" / "tasks" / "task-1 - Cross Branch.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("status: To Do", "status: Done"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mark task done")
    _git(repo, "checkout", "main")
    return repo


def _git_repo_with_branch_only_task(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=False)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main backlog")
    _git(repo, "checkout", "-b", "feature/branch-only")
    _write_task(
        repo,
        "task-2 - Branch Only.md",
        "\n".join(
            [
                "---",
                "id: TASK-2",
                "title: Branch Only",
                "status: To Do",
                "assignee: []",
                "created_date: '2026-05-20'",
                "labels: []",
                "dependencies: []",
                "---",
                "",
            ]
        ),
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "branch only task")
    _git(repo, "checkout", "main")
    return repo


def _git_repo_with_stale_branch_task_and_recent_unrelated_commit(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=False)
    _write_task(
        repo,
        "task-1 - Cross Branch.md",
        "\n".join(
            [
                "---",
                "id: TASK-1",
                "title: Cross Branch",
                "status: To Do",
                "assignee: []",
                "created_date: '2026-05-20'",
                "labels: []",
                "dependencies: []",
                "---",
                "",
            ]
        ),
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main task", env=_git_date_env("2026-05-20T10:00:00Z"))
    _git(repo, "checkout", "-b", "feature/unrelated-work")
    _git(repo, "checkout", "main")
    task_path = repo / "backlog" / "tasks" / "task-1 - Cross Branch.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("status: To Do", "status: In Progress"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "advance task on main", env=_recent_git_date_env(1))
    _git(repo, "checkout", "feature/unrelated-work")
    (repo / "feature.txt").write_text("recent unrelated branch work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "recent unrelated branch work", env=_recent_git_date_env(2))
    _git(repo, "checkout", "main")
    return repo


def _git_repo_with_current_unrelated_commit_after_branch_task_update(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backlog" / "tasks").mkdir(parents=True)
    _write_config(repo, auto_commit=False)
    _write_task(
        repo,
        "task-1 - Cross Branch.md",
        "\n".join(
            [
                "---",
                "id: TASK-1",
                "title: Cross Branch",
                "status: To Do",
                "assignee: []",
                "created_date: '2026-05-20'",
                "labels: []",
                "dependencies: []",
                "---",
                "",
            ]
        ),
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main task", env=_recent_git_date_env(0))
    _git(repo, "checkout", "-b", "feature/status-done")
    task_path = repo / "backlog" / "tasks" / "task-1 - Cross Branch.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("status: To Do", "status: Done"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mark task done", env=_recent_git_date_env(1))
    _git(repo, "checkout", "main")
    (repo / "current.txt").write_text("recent unrelated current work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "recent unrelated current work", env=_recent_git_date_env(2))
    return repo


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


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _git_date_env(timestamp: str) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    }


def _recent_git_date_env(offset_seconds: int) -> dict[str, str]:
    timestamp = datetime.now(timezone.utc) - timedelta(days=1) + timedelta(seconds=offset_seconds)
    return _git_date_env(timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _status_entries(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [entry for entry in result.stdout.split("\0") if entry]
