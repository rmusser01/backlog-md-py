"""Direct tests for the batched git snapshot helpers.

These cover the path-space and quoting seams: `git status`/`git log` report
paths relative to the *repository* root and C-quote non-ASCII names, while the
caller builds paths relative to the *project* root.

Every git process started here -- by the helpers below *and* by the library
itself, which copies ``os.environ`` -- runs with system and global config
disabled and an explicit identity. Without that, an ambient ``~/.gitconfig``
(``commit.gpgsign``, ``core.autocrlf``, an ``init.templateDir`` that installs a
``pre-commit`` hook) makes these tests fail for reasons unrelated to the code.
"""
from __future__ import annotations

import dataclasses
import os
import posixpath
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import inf
from pathlib import Path
from threading import Event

import pytest

from backlog_py.core.init import init_project
from backlog_py.core.repository import MutableRepository
from backlog_py.runtime import git as git_module
from backlog_py.runtime.git import current_task_snapshot_timestamps
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.storage.project import discover_project
from backlog_py.tui.data import LocalBoardDataSource
from backlog_py.tui.models import CreateTaskInput
from backlog_py.runtime.scan_cache import ProjectScanCache

# Config sources git consults implicitly. Each of these can change command
# output (core.autocrlf, core.quotePath), block a commit (commit.gpgsign), or
# run arbitrary code (init.templateDir, core.hooksPath).
_GIT_ENVIRONMENT_PINS = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "backlog-test",
    "GIT_AUTHOR_EMAIL": "backlog-test@example.invalid",
    "GIT_COMMITTER_NAME": "backlog-test",
    "GIT_COMMITTER_EMAIL": "backlog-test@example.invalid",
}
# Ambient repository state that would redirect commands away from the tmp repo.
_INHERITED_GIT_STATE = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG",
    "GIT_CONFIG_SYSTEM",
    "GIT_CEILING_DIRECTORIES",
)


@pytest.fixture(autouse=True)
def pinned_git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every git invocation in this module from the machine's git config."""
    home = tmp_path / "git-home"
    (home / "xdg").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    for key, value in _GIT_ENVIRONMENT_PINS.items():
        monkeypatch.setenv(key, value)
    for key in _INHERITED_GIT_STATE:
        monkeypatch.delenv(key, raising=False)


def _git(repo: Path, *args: str, **date_overrides: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **date_overrides},
    )


def _git_init(repo: Path) -> None:
    # Pinning the initial branch keeps the tests independent of init.defaultBranch.
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")


def _commit(repo: Path, message: str, when: int | None = None) -> None:
    _git(repo, "add", "-A")
    dates = {}
    if when is not None:
        dates = {"GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000"}
    _git(repo, "commit", "-qm", message, **dates)


def _task(project, name: str) -> Path:
    path = project.backlog_dir / "tasks" / name
    path.write_text(
        "---\nid: TASK-1\ntitle: T\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n## Description\n\nx\n",
        encoding="utf-8",
    )
    return path


def test_timestamps_resolve_when_project_root_is_below_the_git_root(tmp_path: Path) -> None:
    """git reports repo-relative paths; the project may be a subdirectory."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo / "pkg").project
    path = _task(project, "task-1 - nested.md")
    _commit(repo, "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a committed, clean task in a nested project reported no timestamp"


def test_timestamps_resolve_when_project_directory_name_has_leading_whitespace(tmp_path: Path) -> None:
    """`rev-parse --show-prefix` must be trimmed of its newline only.

    Stripping all whitespace deletes real path bytes, so every lookup misses and
    every task in the project silently reports ``inf``.
    """
    repo = tmp_path / "repo"
    nested = repo / " pkg"
    nested.mkdir(parents=True)
    _git_init(repo)
    project = init_project(nested).project
    path = _task(project, "task-1 - spaced.md")
    _commit(repo, "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a leading space in the project directory name broke the repo prefix"


def test_timestamps_resolve_for_non_ascii_filenames(tmp_path: Path) -> None:
    """core.quotePath C-quotes non-ASCII names in git log output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - café.md")
    _commit(repo, "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a non-ASCII filename reported no timestamp"


def test_non_utf8_path_in_git_output_does_not_crash_the_scan(tmp_path: Path) -> None:
    """With core.quotePath=false git emits raw bytes, which need not be UTF-8.

    The bad name is injected through the index so the test runs on filesystems
    (APFS) that refuse to create non-UTF-8 names at all.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    good = _task(project, "task-1 - ascii.md")

    _git(repo, "add", "-A")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"broken name\n",
        check=True,
        capture_output=True,
        env=os.environ.copy(),
    ).stdout.decode().strip()
    tasks_dir = project.backlog_dir.relative_to(repo).as_posix() + "/tasks"
    bad_name = os.fsencode(tasks_dir) + b"/task-2 - caf\xe9.md"
    # Staged directly into the index: `git add -A` would drop it again, because
    # the name cannot exist in the worktree on every filesystem.
    subprocess.run(
        ["git", "update-index", "--add", "-z", "--index-info"],
        cwd=repo,
        input=b"100644 " + blob.encode() + b"\t" + bad_name + b"\0",
        check=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    _git(repo, "commit", "-qm", "seed")

    timestamps = current_task_snapshot_timestamps(project, [good])

    assert timestamps[good] != inf, "an undecodable sibling filename broke the whole scan"
    # The undecodable name must survive as the same string Python builds from a
    # filesystem path, i.e. os.fsdecode -> surrogate escapes, not a decode error.
    dirty = git_module._dirty_relative_paths(repo, [tasks_dir])
    assert dirty is not None
    assert os.fsdecode(bad_name) in dirty, "git bytes did not decode to the filesystem path string"


def test_non_utf8_filename_on_disk_resolves(tmp_path: Path) -> None:
    """End-to-end variant of the above; skipped where the filesystem forbids it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    raw = os.fsencode(str(project.backlog_dir / "tasks")) + b"/task-1 - caf\xe9.md"
    path = Path(os.fsdecode(raw))
    try:
        path.write_text("---\nid: TASK-1\ntitle: T\nstatus: To Do\n---\n\nx\n", encoding="utf-8")
    except OSError:
        pytest.skip("filesystem rejects non-UTF-8 filenames")
    _commit(repo, "seed")

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] != inf, "a non-UTF-8 filename reported no timestamp"


def test_dirty_task_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - dirty.md")
    _commit(repo, "seed")
    path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_untracked_task_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - untracked.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_outside_a_worktree_reports_no_timestamp(tmp_path: Path) -> None:
    project = init_project(tmp_path / "plain", no_git=True).project
    path = _task(project, "task-1 - plain.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_repository_without_commits_reports_no_timestamp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - fresh.md")

    assert current_task_snapshot_timestamps(project, [path])[path] == inf


def test_empty_input_is_handled(tmp_path: Path) -> None:
    project = init_project(tmp_path / "plain", no_git=True).project
    assert current_task_snapshot_timestamps(project, []) == {}


def test_evil_merge_reports_the_merge_commit_timestamp(tmp_path: Path) -> None:
    """`git log --name-only` prints nothing for a merge unless asked to.

    A file whose final content was produced during conflict resolution therefore
    picks up an older ancestor's timestamp instead of the merge's.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - merged.md")
    _commit(repo, "seed", when=1700000000)

    _git(repo, "checkout", "-q", "-b", "side")
    path.write_text(path.read_text(encoding="utf-8") + "\nside\n", encoding="utf-8")
    _commit(repo, "side edit", when=1700000100)

    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main edit", when=1700000200)

    _git(repo, "merge", "--no-ff", "--no-commit", "-q", "side")
    path.write_text(path.read_text(encoding="utf-8") + "\nresolved\n", encoding="utf-8")
    _commit(repo, "merge side", when=1700000300)

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert timestamps[path] == 1700000300, "merge-time edit reported an older ancestor's timestamp"


def test_worktree_probe_is_rechecked_after_its_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo created in a PARENT directory leaves no local `.git` marker.

    Keying the memo only on that marker pins the answer to ``False`` for the
    lifetime of the process, silently disabling auto-commit in a TUI or daemon.
    """
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    monkeypatch.setattr(git_module, "_CACHE_TTL_SECONDS", 0.0, raising=False)

    assert git_module._is_git_worktree(child) is False

    _git_init(parent)

    assert git_module._is_git_worktree(child) is True, "worktree probe served a stale cached answer"


def _branch_snapshot_repo(tmp_path: Path, task_count: int) -> object:
    """A project whose `feature` branch carries `task_count` task files."""
    from backlog_py.storage.project import discover_project

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo).project
    _task(project, "task-1 - seed.md")
    _commit(repo, "seed")

    _git(repo, "checkout", "-q", "-b", "feature")
    for index in range(2, task_count + 2):
        _task(project, f"task-{index} - branch.md")
    _commit(repo, "branch tasks")
    _git(repo, "checkout", "-q", "main")

    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _count_git_processes(monkeypatch: pytest.MonkeyPatch, project) -> int:
    calls = 0
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "run", counting_run)
    snapshots = git_module.list_active_branch_task_snapshots(project)
    assert snapshots, "no branch snapshots were loaded, so the count proves nothing"
    return calls


def test_branch_snapshots_do_not_spawn_git_per_task_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for #168.

    Reading one branch cost two subprocesses per task file (`git log` for the
    timestamp, `git show` for the content). On a real project that was ~304,000
    spawns for 67 branches and `overview` never finished.
    """
    small = _count_git_processes(monkeypatch, _branch_snapshot_repo(tmp_path / "small", 2))
    large = _count_git_processes(monkeypatch, _branch_snapshot_repo(tmp_path / "large", 25))

    assert small == large, f"git processes scale with task count ({small} for 2, {large} for 25)"


def test_branch_snapshots_still_return_content_and_timestamps(tmp_path: Path) -> None:
    project = _branch_snapshot_repo(tmp_path, 2)

    snapshots = git_module.list_active_branch_task_snapshots(project)

    assert {Path(snapshot.relative_path).name for snapshot in snapshots} >= {
        "task-2 - branch.md",
        "task-3 - branch.md",
    }
    for snapshot in snapshots:
        assert snapshot.source.startswith("---\n"), "task markdown was not read back intact"
        assert snapshot.committed_at > 0, "snapshot carries no commit timestamp"


@pytest.mark.parametrize("change", ["move", "delete", "age"])
@pytest.mark.parametrize("use_index", [False, True])
def test_warm_scan_tracks_active_branch_tips(tmp_path, monkeypatch, change, use_index):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(git_module, "datetime", Clock)
    monkeypatch.setenv("BACKLOG_PY_SQLITE_INDEX", str(int(use_index)))
    project = init_project(tmp_path / "repo").project
    _git_init(project.root)
    _commit(project.root, "seed")
    _git(project.root, "checkout", "--detach")
    task = _task(project, "task-1 - branch.md")
    when = int(now.timestamp())
    _commit(project.root, "initial branch task", when)
    _git(project.root, "branch", "active")
    task.write_text(task.read_text(encoding="utf-8").replace("title: T", "title: Changed"), encoding="utf-8")
    _commit(project.root, "updated branch task in the same second", when)
    _git(project.root, "tag", "next-tip")
    _git(project.root, "checkout", "main")
    _git(project.root, "pack-refs", "--all")
    cache = ProjectScanCache()

    first = cache.repository(project)
    assert [task.title for task in first.list_tasks()] == ["T"]
    assert cache.repository(project) is first

    if change == "move":
        _git(project.root, "update-ref", "refs/heads/active", "next-tip")
    elif change == "delete":
        _git(project.root, "branch", "-D", "active")
    else:
        now += timedelta(days=project.config.active_branch_days, seconds=1)

    expected_titles = ["Changed"] if change == "move" else []
    assert [task.title for task in cache.repository(project).list_tasks()] == expected_titles


def test_branch_snapshots_read_each_shared_blob_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branches share task files, so content is fetched per blob id, not per entry.

    On a real project this collapsed 143,080 (ref, path) entries into 2,546
    unique blobs, and the whole content read into 0.1s.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project_root = init_project(repo).project
    _task(project_root, "task-1 - shared.md")
    _commit(repo, "seed")
    for name in ("alpha", "beta", "gamma"):
        _git(repo, "checkout", "-q", "-b", name)
        _git(repo, "checkout", "-q", "main")
    config = project_root.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )

    from backlog_py.storage.project import discover_project

    project = discover_project(Path.cwd(), explicit_cwd=repo)
    requested: list[bytes] = []
    real_bytes = git_module._run_git_bytes

    def recording(work_dir, *args, stdin: bytes):
        requested.append(stdin)
        return real_bytes(work_dir, *args, stdin=stdin)

    monkeypatch.setattr(git_module, "_run_git_bytes", recording)
    snapshots = git_module.list_active_branch_task_snapshots(project)

    assert len(snapshots) == 3, "each branch should still contribute its own snapshot"
    assert len(requested) == 1, "content must be read in one cat-file process for the whole scan"
    assert len(requested[0].split()) == 1, "the shared blob was requested more than once"


def _other_checkouts_repo(tmp_path: Path):
    """main holds TASK-1; branch `other` commits TASK-2 and TASK-1.1; worktree `wt` has an uncommitted TASK-3."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    MutableRepository(project).create_task(title="one")
    _commit(repo, "seed")
    _git(repo, "checkout", "-qb", "other")
    MutableRepository(project).create_task(title="two")
    MutableRepository(project).create_task(title="child", parent_task_id="TASK-1")
    _commit(repo, "two")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt")
    (tmp_path / "wt" / "backlog" / "tasks" / "task-3 - three.md").write_text(
        "---\nid: TASK-3\ntitle: three\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n",
        encoding="utf-8",
    )
    return project


def test_create_skips_ids_other_branches_and_worktrees_use(tmp_path: Path) -> None:
    """`task list` shows those tasks and warns on a shared id, so create must not reuse one.

    TASK-3 is uncommitted in another worktree: on no ref, so only its files show it.
    """
    project = _other_checkouts_repo(tmp_path)

    assert MutableRepository(project).create_task(title="four").id == "TASK-4"
    assert MutableRepository(project).create_task(title="child", parent_task_id="TASK-1").id == "TASK-1.2"


@pytest.mark.parametrize("project_path", [Path("."), Path("packages/nested")])
def test_concurrent_task_creates_in_linked_worktrees_reserve_distinct_ids(tmp_path, monkeypatch, project_path):
    base = tmp_path / "repo"
    (base / project_path).mkdir(parents=True)
    _git_init(base)
    project = init_project(base / project_path).project
    MutableRepository(project).create_task(title="Seed")
    _commit(base, "seed")
    sibling = tmp_path / "linked"
    _git(base, "worktree", "add", "-qb", "linked", str(sibling))

    first_read = Event()
    second_read = Event()
    original = MutableRepository._allocation_reserved_ids

    def synchronize_after_read(repository):
        reserved = original(repository)
        if repository.project.root == project.root:
            first_read.set()
            second_read.wait(2)
        else:
            second_read.set()
        return reserved

    monkeypatch.setattr(MutableRepository, "_allocation_reserved_ids", synchronize_after_read)

    first_repository = MutableRepository(project)
    second_board = LocalBoardDataSource(discover_project(sibling / project_path))
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            with_project_write_lock, project, "task_create", lambda: first_repository.create_task(title="First task")
        )
        assert first_read.wait(5)
        second = workers.submit(second_board.create_task, CreateTaskInput(title="Second task"))
        assert first.result(timeout=10).id == "TASK-2"
        assert second.result(timeout=10).id == "TASK-3"


def test_create_skips_child_ids_with_different_padding_in_linked_worktrees(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git_init(root)
    project = init_project(root).project
    repository = MutableRepository(project)
    repository.create_task(title="Parent", task_id="TASK-009")
    _commit(root, "seed")
    sibling = tmp_path / "linked"
    _git(root, "worktree", "add", "-qb", "linked", str(sibling))
    sibling_repository = MutableRepository.from_path(sibling)
    sibling_repository.create_task(title="First child", task_id="TASK-9.1", parent_task_id="TASK-9")

    second_child = repository.create_task(title="Second child", parent_task_id="TASK-009")

    assert second_child.id == "TASK-009.2"


def test_create_allocates_from_this_checkout_only_when_active_branches_are_off(tmp_path: Path) -> None:
    project = _other_checkouts_repo(tmp_path)
    local_only = dataclasses.replace(
        project, config=dataclasses.replace(project.config, check_active_branches=False)
    )

    assert MutableRepository(local_only).create_task(title="four").id == "TASK-2"


def test_other_checkout_scan_uses_one_git_process_for_all_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _other_checkouts_repo(tmp_path)
    _git(project.root, "branch", "extra")
    calls: list[tuple[str, ...]] = []
    real_run, real_bytes = git_module._run_git, git_module._run_git_bytes

    def run(work_dir, *args, **kwargs):
        calls.append(args)
        return real_run(work_dir, *args, **kwargs)

    def batch(work_dir, *args, stdin: bytes):
        calls.append(args)
        return real_bytes(work_dir, *args, stdin=stdin)

    monkeypatch.setattr(git_module, "_run_git", run)
    monkeypatch.setattr(git_module, "_run_git_bytes", batch)
    names = git_module.other_checkout_task_file_names(project)

    assert any(n.startswith("task-2 ") for n in names), "branch-only task missing"
    assert any(n.startswith("task-3 ") for n in names), "uncommitted worktree task missing"
    assert not [c for c in calls if "ls-tree" in c], "a git ls-tree process was forked per ref"
    assert len([c for c in calls if "cat-file" in c]) == 1


def test_worktree_scan_falls_back_when_list_z_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git worktree list -z` needs git >= 2.36; the uncommitted-worktree half must survive older git."""
    project = _other_checkouts_repo(tmp_path)
    original = git_module._run_git

    def without_z(work_dir, *args, **kwargs):
        if args[:2] == ("worktree", "list") and "-z" in args:
            return subprocess.CompletedProcess(["git", *args], 129, "", "error: unknown switch `z'")
        return original(work_dir, *args, **kwargs)

    monkeypatch.setattr(git_module, "_run_git", without_z)

    assert MutableRepository(project).create_task(title="four").id == "TASK-4"


def _record_log_walks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    walks: list[tuple[str, ...]] = []
    real_run = git_module._run_git

    def recording(work_dir, *args, **kwargs):
        if "log" in args and "--raw" in args:
            walks.append(args)
        return real_run(work_dir, *args, **kwargs)

    monkeypatch.setattr(git_module, "_run_git", recording)
    return walks


def test_history_walk_is_reused_until_a_ref_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _branch_snapshot_repo(tmp_path, 2)
    walks = _record_log_walks(monkeypatch)

    first = git_module.list_active_branch_task_snapshots(project)
    assert len(walks) == 1
    assert git_module.list_active_branch_task_snapshots(project) == first
    assert len(walks) == 1, "history was walked again though no ref moved"

    repo = project.root
    _git(repo, "checkout", "-q", "feature")
    _task(project, "task-9 - more.md")
    _commit(repo, "more")
    _git(repo, "checkout", "-q", "main")

    assert len(git_module.list_active_branch_task_snapshots(project)) == len(first) + 1
    assert len(walks) == 2


def test_history_is_walked_only_for_ids_two_files_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit times only settle duplicate ids; a board without any needs no walk."""
    from backlog_py.core.repository import ReadOnlyRepository

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    _task(project, "task-1 - first.md")
    _commit(repo, "seed")
    walks = _record_log_walks(monkeypatch)

    assert [task.title for task in ReadOnlyRepository(project).list_tasks()] == ["T"]
    assert walks == []

    second = _task(project, "task-1 - second.md")  # the same id, TASK-1, in a second file
    _commit(repo, "duplicate")
    ReadOnlyRepository(project).list_tasks()
    assert len(walks) == 1

    # An uncommitted copy outranks every commit: git status settles it.
    second.write_text(second.read_text(encoding="utf-8").replace("title: T", "title: Edited"), encoding="utf-8")
    assert [task.title for task in ReadOnlyRepository(project).list_tasks()] == ["Edited"]
    assert len(walks) == 1, "an id an uncommitted file claims still walked the history"


def test_one_record_per_branch_version_keeps_the_tie_break_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On equal commit times the later copy wins, so a version kept once must
    stand where its later copy stood, not its first."""
    from backlog_py.core import repository as repository_module

    project = init_project(tmp_path / "plain", no_git=True).project
    path = project.backlog_dir.relative_to(project.root).as_posix() + "/tasks/task-99 - x.md"
    monkeypatch.setattr(
        repository_module,
        "list_active_branch_task_snapshots",
        lambda project: [
            git_module.GitTaskSnapshot(
                ref=ref,
                relative_path=path,
                source=f"---\nid: TASK-99\ntitle: {title}\nstatus: To Do\n---\n",
                committed_at=1.0,
            )
            for ref, title in (("a", "X"), ("b", "Y"), ("c", "X"))
        ],
    )

    tasks = repository_module.ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()

    assert [task.title for task in tasks] == ["X"]


def test_a_merge_does_not_make_the_versions_it_brings_in_newer(tmp_path: Path) -> None:
    """Merging main into a feature branch rewrites main's changed task files on
    that branch; the edit that wrote a version, not the merge, says how new it is."""
    from backlog_py.core.repository import ReadOnlyRepository

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace("check_active_branches: false", "check_active_branches: true"),
        encoding="utf-8",
    )
    start = int(datetime.now(timezone.utc).timestamp()) - 1000
    ours = _task(project, "task-1 - ours.md")
    _commit(repo, "ours", start + 100)
    _git(repo, "checkout", "-qb", "feature")
    theirs = _task(project, "task-1 - theirs.md")
    _commit(repo, "theirs", start + 200)
    _git(repo, "checkout", "-q", "main")
    ours.write_text(ours.read_text(encoding="utf-8").replace("title: T", "title: Ours edited"), encoding="utf-8")
    _commit(repo, "edit ours", start + 300)
    _git(repo, "checkout", "-q", "feature")
    theirs.write_text(theirs.read_text(encoding="utf-8").replace("title: T", "title: Theirs edited"), encoding="utf-8")
    _commit(repo, "edit theirs", start + 400)
    merged = f"@{start + 500} +0000"
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge main", "main", GIT_AUTHOR_DATE=merged, GIT_COMMITTER_DATE=merged)
    _git(repo, "checkout", "-q", "main")

    tasks = ReadOnlyRepository(discover_project(repo), refresh_remote_refs=False).list_tasks()

    assert [task.title for task in tasks] == ["Theirs edited"], "the merge outdated the later edit"


def test_a_reverted_version_is_as_new_as_the_revert(tmp_path: Path) -> None:
    from backlog_py.core.repository import ReadOnlyRepository

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    ours = _task(project, "task-1 - ours.md")
    _commit(repo, "ours", 1700000100)
    ours.write_text(ours.read_text(encoding="utf-8").replace("title: T", "title: Edited"), encoding="utf-8")
    _commit(repo, "edit ours", 1700000200)
    theirs = _task(project, "task-1 - theirs.md")
    theirs.write_text(theirs.read_text(encoding="utf-8").replace("title: T", "title: Theirs"), encoding="utf-8")
    _commit(repo, "theirs", 1700000300)
    _git(repo, "revert", "--no-edit", "HEAD~1", GIT_COMMITTER_DATE="@1700000400 +0000")

    tasks = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()

    assert [task.title for task in tasks] == ["T"], "the revert was dated by the version's first commit"


@pytest.mark.parametrize("backlog_dir", [":(top)weird", "."])
def test_branch_tasks_and_dates_resolve_for_any_backlog_directory(tmp_path: Path, backlog_dir: str) -> None:
    """A backlog named like pathspec magic, or the project root itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo, backlog_dir=backlog_dir).project
    path = _task(project, "task-2 - y.md")
    _commit(repo, "main backlog")
    _git(repo, "checkout", "-q", "-b", "feature/x")
    source = _task(project, "task-1 - x.md").read_text(encoding="utf-8")
    _commit(repo, "add task")
    _git(repo, "checkout", "-q", "main")

    snapshots = git_module.list_active_branch_task_snapshots(project)

    expected = posixpath.normpath(f"{backlog_dir}/tasks/task-1 - x.md")
    assert (expected, source) in [(snapshot.relative_path, snapshot.source) for snapshot in snapshots]
    assert current_task_snapshot_timestamps(project, [path])[path] != inf


@pytest.mark.parametrize("name", ['task-1 - "quoted".md', "task-1 - tab\there.md", "task-1 - line\nbreak.md"])
def test_dates_resolve_for_names_git_would_quote(tmp_path: Path, name: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, name)
    _commit(repo, "seed")

    assert current_task_snapshot_timestamps(project, [path])[path] != inf
