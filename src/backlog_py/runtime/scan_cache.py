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


_CACHES: dict[str, ProjectScanCache] = {}
_CACHES_LOCK = threading.Lock()


def read_repository(project: BacklogProject) -> ReadOnlyRepository:
    """A read-only repository for `project`, reusing an unchanged scan.

    For a long-lived process answering many requests about one project -- the
    MCP stdio server is the case this exists for -- rebuilding the repository
    per call means re-parsing every task file per call. The cache is keyed by
    project root and rebuilt whenever the files change, so a caller never sees
    state older than its own last write.

    Args:
        project: The project to read.

    Returns:
        ReadOnlyRepository: current for the project as of this call.
    """
    key = str(project.root.resolve())
    with _CACHES_LOCK:
        cache = _CACHES.get(key)
        if cache is None:
            cache = ProjectScanCache()
            _CACHES[key] = cache
    return cache.repository(project)


def clear_read_repositories() -> None:
    """Drop every cached scan. For tests that mutate a project out of band."""
    with _CACHES_LOCK:
        _CACHES.clear()
