from __future__ import annotations

# autoCommit intentionally invokes local git with fixed argv and no shell.
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from backlog_py.core.models import BacklogProject
from backlog_py.storage.config import load_config


@dataclass(frozen=True)
class AutoCommitContext:
    """Project git state captured before a write mutation runs."""

    work_dir: Path
    git_available: bool
    clean_before: bool


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
    if not _auto_commit_enabled(project):
        return
    if not context.git_available:
        logger.warning("Skipping auto-commit for {}: project is not inside a git worktree", operation)
        return
    if not context.clean_before:
        logger.warning("Skipping auto-commit for {}: project had pre-existing git changes", operation)
        return
    if not _has_project_changes(context.work_dir):
        return

    add = _run_git(context.work_dir, "add", "-A", "--", ".")
    if add.returncode != 0:
        logger.warning("Skipping auto-commit for {}: git add failed: {}", operation, _git_error(add))
        return

    commit = _run_git(context.work_dir, "commit", "-m", f"backlog: {operation}")
    if commit.returncode == 0:
        return

    _run_git(context.work_dir, "reset", "--", ".")
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


def _auto_commit_enabled(project: BacklogProject) -> bool:
    try:
        return load_config(project.config_path).auto_commit
    except (OSError, ValueError):
        return project.config.auto_commit


def _is_git_worktree(work_dir: Path) -> bool:
    result = _run_git(work_dir, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _has_project_changes(work_dir: Path) -> bool:
    result = _run_git(work_dir, "status", "--porcelain", "--untracked-files=all", "--", ".")
    return bool(result.stdout.strip()) if result.returncode == 0 else False


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _run_git(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
