import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.runtime.locks import (
    DaemonRuntimeLock,
    LockTimeoutError,
    ProjectWriteLock,
    init_lock_key,
    project_lock_key,
    with_init_lock,
    with_project_write_lock,
)
import backlog_py.runtime.locks as runtime_locks


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevated Windows privileges")
def test_project_lock_key_uses_resolved_project_root(tmp_path):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(project_root, target_is_directory=True)

    assert project_lock_key(project_root) == project_lock_key(link)


def test_project_lock_key_does_not_embed_project_path(tmp_path):
    project_root = tmp_path / "repo"

    key = project_lock_key(project_root)

    assert str(project_root) not in key
    assert "/" not in key
    assert "\\" not in key
    assert key.startswith("project-")


def test_init_lock_key_is_separate_from_project_lock_key(tmp_path):
    target = tmp_path / "repo"

    assert init_lock_key(target) != project_lock_key(target)
    assert init_lock_key(target).startswith("init-")


def test_project_write_lock_times_out_for_same_project(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    with ProjectWriteLock(project_root, operation="outer").acquire(timeout=0.1):
        result = _attempt_project_lock(project_root, tmp_path / "state", operation="inner", timeout=0.05)

    assert result == "timeout"


def test_project_write_lock_allows_different_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    first = tmp_path / "repo-a"
    second = tmp_path / "repo-b"
    first.mkdir()
    second.mkdir()

    with ProjectWriteLock(first, operation="outer").acquire(timeout=0.1):
        result = _attempt_project_lock(second, tmp_path / "state", operation="inner", timeout=0.2)

    assert result == "acquired"


def test_project_write_lock_metadata_does_not_block_after_release(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")

    with lock.acquire(timeout=0.1):
        metadata_path = lock.metadata_path
        assert metadata_path.is_file()

    with ProjectWriteLock(project_root, operation="task_edit").acquire(timeout=0.1) as acquired:
        assert acquired.metadata_path == metadata_path
        assert metadata_path.is_file()
        assert '"task_edit"' in metadata_path.read_text(encoding="utf-8")


def test_project_write_lock_metadata_records_canonical_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    with ProjectWriteLock(project_root, operation="task_edit").acquire(timeout=0.1):
        locks = runtime_locks.list_runtime_locks()

    assert locks == [
        {
            "acquired_at": locks[0]["acquired_at"],
            "active": True,
            "key": project_lock_key(project_root),
            "kind": "project",
            "lock_path": str(ProjectWriteLock(project_root, operation="ignored").lock_path),
            "metadata_path": str(ProjectWriteLock(project_root, operation="ignored").metadata_path),
            "operation": "task_edit",
            "pid": os.getpid(),
            "project_root": str(project_root.resolve()),
        }
    ]


def test_list_runtime_locks_marks_released_metadata_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    with ProjectWriteLock(project_root, operation="task_create").acquire(timeout=0.1):
        pass

    locks = runtime_locks.list_runtime_locks()

    assert locks[0]["kind"] == "project"
    assert locks[0]["active"] is False


def test_daemon_runtime_lock_is_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    with DaemonRuntimeLock(operation="daemon_start").acquire(timeout=0.1):
        result = _attempt_daemon_lock(tmp_path / "state", operation="daemon_start", timeout=0.05)

    assert result == "timeout"


def test_lock_timeout_error_includes_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    with ProjectWriteLock(project_root, operation="outer").acquire(timeout=0.1):
        with pytest.raises(LockTimeoutError, match="inner"):
            ProjectWriteLock(project_root, operation="inner").acquire(timeout=0).close()


def test_with_project_write_lock_runs_callback_under_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(tmp_path / "repo")

    result = with_project_write_lock(project, "task_create", lambda: "created")

    assert result == "created"


def test_with_init_lock_runs_callback_under_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    result = with_init_lock(tmp_path / "repo", "init_project", lambda: "initialized")

    assert result == "initialized"


def _project(root: Path) -> BacklogProject:
    return BacklogProject(
        root=root,
        backlog_dir=root / "backlog",
        config_path=root / "backlog" / "config.yml",
        config=BacklogConfig(project_name="demo"),
    )


def _attempt_project_lock(project_root: Path, state_dir: Path, *, operation: str, timeout: float) -> str:
    return _attempt_lock(
        state_dir,
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, sys.argv[4])
            from backlog_py.runtime.locks import LockTimeoutError, ProjectWriteLock

            try:
                with ProjectWriteLock(Path(sys.argv[1]), operation=sys.argv[2]).acquire(timeout=float(sys.argv[3])):
                    print("acquired")
            except LockTimeoutError:
                print("timeout")
            """
        ),
        str(project_root),
        operation,
        str(timeout),
    )


def _attempt_daemon_lock(state_dir: Path, *, operation: str, timeout: float) -> str:
    return _attempt_lock(
        state_dir,
        textwrap.dedent(
            """
            import sys

            sys.path.insert(0, sys.argv[3])
            from backlog_py.runtime.locks import DaemonRuntimeLock, LockTimeoutError

            try:
                with DaemonRuntimeLock(operation=sys.argv[1]).acquire(timeout=float(sys.argv[2])):
                    print("acquired")
            except LockTimeoutError:
                print("timeout")
            """
        ),
        operation,
        str(timeout),
    )


def _attempt_lock(state_dir: Path, script: str, *args: str) -> str:
    env = {
        **os.environ,
        "BACKLOG_PY_STATE_DIR": str(state_dir),
    }
    repo_src = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", script, *args, str(repo_src)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    return result.stdout.strip()
