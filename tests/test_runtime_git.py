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
