from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, TextIO, TypeVar

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.git import maybe_auto_commit, prepare_auto_commit
from backlog_py.runtime.state import ensure_state_layout

T = TypeVar("T")

# Lock and metadata files accumulate one pair per project/init root ever locked.
# Prune only long-idle pairs: a week of inactivity keeps the window in which a
# waiter could be poised on a lock we are about to unlink vanishingly small.
DEFAULT_LOCK_PRUNE_AGE_SECONDS = 7 * 24 * 60 * 60


class LockTimeoutError(TimeoutError):
    """Raised when a runtime filesystem lock cannot be acquired before timeout."""


class ProjectWriteLock:
    """Cross-process lock for mutations to one Backlog.md project."""

    def __init__(self, project_root: Path, *, operation: str) -> None:
        self.project_root = _resolve_path(project_root)
        self.operation = operation
        self._lock = _RuntimeFileLock(
            key=project_lock_key(self.project_root),
            kind="project",
            operation=operation,
            metadata={"project_root": str(self.project_root)},
        )

    @property
    def lock_path(self) -> Path:
        return self._lock.lock_path

    @property
    def metadata_path(self) -> Path:
        return self._lock.metadata_path

    def acquire(self, *, timeout: float = 5.0, poll_interval: float = 0.05) -> "_HeldRuntimeLock":
        return self._lock.acquire(timeout=timeout, poll_interval=poll_interval)


class DaemonRuntimeLock:
    """Cross-process lock guarding singleton daemon startup and runtime state."""

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        self._lock = _RuntimeFileLock(key="daemon-runtime", kind="daemon", operation=operation)

    @property
    def lock_path(self) -> Path:
        return self._lock.lock_path

    @property
    def metadata_path(self) -> Path:
        return self._lock.metadata_path

    def acquire(self, *, timeout: float = 5.0, poll_interval: float = 0.05) -> "_HeldRuntimeLock":
        return self._lock.acquire(timeout=timeout, poll_interval=poll_interval)


def project_lock_key(project_root: Path) -> str:
    """Return a stable lock key for a resolved project root."""
    return f"project-{_path_digest(project_root)}"


def init_lock_key(target_root: Path) -> str:
    """Return a stable lock key for initialization of a target root."""
    return f"init-{_path_digest(target_root)}"


def with_project_write_lock(project: BacklogProject, operation: str, fn: Callable[[], T]) -> T:
    """Run a callback while holding the project write lock."""
    with ProjectWriteLock(project.root, operation=operation).acquire():
        auto_commit_context = prepare_auto_commit(project)
        result = fn()
        maybe_auto_commit(project, operation, auto_commit_context)
        return result


def with_init_lock(target_root: Path, operation: str, fn: Callable[[], T]) -> T:
    """Run a callback while holding the init-root lock."""
    resolved_target = _resolve_path(target_root)
    lock = _RuntimeFileLock(
        key=init_lock_key(resolved_target),
        kind="init-root",
        operation=operation,
        metadata={"target_root": str(resolved_target)},
    )
    with lock.acquire():
        return fn()


class _RuntimeFileLock:
    def __init__(
        self,
        *,
        key: str,
        kind: str,
        operation: str,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.key = key
        self.kind = kind
        self.operation = operation
        self.metadata = dict(metadata or {})
        layout = ensure_state_layout()
        self.lock_path = layout.locks_dir / f"{key}.lock"
        self.metadata_path = layout.locks_dir / f"{key}.json"

    def acquire(self, *, timeout: float = 5.0, poll_interval: float = 0.05) -> "_HeldRuntimeLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(timeout, 0)
        handle = self.lock_path.open("a+", encoding="utf-8")
        _prepare_lock_file(handle)
        while True:
            try:
                _try_lock(handle)
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise LockTimeoutError(
                        f"Timed out acquiring {self.kind} lock for operation {self.operation!r}"
                    ) from exc
                time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0)))
                continue

            if not _locked_handle_is_current(handle, self.lock_path):
                # The lock lives on the inode, not on the path: whoever unlinked
                # the file we just locked left us excluding nobody, and the next
                # acquirer would create a fresh inode and "win" it too. Drop this
                # lock and race for whatever now lives at the path.
                _release_handle(handle)
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"Timed out acquiring {self.kind} lock for operation {self.operation!r}"
                    )
                time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0)))
                handle = self.lock_path.open("a+", encoding="utf-8")
                _prepare_lock_file(handle)
                continue

            held = _HeldRuntimeLock(self, handle)
            try:
                self._write_metadata()
            except Exception:
                held.close()
                raise
            return held

    def _write_metadata(self) -> None:
        metadata = {
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
            "kind": self.kind,
            "key": self.key,
            "operation": self.operation,
            "pid": os.getpid(),
            **self.metadata,
        }
        _write_json(self.metadata_path, metadata)

    def _write_released_metadata(self) -> None:
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        raw["active"] = False
        raw["released_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(self.metadata_path, raw)


class _HeldRuntimeLock:
    def __init__(self, lock: _RuntimeFileLock, handle: TextIO) -> None:
        self.lock = lock
        self.lock_path = lock.lock_path
        self.metadata_path = lock.metadata_path
        self._handle = handle
        self._closed = False

    def __enter__(self) -> "_HeldRuntimeLock":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with suppress(OSError, json.JSONDecodeError):
                self.lock._write_released_metadata()
            _unlock(self._handle)
        finally:
            self._handle.close()


def list_runtime_locks() -> list[dict[str, object]]:
    """Return token-safe runtime lock metadata for daemon diagnostics."""
    layout = ensure_state_layout()
    locks: list[dict[str, object]] = []
    for metadata_path in sorted(layout.locks_dir.glob("*.json")):
        metadata = _read_lock_metadata(metadata_path)
        if metadata is None:
            continue
        lock_path = _lock_path_for_metadata(metadata_path)
        metadata["lock_path"] = str(lock_path)
        metadata["metadata_path"] = str(metadata_path)
        metadata["active"] = _metadata_lock_is_active(metadata, lock_path)
        locks.append(metadata)
    return locks


def prune_stale_locks(*, min_age_seconds: float = DEFAULT_LOCK_PRUNE_AGE_SECONDS) -> list[Path]:
    """Remove lock/metadata pairs whose owner is provably gone and long idle.

    Safety rules, in order:

    * The metadata must parse and must not describe an owner that may still be
      running (``active`` metadata whose pid is alive is always kept).
    * The metadata must not have been touched within ``min_age_seconds``; it is
      rewritten on every acquire and release, so it dates the last use.
    * The lock file is unlinked only while this process itself holds its flock,
      which proves no other process holds it, and only after re-checking that
      the metadata did not change while we were taking that lock and that the
      path still names the inode we locked.
    * The lock file to unlink is derived from the metadata file's name, never
      from the ``key`` recorded inside it.

    Anything that cannot be proven dead — unreadable metadata, a lock file with
    no metadata, a flock we cannot take — is left in place. Returns the removed
    paths.
    """
    layout = ensure_state_layout()
    cutoff = time.time() - max(min_age_seconds, 0)
    try:
        metadata_paths = sorted(layout.locks_dir.glob("*.json"))
    except OSError:
        return []
    removed: list[Path] = []
    for metadata_path in metadata_paths:
        metadata = _read_lock_metadata(metadata_path)
        if metadata is None or _lock_owner_may_be_live(metadata):
            continue
        metadata_stat = _stat(metadata_path)
        if metadata_stat is None:
            continue
        # The age gate exists so a lock released moments ago is not swept out
        # from under a process about to reacquire it. A lock whose project
        # directory no longer exists has no such future: nothing can take it
        # again, and until it ages out it keeps a deleted worktree or a test's
        # temp directory listed in `daemon status`.
        if metadata_stat.st_mtime > cutoff and not _project_root_is_gone(metadata):
            continue
        lock_path = _lock_path_for_metadata(metadata_path)
        removed.extend(_remove_dead_lock(lock_path, metadata_path, metadata_stat))
    return removed


def _project_root_is_gone(metadata: dict[str, object]) -> bool:
    """Whether this lock names a project directory that no longer exists.

    Only a recorded absolute path counts. Anything unreadable, relative, or
    simply absent from the metadata is treated as still present, so an odd
    record is kept rather than pruned on a guess.
    """
    project_root = metadata.get("project_root")
    if not isinstance(project_root, str) or not project_root:
        return False
    try:
        return not Path(project_root).is_dir()
    except OSError:
        return False


def _lock_path_for_metadata(metadata_path: Path) -> Path:
    """Return the lock file paired with ``metadata_path``.

    Derived from the metadata file's own name, never from the ``key`` inside its
    body: that body is writable by anyone who can write the state directory, and
    a planted ``"key": "../../x"`` would otherwise aim the pruner's unlink at an
    arbitrary ``*.lock`` outside the locks directory.
    """
    return metadata_path.with_suffix(".lock")


def _lock_owner_may_be_live(metadata: dict[str, object]) -> bool:
    if metadata.get("active") is not True:
        return False  # the holder wrote its released marker before unlocking
    pid = metadata.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return True  # active with an unknown owner: assume it is live
    return _process_may_be_alive(pid)


def _process_may_be_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates the target on Windows; never probe there.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _remove_dead_lock(lock_path: Path, metadata_path: Path, metadata_stat: os.stat_result) -> list[Path]:
    if not lock_path.exists():
        # Metadata left behind without its lock file: nothing can be held.
        return _unlink_if_unchanged(metadata_path, metadata_stat)
    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        return []
    try:
        _prepare_lock_file(handle)
        try:
            _try_lock(handle)
        except OSError:
            return []  # held (or unprobeable): leave it strictly alone
        try:
            if not _locked_handle_is_current(handle, lock_path):
                return []  # the path no longer names the inode we locked
            current = _stat(metadata_path)
            if (
                current is None
                or current.st_mtime_ns != metadata_stat.st_mtime_ns
                or current.st_ino != metadata_stat.st_ino
            ):
                return []  # someone used this lock while we were taking it
            # Unlink while still holding the flock. That does not by itself shut
            # acquirers out — one may already be spinning on this very inode —
            # but such an acquirer revalidates the inode after it locks and
            # reopens the path, so it cannot end up holding an orphan.
            removed = _unlink_if_unchanged(metadata_path, metadata_stat)
            removed.extend(_unlink_path(lock_path))
            return removed
        finally:
            with suppress(OSError):
                _unlock(handle)
    finally:
        handle.close()


def _unlink_if_unchanged(path: Path, expected: os.stat_result) -> list[Path]:
    current = _stat(path)
    if current is None or current.st_mtime_ns != expected.st_mtime_ns or current.st_ino != expected.st_ino:
        return []
    return _unlink_path(path)


def _unlink_path(path: Path) -> list[Path]:
    try:
        path.unlink()
    except OSError:
        return []
    return [path]


def _locked_handle_is_current(handle: TextIO, path: Path) -> bool:
    """Return True when the locked descriptor is still the file living at ``path``.

    ``flock``/``msvcrt.locking`` attach to the open file, not to the name, so a
    lock taken on an inode that has since been unlinked (or replaced) guards
    nothing: the next acquirer creates a new file at the same path and locks
    that instead. A vanished path counts as a mismatch.
    """
    try:
        locked = os.fstat(handle.fileno())
    except (OSError, ValueError):
        return False
    current = _stat(path)
    if current is None:
        return False
    return (locked.st_ino, locked.st_dev) == (current.st_ino, current.st_dev)


def _release_handle(handle: TextIO) -> None:
    try:
        with suppress(OSError):
            _unlock(handle)
    finally:
        handle.close()


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _read_lock_metadata(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items() if _is_json_scalar_or_null(value)}


def _metadata_lock_is_active(metadata: dict[str, object], lock_path: Path) -> bool:
    if metadata.get("active") is not True:
        return False
    if metadata.get("pid") == os.getpid():
        return True
    return _lock_file_is_active(lock_path)


def _lock_file_is_active(path: Path) -> bool:
    try:
        with path.open("a+", encoding="utf-8") as handle:
            _prepare_lock_file(handle)
            try:
                _try_lock(handle)
            except BlockingIOError:
                return True
            _unlock(handle)
            return False
    except OSError:
        return False


def _is_json_scalar_or_null(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _path_digest(path: Path) -> str:
    resolved = _resolve_path(path)
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


def _resolve_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _prepare_lock_file(handle: TextIO) -> None:
    if os.name != "nt":
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write("\0")
        handle.flush()


def _try_lock(handle: TextIO) -> None:
    if os.name == "nt":
        _try_lock_windows(handle)
        return
    _try_lock_posix(handle)


def _unlock(handle: TextIO) -> None:
    if os.name == "nt":
        _unlock_windows(handle)
        return
    _unlock_posix(handle)


def _try_lock_posix(handle: TextIO) -> None:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise BlockingIOError(exc.errno, exc.strerror) from exc
        raise


def _unlock_posix(handle: TextIO) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _try_lock_windows(handle: TextIO) -> None:
    import msvcrt

    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EDEADLK, errno.EAGAIN}:
            raise BlockingIOError(exc.errno, exc.strerror) from exc
        raise


def _unlock_windows(handle: TextIO) -> None:
    import msvcrt

    handle.seek(0)
    with suppress(OSError):
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _write_json(path: Path, data: dict[str, object]) -> None:
    payload = f"{json.dumps(data, sort_keys=True)}\n"
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        with suppress(OSError):
            temp_path.unlink()
        raise
