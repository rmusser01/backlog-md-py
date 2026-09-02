import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.runtime.locks import (
    DaemonRuntimeLock,
    LockTimeoutError,
    ProjectWriteLock,
    init_lock_key,
    list_runtime_locks,
    project_lock_key,
    prune_stale_locks,
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


def test_prune_stale_locks_removes_old_released_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")
    with lock.acquire(timeout=0.1):
        pass
    _backdate(lock.metadata_path, days=30)

    removed = prune_stale_locks(min_age_seconds=7 * 24 * 60 * 60)

    assert sorted(removed) == sorted([lock.lock_path, lock.metadata_path])
    assert not lock.lock_path.exists()
    assert not lock.metadata_path.exists()


def test_prune_stale_locks_keeps_recently_released_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")
    with lock.acquire(timeout=0.1):
        pass

    assert prune_stale_locks(min_age_seconds=7 * 24 * 60 * 60) == []
    assert lock.lock_path.exists()
    assert lock.metadata_path.exists()


def test_prune_stale_locks_never_removes_a_held_lock(tmp_path, monkeypatch):
    """Even metadata claiming the lock is long released cannot condemn it."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")

    with lock.acquire(timeout=0.1):
        metadata = json.loads(lock.metadata_path.read_text(encoding="utf-8"))
        metadata["active"] = False
        metadata["pid"] = 999_999
        lock.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        _backdate(lock.metadata_path, days=30)

        removed = prune_stale_locks(min_age_seconds=0)

        assert removed == []
        assert lock.lock_path.exists()
        assert lock.metadata_path.exists()


def test_prune_stale_locks_removes_locks_whose_owner_crashed(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")
    with lock.acquire(timeout=0.1):
        pass
    # A crashed holder leaves active metadata behind; the kernel released flock.
    metadata = json.loads(lock.metadata_path.read_text(encoding="utf-8"))
    metadata["active"] = True
    metadata["pid"] = 999_999
    lock.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    _backdate(lock.metadata_path, days=30)

    removed = prune_stale_locks(min_age_seconds=7 * 24 * 60 * 60)

    assert sorted(removed) == sorted([lock.lock_path, lock.metadata_path])


def test_prune_stale_locks_keeps_lock_files_without_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout_locks = tmp_path / "state" / "locks"
    layout_locks.mkdir(parents=True, exist_ok=True)
    orphan = layout_locks / "project-orphan.lock"
    orphan.write_text("", encoding="utf-8")
    _backdate(orphan, days=30)

    assert prune_stale_locks(min_age_seconds=0) == []
    assert orphan.exists(), "removed a lock whose state could not be proven dead"


def test_prune_stale_locks_cannot_hand_two_acquirers_the_same_lock(tmp_path, monkeypatch):
    """flock lives on the inode, not the path: pruning must not fork the lock.

    Drives the exact reviewed interleaving with events (no sleeps):

    * the pruner takes the flock and is gated just before it unlinks;
    * a real acquirer opens the path and piles up in its retry loop (it has not
      written metadata yet, so the pruner's metadata re-check still passes);
    * the pruner unlinks metadata + lock file, then unlocks;
    * the acquirer's next retry succeeds -- on an orphaned inode.

    At that point the acquirer believes it holds the project write lock, so no
    second acquirer may take it.
    """
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    lock = ProjectWriteLock(project_root, operation="task_create")
    with lock.acquire(timeout=1):
        pass
    _backdate(lock.metadata_path, days=30)

    pruner_holds_flock = threading.Event()
    waiter_is_spinning = threading.Event()
    waiter_finished_acquire = threading.Event()
    release_waiter = threading.Event()

    real_try_lock = runtime_locks._try_lock
    real_unlink_if_unchanged = runtime_locks._unlink_if_unchanged

    def instrumented_try_lock(handle):
        try:
            real_try_lock(handle)
        except BlockingIOError:
            if threading.current_thread().name == "waiter":
                waiter_is_spinning.set()
            raise

    def gated_unlink_if_unchanged(path, expected):
        if path == lock.metadata_path:
            # The pruner holds the flock and is about to unlink; let a genuine
            # acquirer pile up behind it before the file goes away.
            pruner_holds_flock.set()
            assert waiter_is_spinning.wait(10), "waiter never reached the retry loop"
        return real_unlink_if_unchanged(path, expected)

    monkeypatch.setattr(runtime_locks, "_try_lock", instrumented_try_lock)
    monkeypatch.setattr(runtime_locks, "_unlink_if_unchanged", gated_unlink_if_unchanged)

    waiter_outcome: list[str] = []

    def run_pruner() -> None:
        prune_stale_locks(min_age_seconds=0)

    def run_waiter() -> None:
        try:
            with ProjectWriteLock(project_root, operation="waiter").acquire(timeout=10):
                waiter_outcome.append("acquired")
                waiter_finished_acquire.set()
                assert release_waiter.wait(10)
        except LockTimeoutError:
            waiter_outcome.append("timeout")
        finally:
            waiter_finished_acquire.set()

    pruner = threading.Thread(target=run_pruner, name="pruner")
    waiter = threading.Thread(target=run_waiter, name="waiter")
    pruner.start()
    try:
        assert pruner_holds_flock.wait(10), "pruner never reached the unlink"
        waiter.start()
        assert waiter_finished_acquire.wait(10), "waiter never finished acquiring"
        assert waiter_outcome == ["acquired"], "the waiter should still get the lock, just a real one"
        with pytest.raises(LockTimeoutError, match="intruder"):
            ProjectWriteLock(project_root, operation="intruder").acquire(timeout=0.2).close()
    finally:
        release_waiter.set()
        waiter.join(timeout=10)
        pruner.join(timeout=10)

    assert not waiter.is_alive()
    assert not pruner.is_alive()


def test_prune_stale_locks_ignores_a_traversal_key_planted_in_metadata(tmp_path, monkeypatch):
    """The lock path is derived from the metadata file, never from its contents."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    locks_dir = tmp_path / "state" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    outsider = tmp_path / "outsider.lock"
    outsider.write_text("", encoding="utf-8")
    planted = locks_dir / "project-planted.json"
    planted.write_text(
        json.dumps({"active": False, "key": "../../outsider", "kind": "project", "pid": 999_999}),
        encoding="utf-8",
    )
    _backdate(planted, days=30)

    removed = prune_stale_locks(min_age_seconds=0)

    assert outsider.exists(), "pruning followed a traversal key out of the locks directory"
    assert removed == [planted]


def _backdate(path: Path, *, days: float) -> None:
    stamp = time.time() - days * 24 * 60 * 60
    os.utime(path, (stamp, stamp))


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


def test_prune_removes_a_lock_whose_project_directory_is_gone(tmp_path, monkeypatch):
    """A deleted worktree or a test's temp directory should not linger for a week.

    The age gate protects a lock that something may be about to reacquire. A
    lock naming a directory that no longer exists has no such future, and until
    it aged out it kept vanished projects listed in `daemon status`.
    """
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    gone = tmp_path / "deleted-worktree"
    gone.mkdir()
    with ProjectWriteLock(gone, operation="test").acquire():
        pass
    gone.rmdir()

    removed = prune_stale_locks(min_age_seconds=7 * 24 * 60 * 60)

    assert removed, "a lock for a directory that no longer exists was kept"
    assert all(lock.get("project_root") != str(gone) for lock in list_runtime_locks())


def test_prune_keeps_a_recent_lock_whose_project_still_exists(tmp_path, monkeypatch):
    """The age gate still applies to every project that is actually there."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    live = tmp_path / "live-project"
    live.mkdir()
    with ProjectWriteLock(live, operation="test").acquire():
        pass

    assert prune_stale_locks(min_age_seconds=7 * 24 * 60 * 60) == []


def test_status_omits_a_project_directory_that_no_longer_exists(tmp_path, monkeypatch):
    """Between prunes a project can vanish; status must not claim otherwise."""
    from backlog_py.runtime.state import RuntimeRecord, runtime_status

    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    live = tmp_path / "still-here"
    gone = tmp_path / "not-anymore"
    live.mkdir()
    gone.mkdir()
    for root in (live, gone):
        with ProjectWriteLock(root, operation="test").acquire():
            pass
    gone.rmdir()

    status = runtime_status(
        RuntimeRecord(
            pid=1, host="127.0.0.1", port=1, endpoint="http://127.0.0.1:1/mcp",
            token="t", started_at="now", version="0", log_path=tmp_path / "log",
        )
    )

    assert str(live) in status["known_projects"]
    assert str(gone) not in status["known_projects"]
