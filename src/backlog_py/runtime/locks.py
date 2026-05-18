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
        key = str(metadata.get("key") or metadata_path.stem)
        lock_path = layout.locks_dir / f"{key}.lock"
        metadata["lock_path"] = str(lock_path)
        metadata["metadata_path"] = str(metadata_path)
        metadata["active"] = _metadata_lock_is_active(metadata, lock_path)
        locks.append(metadata)
    return locks


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
