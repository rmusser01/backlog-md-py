from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.git import read_index_git_freshness
from backlog_py.runtime.state import resolve_state_dir


SCHEMA_VERSION = 2

# A rebuild reads Markdown outside of the index write transaction, so the tree
# can move underneath it. The fingerprint is re-taken afterwards and the rebuild
# is retried when it moved; a tree that keeps changing is served fresh but
# uncached instead of looping forever.
MAX_REBUILD_ATTEMPTS = 3

# Freshness is decided from st_size + st_mtime_ns, which costs one stat() per
# task file instead of a full read. That is inconclusive for a file whose mtime
# is barely older than "now": filesystem timestamp granularity can be as coarse
# as one second (and utilities such as `git checkout` restore mtimes), so a
# same-size edit applied within the same tick would be invisible. Files inside
# this window therefore fall back to a SHA-256 content check.
#
# Tradeoff: a same-size edit that also restores an mtime older than the window
# (deliberate `os.utime` backdating) is not detected. That is the same exposure
# git's own stat cache accepts, and the index is disposable - deleting it, or
# any ordinary edit, restores correctness.
RACY_MTIME_WINDOW_NS = 2_000_000_000

# How long a statement waits for a lock held by another reader or writer, and
# the much shorter budget for the optional WAL journal-mode switch.
BUSY_TIMEOUT_MS = 30_000
WAL_SWITCH_TIMEOUT_MS = 250


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
    now_ns = time.time_ns()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_root": str(project.root.resolve()),
        "backlog_dir": str(project.backlog_dir.resolve()),
        "config_path": str(project.config_path.resolve()),
        "config": _file_signature(project.root, project.config_path, now_ns=now_ns),
        "include_active_branch_snapshots": include_active_branch_snapshots,
        "git": read_index_git_freshness(project) if include_active_branch_snapshots else None,
        "settings": {
            "remote_operations": project.config.remote_operations,
            "check_active_branches": project.config.check_active_branches,
            "active_branch_days": project.config.active_branch_days,
        },
        "task_files": _task_file_signatures(project, now_ns=now_ns),
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

    def fingerprint_factory() -> str:
        return build_project_fingerprint(
            project,
            include_active_branch_snapshots=include_active_branch_snapshots,
        )

    try:
        return _load_task_sources(path, fingerprint_factory=fingerprint_factory, rebuild=rebuild)
    except (OSError, sqlite3.DatabaseError, SQLiteIndexError) as exc:
        if _is_contention_error(exc):
            # Contention, not corruption (a busy database, or a peer that reset
            # it between our schema check and our query). Deleting the database
            # here would pull it out from under the readers we are contending
            # with, so retry against whatever they left behind instead.
            try:
                return _load_task_sources(path, fingerprint_factory=fingerprint_factory, rebuild=rebuild)
            except (OSError, sqlite3.DatabaseError, SQLiteIndexError) as retry_exc:
                raise SQLiteIndexError(f"Unable to read SQLite read index: {path}") from retry_exc
        try:
            _discard_index(path)
        except OSError:
            raise SQLiteIndexError(f"Unable to reset SQLite read index: {path}") from exc
        try:
            return _load_task_sources(path, fingerprint_factory=fingerprint_factory, rebuild=rebuild)
        except (OSError, sqlite3.DatabaseError, SQLiteIndexError) as retry_exc:
            raise SQLiteIndexError(f"Unable to rebuild SQLite read index: {path}") from retry_exc


# SQLite's primary result codes for "somebody else holds the lock". Extended
# codes pack the primary code into the low byte (SQLITE_BUSY_SNAPSHOT is
# 5 | 2<<8), so classification masks it back out.
_CONTENTION_ERROR_CODES = frozenset((sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))
_SQLITE_PRIMARY_CODE_MASK = 0xFF


def _is_contention_error(exc: BaseException) -> bool:
    """True only for a transient lock conflict, not for a broken index.

    ``sqlite3.OperationalError`` is far broader than contention: it is also what
    "unable to open database file" and "attempt to write a readonly database"
    arrive as. Retrying those is pointless - the second attempt fails the same
    way - and, because the retry path never discards the file, a persistently
    unusable index used to strand every reader on the Markdown fallback with no
    way back. Only the primary result code separates the two cases, so the class
    alone is not enough to decide.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if not isinstance(code, int):
        return False
    return (code & _SQLITE_PRIMARY_CODE_MASK) in _CONTENTION_ERROR_CODES


def _load_task_sources(
    path: Path,
    *,
    fingerprint_factory: Callable[[], str],
    rebuild: Callable[[], Sequence[IndexedTaskSource]],
) -> list[IndexedTaskSource]:
    _prepare_index_file(path)
    try:
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise SQLiteIndexError(f"Unable to open SQLite read index: {path}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        _enable_wal(connection)
        _ensure_schema(connection)

        fingerprint = fingerprint_factory()
        cached = _read_cached_task_sources(connection, fingerprint=fingerprint)
        if cached is not None:
            return cached

        # Rebuilding reads and parses every task file and shells out to git, so
        # it runs with no transaction open: holding a write lock here would
        # stall every other reader of this index for the whole rebuild.
        for _attempt in range(MAX_REBUILD_ATTEMPTS):
            sources = list(rebuild())
            # The tree was unlocked while we read it, so the fingerprint is
            # re-taken. Only when it still matches do the rebuilt rows actually
            # describe the state the fingerprint names, which is what makes it
            # safe to publish them under that fingerprint.
            revalidated = fingerprint_factory()
            if revalidated == fingerprint:
                _publish_task_sources(connection, fingerprint=fingerprint, sources=sources)
                return sources
            fingerprint = revalidated
            # Somebody else may already have indexed the state we raced into.
            cached = _read_cached_task_sources(connection, fingerprint=fingerprint)
            if cached is not None:
                return cached
        # The tree keeps moving; serve what we read (exactly what a direct
        # Markdown read would have returned) without caching a snapshot that no
        # fingerprint truthfully describes.
        return sources
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Switch the database to WAL, tolerating a lost race for the switch."""
    # journal_mode lives in the database file, not the connection: whichever
    # connection wins the race sets it for everybody, and a connection that
    # loses still reads and writes correctly. So a busy database here is not a
    # broken index (treating it as one used to delete a database that other
    # readers had open), and it is not worth the full busy timeout either - the
    # switch needs an exclusive lock, so any concurrent reader would otherwise
    # stall this read for 30 seconds before the harmless failure.
    connection.execute(f"PRAGMA busy_timeout={WAL_SWITCH_TIMEOUT_MS}")
    try:
        with suppress(sqlite3.OperationalError):
            connection.execute("PRAGMA journal_mode=WAL")
    finally:
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


def _read_cached_task_sources(
    connection: sqlite3.Connection,
    *,
    fingerprint: str,
) -> list[IndexedTaskSource] | None:
    """Return cached sources when the index matches the fingerprint."""
    # A deferred (read-only) transaction keeps the fingerprint check and the row
    # read consistent without ever taking the write lock.
    connection.execute("BEGIN DEFERRED")
    try:
        if _metadata_value(connection, "fingerprint") != fingerprint:
            return None
        return _read_task_sources(connection)
    finally:
        connection.commit()


def _publish_task_sources(
    connection: sqlite3.Connection,
    *,
    fingerprint: str,
    sources: Sequence[IndexedTaskSource],
) -> None:
    """Commit rebuilt sources in a short write transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        _replace_task_sources(connection, fingerprint=fingerprint, sources=sources)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _ensure_schema(connection: sqlite3.Connection) -> None:
    if _schema_is_current(connection):
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
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
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _schema_is_current(connection: sqlite3.Connection) -> bool:
    """Return whether the schema is already usable, without locking for writes."""
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('metadata', 'task_sources')"
        ).fetchall()
    }
    if tables != {"metadata", "task_sources"}:
        return False
    schema_value = _metadata_value(connection, "schema_version")
    if schema_value is None:
        return False
    if schema_value != str(SCHEMA_VERSION):
        raise SQLiteIndexError("SQLite read index schema version changed")
    return True


def _prepare_index_file(path: Path) -> None:
    """Create the index directory and database file with private permissions."""
    # sqlite3.connect() would create the database with the process umask
    # (verified 0o644), and every task's full Markdown source lives in it, so
    # the file is pre-created privately before SQLite ever opens it. SQLite
    # copies the database file's mode onto its -wal/-shm sidecars.
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(directory, 0o700)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise SQLiteIndexError(f"Unable to create SQLite read index: {path}") from exc
    os.close(descriptor)
    _restrict_permissions(path, 0o600)


def _restrict_permissions(path: Path, mode: int) -> None:
    # Mirrors runtime.state._chmod_private: umask, an inherited directory mode
    # or a pre-existing file can all leave the target too permissive, and a
    # filesystem that rejects chmod must not break the index.
    with suppress(OSError, NotImplementedError):
        path.chmod(mode)


def _discard_index(path: Path) -> None:
    """Delete a database that could not be used."""
    # Only the database file. SQLite memory-maps the -shm sidecar, and unlinking
    # it while another reader has the database open crashes that reader with
    # SIGBUS; the sidecars are also what makes SQLite's own cross-connection
    # locking work. SQLite removes them itself when the last connection closes.
    path.unlink(missing_ok=True)


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


def _task_file_signatures(project: BacklogProject, *, now_ns: int) -> list[dict[str, object]]:
    signatures: list[dict[str, object]] = []
    for bucket in ("tasks", "completed"):
        directory = project.backlog_dir / bucket
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            signatures.append(_file_signature(project.root, path, now_ns=now_ns))
    return signatures


def _file_signature(root: Path, path: Path, *, now_ns: int) -> dict[str, object]:
    try:
        stat = path.stat()
        payload: dict[str, object] = {
            "relative_path": path.relative_to(root).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        if _is_racy_mtime(stat.st_mtime_ns, now_ns=now_ns):
            # Recently touched: stat alone cannot rule out a same-size edit
            # landing in the same timestamp tick, so pay for the content read.
            payload["sha256"] = _sha256(path)
    except (OSError, ValueError):
        return {
            "relative_path": str(path),
            "missing": True,
        }
    return payload


def _is_racy_mtime(mtime_ns: int, *, now_ns: int) -> bool:
    # A future mtime (clock skew on a network filesystem) is racy as well, so
    # compare the distance rather than only the past side of the window.
    return abs(now_ns - mtime_ns) < RACY_MTIME_WINDOW_NS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
