from __future__ import annotations

import concurrent.futures
import os
import shutil
import sqlite3
import stat as stat_module
import sys
import time
from pathlib import Path

import pytest

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.indexing import sqlite as sqlite_index
from backlog_py.indexing.sqlite import SQLiteIndexError, _is_contention_error
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"

TASK_RELATIVE_PATH = "backlog/tasks/task-1 - Example-task.md"
ONE_HOUR_NS = 3_600_000_000_000


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _set_mtime_ns(path: Path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _indexed_source(source: str):
    from backlog_py.indexing.sqlite import IndexedTaskSource

    return IndexedTaskSource(
        bucket="tasks",
        relative_path=TASK_RELATIVE_PATH,
        source=source,
        committed_at=0.0,
    )


def _project(repo: Path) -> BacklogProject:
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _task_ids(repository: ReadOnlyRepository) -> list[str]:
    return [task.id for task in repository.list_tasks()]


def test_sqlite_index_preserves_read_repository_output(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))

    direct = ReadOnlyRepository(project, use_sqlite_index=False)
    indexed = ReadOnlyRepository(project, use_sqlite_index=True)

    assert _task_ids(indexed) == _task_ids(direct)
    assert [task.id for task in indexed.search_tasks("parser preservation")] == [
        task.id for task in direct.search_tasks("parser preservation")
    ]
    assert {
        status: [task.id for task in tasks]
        for status, tasks in indexed.board().items()
    } == {
        status: [task.id for task in tasks]
        for status, tasks in direct.board().items()
    }


def test_sqlite_index_is_disposable_and_rebuildable(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing.sqlite import index_path_for_project

    index_path = index_path_for_project(project)
    assert not index_path.exists()

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]
    assert index_path.exists()

    index_path.unlink()

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]
    assert index_path.exists()


def test_sqlite_index_invalidates_when_task_file_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _copy_fixture_repo(tmp_path)
    project = _project(repo)

    assert ReadOnlyRepository(project, use_sqlite_index=True).get_task("TASK-1").title == "Example task"

    task_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("title: Example task", "title: Updated task"),
        encoding="utf-8",
    )

    assert ReadOnlyRepository(project, use_sqlite_index=True).get_task("TASK-1").title == "Updated task"


def test_sqlite_index_recovers_from_corrupt_database(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing.sqlite import index_path_for_project

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]
    index_path = index_path_for_project(project)
    index_path.write_bytes(b"not a sqlite database")

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]


def test_read_repository_falls_back_to_markdown_when_sqlite_index_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing import sqlite as sqlite_index

    def fail_index(*_args, **_kwargs):
        raise sqlite_index.SQLiteIndexError("forced failure")

    monkeypatch.setattr(sqlite_index, "load_task_sources", fail_index)

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]


def test_sqlite_index_supports_concurrent_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))

    def read_ids() -> list[str]:
        return _task_ids(ReadOnlyRepository(project, use_sqlite_index=True))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: read_ids(), range(8)))

    assert results == [["TASK-1"]] * 8


def test_sqlite_index_losing_the_wal_switch_does_not_discard_a_live_database(tmp_path, monkeypatch):
    # Readers racing on a fresh index lose the WAL journal-mode switch with
    # SQLITE_BUSY. Treating that as corruption deletes a database that other
    # readers still have open, which cascades into malformed-image errors and,
    # once the memory-mapped -shm sidecar goes with it, a SIGBUS crash.
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing import sqlite as sqlite_index

    rebuilds: list[str] = []

    def rebuild():
        rebuilds.append("rebuilt")
        return [_indexed_source("cached")]

    def load():
        return sqlite_index.load_task_sources(
            project,
            include_active_branch_snapshots=False,
            rebuild=rebuild,
        )

    assert [source.source for source in load()] == ["cached"]
    index_path = sqlite_index.index_path_for_project(project)
    rebuilds.clear()

    # Put the database back into rollback-journal mode so the next open retries
    # the WAL switch, then hold a read lock so that switch loses the race.
    reset = sqlite3.connect(index_path, isolation_level=None)
    try:
        reset.execute("PRAGMA journal_mode=DELETE")
    finally:
        reset.close()

    discards: list[Path] = []
    real_discard = sqlite_index._discard_index
    def tracking_discard(path: Path) -> None:
        discards.append(Path(path))
        real_discard(path)

    monkeypatch.setattr(sqlite_index, "_discard_index", tracking_discard)

    blocker = sqlite3.connect(index_path, isolation_level=None)
    try:
        blocker.execute("BEGIN DEFERRED")
        blocker.execute("SELECT count(*) FROM task_sources").fetchone()
        started = time.monotonic()
        sources = load()
        elapsed = time.monotonic() - started
    finally:
        blocker.close()

    # The switch needs an exclusive lock the blocker is holding: waiting out the
    # 30s busy timeout for an optional optimization would stall every read.
    assert elapsed < 5
    assert discards == []
    assert rebuilds == []
    assert [source.source for source in sources] == ["cached"]


def test_sqlite_index_reset_keeps_sidecars_of_open_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing import sqlite as sqlite_index

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]
    index_path = sqlite_index.index_path_for_project(project)

    # Stand in for a reader that has the index open: SQLite has its -shm sidecar
    # memory mapped, and unlinking that mapping kills the reader with SIGBUS.
    reader = sqlite3.connect(index_path, isolation_level=None)
    try:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("SELECT count(*) FROM task_sources").fetchone()
        sidecars = [
            index_path.with_name(f"{index_path.name}-wal"),
            index_path.with_name(f"{index_path.name}-shm"),
        ]
        present_before = [path.name for path in sidecars if path.exists()]
        assert present_before

        sqlite_index._discard_index(index_path)

        assert not index_path.exists()
        assert [path.name for path in sidecars if path.exists()] == present_before
        assert reader.execute("SELECT count(*) FROM task_sources").fetchone()[0] == 1
    finally:
        reader.close()


def test_sqlite_index_fingerprint_tracks_config_and_backlog_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _copy_fixture_repo(tmp_path)
    project = _project(repo)
    from backlog_py.indexing.sqlite import build_project_fingerprint

    original = build_project_fingerprint(project, include_active_branch_snapshots=True)

    project.config_path.write_text(
        project.config_path.read_text(encoding="utf-8").replace(
            "checkActiveBranches: false",
            "checkActiveBranches: true\nactiveBranchDays: 14",
        ),
        encoding="utf-8",
    )
    config_changed = build_project_fingerprint(_project(repo), include_active_branch_snapshots=True)
    assert config_changed != original

    root_config = repo / "backlog.config.yml"
    root_config.write_text(
        "projectName: basic-fixture\nbacklogDirectory: .backlog\ncheckActiveBranches: false\n",
        encoding="utf-8",
    )
    (repo / ".backlog" / "tasks").mkdir(parents=True)
    backlog_changed = build_project_fingerprint(_project(repo), include_active_branch_snapshots=True)
    assert backlog_changed != config_changed


def test_sqlite_index_rebuild_runs_without_holding_the_write_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing import sqlite as sqlite_index

    index_path = sqlite_index.index_path_for_project(project)
    probe: list[str] = []

    def rebuild():
        # A rebuild reads every task file and shells out to git; while that
        # happens no writer may be locked out of the index database.
        connection = sqlite3.connect(index_path, timeout=0.25, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS lock_probe (id INTEGER PRIMARY KEY)")
            connection.execute("COMMIT")
            probe.append("acquired")
        except sqlite3.OperationalError as exc:
            probe.append(f"blocked: {exc}")
        finally:
            connection.close()
        return [_indexed_source("rebuilt")]

    sources = sqlite_index.load_task_sources(
        project,
        include_active_branch_snapshots=False,
        rebuild=rebuild,
    )

    assert probe == ["acquired"]
    assert [source.source for source in sources] == ["rebuilt"]


def test_sqlite_index_warm_read_does_not_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing import sqlite as sqlite_index

    rebuilds: list[str] = []

    def rebuild():
        rebuilds.append("rebuilt")
        return [_indexed_source("cached")]

    def load():
        return sqlite_index.load_task_sources(
            project,
            include_active_branch_snapshots=False,
            rebuild=rebuild,
        )

    assert [source.source for source in load()] == ["cached"]
    assert [source.source for source in load()] == ["cached"]
    assert rebuilds == ["rebuilt"]


def test_sqlite_index_does_not_cache_a_rebuild_that_raced_a_task_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _copy_fixture_repo(tmp_path)
    project = _project(repo)
    from backlog_py.indexing import sqlite as sqlite_index

    task_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    original_text = task_path.read_text(encoding="utf-8")
    settled_ns = time.time_ns() - ONE_HOUR_NS
    _set_mtime_ns(task_path, settled_ns)
    raced_text = f"{original_text}\nA raced edit that also changes the file size.\n"

    def racing_rebuild():
        # Simulate the tree changing underneath a rebuild that reads Markdown
        # outside of the index write transaction.
        if task_path.read_text(encoding="utf-8") != raced_text:
            task_path.write_text(raced_text, encoding="utf-8")
        return [_indexed_source(raced_text)]

    first = sqlite_index.load_task_sources(
        project,
        include_active_branch_snapshots=False,
        rebuild=racing_rebuild,
    )
    assert [source.source for source in first] == [raced_text]

    # Restore the exact state (content, size and mtime) that the pre-rebuild
    # fingerprint described. A cache entry stored under that fingerprint would
    # now be served even though it never described this state.
    task_path.write_text(original_text, encoding="utf-8")
    _set_mtime_ns(task_path, settled_ns)

    rebuilds: list[str] = []

    def current_rebuild():
        rebuilds.append("rebuilt")
        return [_indexed_source(original_text)]

    second = sqlite_index.load_task_sources(
        project,
        include_active_branch_snapshots=False,
        rebuild=current_rebuild,
    )

    assert rebuilds == ["rebuilt"]
    assert [source.source for source in second] == [original_text]


def test_sqlite_index_fingerprint_does_not_hash_settled_task_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _copy_fixture_repo(tmp_path)
    project = _project(repo)
    from backlog_py.indexing import sqlite as sqlite_index

    settled_ns = time.time_ns() - ONE_HOUR_NS
    task_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    for path in (task_path, project.config_path):
        _set_mtime_ns(path, settled_ns)

    hashed: list[Path] = []
    real_sha256 = sqlite_index._sha256

    def tracking_sha256(path: Path) -> str:
        hashed.append(Path(path))
        return real_sha256(path)

    monkeypatch.setattr(sqlite_index, "_sha256", tracking_sha256)

    fingerprint = sqlite_index.build_project_fingerprint(
        project,
        include_active_branch_snapshots=False,
    )

    assert hashed == []
    assert "task-1 - Example-task.md" in fingerprint


def test_sqlite_index_fingerprint_detects_same_size_edit_with_preserved_mtime(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    repo = _copy_fixture_repo(tmp_path)
    project = _project(repo)
    from backlog_py.indexing.sqlite import build_project_fingerprint

    task_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    # A task file written moments ago: mtime granularity cannot prove freshness.
    fresh_ns = time.time_ns()
    _set_mtime_ns(task_path, fresh_ns)
    original = build_project_fingerprint(project, include_active_branch_snapshots=False)

    text = task_path.read_text(encoding="utf-8")
    edited = text.replace("title: Example task", "title: Updated task")
    assert len(edited) == len(text)
    assert edited != text
    task_path.write_text(edited, encoding="utf-8")
    _set_mtime_ns(task_path, fresh_ns)
    assert task_path.stat().st_size == len(text.encode("utf-8"))

    assert build_project_fingerprint(project, include_active_branch_snapshots=False) != original


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes are not enforced on Windows")
def test_sqlite_index_database_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    from backlog_py.indexing.sqlite import index_path_for_project

    assert _task_ids(ReadOnlyRepository(project, use_sqlite_index=True)) == ["TASK-1"]

    index_path = index_path_for_project(project)
    assert stat_module.S_IMODE(index_path.stat().st_mode) == 0o600
    assert stat_module.S_IMODE(index_path.parent.stat().st_mode) == 0o700
    for sidecar in (
        index_path.with_name(f"{index_path.name}-wal"),
        index_path.with_name(f"{index_path.name}-shm"),
    ):
        if sidecar.exists():
            assert stat_module.S_IMODE(sidecar.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# Error classification: contention retries, a broken index self-heals
# ---------------------------------------------------------------------------


def _operational_error(message: str, code: int) -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = code
    return exc


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_operational_error("database is locked", sqlite3.SQLITE_BUSY), True),
        # Extended codes carry the primary code in the low byte.
        (_operational_error("snapshot busy", sqlite3.SQLITE_BUSY | (2 << 8)), True),
        (_operational_error("database table is locked", sqlite3.SQLITE_LOCKED), True),
        (_operational_error("unable to open database file", sqlite3.SQLITE_CANTOPEN), False),
        (_operational_error("attempt to write a readonly database", sqlite3.SQLITE_READONLY), False),
        (sqlite3.DatabaseError("file is not a database"), False),
        (OSError("boom"), False),
        (SQLiteIndexError("boom"), False),
    ],
)
def test_only_lock_contention_counts_as_transient(exc, expected):
    """`OperationalError` is far broader than contention.

    "unable to open database file" and "attempt to write a readonly database"
    arrive as `OperationalError` too. Retrying those is pointless and, because
    the retry path never discards the file, a persistently unusable index used
    to strand every reader on the Markdown fallback with no way back.
    """
    assert _is_contention_error(exc) is expected


def test_a_persistently_broken_index_is_discarded_and_rebuilt(tmp_path, monkeypatch):
    """A non-contention failure must self-heal rather than fall back forever."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    discarded: list[Path] = []
    attempts = {"count": 0}

    real_discard = sqlite_index._discard_index

    def counting_discard(path):
        discarded.append(path)
        return real_discard(path)

    def failing_then_working(path, *, fingerprint_factory, rebuild):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _operational_error("unable to open database file", sqlite3.SQLITE_CANTOPEN)
        return []

    monkeypatch.setattr(sqlite_index, "_discard_index", counting_discard)
    monkeypatch.setattr(sqlite_index, "_load_task_sources", failing_then_working)

    sqlite_index.load_task_sources(project, include_active_branch_snapshots=False, rebuild=lambda: [])

    assert discarded, "a broken index was never discarded, so readers could never recover"
    assert attempts["count"] >= 2, "the index was not rebuilt after being discarded"


def test_contention_retries_without_discarding_the_database(tmp_path, monkeypatch):
    """Deleting a merely-busy database would pull it out from under live readers."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project = _project(_copy_fixture_repo(tmp_path))
    discarded: list[Path] = []
    attempts = {"count": 0}

    def busy_then_working(path, *, fingerprint_factory, rebuild):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _operational_error("database is locked", sqlite3.SQLITE_BUSY)
        return []

    monkeypatch.setattr(sqlite_index, "_discard_index", lambda path: discarded.append(path))
    monkeypatch.setattr(sqlite_index, "_load_task_sources", busy_then_working)

    sqlite_index.load_task_sources(project, include_active_branch_snapshots=False, rebuild=lambda: [])

    assert discarded == [], "a busy database was deleted out from under its readers"
    assert attempts["count"] == 2
