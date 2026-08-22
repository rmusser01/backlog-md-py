from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.git import read_index_git_freshness
from backlog_py.runtime.state import resolve_state_dir


SCHEMA_VERSION = 2


class SQLiteIndexError(RuntimeError):
    """Raised when the disposable SQLite read index cannot be used."""


@dataclass(frozen=True)
class IndexedTaskSource:
    """Task markdown source loaded from the disposable read index."""

    bucket: str
    relative_path: str
    source: str


def index_path_for_project(project: BacklogProject) -> Path:
    """Return the disposable SQLite index path for a Backlog project."""
    identity = "\0".join(
        [
            str(project.root.resolve()),
            str(project.backlog_dir.resolve()),
            str(project.config_path.resolve()),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return resolve_state_dir() / "indexes" / f"{digest}.sqlite3"


def build_project_fingerprint(
    project: BacklogProject,
    *,
    include_active_branch_snapshots: bool,
) -> str:
    """Return a stable fingerprint for all inputs that make the index valid."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(project.root.resolve()),
        "backlog_dir": str(project.backlog_dir.resolve()),
        "config_path": str(project.config_path.resolve()),
        "config": _file_signature(project.root, project.config_path),
        "include_active_branch_snapshots": include_active_branch_snapshots,
        "git": read_index_git_freshness(project) if include_active_branch_snapshots else None,
        "settings": {
            "remote_operations": project.config.remote_operations,
            "check_active_branches": project.config.check_active_branches,
            "active_branch_days": project.config.active_branch_days,
        },
        "task_files": _task_file_signatures(project),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_task_sources(
    project: BacklogProject,
    *,
    include_active_branch_snapshots: bool,
    rebuild: Callable[[], Sequence[IndexedTaskSource]],
) -> list[IndexedTaskSource]:
    """Load task sources from SQLite, rebuilding from Markdown when stale."""
    path = index_path_for_project(project)
    fingerprint = build_project_fingerprint(
        project,
        include_active_branch_snapshots=include_active_branch_snapshots,
    )
    try:
        return _load_task_sources(path, fingerprint=fingerprint, rebuild=rebuild)
    except (OSError, sqlite3.DatabaseError, SQLiteIndexError) as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise SQLiteIndexError(f"Unable to reset SQLite read index: {path}") from exc
        try:
            return _load_task_sources(path, fingerprint=fingerprint, rebuild=rebuild)
        except (OSError, sqlite3.DatabaseError, SQLiteIndexError) as retry_exc:
            raise SQLiteIndexError(f"Unable to rebuild SQLite read index: {path}") from retry_exc


def _load_task_sources(
    path: Path,
    *,
    fingerprint: str,
    rebuild: Callable[[], Sequence[IndexedTaskSource]],
) -> list[IndexedTaskSource]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path, timeout=30)
    except sqlite3.Error as exc:
        raise SQLiteIndexError(f"Unable to open SQLite read index: {path}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        _ensure_schema(connection)
        if _metadata_value(connection, "fingerprint") != fingerprint:
            sources = list(rebuild())
            _replace_task_sources(connection, fingerprint=fingerprint, sources=sources)
        else:
            sources = _read_task_sources(connection)
        connection.commit()
        return sources
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_sources (
            ordinal INTEGER PRIMARY KEY,
            bucket TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            source TEXT NOT NULL
        )
        """
    )
    schema_value = _metadata_value(connection, "schema_version")
    if schema_value not in {None, str(SCHEMA_VERSION)}:
        raise SQLiteIndexError("SQLite read index schema version changed")
    connection.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


def _metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _replace_task_sources(
    connection: sqlite3.Connection,
    *,
    fingerprint: str,
    sources: Sequence[IndexedTaskSource],
) -> None:
    connection.execute("DELETE FROM task_sources")
    connection.executemany(
        """
        INSERT INTO task_sources (ordinal, bucket, relative_path, source)
        VALUES (?, ?, ?, ?)
        """,
        [
            (index, source.bucket, source.relative_path, source.source)
            for index, source in enumerate(sources)
        ],
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("fingerprint", fingerprint),
    )


def _read_task_sources(connection: sqlite3.Connection) -> list[IndexedTaskSource]:
    rows = connection.execute(
        "SELECT bucket, relative_path, source FROM task_sources ORDER BY ordinal"
    ).fetchall()
    return [
        IndexedTaskSource(
            bucket=str(row["bucket"]),
            relative_path=str(row["relative_path"]),
            source=str(row["source"]),
        )
        for row in rows
    ]


def _task_file_signatures(project: BacklogProject) -> list[dict[str, object]]:
    signatures: list[dict[str, object]] = []
    for bucket in ("tasks", "completed"):
        directory = project.backlog_dir / bucket
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            signatures.append(_file_signature(project.root, path))
    return signatures


def _file_signature(root: Path, path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
        payload = {
            "relative_path": path.relative_to(root).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256(path),
        }
    except (OSError, ValueError):
        payload = {
            "relative_path": str(path),
            "missing": True,
        }
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
