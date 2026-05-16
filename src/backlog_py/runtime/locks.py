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
from typing import Callable, TextIO, TypeVar

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.state import ensure_state_layout

T = TypeVar("T")


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
        return fn()


def with_init_lock(target_root: Path, operation: str, fn: Callable[[], T]) -> T:
    """Run a callback while holding the init-root lock."""
    lock = _RuntimeFileLock(key=init_lock_key(target_root), kind="init-root", operation=operation)
    with lock.acquire():
        return fn()


class _RuntimeFileLock:
    def __init__(self, *, key: str, kind: str, operation: str) -> None:
        self.key = key
        self.kind = kind
        self.operation = operation
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
            "kind": self.kind,
            "key": self.key,
            "operation": self.operation,
            "pid": os.getpid(),
        }
        _write_json(self.metadata_path, metadata)


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
            _unlock(self._handle)
        finally:
            self._handle.close()


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
