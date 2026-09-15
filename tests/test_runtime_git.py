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

import os
import subprocess
import time
from math import inf
from pathlib import Path

import pytest

from backlog_py.core.init import init_project
from backlog_py.runtime import git as git_module
from backlog_py.runtime.git import current_task_snapshot_timestamps

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


def test_log_falls_back_when_diff_merges_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--diff-merges` needs git >= 2.31; on older git it must not blank the scan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    project = init_project(repo).project
    path = _task(project, "task-1 - oldgit.md")
    _commit(repo, "seed")

    original = git_module._run_git
    rejected: list[tuple[str, ...]] = []

    def without_diff_merges(work_dir, *args, **kwargs):
        if any(arg.startswith("--diff-merges") for arg in args):
            rejected.append(args)
            return subprocess.CompletedProcess(
                ["git", *args], 129, "", "error: unknown option `diff-merges=first-parent'"
            )
        return original(work_dir, *args, **kwargs)

    monkeypatch.setattr(git_module, "_run_git", without_diff_merges)

    timestamps = current_task_snapshot_timestamps(project, [path])

    assert rejected, "the log walk no longer asks for merge diffs, so this guard tests nothing"
    assert timestamps[path] != inf, "an unsupported log option made every task report no timestamp"


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


def _recent(seconds_ago: int) -> int:
    """A commit date inside `active_branch_days`, so the branch is actually scanned."""
    return int(time.time()) - seconds_ago


def _branches_with_tasks(tmp_path: Path, branch_count: int) -> object:
    """A project with `branch_count` branches, each carrying its own extra task."""
    from backlog_py.storage.project import discover_project

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo).project
    _task(project, "task-1 - seed.md")
    _commit(repo, "seed", when=_recent(3600))

    for index in range(branch_count):
        name = f"feature-{index}"
        _git(repo, "checkout", "-q", "-b", name)
        _task(project, f"task-{index + 2} - {name}.md")
        _commit(repo, f"tasks for {name}", when=_recent(1800 - index))
        _git(repo, "checkout", "-q", "main")

    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _count_git_log_walks(monkeypatch: pytest.MonkeyPatch, project) -> int:
    walks = 0
    real_run_git = git_module._run_git

    def counting(work_dir, *args):
        nonlocal walks
        if "log" in args and "--name-only" not in args and "-1" not in args:
            walks += 1
        return real_run_git(work_dir, *args)

    monkeypatch.setattr(git_module, "_run_git", counting)
    assert git_module.list_active_branch_task_snapshots(project), "no snapshots loaded"
    return walks


def test_branch_snapshots_walk_history_once_regardless_of_ref_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for #170.

    History was walked once per ref, and branches share nearly all of their
    commits, so 67 refs re-traversed the same history 67 times: 46s of a 50.7s
    scan. One union walk over every ref costs ~1s and visits each commit once.
    """
    few = _count_git_log_walks(monkeypatch, _branches_with_tasks(tmp_path / "few", 2))
    many = _count_git_log_walks(monkeypatch, _branches_with_tasks(tmp_path / "many", 8))

    assert few == many == 1, f"history walks scale with ref count ({few} for 2 refs, {many} for 8)"


def test_newer_branch_content_wins_a_duplicate_id(tmp_path: Path) -> None:
    """The timestamp exists only to break id ties; that outcome must not drift."""
    from backlog_py.core.repository import ReadOnlyRepository
    from backlog_py.storage.project import discover_project

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo).project
    task_name = "task-1 - contested.md"
    path = project.backlog_dir / "tasks" / task_name

    def write(title: str) -> None:
        path.write_text(
            f"---\nid: TASK-1\ntitle: {title}\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
            "## Description\n\nx\n",
            encoding="utf-8",
        )

    write("Original")
    _commit(repo, "seed", when=_recent(3600))
    _git(repo, "checkout", "-q", "-b", "older")
    write("Older branch")
    _commit(repo, "older", when=_recent(2400))
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "newer")
    write("Newer branch")
    _commit(repo, "newer", when=_recent(1200))
    _git(repo, "checkout", "-q", "main")

    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )
    tasks = ReadOnlyRepository.from_path(repo).list_tasks()

    assert [task.title for task in tasks] == ["Newer branch"]


def test_branch_snapshots_fall_back_to_the_ref_tip_when_the_walk_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shallow clone or an old git can leave the walk empty; snapshots still rank."""
    project = _branches_with_tasks(tmp_path, 2)
    monkeypatch.setattr(git_module, "_blob_introduction_timestamps", lambda *args, **kwargs: {})

    snapshots = git_module.list_active_branch_task_snapshots(project)

    assert snapshots
    assert all(snapshot.committed_at > 0 for snapshot in snapshots), (
        "a snapshot with no timestamp sorts below everything and silently loses every tie"
    )


def test_identical_content_on_two_refs_resolves_to_one_timestamp(tmp_path: Path) -> None:
    """Recency is a property of the content, not of the branch carrying it.

    Two refs holding the same blob at the same path are the same version of the
    task, so they rank equally — and at the newest time that content was written
    anywhere, not at whenever each branch happened to touch the path.
    """
    from backlog_py.storage.project import discover_project

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo).project
    _task(project, "task-1 - seed.md")
    _commit(repo, "seed", when=_recent(3600))

    shared = "---\nid: TASK-2\ntitle: Shared\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\nx\n"
    path = project.backlog_dir / "tasks" / "task-2 - shared.md"
    for name, seconds_ago in (("early", 2400), ("late", 600)):
        _git(repo, "checkout", "-q", "-b", name, "main")
        path.write_text(shared, encoding="utf-8")
        _commit(repo, f"write on {name}", when=_recent(seconds_ago))
    _git(repo, "checkout", "-q", "main")
    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )

    snapshots = [
        snapshot
        for snapshot in git_module.list_active_branch_task_snapshots(
            discover_project(Path.cwd(), explicit_cwd=repo)
        )
        if snapshot.relative_path.endswith("task-2 - shared.md")
    ]

    assert len(snapshots) == 2, "both branches should contribute a snapshot"
    assert len({snapshot.blob_id for snapshot in snapshots}) == 1, "same content, one blob id"
    assert len({snapshot.committed_at for snapshot in snapshots}) == 1, (
        "identical content ranked differently by branch"
    )
    assert max(snapshot.committed_at for snapshot in snapshots) == pytest.approx(
        _recent(600), abs=5
    ), "the shared version should carry the newest time it was written"


def _refs_sharing_one_tree(tmp_path: Path, branch_count: int) -> tuple[Path, object]:
    """`branch_count` branches whose trees are byte-identical to main's."""
    from backlog_py.storage.project import discover_project

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git_init(repo)
    project = init_project(repo).project
    for index in range(1, 4):
        _task(project, f"task-{index} - shared.md")
    _commit(repo, "seed", when=_recent(3600))
    for index in range(branch_count):
        _git(repo, "branch", f"feature-{index}")
    config = project.config_path
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "check_active_branches: false", "check_active_branches: true"
        ),
        encoding="utf-8",
    )
    return repo, discover_project(Path.cwd(), explicit_cwd=repo)


def test_branch_snapshots_parse_each_blob_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing dominates a cross-branch scan, so it must scale with content.

    On a real project 150,162 snapshots carried only 2,615 distinct blobs;
    parsing per snapshot cost 45s of a 48.8s command.
    """
    from backlog_py.core import repository as repository_module

    def count_parses(branch_count: int) -> int:
        repo, project = _refs_sharing_one_tree(tmp_path / f"n{branch_count}", branch_count)
        parses = 0
        real_parse = repository_module.parse_task_markdown

        def counting(source: str):
            nonlocal parses
            parses += 1
            return real_parse(source)

        monkeypatch.setattr(repository_module, "parse_task_markdown", counting)
        snapshots = git_module.list_active_branch_task_snapshots(project)
        assert len(snapshots) == 3 * branch_count, "each branch should contribute every task"
        repository_module._load_active_branch_records(project)
        monkeypatch.undo()
        return parses

    assert count_parses(2) == count_parses(8), "parsing scales with refs, not with content"
