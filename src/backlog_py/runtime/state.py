from __future__ import annotations

import json
import os
import secrets
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

# Every daemon start allocates its own log file, so the logs directory would
# otherwise grow without bound. Keep a small, useful tail of recent starts.
DEFAULT_RETAINED_DAEMON_LOGS = 10
# Only per-start logs (allocate_log_path) are rotated; the fixed-name log an
# unmanaged foreground daemon appends to is deliberately excluded.
_DAEMON_LOG_GLOB = "backlog-md-py-daemon-*.log"


@dataclass(frozen=True)
class StateLayout:
    """Directory layout for local daemon runtime state."""

    root: Path
    runtime_dir: Path
    locks_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class RuntimeRecord:
    """Token-bearing record for a running singleton daemon."""

    pid: int
    host: str
    port: int
    endpoint: str
    token: str
    started_at: str
    version: str
    log_path: Path


def resolve_state_dir(
    env: Mapping[str, str] = os.environ,
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """Resolve the user-specific backlog-md-py state directory."""
    override = _text_env(env, "BACKLOG_PY_STATE_DIR")
    if override:
        return Path(override).expanduser()

    home_path = home if home is not None else Path.home()
    if platform == "darwin":
        return home_path / "Library" / "Application Support" / "backlog-md-py"
    if platform.startswith("win"):
        local_app_data = _text_env(env, "LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser() / "backlog-md-py"
        return home_path / "AppData" / "Local" / "backlog-md-py"

    xdg_state_home = _text_env(env, "XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "backlog-md-py"
    return home_path / ".local" / "state" / "backlog-md-py"


def ensure_state_layout() -> StateLayout:
    """Create and return the daemon runtime state directory layout."""
    root = resolve_state_dir()
    layout = StateLayout(
        root=root,
        runtime_dir=root / "runtime",
        locks_dir=root / "locks",
        logs_dir=root / "logs",
    )
    for directory in (layout.runtime_dir, layout.locks_dir, layout.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return layout


def runtime_record_path(layout: StateLayout) -> Path:
    """Return the singleton daemon runtime record path."""
    return layout.runtime_dir / "daemon.json"


def write_runtime_record(record: RuntimeRecord, layout: StateLayout) -> None:
    """Write a token-bearing runtime record with restrictive permissions."""
    layout.runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_record_path(layout)
    data = {
        "pid": record.pid,
        "host": record.host,
        "port": record.port,
        "endpoint": record.endpoint,
        "token": record.token,
        "started_at": record.started_at,
        "version": record.version,
        "log_path": str(record.log_path),
    }
    _write_private_json(target, data)


def read_runtime_record(layout: StateLayout) -> RuntimeRecord | None:
    """Read the daemon runtime record, returning None when it is unavailable."""
    path = runtime_record_path(layout)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return RuntimeRecord(
            pid=int(raw["pid"]),
            host=str(raw["host"]),
            port=int(raw["port"]),
            endpoint=str(raw["endpoint"]),
            token=str(raw["token"]),
            started_at=str(raw["started_at"]),
            version=str(raw["version"]),
            log_path=Path(str(raw["log_path"])),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def delete_runtime_record(layout: StateLayout) -> None:
    """Remove the daemon runtime record if it exists."""
    with suppress(FileNotFoundError):
        runtime_record_path(layout).unlink()


def runtime_status(record: RuntimeRecord) -> dict[str, object]:
    """Return a JSON-safe runtime status mapping without token material."""
    from backlog_py.runtime.locks import list_runtime_locks

    locks = list_runtime_locks()
    known_projects = sorted(
        {
            str(lock["project_root"])
            for lock in locks
            if lock.get("kind") == "project" and isinstance(lock.get("project_root"), str)
        }
    )
    return {
        "pid": record.pid,
        "host": record.host,
        "port": record.port,
        "endpoint": record.endpoint,
        "started_at": record.started_at,
        "version": record.version,
        "log_path": str(record.log_path),
        "known_projects": known_projects,
        "locks": locks,
    }


def allocate_log_path(layout: StateLayout) -> Path:
    """Return a unique daemon log path inside the state logs directory."""
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return layout.logs_dir / f"backlog-md-py-daemon-{stamp}-{os.getpid()}-{secrets.token_hex(4)}.log"


def prune_daemon_logs(
    layout: StateLayout,
    *,
    keep: int = DEFAULT_RETAINED_DAEMON_LOGS,
    exclude: Iterable[Path] = (),
) -> list[Path]:
    """Delete all but the `keep` most recent per-start daemon logs.

    Returns the paths that were removed. Deleting a log a running daemon still
    holds open is harmless on POSIX (it keeps writing to the open inode); on
    Windows the delete simply fails and the log is retained.
    """
    if keep < 0:
        raise ValueError("keep must not be negative")
    kept = {_normalized(path) for path in exclude}
    try:
        candidates = [path for path in layout.logs_dir.glob(_DAEMON_LOG_GLOB) if path.is_file()]
    except OSError:
        return []
    candidates = [path for path in candidates if _normalized(path) not in kept]
    candidates.sort(key=_modified_at, reverse=True)
    removed: list[Path] = []
    for path in candidates[keep:]:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def _normalized(path: Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _write_private_json(path: Path, data: dict[str, object]) -> None:
    payload = f"{json.dumps(data, sort_keys=True)}\n"
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _chmod_private(path)
    except Exception:
        with suppress(OSError):
            temp_path.unlink()
        raise
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _text_env(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
