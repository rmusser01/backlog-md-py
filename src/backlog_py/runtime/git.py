from __future__ import annotations

import os
import posixpath
import sys
# autoCommit intentionally invokes local git with fixed argv and no shell.
import subprocess  # nosec B404
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import inf
from pathlib import Path
from time import monotonic

from loguru import logger

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.runtime import progress
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

# A commit's history never changes, so the answers below hold until a ref moves.
# Each holds the latest scan's refs only, so a moved ref drops its old answer.
# ponytail: module-global, never released for a project the process stops
# reading; tie them to ProjectScanCache eviction if a daemon serves many repos.
# (work_dir, backlog path) -> (commits walked from, (path, blob id) -> commit time)
_CONTENT_WALKS: dict[tuple[str, str], tuple[frozenset[str], dict[tuple[str, str], float]]] = {}
# (work_dir, backlog path) -> {branch tip: {task path: blob id}}
_BRANCH_TREES: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
# That walk reads the whole task history of every active branch: local work,
# not a hang, so the usual cap would only turn a slow disk into wrong ranking.
# 3 to 5s for 150 branches of a 4,700-task repository.
_CONTENT_WALK_TIMEOUT_SECONDS = 300


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
    commit_args.extend(("--only", "--", *pathspecs))
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


def current_task_snapshot_timestamps(
    project: BacklogProject, paths: Sequence[Path]
) -> dict[Path, float]:
    """When each task file's content was last committed, from `_task_content_dates`.

    A modified, untracked or otherwise unknown file reports ``inf``, newer than
    any commit. A bounded number of git calls answers the whole set: the
    per-file form forked three per task while holding the project write lock.
    """
    results: dict[Path, float] = {path: inf for path in paths}
    located = _task_git_paths(project, paths)
    backlog_path = _relative_backlog_path(project)
    if located is None or backlog_path is None:
        return results
    git_paths, scope = located
    work_dir = project.root
    dirty = _dirty_relative_paths(work_dir, scope)
    if dirty is None:
        # Status failed: every path stays ``inf``, matching the per-file
        # implementation's fail-closed behaviour.
        return results
    clean = [path for path, git_path in git_paths.items() if git_path not in dirty]
    if not clean:
        return results
    # A clean file holds the blob HEAD has at its path.
    blobs, _ = _task_entries_for_ref(work_dir, "HEAD", backlog_path)
    dates = _task_content_dates(project, backlog_path) if blobs else {}
    for path in clean:
        name = _relative_path(work_dir, path)
        if name is not None and name in blobs:
            results[path] = dates.get((name, blobs[name]), inf)
    return results


def uncommitted_task_paths(project: BacklogProject, paths: Sequence[Path]) -> set[Path] | None:
    """The ``paths`` that are modified or untracked, or ``None`` when git cannot say."""
    located = _task_git_paths(project, paths)
    if located is None:
        return None
    git_paths, scope = located
    dirty = _dirty_relative_paths(project.root, scope)
    if dirty is None:
        return None
    return {path for path, git_path in git_paths.items() if git_path in dirty}


def _task_git_paths(
    project: BacklogProject, paths: Sequence[Path]
) -> tuple[dict[Path, str], list[str]] | None:
    """Each path as git names it, and the pathspecs that cover them; ``None`` outside a worktree."""
    if not paths or not _is_git_worktree(project.root):
        return None
    relative_paths: dict[Path, str] = {}
    for path in paths:
        relative = _relative_path(project.root, path)
        if relative is not None:
            relative_paths[path] = relative
    if not relative_paths:
        return None

    # git reports paths relative to the REPOSITORY root, which is not necessarily
    # the project root — `discover_project` stops at the first backlog/config.yml,
    # so <repo>/pkg/backlog/config.yml gives project.root == <repo>/pkg. Translate
    # into git's path-space before comparing, or every task silently reports inf.
    prefix = _repository_prefix(project.root)
    git_paths = {path: f"{prefix}{relative}" for path, relative in relative_paths.items()}

    # Pathspecs are resolved relative to cwd (the project root), while the output
    # above is repo-relative — the two path-spaces must not be mixed.
    scope = sorted({posixpath.dirname(value) or "." for value in relative_paths.values()})
    return git_paths, scope


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


def _task_content_dates(project: BacklogProject, backlog_path: str) -> dict[tuple[str, str], float]:
    """When each task file version, a ``(path, blob id)``, was last written, in one walk.

    The walk covers HEAD and every active branch at once: a version shared by
    sixty branches is one blob at one path, and when it was written does not
    depend on which branch carries it.

    ponytail: a rebase or cherry-pick rewrites a version at its own commit
    time, so a rebased copy looks as new as the rebase. Author time (`%at`)
    keeps the edit's time instead, at the cost of dating an amended version by
    its first commit. On a 29-branch project, rebased copies made 2 of 2,391
    winners differ from per-file `git log -1`.
    """
    work_dir = project.root
    head = _run_git(work_dir, "rev-parse", "--verify", "--quiet", "HEAD")
    tips = {head.stdout.strip()} if head.returncode == 0 else set()
    if project.config.check_active_branches:
        tips.update(tip for _, tip in _recent_branch_tips(project, _current_branch_name(work_dir)))
    key = (str(work_dir), backlog_path)
    cached = _CONTENT_WALKS.get(key)
    if cached is not None and cached[0] == tips:
        return cached[1]
    if not tips:
        return {}

    progress.report(f"Reading task history of {len(tips)} branches")
    # `--relative`: paths relative to the project root, as ls-tree and the
    # checkout name them. `--no-renames`: rename detection compares contents
    # for every commit that moves a task between directories, up to half the
    # walk; without it a move is a deletion plus an addition of the same blob.
    # `log -c`: a merge lists only the files whose version matches none of its
    # parents, a conflict resolved in the merge. One that brings a version in
    # from a parent did not write it, and dating it there would make every
    # version a merge of the main branch carries look new. With `--raw` given,
    # `-c` prints no patch. `-z`: names unquoted, each in its own field.
    log_args = ("log", "-c", "-z", "--format=%ct", "--raw", "--no-abbrev", "--no-renames", "--relative")
    scope = (f"{backlog_path}/tasks", f"{backlog_path}/completed")
    result = _run_git(
        work_dir, *log_args, *sorted(tips), "--", *scope, timeout=_CONTENT_WALK_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        logger.warning("Ranking duplicate task ids without commit times: git log failed: {}", _git_error(result))
        return {}

    dates: dict[tuple[str, str], float] = {}
    committed_at = -inf
    # Fields: a commit time, then per file ":<old mode> <new mode> <old blob>
    # <new blob> <status>" and its path, with one more colon, mode and blob per
    # extra parent of a merge. A path is always read right after its change,
    # so only times and changes reach the branches below.
    fields = iter(result.stdout.split("\0"))
    for field in fields:
        field = field.strip("\n")
        if field.isdigit():
            committed_at = float(field)
        elif field.startswith(":"):
            name = next(fields, "")
            change = field.split()
            if len(change) < 5 or not change[-2].strip("0"):
                continue  # a deletion, which writes no blob
            version = (sys.intern(name), sys.intern(change[-2]))
            # The newest write, not the first sighting: log order follows
            # commit dates only as far as the clocks that wrote them agreed.
            if committed_at > dates.get(version, -inf):
                dates[version] = committed_at
    _CONTENT_WALKS[key] = (frozenset(tips), dates)
    return dates


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

    refs = _recent_branch_tips(project, _current_branch_name(work_dir))
    trees_key = (str(work_dir), backlog_path)
    known = _BRANCH_TREES.get(trees_key, {})
    trees: dict[str, dict[str, str]] = {}
    entries: list[tuple[str, str, str, str]] = []
    for index, (ref, tip) in enumerate(refs, start=1):
        blobs = trees.get(tip, known.get(tip))
        final = blobs is not None
        if blobs is None:
            progress.report(f"Reading branch {index}/{len(refs)}: {ref.rsplit('/', 1)[-1]}")
            blobs, final = _task_entries_for_ref(work_dir, tip, backlog_path)
        if final:
            trees[tip] = blobs
        entries.extend((ref, tip, relative_path, oid) for relative_path, oid in blobs.items())
    _BRANCH_TREES[trees_key] = trees
    dates = _task_content_dates(project, backlog_path) if entries else {}

    # Branches overwhelmingly share task files, and a shared file is one blob
    # with one id, so the unique-id set is a small fraction of the entry count.
    unique_blobs = {oid for *_, oid in entries}
    progress.report(f"Reading {len(unique_blobs)} task versions from {len(refs)} branches")
    sources = _blob_contents(work_dir, unique_blobs)

    tip_dates: dict[str, float] = {}
    snapshots: list[GitTaskSnapshot] = []
    for ref, tip, relative_path, oid in entries:
        source = sources.get(oid)
        if source is None:
            continue
        committed_at = dates.get((relative_path, oid))
        if committed_at is None:
            # A version the walk never saw written (a failed walk, a commit
            # older than a shallow clone) takes its branch's tip time, once.
            if tip not in tip_dates:
                tip_dates[tip] = _ref_commit_timestamp(work_dir, tip)
            committed_at = tip_dates[tip]
        snapshots.append(
            GitTaskSnapshot(
                ref=ref,
                relative_path=relative_path,
                source=source,
                committed_at=committed_at,
            )
        )
    return snapshots


def task_allocation_lock_scope(project: BacklogProject) -> tuple[Path, str] | None:
    """Identify a backlog across all linked worktrees of one Git repository."""
    backlog_path = _relative_backlog_path(project)
    if backlog_path is None or not _is_git_worktree(project.root):
        return None
    result = _run_git(project.root, "rev-parse", "--git-common-dir")
    common_dir = result.stdout.rstrip("\r\n")
    if result.returncode != 0 or not common_dir:
        return None
    repository_path = posixpath.normpath(f"{_repository_prefix(project.root)}{backlog_path}")
    return (project.root / common_dir).resolve(), repository_path


def other_checkout_task_file_names(project: BacklogProject) -> list[str]:
    """Names of the task files on active branches and in other ``git worktree`` checkouts.

    Names only, because an id reservation needs no content: this skips the
    per-ref log walk and the blob reads that make the full branch scan slow. An
    uncommitted task in another worktree is on no ref, so the worktree
    directories are listed too. Follows ``checkActiveBranches``.
    """
    if not project.config.check_active_branches or not _is_git_worktree(project.root):
        return []
    backlog_path = _relative_backlog_path(project)
    if backlog_path is None:
        return []
    buckets = ("tasks", "completed", "archive/tasks")
    prefix = _repository_prefix(project.root)
    names = _tree_file_names(
        project.root,
        [
            f"{tip}:{posixpath.normpath(f'{prefix}{backlog_path}/{bucket}')}"
            for _, tip in _recent_branch_tips(project, _current_branch_name(project.root))
            for bucket in buckets
        ],
    )
    result = _run_git(project.root, "worktree", "list", "--porcelain", "-z")
    if result.returncode == 0:
        fields = result.stdout.split("\0")
    else:
        # `-z` needs git >= 2.36; older porcelain output is newline-separated and unquoted.
        result = _run_git(project.root, "worktree", "list", "--porcelain")
        if result.returncode != 0:
            return names
        fields = result.stdout.split("\n")
    own = project.backlog_dir.resolve()
    for field in fields:
        if not field.startswith("worktree "):
            continue
        backlog_dir = Path(field.removeprefix("worktree ")) / prefix / backlog_path
        if backlog_dir.resolve() != own:
            names.extend(path.name for bucket in buckets for path in (backlog_dir / bucket).glob("*.md"))
    return names


def _tree_file_names(work_dir: Path, specs: list[str]) -> list[str]:
    """File names of ``.md`` blobs in each ``<ref>:<dir>`` tree, in one ``cat-file --batch`` process."""
    if not specs:
        return []
    stdin = "".join(f"{spec}\n" for spec in specs).encode()
    result = _run_git_bytes(work_dir, "cat-file", "--batch", stdin=stdin)
    if result.returncode != 0:
        return []
    encoding = sys.getfilesystemencoding()
    output = result.stdout
    names: list[str] = []
    offset = 0
    while True:
        end = output.find(b"\n", offset)
        if end == -1:
            break
        fields = output[offset:end].rsplit(b" ", 2)
        offset = end + 1
        if len(fields) != 3 or not fields[2].isdigit():
            continue  # "<request> missing" carries no payload
        size = int(fields[2])
        payload = output[offset : offset + size]
        offset += size + 1
        if fields[1] != b"tree":
            continue
        oid_len = len(fields[0]) // 2  # raw oid: 20 bytes for SHA-1, 32 for SHA-256
        pos = 0
        while pos < len(payload):
            nul = payload.index(b"\0", pos)
            mode, _, name = payload[pos:nul].partition(b" ")
            pos = nul + 1 + oid_len
            if not mode.startswith(b"4") and name.endswith(b".md"):
                names.append(name.decode(encoding, "surrogateescape"))
    return names


def _blob_contents(work_dir: Path, oids: Collection[str]) -> dict[str, str]:
    """Read every blob id in one ``git cat-file --batch`` process.

    A `git show` per file per branch forked two subprocesses per task per branch;
    a project with 67 active branches and ~2300 tasks each never finished a scan.
    Requesting object ids rather than ``<ref>:<path>`` also keeps the request
    stream free of any path bytes, which --batch delimits by newline.
    """
    requests = sorted(oids)
    if not requests:
        return {}

    stdin = "".join(f"{oid}\n" for oid in requests).encode("ascii")
    result = _run_git_bytes(work_dir, "cat-file", "--batch", stdin=stdin)
    if result.returncode != 0:
        return {}

    encoding = sys.getfilesystemencoding()
    contents: dict[str, str] = {}
    output = result.stdout
    offset = 0
    for oid in requests:
        end_of_header = output.find(b"\n", offset)
        if end_of_header == -1:
            break
        header = output[offset:end_of_header]
        offset = end_of_header + 1
        # "<oid> <type> <size>" for a hit; "<request> missing" (or "ambiguous")
        # otherwise, and those carry no payload to skip.
        fields = header.rsplit(b" ", 2)
        if len(fields) != 3 or not fields[2].isdigit():
            continue
        size = int(fields[2])
        payload = output[offset : offset + size]
        offset += size + 1  # git terminates each payload with a newline
        if fields[1] == b"blob":
            contents[oid] = payload.decode(encoding, "surrogateescape")
    return contents


def _run_git_bytes(work_dir: Path, *args: str, stdin: bytes) -> subprocess.CompletedProcess[bytes]:
    """`_run_git` for commands whose output is length-delimited binary.

    `cat-file --batch` sizes its payloads in bytes, so the stream cannot be
    decoded before it is framed.
    """
    command = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            input=stdin,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, b"", b"")
    except OSError:
        return subprocess.CompletedProcess(command, 127, b"", b"")


def read_index_git_freshness(project: BacklogProject) -> dict[str, object]:
    """Return git inputs that affect cached task visibility and ordering."""
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
    current = _current_branch_name(work_dir)
    freshness["current_branch"] = current
    freshness["active_refs"] = _recent_branch_tips(project, current) if project.config.check_active_branches else []
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


def _recent_branch_tips(project: BacklogProject, current: str) -> list[tuple[str, str]]:
    """Return active ref names and their tip OIDs, ordered by timestamp and name."""
    days = max(project.config.active_branch_days, 0)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    namespaces = ["refs/heads"]
    if project.config.remote_operations:
        namespaces.append("refs/remotes")
    result = _run_git(
        project.root,
        "for-each-ref",
        "--format=%(refname)\t%(objectname)\t%(committerdate:unix)",
        *namespaces,
    )
    if result.returncode != 0:
        return []
    refs: list[tuple[int, str, str]] = []
    for line in result.stdout.split("\n"):
        fields = line.split("\t")
        if len(fields) != 3 or not fields[2].isdigit():
            continue
        refname, oid, timestamp = fields[0], fields[1], int(fields[2])
        if refname == f"refs/heads/{current}" or refname.endswith("/HEAD"):
            continue
        if timestamp < cutoff:
            continue
        refs.append((timestamp, refname, oid))
    return [(ref, oid) for _, ref, oid in sorted(refs)]


def _current_branch_name(work_dir: Path) -> str:
    result = _run_git(work_dir, "branch", "--show-current")
    return result.stdout.removesuffix("\n") if result.returncode == 0 else ""


def _task_entries_for_ref(
    work_dir: Path, ref: str, backlog_path: str
) -> tuple[dict[str, str], bool]:
    """Blob id of every task file at ``ref``.

    Returns:
        The entries, and whether they are final for ``ref``: not when a git call
        failed, so a passing failure is retried by the next scan instead of
        being remembered.
    """
    result = _run_git(
        work_dir,
        "ls-tree",
        "-r",
        # Without this git quotes a name holding a quote, tab, newline or
        # non-ASCII byte, and matches no path on disk.
        "-z",
        ref,
        "--",
        f"{backlog_path}/tasks",
        f"{backlog_path}/completed",
    )
    if result.returncode != 0:
        return {}, False
    # "<mode> <type> <oid>\t<path>"
    oids: dict[str, str] = {}
    for line in result.stdout.split("\0"):
        metadata, tab, name = line.partition("\t")
        if not tab:
            continue
        fields = metadata.split()
        if len(fields) != 3 or fields[1] != "blob":
            continue
        if _is_task_markdown_path(name, backlog_path):
            # Branches share nearly every name and blob, and these entries are
            # kept for each branch: interned, they are stored once.
            oids[sys.intern(name)] = sys.intern(fields[2])
    return oids, True


def _ref_commit_timestamp(work_dir: Path, ref: str) -> float:
    result = _run_git(work_dir, "log", "-1", "--format=%ct", ref)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _is_task_markdown_path(relative_path: str, backlog_path: str) -> bool:
    # git names a root backlog's files "tasks/...", never "./tasks/...".
    prefix = "" if backlog_path == "." else f"{backlog_path}/"
    return relative_path.endswith(".md") and relative_path.startswith((f"{prefix}tasks/", f"{prefix}completed/"))


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _run_git(
    work_dir: Path, *args: str, timeout: float = GIT_COMMAND_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    # GIT_TERMINAL_PROMPT=0 makes git fail fast instead of blocking on an
    # interactive credential prompt; the timeout bounds a slow/unreachable
    # remote so read commands and the TUI refresh cannot hang indefinitely.
    # GIT_OPTIONAL_LOCKS=0 keeps the frequent read-only `status` calls from taking
    # the index lock, so they cannot collide with the user's own git commands.
    # GIT_LITERAL_PATHSPECS=1: every path passed here is a real file or
    # directory, and a backlog directory named `:(top)x` is legal.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_LITERAL_PATHSPECS": "1"}
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
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"git command timed out after {timeout}s")
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
