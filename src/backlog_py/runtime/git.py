from __future__ import annotations

import os
# autoCommit intentionally invokes local git with fixed argv and no shell.
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import inf
from pathlib import Path

from loguru import logger

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.storage.config import load_config


# Upper bound for any single git invocation so an unreachable remote or a
# hanging credential helper cannot pin a caller (CLI command, TUI refresh).
GIT_COMMAND_TIMEOUT_SECONDS = 30


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

    add = _run_git(context.work_dir, "add", "-A", "--", *pathspecs)
    if add.returncode != 0:
        logger.warning("Skipping auto-commit for {}: git add failed: {}", operation, _git_error(add))
        return

    commit_args = ["commit"]
    if config.bypass_git_hooks:
        commit_args.append("--no-verify")
    commit_args.extend(("-m", f"backlog: {operation}"))
    commit = _run_git(context.work_dir, *commit_args)
    if commit.returncode == 0:
        return

    _run_git(context.work_dir, "reset", "--", *pathspecs)
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


def current_task_snapshot_timestamp(project: BacklogProject, path: Path) -> float:
    """Return the current checkout timestamp for one task file."""
    work_dir = project.root
    if not _is_git_worktree(work_dir):
        return inf
    relative_path = _relative_path(project.root, path)
    if relative_path is None:
        return inf
    if _has_path_changes(project.root, relative_path):
        return inf
    result = _run_git(work_dir, "log", "-1", "--format=%ct", "HEAD", "--", relative_path)
    if result.returncode != 0:
        return inf
    try:
        return float(result.stdout.strip())
    except ValueError:
        return inf


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
    for ref in _recent_branch_refs(project):
        paths = _task_paths_for_ref(work_dir, ref, backlog_path)
        for relative_path, committed_at in paths.items():
            show = _run_git(work_dir, "show", f"{ref}:{relative_path}")
            if show.returncode != 0:
                continue
            snapshots.append(
                GitTaskSnapshot(
                    ref=ref,
                    relative_path=relative_path,
                    source=show.stdout,
                    committed_at=committed_at,
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
        {
            "ref": ref,
            "timestamp": _ref_commit_timestamp(work_dir, ref),
        }
        for ref in _recent_branch_refs(project)
    ]
    return freshness


def _auto_commit_config(project: BacklogProject) -> BacklogConfig:
    try:
        return load_config(project.config_path)
    except (OSError, ValueError):
        return project.config


def _is_git_worktree(work_dir: Path) -> bool:
    result = _run_git(work_dir, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _has_project_changes(work_dir: Path) -> bool:
    result = _run_git(work_dir, "status", "--porcelain", "--untracked-files=all", "--", ".")
    return bool(result.stdout.strip()) if result.returncode == 0 else False


def _has_changes_in(work_dir: Path, pathspecs: list[str]) -> bool:
    if not pathspecs:
        return False
    result = _run_git(work_dir, "status", "--porcelain", "--untracked-files=all", "--", *pathspecs)
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


def _has_path_changes(work_dir: Path, relative_path: str) -> bool:
    result = _run_git(work_dir, "status", "--porcelain", "--untracked-files=all", "--", relative_path)
    return bool(result.stdout.strip()) if result.returncode == 0 else True


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


def _recent_branch_refs(project: BacklogProject) -> list[str]:
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
    return [ref for _, ref in sorted(refs)]


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


def _task_paths_for_ref(work_dir: Path, ref: str, backlog_path: str) -> dict[str, float]:
    result = _run_git(
        work_dir,
        "ls-tree",
        "-r",
        "--name-only",
        ref,
        "--",
        f"{backlog_path}/tasks",
        f"{backlog_path}/completed",
    )
    if result.returncode != 0:
        return {}
    return {
        line.strip(): _ref_path_commit_timestamp(work_dir, ref, line.strip())
        for line in result.stdout.splitlines()
        if _is_task_markdown_path(line.strip(), backlog_path)
    }


def _ref_commit_timestamp(work_dir: Path, ref: str) -> float:
    result = _run_git(work_dir, "log", "-1", "--format=%ct", ref)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _ref_path_commit_timestamp(work_dir: Path, ref: str, relative_path: str) -> float:
    result = _run_git(work_dir, "log", "-1", "--format=%ct", ref, "--", relative_path)
    if result.returncode != 0 or not result.stdout.strip():
        return _ref_commit_timestamp(work_dir, ref)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return _ref_commit_timestamp(work_dir, ref)


def _is_task_markdown_path(relative_path: str, backlog_path: str) -> bool:
    return (
        relative_path.endswith(".md")
        and (
            relative_path.startswith(f"{backlog_path}/tasks/")
            or relative_path.startswith(f"{backlog_path}/completed/")
        )
    )


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _run_git(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    # GIT_TERMINAL_PROMPT=0 makes git fail fast instead of blocking on an
    # interactive credential prompt; the timeout bounds a slow/unreachable
    # remote so read commands and the TUI refresh cannot hang indefinitely.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 124, "", f"git command timed out after {GIT_COMMAND_TIMEOUT_SECONDS}s"
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
