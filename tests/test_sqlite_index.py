from __future__ import annotations

import concurrent.futures
import shutil
from pathlib import Path

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


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
