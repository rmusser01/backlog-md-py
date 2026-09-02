"""The shared scan cache must not grow without bound.

A parsed repository is large -- a 2310-task project measured ~150-300 MB of live
objects. The singleton daemon serves many agents across many projects, so an
unbounded cache turned it from a 38 MB background process into a 2.2 GB one
after eight projects, with nothing ever released.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backlog_py.core.init import init_project
from backlog_py.runtime import scan_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    scan_cache.clear_read_repositories()
    yield
    scan_cache.clear_read_repositories()


def _project(tmp_path: Path, name: str):
    root = tmp_path / name
    root.mkdir(parents=True)
    return init_project(root, no_git=True).project


def test_cache_holds_at_most_the_configured_number_of_projects(tmp_path):
    projects = [_project(tmp_path, f"p{i}") for i in range(scan_cache.MAX_CACHED_PROJECTS + 3)]

    for index, project in enumerate(projects):
        scan_cache.read_repository(project, now=float(index))

    assert scan_cache.cached_project_count() <= scan_cache.MAX_CACHED_PROJECTS


def test_the_project_being_read_is_never_the_one_evicted(tmp_path):
    """Evicting the caller's own project would defeat the cache entirely."""
    projects = [_project(tmp_path, f"p{i}") for i in range(scan_cache.MAX_CACHED_PROJECTS + 2)]
    for index, project in enumerate(projects):
        scan_cache.read_repository(project, now=float(index))

    repeated = scan_cache.read_repository(projects[-1], now=100.0)
    again = scan_cache.read_repository(projects[-1], now=101.0)

    assert repeated is again, "the live project was evicted between two reads of it"


def test_an_idle_project_is_dropped(tmp_path):
    """A daemon that served a burst hours ago should not still be holding it."""
    first = _project(tmp_path, "first")
    second = _project(tmp_path, "second")

    scan_cache.read_repository(first, now=0.0)
    assert scan_cache.cached_project_count() == 1

    scan_cache.read_repository(second, now=scan_cache.IDLE_EVICTION_SECONDS + 1.0)

    assert scan_cache.cached_project_count() == 1, "the idle project was kept"


def test_a_busy_project_survives_while_it_stays_busy(tmp_path):
    """Eviction is by idleness, not by age."""
    project = _project(tmp_path, "busy")

    first = scan_cache.read_repository(project, now=0.0)
    for tick in range(1, 10):
        latest = scan_cache.read_repository(project, now=tick * (scan_cache.IDLE_EVICTION_SECONDS / 2))

    assert latest is first, "a continuously used project was dropped"
