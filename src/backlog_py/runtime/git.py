from __future__ import annotations

import os
import posixpath
import sys
# autoCommit intentionally invokes local git with fixed argv and no shell.
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import inf
from pathlib import Path, PurePosixPath
from time import monotonic

from loguru import logger

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.storage.config import load_config


# Upper bound for any single git invocation so an unreachable remote or a
# hanging credential helper cannot pin a caller (CLI command, TUI refresh).
GIT_COMMAND_TIMEOUT_SECONDS = 30

# Repository layout is cached to keep a scan of N task files from forking O(N)
# git processes, but the answers are not immutable: a `git init` anywhere at or
# above the project changes both of them. Entries therefore expire, so a
# long-lived process (TUI, MCP server) heals within seconds instead of serving
# the stale answer until restart. The window is long enough that a single scan
# still pays for the probe once. Read through the module at call time so tests
# can shorten it.
_CACHE_TTL_SECONDS = 5.0
# work_dir -> (probe time, ".git" existed when probed, is-inside-work-tree result)
_WORKTREE_CACHE: dict[str, tuple[float, bool, bool]] = {}
# work_dir -> (probe time, path of work_dir relative to the repo root: "" or "pkg/")
_PREFIX_CACHE: dict[str, tuple[float, str]] = {}


@dataclass(frozen=True)
class AutoCommitContext:
    """Project git state captured before a write mutation runs."""

    work_dir: Path
    git_available: bool
    clean_before: bool


@dataclass(frozen=True)
class GitTaskSnapshot:
    """Task markdown captured from a git branch without checking it out."""

    ref: str
    relative_path: str
    source: str
    committed_at: float


def prepare_auto_commit(project: BacklogProject) -> AutoCommitContext:
    """Capture project git state before a mutation so unrelated dirt is not committed."""
    work_dir = project.root
    if not _is_git_worktree(work_dir):
        return AutoCommitContext(work_dir=work_dir, git_available=False, clean_before=False)
    return AutoCommitContext(
        work_dir=work_dir,
        git_available=True,
        clean_before=not _has_project_changes(work_dir),
    )


def maybe_auto_commit(project: BacklogProject, operation: str, context: AutoCommitContext) -> None:
    """Commit mutation results when post-mutation config enables autoCommit."""
    config = _auto_commit_config(project)
    if not config.auto_commit:
        return
    if not context.git_available:
        logger.warning("Skipping auto-commit for {}: project is not inside a git worktree", operation)
        return
    if not context.clean_before:
        logger.warning("Skipping auto-commit for {}: project had pre-existing git changes", operation)
        return
    pathspecs = _auto_commit_pathspecs(project)
    if not _has_changes_in(context.work_dir, pathspecs):
        return

    add = _run_git(context.work_dir, "--literal-pathspecs", "add", "-A", "--", *pathspecs)
    if add.returncode != 0:
        logger.warning("Skipping auto-commit for {}: git add failed: {}", operation, _git_error(add))
        return

    commit_args = ["--literal-pathspecs", "commit"]
    if config.bypass_git_hooks:
        commit_args.append("--no-verify")
    commit_args.extend(("-m", f"backlog: {operation}"))
    commit_args.extend(("--only", "--", *pathspecs))
    commit = _run_git(context.work_dir, *commit_args)
    if commit.returncode == 0:
        return

    _run_git(context.work_dir, "--literal-pathspecs", "reset", "--", *pathspecs)
    logger.warning("Skipping auto-commit for {}: git commit failed: {}", operation, _git_error(commit))


def maybe_fetch_remote_refs(project: BacklogProject) -> None:
    """Refresh remote-tracking refs when remote operations are enabled."""
    if not project.config.remote_operations or not project.config.check_active_branches:
        return
    work_dir = project.root
    if not _is_git_worktree(work_dir):
        return
    remotes = _run_git(work_dir, "remote")
    if remotes.returncode != 0 or not remotes.stdout.strip():
        return
    fetch = _run_git(work_dir, "fetch", "--all", "--prune")
    if fetch.returncode != 0:
        logger.warning("Skipping remote refresh: git fetch failed: {}", _git_error(fetch))


def current_task_snapshot_timestamps(
    project: BacklogProject, paths: Sequence[Path]
) -> dict[Path, float]:
    """Checkout timestamps for many task files using a bounded number of git calls.

    The per-file form cost three subprocesses each (worktree probe, status, log),
    so a scan of N tasks forked O(3N) git processes while holding the project
    write lock. This resolves the whole set with one status call and one log walk.
    """
    results: dict[Path, float] = {path: inf for path in paths}
    if not paths:
        return results
    work_dir = project.root
    if not _is_git_worktree(work_dir):
        return results

    relative_paths: dict[Path, str] = {}
    for path in paths:
        relative = _relative_path(project.root, path)
        if relative is not None:
            relative_paths[path] = relative
    if not relative_paths:
        return results

    # git reports paths relative to the REPOSITORY root, which is not necessarily
    # the project root — `discover_project` stops at the first backlog/config.yml,
    # so <repo>/pkg/backlog/config.yml gives project.root == <repo>/pkg. Translate
    # into git's path-space before comparing, or every task silently reports inf.
    prefix = _repository_prefix(work_dir)
    git_paths = {path: f"{prefix}{relative}" for path, relative in relative_paths.items()}

    # Pathspecs are resolved relative to cwd (the project root), while the output
    # above is repo-relative — the two path-spaces must not be mixed.
    scope = sorted({posixpath.dirname(value) or "." for value in relative_paths.values()})
    dirty = _dirty_relative_paths(work_dir, scope)
    if dirty is None:
        # Status failed: every path stays ``inf``, matching the per-file
        # implementation's fail-closed behaviour.
        return results
    committed = _last_commit_timestamps(work_dir, scope)

    for path, git_path in git_paths.items():
        if git_path in dirty:
            continue
        results[path] = committed.get(git_path, inf)
    return results


def _repository_prefix(work_dir: Path) -> str:
    """Path of ``work_dir`` relative to the repository root, with a trailing slash."""
    key = str(work_dir)
    now = monotonic()
    cached = _PREFIX_CACHE.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    result = _run_git(work_dir, "rev-parse", "--show-prefix")
    # git terminates the prefix with a single newline and nothing else. Every
    # other byte belongs to the path: `strip()` would delete the leading space
    # of a directory literally named " pkg", and then no lookup ever matches.
    prefix = result.stdout.removesuffix("\n") if result.returncode == 0 else ""
    _PREFIX_CACHE[key] = (now, prefix)
    return prefix


def _dirty_relative_paths(work_dir: Path, scope: Sequence[str]) -> set[str] | None:
    """Repository-relative paths with uncommitted changes under ``scope``.

    Returns ``None`` when git status fails, so callers can fail closed.
    """
    result = _run_git(
        work_dir, "status", "--porcelain", "-z", "--untracked-files=all", "--", *scope
    )
    if result.returncode != 0:
        return None
    entries = [entry for entry in result.stdout.split("\0") if entry]
    dirty: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        dirty.add(name)
        if status[0] in {"R", "C"} and index < len(entries):
            # Renames and copies carry the source path as a second record.
            dirty.add(entries[index])
            index += 1
    return dirty


def _last_commit_timestamps(work_dir: Path, scope: Sequence[str]) -> dict[str, float]:
    """Most recent commit timestamp per path under ``scope``, in one log walk."""
    log_args = ("-c", "core.quotePath=false", "log", "--format=%x00%ct", "--name-only")
    # `--name-only` prints nothing for a merge commit, so a file whose content was
    # produced while resolving a conflict would be attributed to an older ancestor
    # instead of the merge. Ordinary merges are simplified out of a path-limited
    # log and so are unaffected. `--diff-merges` needs git >= 2.31; older git
    # rejects the option outright, which would report *every* task as unknown, so
    # fall back to the plain walk when git says it does not know the option.
    result = _run_git(work_dir, *log_args, "--diff-merges=first-parent", "HEAD", "--", *scope)
    if result.returncode != 0 and "diff-merges" in (result.stderr or ""):
        result = _run_git(work_dir, *log_args, "HEAD", "--", *scope)
    if result.returncode != 0:
        return {}
    timestamps: dict[str, float] = {}
    for chunk in result.stdout.split("\0"):
        if not chunk.strip():
            continue
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        try:
            committed_at = float(lines[0].strip())
        except ValueError:
            continue
        for name in lines[1:]:
            # log walks newest-first, so the first sighting is the latest commit.
            timestamps.setdefault(name, committed_at)
    return timestamps


def list_active_branch_task_snapshots(project: BacklogProject) -> list[GitTaskSnapshot]:
    """Load task markdown from recently active branches without mutating the worktree."""
    if not project.config.check_active_branches:
        return []
    work_dir = project.root
    if not _is_git_worktree(work_dir):
        return []

    backlog_path = _relative_backlog_path(project)
    if backlog_path is None:
        return []

    snapshots: list[GitTaskSnapshot] = []
    for ref, fallback_timestamp in _recent_branch_refs(project):
        paths = _task_paths_for_ref(work_dir, ref, backlog_path, fallback_timestamp)
        sources = _task_sources_for_ref(work_dir, paths)
        for relative_path in sorted(sources):
            snapshots.append(
                GitTaskSnapshot(
                    ref=ref,
                    relative_path=relative_path,
                    source=sources[relative_path],
                    committed_at=paths.get(relative_path, (fallback_timestamp, ""))[0],
                )
            )
    return snapshots


def read_index_git_freshness(project: BacklogProject) -> dict[str, object]:
    """Return git inputs that affect disposable read-index freshness."""
    work_dir = project.root
    freshness: dict[str, object] = {
        "remote_operations": project.config.remote_operations,
        "check_active_branches": project.config.check_active_branches,
        "active_branch_days": project.config.active_branch_days,
        "is_git_worktree": _is_git_worktree(work_dir),
    }
    if not freshness["is_git_worktree"]:
        return freshness

    head = _run_git(work_dir, "rev-parse", "HEAD")
    freshness["head"] = head.stdout.strip() if head.returncode == 0 else ""
    freshness["current_branch"] = _current_branch_name(work_dir)
    if not project.config.check_active_branches:
        freshness["active_refs"] = []
        return freshness

    freshness["active_refs"] = [
        {"ref": ref, "timestamp": timestamp} for ref, timestamp in _recent_branch_refs(project)
    ]
    return freshness


def _auto_commit_config(project: BacklogProject) -> BacklogConfig:
    try:
        return load_config(project.config_path)
    except (OSError, ValueError):
        return project.config


def _is_git_worktree(work_dir: Path) -> bool:
    """Memoized worktree probe.

    This runs on every repository scan, so the subprocess cost dominates large
    projects. Two things invalidate the memo:

    * the presence of a local ``.git`` — a directory in a normal clone, a *file*
      in a submodule or linked worktree, so ``exists()`` is the right test;
    * age. The marker alone cannot see a ``git init`` in a *parent* directory,
      which leaves the project inside a worktree with no local ``.git`` at all.
      Without expiry that answer would stay ``False`` for the lifetime of the
      process, silently disabling auto-commit in a TUI or daemon until restart.
    """
    key = str(work_dir)
    marker = (work_dir / ".git").exists()
    now = monotonic()
    cached = _WORKTREE_CACHE.get(key)
    if cached is not None and cached[1] == marker and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[2]
    result = _run_git(work_dir, "rev-parse", "--is-inside-work-tree")
    value = result.returncode == 0 and result.stdout.strip() == "true"
    _WORKTREE_CACHE[key] = (now, marker, value)
    return value


def _has_project_changes(work_dir: Path) -> bool:
    result = _run_git(
        work_dir, "--literal-pathspecs", "status", "--porcelain", "--untracked-files=all", "--", "."
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else False


def _has_changes_in(work_dir: Path, pathspecs: list[str]) -> bool:
    if not pathspecs:
        return False
    result = _run_git(
        work_dir,
        "--literal-pathspecs",
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *pathspecs,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else False


def _auto_commit_pathspecs(project: BacklogProject) -> list[str]:
    """Files auto-commit is allowed to stage: the backlog dir and its config.

    Restricting the pathspec keeps unrelated files written during the locked
    operation (an editor session, a status-change hook) out of the commit.
    """
    pathspecs: list[str] = []
    backlog_rel = _relative_path(project.root, project.backlog_dir)
    if backlog_rel is not None:
        pathspecs.append(backlog_rel)
    config_rel = _relative_path(project.root, project.config_path)
    if config_rel is not None and not (backlog_rel and config_rel.startswith(f"{backlog_rel}/")):
        pathspecs.append(config_rel)
    return pathspecs


def _relative_path(root: Path, path: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _relative_backlog_path(project: BacklogProject) -> str | None:
    try:
        return project.backlog_dir.relative_to(project.root).as_posix()
    except ValueError:
        return None


def _recent_branch_refs(project: BacklogProject) -> list[tuple[str, float]]:
    days = max(project.config.active_branch_days, 0)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    namespaces = ["refs/heads"]
    if project.config.remote_operations:
        namespaces.append("refs/remotes")
    result = _run_git(
        project.root,
        "for-each-ref",
        "--format=%(refname)\t%(refname:short)\t%(committerdate:unix)",
        *namespaces,
    )
    if result.returncode != 0:
        return []
    current = _current_branch_name(project.root)
    refs: list[tuple[float, str]] = []
    for line in result.stdout.splitlines():
        refname, short_name, timestamp = _split_ref_line(line)
        if refname is None or short_name is None or timestamp is None:
            continue
        if short_name == current or short_name.endswith("/HEAD"):
            continue
        if timestamp < cutoff:
            continue
        refs.append((timestamp, refname))
    return [(ref, timestamp) for timestamp, ref in sorted(refs)]


def _current_branch_name(work_dir: Path) -> str:
    result = _run_git(work_dir, "branch", "--show-current")
    return result.stdout.strip() if result.returncode == 0 else ""


def _split_ref_line(line: str) -> tuple[str | None, str | None, float | None]:
    parts = line.split("\t")
    if len(parts) != 3:
        return None, None, None
    try:
        timestamp = float(parts[2])
    except ValueError:
        return None, None, None
    return parts[0].strip(), parts[1].strip(), timestamp


def _task_bucket_paths(backlog_path: str) -> tuple[str, ...]:
    return tuple(
        posixpath.normpath(posixpath.join(backlog_path, directory))
        for directory in ("tasks", "completed")
    )


def _task_paths_for_ref(
    work_dir: Path, ref: str, backlog_path: str, fallback_timestamp: float
) -> dict[str, tuple[float, str]]:
    """Return live task paths and latest timestamps from one NUL-framed log."""
    bucket_paths = _task_bucket_paths(backlog_path)
    result = _run_git_bytes(
        work_dir,
        "--literal-pathspecs",
        "-c",
        "core.quotePath=false",
        "log",
        "-z",
        "--format=%x00%ct",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        # Old-compatible first-parent merge diffs; avoids false timestamps from parent two.
        "-m",
        "--first-parent",
        ref,
        "--",
        *bucket_paths,
    )
    if result.returncode != 0:
        return {}

    paths: dict[str, tuple[float, str]] = {}
    seen: set[str] = set()
    records = result.stdout.split(b"\0")
    index = 0
    while index < len(records):
        if records[index]:
            index += 1
            continue
        index += 1
        if index >= len(records) or not records[index]:
            continue
        try:
            committed_at = float(records[index])
        except ValueError:
            committed_at = fallback_timestamp
        index += 1
        first_entry = True
        while index < len(records) and records[index]:
            metadata = records[index]
            if first_entry:
                metadata = metadata.removeprefix(b"\n")
                first_entry = False
            index += 1
            if index >= len(records) or not records[index]:
                break
            relative_path = os.fsdecode(records[index])
            index += 1
            if relative_path in seen:
                continue
            seen.add(relative_path)
            fields = metadata.split()
            if (
                len(fields) == 5
                and fields[0].startswith(b":")
                and fields[1] in {b"100644", b"100755"}
                and fields[4][:1] != b"D"
                and _is_task_markdown_path(relative_path, backlog_path)
            ):
                paths[relative_path] = (committed_at, fields[3].decode("ascii"))
    return paths


def _task_sources_for_ref(
    work_dir: Path,
    paths: dict[str, tuple[float, str]],
) -> dict[str, str]:
    requested = sorted(paths.items())
    if not requested:
        return {}
    result = _run_git_bytes(
        work_dir,
        "cat-file",
        "--batch",
        input_data=b"".join(f"{blob_id}\n".encode() for _, (_, blob_id) in requested),
    )
    if result.returncode != 0:
        return {}

    sources: dict[str, str] = {}
    offset = 0
    for relative_path, _ in requested:
        header_end = result.stdout.find(b"\n", offset)
        if header_end < 0:
            return {}
        header = result.stdout[offset:header_end].split()
        if len(header) != 3 or header[1] != b"blob":
            return {}
        try:
            size = int(header[2])
        except ValueError:
            return {}
        start = header_end + 1
        end = start + size
        if end >= len(result.stdout) or result.stdout[end : end + 1] != b"\n":
            return {}
        sources[relative_path] = result.stdout[start:end].decode(
            sys.getfilesystemencoding(), errors="surrogateescape"
        )
        offset = end + 1
    if offset != len(result.stdout):
        return {}
    return sources


def _is_task_markdown_path(relative_path: str, backlog_path: str) -> bool:
    parts = relative_path.split("/")
    backlog_parts = PurePosixPath(backlog_path).parts
    return (
        relative_path.endswith(".md")
        and not relative_path.startswith("/")
        and not any(part in {"", ".", ".."} for part in parts)
        and len(parts) > len(backlog_parts) + 1
        and tuple(parts[: len(backlog_parts)]) == backlog_parts
        and parts[len(backlog_parts)] in {"tasks", "completed"}
    )


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _run_git(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    # GIT_TERMINAL_PROMPT=0 makes git fail fast instead of blocking on an
    # interactive credential prompt; the timeout bounds a slow/unreachable
    # remote so read commands and the TUI refresh cannot hang indefinitely.
    # GIT_OPTIONAL_LOCKS=0 keeps the frequent read-only `status` calls from taking
    # the index lock, so they cannot collide with the user's own git commands.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
            # Paths are bytes to git, and `core.quotePath=false` asks it to emit
            # them raw. A filename that is not valid UTF-8 would abort the whole
            # scan under strict decoding; surrogateescape preserves the bytes and
            # reproduces exactly the string ``os.fsdecode`` (and therefore
            # ``Path``) builds for the same file, so the two still compare equal.
            encoding=sys.getfilesystemencoding(),
            errors="surrogateescape",
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 124, "", f"git command timed out after {GIT_COMMAND_TIMEOUT_SECONDS}s"
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _run_git_bytes(
    work_dir: Path, *args: str, input_data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run git without decoding binary input or output."""
    command = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            input=input_data,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            b"",
            f"git command timed out after {GIT_COMMAND_TIMEOUT_SECONDS}s".encode(),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, b"", os.fsencode(str(exc)))
