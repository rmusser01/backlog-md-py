from datetime import datetime, timedelta, timezone

from backlog_py.browser import service as browser_service
from backlog_py.core.init import init_project
from backlog_py.core.repository import MutableRepository
from backlog_py.orchestration import service as orchestration_service
from backlog_py.runtime.scan_cache import ProjectScanCache


def test_warm_board_follows_policy_edits_without_reparsing_tasks(tmp_path):
    project = init_project(tmp_path, no_git=True).project
    MutableRepository(project).create_task(title="Ready to check", status="To Do")
    policy_path = project.backlog_dir / "orchestration.yml"
    policy_source = """states:
  todo:
    claimable: true
  review:
    claimable: true
  complete:
    terminal: true
transitions:
  review: [todo]
  todo: [complete]
"""
    policy_path.write_text(policy_source, encoding="utf-8")
    cache = ProjectScanCache()
    repository = cache.repository(project)

    def category():
        return browser_service.build_board_payload(project, cache=cache)["columns"]["To Do"][0]["queueCategory"]

    assert category() == "eligible"
    policy_path.write_text(
        policy_source.replace("todo:\n    claimable: true", "todo:\n    claimable: false"),
        encoding="utf-8",
    )
    assert category() == "in_workflow"
    policy_path.unlink()
    assert category() == "eligible"
    assert cache.repository(project) is repository


def test_warm_board_expires_leases_without_reparsing_tasks(tmp_path, monkeypatch):
    project = init_project(tmp_path, no_git=True).project
    task = MutableRepository(project).create_task(title="Leased task", status="To Do")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(orchestration_service, "datetime", Clock)
    monkeypatch.setattr(browser_service, "datetime", Clock)
    orchestration_service.OrchestrationService(project).claim_task(
        task.id, actor="runner", lease_ttl_seconds=15, expected_version=0,
    )
    cache = ProjectScanCache()
    repository = cache.repository(project)

    first = browser_service.build_board_payload(project, cache=cache)
    assert first["queueCategories"] == ["claimed"]
    now += timedelta(seconds=15)
    next_board = browser_service.build_board_payload(project, cache=cache)
    assert next_board["queueCategories"] == ["stale_claim"]
    assert next_board["revision"] != first["revision"]
    assert cache.repository(project) is repository


def test_a_racy_write_does_not_rescan_once_it_ages(tmp_path, monkeypatch):
    """A file written just now is fingerprinted by content; once its mtime aged
    out of the racy window the hash used to vanish from the fingerprint, so an
    unchanged project was re-scanned two seconds after every write."""
    import os

    from backlog_py.indexing.sqlite import RACY_MTIME_WINDOW_NS
    from backlog_py.runtime import scan_cache

    project = init_project(tmp_path, no_git=True).project
    task = MutableRepository(project).create_task(title="Fresh", status="To Do")
    cache = ProjectScanCache()
    repository = cache.repository(project)
    stat = task.path.stat()

    real_time_ns = scan_cache.time.time_ns
    monkeypatch.setattr(scan_cache.time, "time_ns", lambda: real_time_ns() + 2 * RACY_MTIME_WINDOW_NS)
    assert cache.repository(project) is repository, "an unchanged project was re-scanned"

    # Same size, same mtime, different content: only the retained hash sees it.
    source = task.path.read_text(encoding="utf-8")
    task.path.write_text(source.replace("Fresh", "Stale"), encoding="utf-8")
    os.utime(task.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    refreshed = cache.repository(project)
    assert refreshed is not repository
    assert refreshed.get_task(task.id).title == "Stale"
