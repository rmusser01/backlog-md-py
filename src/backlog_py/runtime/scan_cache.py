"""One project scan, shared for as long as the task files are unchanged.

Every surface that renders a board -- the TUI, the browser service, the MCP
tools -- used to re-read and re-parse every task file for each request or tool
call. At 2310 tasks that is ~1.5s each, repeated over files that had not
changed.

Deciding freshness is far cheaper than re-reading: stat-ing the same 2310 files
costs 27ms. The correctness of that shortcut rests on
``indexing.sqlite._file_signature``, which already handles the case stat alone
cannot settle -- a file touched moments ago, where size and mtime could still
hide an edit -- by hashing its content.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.indexing.sqlite import _file_signature


class ProjectScanCache:
    """Reuse one project scan across requests while the task files are unchanged.

    Every request rebuilt the board from nothing: 2310 task files parsed for a
    board, and again for a single task card. Stat-ing those files instead costs
    27ms against 1.7s to parse them, so freshness is decided from a signature and
    the parse is only repeated when something actually changed.

    Correctness rests on `backlog_py.indexing.sqlite._file_signature`, which
    already solves the hard part: a file touched within the last moment cannot be
    ruled out by size and mtime alone, so its content is hashed instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signature: str | None = None
        self._repository: ReadOnlyRepository | None = None
        self._board_payload: dict[str, object] | None = None

    def repository(self, project: BacklogProject) -> ReadOnlyRepository:
        """A repository whose scan is current for `project`."""
        with self._lock:
            self._refresh_locked(project)
            assert self._repository is not None
            return self._repository

    def board_payload(
        self, project: BacklogProject, build: Callable[[ReadOnlyRepository], dict[str, object]]
    ) -> dict[str, object]:
        """The unfiltered board payload, built at most once per project state."""
        with self._lock:
            self._refresh_locked(project)
            assert self._repository is not None
            if self._board_payload is None:
                self._board_payload = build(self._repository)
            return self._board_payload

    def _refresh_locked(self, project: BacklogProject) -> None:
        signature = _project_signature(project)
        if signature != self._signature or self._repository is None:
            self._signature = signature
            # refresh_remote_refs stays off: a cached scan must not fire network
            # calls on a request path.
            self._repository = ReadOnlyRepository(project, refresh_remote_refs=False)
            self._board_payload = None
            # Warm the visible-record cache while still holding the lock, so two
            # concurrent requests cannot both pay for the same scan. `get_task`
            # consults this set first, so task detail is warmed by it too.
            self._repository.board()


def _project_signature(project: BacklogProject) -> str:
    """A cheap fingerprint of every task file the repository can resolve."""
    now_ns = time.time_ns()
    signatures: list[dict[str, object]] = []
    for bucket in ("tasks", "completed", "archive/tasks", "drafts"):
        directory = project.backlog_dir.joinpath(*bucket.split("/"))
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            signatures.append(_file_signature(project.root, path, now_ns=now_ns))
    return json.dumps(signatures, sort_keys=True, separators=(",", ":"))


# A parsed repository is large, so this cache is bounded twice over: by how many
# projects it holds and by how long an untouched one survives.
#
# Measured on the singleton daemon, RSS after reading eight projects:
#
#   no cache at all      1117 MB   (parsing is simply expensive, and CPython
#                                   does not return freed arenas to the OS)
#   unbounded cache      2197 MB   (every project ever read, held forever)
#   bounded, as here     1495 MB
#
# So most of that baseline is not the cache -- but an unbounded one doubled it,
# which matters most for the daemon, whose whole purpose is to be shared by many
# agents across many projects.
MAX_CACHED_PROJECTS = 4
IDLE_EVICTION_SECONDS = 120.0

_CACHES: "OrderedDict[str, _CacheEntry]" = OrderedDict()
_CACHES_LOCK = threading.Lock()


@dataclass
class _CacheEntry:
    cache: ProjectScanCache
    last_used: float


def read_repository(project: BacklogProject, *, now: float | None = None) -> ReadOnlyRepository:
    """A read-only repository for `project`, reusing an unchanged scan.

    For a long-lived process answering many requests about one project --
    the MCP stdio server and the singleton daemon -- rebuilding the repository
    per call means re-parsing every task file per call. The cache is keyed by
    project root and rebuilt whenever the files change, so a caller never sees
    state older than its own last write.

    It is bounded in both directions: at most `MAX_CACHED_PROJECTS` projects are
    held, and any project untouched for `IDLE_EVICTION_SECONDS` is dropped. A
    daemon that serves a burst of work therefore keeps its scans for the burst
    and releases them afterwards, instead of holding every project it has ever
    been asked about.

    Args:
        project: The project to read.
        now: Current time, for tests that need a deterministic clock.

    Returns:
        ReadOnlyRepository: current for the project as of this call.
    """
    key = str(project.root.resolve())
    timestamp = time.monotonic() if now is None else now
    with _CACHES_LOCK:
        _evict_locked(timestamp, keep=key)
        entry = _CACHES.get(key)
        if entry is None:
            entry = _CacheEntry(cache=ProjectScanCache(), last_used=timestamp)
            _CACHES[key] = entry
        else:
            entry.last_used = timestamp
        _CACHES.move_to_end(key)
        cache = entry.cache
    return cache.repository(project)


def _evict_locked(now: float, *, keep: str) -> None:
    """Drop idle projects, then the least recently used ones over the cap."""
    for key, entry in list(_CACHES.items()):
        if key != keep and now - entry.last_used > IDLE_EVICTION_SECONDS:
            del _CACHES[key]
    while len(_CACHES) >= MAX_CACHED_PROJECTS and next(iter(_CACHES)) != keep:
        _CACHES.popitem(last=False)


def cached_project_count() -> int:
    """How many project scans are currently held. For tests and diagnostics."""
    with _CACHES_LOCK:
        return len(_CACHES)


def clear_read_repositories() -> None:
    """Drop every cached scan. For tests that mutate a project out of band."""
    with _CACHES_LOCK:
        _CACHES.clear()
