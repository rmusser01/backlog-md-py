import http.client
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.documents import DocumentRecord, DocumentService
from backlog_py.core.milestones import MilestoneService
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.storage.config import get_definition_of_done_defaults, replace_definition_of_done_defaults, set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_browser_service_module_does_not_embed_full_board_assets():
    source = Path("src/backlog_py/browser/service.py").read_text(encoding="utf-8")

    assert "<style>" not in source
    assert "<script>" not in source
    assert "let draggedTaskId = null;" not in source


def test_browser_board_asset_uses_static_javascript_escape_sequences():
    source = Path("src/backlog_py/browser/assets/board.js").read_text(encoding="utf-8")

    assert r"/^\\[" not in source
    assert r"\\s+" not in source
    assert r".split(/[\\n,]/)" not in source
    assert r'"\\n"' not in source
    assert r"const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);" in source


def test_browser_service_serves_health_board_json_and_html(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        health = _get_json(f"{service.root_url}/health")
        board = _get_json(f"{service.root_url}/api/board")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert health == {"ok": True, "projectName": "basic-fixture"}
    assert board["project"]["name"] == "basic-fixture"
    assert board["statuses"] == ["To Do", "In Progress", "Done"]
    assert board["columns"]["In Progress"][0]["id"] == "TASK-1"
    assert board["columns"]["In Progress"][0]["title"] == "Example task"
    assert "basic-fixture" in html
    assert "In Progress" in html
    assert "TASK-1" in html
    assert "Example task" in html


def test_browser_board_payload_includes_orchestration_queue_state(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    task = board["columns"]["In Progress"][0]
    assert board["queueCategories"] == ["in_workflow"]
    assert board["queueCategoryFilter"] is None
    assert task["queueCategory"] == "in_workflow"
    assert task["effectiveStatus"] == "inprogress"
    assert task["orchestrationVersion"] == 0
    assert task["validationIssues"] == []
    assert task["runHistoryIssues"] == []


def test_browser_board_queue_category_filter_is_readonly(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")
    lock_operations = []

    from backlog_py.browser import service as browser_service

    def tracking_lock(project, operation, fn):
        lock_operations.append(operation)
        return fn()

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        unfiltered = _get_json(f"{service.root_url}/api/board")
        in_workflow = _get_json(f"{service.root_url}/api/board?queueCategory=in_workflow")
        claimed = _get_json(f"{service.root_url}/api/board?queueCategory=claimed")
    finally:
        service.shutdown()

    assert in_workflow["queueCategoryFilter"] == "in_workflow"
    assert in_workflow["columns"]["In Progress"][0]["id"] == "TASK-1"
    assert claimed["queueCategoryFilter"] == "claimed"
    assert claimed["columns"]["In Progress"] == []
    assert in_workflow["revision"] == unfiltered["revision"]
    assert claimed["revision"] == unfiltered["revision"]
    assert lock_operations == []
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_board_html_embeds_one_complete_script_block(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert html.count("<script>") == 1
    script = html.split("<script>", maxsplit=1)[1].split("</script>", maxsplit=1)[0]
    assert "<script>" not in script
    assert "async function submitTaskEdit" in script
    assert 'document.querySelectorAll("[data-task-edit]")' in script


def test_browser_service_serves_favicon_without_not_found(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _get_response_bytes(f"{service.root_url}/favicon.ico")
        request_log = _get_json(f"{service.root_url}/api/service/requests")
    finally:
        service.shutdown()

    assert response["status"] == 204
    assert response["contentType"] == "image/x-icon"
    assert response["body"] == b""
    assert request_log["requests"][-1]["path"] == "/favicon.ico"
    assert request_log["requests"][-1]["status"] == 204


def test_browser_service_status_endpoint_returns_runtime_metadata(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        status = _get_json(f"{service.root_url}api/service/status")
    finally:
        service.shutdown()

    assert status == {
        "ok": True,
        "projectName": "basic-fixture",
        "projectRoot": str(repo),
        "backlogDir": str(repo / "backlog"),
        "host": "127.0.0.1",
        "port": service.port,
        "rootUrl": service.root_url,
        "shutdownSupported": True,
        "shutdownInProgress": False,
        "shutdownRequestedAt": None,
    }


def test_browser_service_shutdown_endpoint_rejects_cross_origin(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json_response(f"{service.root_url}api/service/shutdown", {}, origin="https://example.com")
        health = _get_json(f"{service.root_url}health")
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert health == {"ok": True, "projectName": "basic-fixture"}


def test_browser_service_shutdown_endpoint_stops_service(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(f"{service.root_url}api/service/shutdown", {})
        service.thread.join(timeout=2)
        assert response == {
            "status": 202,
            "body": {
                "ok": True,
                "message": "Shutdown scheduled",
                "shutdownInProgress": True,
                "alreadyScheduled": False,
            },
        }
        assert not service.thread.is_alive()
    finally:
        if service.thread.is_alive():
            service.shutdown()
        else:
            service.server.server_close()


def test_browser_service_shutdown_endpoint_is_idempotent_while_shutdown_is_pending(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    scheduled = []

    from backlog_py.browser import service as browser_service

    def fake_schedule(server):
        scheduled.append(server)

    monkeypatch.setattr(browser_service, "_schedule_server_shutdown", fake_schedule)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before = _get_json(f"{service.root_url}api/service/status")
        first = _post_json_response(f"{service.root_url}api/service/shutdown", {})
        after = _get_json(f"{service.root_url}api/service/status")
        second = _post_json_response(f"{service.root_url}api/service/shutdown", {})
    finally:
        service.shutdown()

    assert before["shutdownInProgress"] is False
    assert before["shutdownRequestedAt"] is None
    assert first["body"] == {
        "ok": True,
        "message": "Shutdown scheduled",
        "shutdownInProgress": True,
        "alreadyScheduled": False,
    }
    assert after["shutdownInProgress"] is True
    assert isinstance(after["shutdownRequestedAt"], str)
    assert after["shutdownRequestedAt"].endswith("Z")
    assert second["body"] == {
        "ok": True,
        "message": "Shutdown already scheduled",
        "shutdownInProgress": True,
        "alreadyScheduled": True,
    }
    assert scheduled == [service.server]


def test_browser_board_sse_endpoint_reports_pending_shutdown(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    scheduled = []

    from backlog_py.browser import service as browser_service

    def fake_schedule(server):
        scheduled.append(server)

    monkeypatch.setattr(browser_service, "_schedule_server_shutdown", fake_schedule)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _post_json_response(f"{service.root_url}api/service/shutdown", {})
        response = _get_response_text(f"{service.root_url}/api/board/events")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["contentType"] == "text/event-stream; charset=utf-8"
    assert "retry: 5000\n" in response["body"]
    assert "event: shutdown\n" in response["body"]
    data_line = next(line for line in response["body"].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["shutdownInProgress"] is True
    assert payload["shutdownRequestedAt"].endswith("Z")
    assert scheduled == [service.server]


def test_browser_service_request_log_endpoint_records_recent_requests_without_query_strings(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _get_json(f"{service.root_url}health?token=secret")
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_text(f"{service.root_url}missing?secret=hidden")
        request_log = _get_json(f"{service.root_url}api/service/requests")
    finally:
        service.shutdown()

    assert exc.value.code == 404
    assert request_log["limit"] == 50
    assert request_log["requests"][-2:] == [
        {
            "method": "GET",
            "path": "/health",
            "status": 200,
            "contentType": "application/json",
            "timestamp": request_log["requests"][-2]["timestamp"],
        },
        {
            "method": "GET",
            "path": "/missing",
            "status": 404,
            "contentType": "application/json",
            "timestamp": request_log["requests"][-1]["timestamp"],
        },
    ]
    assert request_log["requests"][-2]["timestamp"].endswith("Z")
    assert request_log["requests"][-1]["timestamp"].endswith("Z")
    assert "secret" not in json.dumps(request_log)
    assert "hidden" not in json.dumps(request_log)


def test_browser_service_request_log_keeps_bounded_recent_entries(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _get_text(service.root_url)
        for index in range(60):
            _get_json(f"{service.root_url}health?index={index}")
        request_log = _get_json(f"{service.root_url}api/service/requests")
    finally:
        service.shutdown()

    assert request_log["limit"] == 50
    assert len(request_log["requests"]) == 50
    assert {entry["path"] for entry in request_log["requests"]} == {"/health"}
    assert all(entry["status"] == 200 for entry in request_log["requests"])


def test_browser_board_payload_revision_changes_after_external_task_file_update(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before = _get_json(f"{service.root_url}/api/board")
        task_file = _task_file(repo)
        task_file.write_text(
            task_file.read_text(encoding="utf-8").replace(
                "title: Example task",
                "title: Externally updated task",
            ),
            encoding="utf-8",
        )
        after = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert before["revision"] != after["revision"]
    assert after["columns"]["In Progress"][0]["title"] == "Externally updated task"


def test_browser_board_endpoint_does_not_refresh_remote_refs_during_polling(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    remote_refreshes = []

    from backlog_py.core import repository as repository_module
    from backlog_py.browser.service import start_browser_service

    monkeypatch.setattr(
        repository_module,
        "maybe_fetch_remote_refs",
        lambda project: remote_refreshes.append(project.root),
    )

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _get_json(f"{service.root_url}/api/board")
        _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert remote_refreshes == []


def test_browser_board_sse_endpoint_returns_revision_event_without_remote_refresh(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    remote_refreshes = []

    from backlog_py.core import repository as repository_module
    from backlog_py.browser.service import start_browser_service

    monkeypatch.setattr(
        repository_module,
        "maybe_fetch_remote_refs",
        lambda project: remote_refreshes.append(project.root),
    )

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        board = _get_json(f"{service.root_url}/api/board")
        response = _get_response_text(f"{service.root_url}/api/board/events")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["contentType"] == "text/event-stream; charset=utf-8"
    assert "retry: 5000\n" in response["body"]
    assert "event: revision\n" in response["body"]
    data_line = next(line for line in response["body"].splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == {"revision": board["revision"]}
    assert remote_refreshes == []


def test_browser_board_html_exposes_live_refresh_sse_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'data-board-revision="' in html
    assert "connectBoardRevisionEvents" in html
    assert 'new EventSource("/api/board/events")' in html
    assert "handleBoardRevision" in html
    assert "startBoardRevisionPolling" in html
    assert "pollBoardRevision" in html
    assert "hasOpenDialog" in html
    assert "setInterval" in html
    assert '!("EventSource" in window)' in html
    assert "/api/board" in html
    assert "/api/board/events" in html


def test_browser_board_html_exposes_sse_shutdown_client_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'addEventListener("shutdown"' in html
    assert "closeBoardRevisionEvents" in html
    assert "stopBoardRevisionPolling" in html
    assert "Server shutdown was requested" in html


def test_browser_board_html_exposes_responsive_layout_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert "@media (max-width: 720px)" in html
    assert "header { flex-direction: column;" in html
    assert ".header-actions { width: 100%; justify-content: flex-start;" in html
    assert ".header-actions button { flex: 1 1 180px;" in html
    assert ".board { grid-template-columns: 1fr;" in html
    assert ".task-actions { justify-content: flex-start;" in html
    assert "dialog { max-height: calc(100dvh - 24px);" in html
    assert ".form-actions { flex-direction: column-reverse;" in html


def test_browser_board_html_exposes_service_lifecycle_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="service-status-open"' in html
    assert 'id="service-status-dialog"' in html
    assert 'id="service-shutdown-confirm"' in html
    assert 'id="service-request-log"' in html
    assert "openServiceStatus" in html
    assert "refreshServiceRequests" in html
    assert "renderServiceRequestLog" in html
    assert "submitServiceShutdown" in html
    assert "shutdownInProgress" in html
    assert "shutdownRequestedAt" in html
    assert "/api/service/status" in html
    assert "/api/service/requests" in html
    assert "/api/service/shutdown" in html


def test_browser_task_detail_endpoint_returns_readonly_sections(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    MutableRepository.from_path(repo).replace_task_source(
        "TASK-1",
        _task_file(repo).read_text(encoding="utf-8")
        + "\n## Run History\n"
        + "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "```yaml\n"
        + "event_id: run-1\n"
        + "type: record_run\n"
        + "actor: codex\n"
        + "timestamp: '2026-06-26T18:04:00Z'\n"
        + "result: succeeded\n"
        + "task_id: TASK-1\n"
        + "```\n"
        + "Implemented and verified.\n"
        + "<!-- RUN_HISTORY_ENTRY:END -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        task = _get_json(f"{service.root_url}api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert task["id"] == "TASK-1"
    assert task["title"] == "Example task"
    assert task["path"] == "backlog/tasks/task-1 - Example-task.md"
    assert task["createdDate"] == "2026-05-10 10:00"
    assert task["description"].startswith("Implement a fixture")
    assert task["acceptanceCriteria"][0] == {
        "checked": True,
        "itemId": "1",
        "text": "Preserve completed acceptance criteria raw line",
    }
    assert task["acceptanceCriteria"][2] == {
        "checked": False,
        "itemId": None,
        "text": "Plain checklist item without an id",
    }
    assert task["definitionOfDone"][1] == {
        "checked": False,
        "itemId": "2",
        "text": "Verification recorded",
    }
    assert task["queueCategory"] == "in_workflow"
    assert task["runHistoryIssues"] == []
    assert task["runHistoryEvents"] == [
        {
            "eventId": "run-1",
            "type": "record_run",
            "actor": "codex",
            "timestamp": "2026-06-26T18:04:00Z",
            "result": "succeeded",
            "summary": "Implemented and verified.",
            "taskId": "TASK-1",
            "fromStatus": "",
            "toStatus": "",
            "splitMode": "",
            "files": [],
            "verification": [],
            "metadata": {},
        }
    ]


def test_browser_task_detail_reports_malformed_run_history_as_validation_issue(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    _task_file(repo).write_text(
        _task_file(repo).read_text(encoding="utf-8")
        + "\n## Run History\n"
        + "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n",
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        board = _get_json(f"{service.root_url}/api/board")
        task = _get_json(f"{service.root_url}api/tasks/TASK-1")
    finally:
        service.shutdown()

    board_task = board["columns"]["In Progress"][0]
    assert board_task["queueCategory"] == "invalid"
    assert [issue["code"] for issue in board_task["runHistoryIssues"]] == ["run_history_entry_unterminated"]
    assert task["runHistoryEvents"] == []
    assert [issue["code"] for issue in task["runHistoryIssues"]] == ["run_history_entry_unterminated"]


def test_browser_task_detail_endpoint_returns_markdown_html_sections(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    task_file = _task_file(repo)
    task_file.write_text(
        task_file.read_text(encoding="utf-8").replace(
            "Implement a fixture that exercises parser preservation behavior.\n"
            "This paragraph must remain untouched by a no-op render.",
            "## Rendered heading\n\n"
            "- First bullet\n"
            "- **Second** bullet\n\n"
            "```mermaid\n"
            "graph TD\n"
            "  A --> B\n"
            "```",
        ),
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        task = _get_json(f"{service.root_url}api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert "<h2>Rendered heading</h2>" in task["descriptionHtml"]
    assert "<li>First bullet</li>" in task["descriptionHtml"]
    assert "<strong>Second</strong>" in task["descriptionHtml"]
    assert 'data-mermaid-diagram="true"' in task["descriptionHtml"]
    assert 'class="mermaid"' in task["descriptionHtml"]
    assert "A --&gt; B" in task["descriptionHtml"]
    assert "<li>Keep frontmatter order stable.</li>" in task["implementationNotesHtml"]
    assert task["finalSummaryHtml"] == "<p>No final summary yet.</p>"


def test_browser_task_detail_preserves_empty_owned_description_after_notes_edit(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    repository = MutableRepository.from_path(repo)
    repository.create_task(title="Notes only task", task_id="TASK-2")
    repository.edit_task("TASK-2", notes="Edited in a scratch project.")
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        task = _get_json(f"{service.root_url}api/tasks/TASK-2")
    finally:
        service.shutdown()

    assert task["description"] == ""
    assert task["descriptionHtml"] == ""
    assert task["implementationNotes"] == "Edited in a scratch project."
    assert task["implementationNotesHtml"] == "<p>Edited in a scratch project.</p>"


def test_browser_task_detail_markdown_html_escapes_unsafe_content(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    task_file = _task_file(repo)
    task_file.write_text(
        task_file.read_text(encoding="utf-8").replace(
            "Implement a fixture that exercises parser preservation behavior.\n"
            "This paragraph must remain untouched by a no-op render.",
            "<img src=x onerror=alert(1)>\n"
            "<script>alert('x')</script>\n\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[\"<script>\"] --> B\n"
            "```",
        ),
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        task = _get_json(f"{service.root_url}api/tasks/TASK-1")
    finally:
        service.shutdown()

    html = task["descriptionHtml"]
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert 'data-mermaid-diagram="true"' in html
    assert 'class="mermaid"' in html
    assert "A[&quot;&lt;script&gt;&quot;] --&gt; B" in html


def test_browser_documents_endpoints_return_readonly_markdown_payloads(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    docs_dir = repo / "backlog" / "docs" / "guides"
    docs_dir.mkdir(parents=True)
    (docs_dir / "setup.md").write_text(
        "---\n"
        "id: DOC-SETUP\n"
        "title: Setup Guide\n"
        "type: guide\n"
        "tags:\n"
        "  - setup\n"
        "  - agents\n"
        "---\n\n"
        "## Install\n\n"
        "Run `uv sync`.\n\n"
        "```mermaid\n"
        "graph TD\n"
        "  A --> B\n"
        "```\n",
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        listing = _get_json(f"{service.root_url}/api/docs")
        detail = _get_json(f"{service.root_url}/api/docs/DOC-SETUP")
        detail_by_path = _get_json(f"{service.root_url}/api/docs/guides%2Fsetup.md")
    finally:
        service.shutdown()

    assert listing == [
        {
            "id": "DOC-SETUP",
            "title": "Setup Guide",
            "type": "guide",
            "path": "guides/setup.md",
            "tags": ["setup", "agents"],
        }
    ]
    assert detail["id"] == "DOC-SETUP"
    assert detail["title"] == "Setup Guide"
    assert detail["path"] == "guides/setup.md"
    assert detail["content"].startswith("## Install")
    assert "<h2>Install</h2>" in detail["contentHtml"]
    assert "<code>uv sync</code>" in detail["contentHtml"]
    assert 'data-mermaid-diagram="true"' in detail["contentHtml"]
    assert "A --&gt; B" in detail["contentHtml"]
    assert detail_by_path == detail


def test_document_detail_payload_omits_derived_leading_title_from_html(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    docs_dir = repo / "backlog" / "docs"
    docs_dir.mkdir()
    document_path = docs_dir / "lesson.md"
    document_path.write_text("# Lesson evidence\n\nBody text.\n", encoding="utf-8")
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    document = DocumentService(project).view_document("lesson.md")

    from backlog_py.browser.service import _document_detail_payload

    payload = _document_detail_payload(document)

    assert document.title == "Lesson evidence"
    assert document.content == "# Lesson evidence\n\nBody text."
    assert payload["content"] == "# Lesson evidence\n\nBody text."
    assert "<h1>" not in payload["contentHtml"]
    assert "<h1>Lesson evidence</h1>" not in payload["contentHtml"]
    assert "<p>Body text.</p>" in payload["contentHtml"]


def test_document_detail_payload_normalizes_whitespace_when_omitting_leading_title(tmp_path):
    document = DocumentRecord(
        id=None,
        title="Lessons: testing evidence",
        path=tmp_path / "lesson.md",
        path_relative="lesson.md",
        content="# Lessons:   testing evidence\n\nBody text.",
        body_source="# Lessons:   testing evidence\n\nBody text.",
        frontmatter={},
        raw_source="# Lessons:   testing evidence\n\nBody text.",
    )

    from backlog_py.browser.service import _document_detail_payload

    payload = _document_detail_payload(document)

    assert payload["content"] == document.content
    assert "<h1>" not in payload["contentHtml"]
    assert "<h1>Lessons:   testing evidence</h1>" not in payload["contentHtml"]
    assert "<p>Body text.</p>" in payload["contentHtml"]


def test_document_detail_payload_keeps_different_leading_heading_in_html(tmp_path):
    document = DocumentRecord(
        id=None,
        title="Document title",
        path=tmp_path / "lesson.md",
        path_relative="lesson.md",
        content="# Different heading\n\nBody text.",
        body_source="# Different heading\n\nBody text.",
        frontmatter={},
        raw_source="# Different heading\n\nBody text.",
    )

    from backlog_py.browser.service import _document_detail_payload

    payload = _document_detail_payload(document)

    assert payload["content"] == document.content
    assert "<h1>Different heading</h1>" in payload["contentHtml"]


def test_document_detail_payload_keeps_empty_leading_heading_marker_in_html(tmp_path):
    document = DocumentRecord(
        id=None,
        title="",
        path=tmp_path / "lesson.md",
        path_relative="lesson.md",
        content="# \n\nBody text.",
        body_source="# \n\nBody text.",
        frontmatter={},
        raw_source="# \n\nBody text.",
    )

    from backlog_py.browser.service import _document_detail_payload

    payload = _document_detail_payload(document)

    assert payload["content"] == document.content
    assert "<p>#</p>" in payload["contentHtml"]
    assert "<p>Body text.</p>" in payload["contentHtml"]


def test_browser_document_detail_endpoint_rejects_invalid_encoded_path(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_json(f"{service.root_url}/api/docs/%2E%2E%2Fsecret")
        body = json.loads(exc.value.read().decode("utf-8"))
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert body == {"error": "Invalid document path"}


def test_browser_decisions_endpoints_return_readonly_markdown_payloads(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    decisions_dir = repo / "backlog" / "decisions"
    decisions_dir.mkdir(parents=True)
    (decisions_dir / "decision-1 - Use-SQLite.md").write_text(
        "---\n"
        "id: decision-1\n"
        "title: Use SQLite\n"
        "date: 2026-05-20 10:00\n"
        "status: accepted\n"
        "---\n\n"
        "## Context\n\n"
        "Need durable local state.\n\n"
        "## Decision\n\n"
        "Use **SQLite**.\n\n"
        "## Consequences\n\n"
        "- Simple backups\n",
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        listing = _get_json(f"{service.root_url}/api/decisions")
        detail = _get_json(f"{service.root_url}/api/decisions/1")
    finally:
        service.shutdown()

    assert listing == [
        {
            "id": "decision-1",
            "title": "Use SQLite",
            "status": "accepted",
            "date": "2026-05-20 10:00",
        }
    ]
    assert detail["id"] == "decision-1"
    assert detail["title"] == "Use SQLite"
    assert detail["path"] == "decision-1 - Use-SQLite.md"
    assert detail["context"] == "Need durable local state."
    assert detail["decision"] == "Use **SQLite**."
    assert "<strong>SQLite</strong>" in detail["decisionHtml"]
    assert "<li>Simple backups</li>" in detail["consequencesHtml"]


def test_browser_board_html_exposes_document_and_decision_readonly_dialogs(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="documents-open"' in html
    assert 'id="documents-dialog"' in html
    assert 'id="documents-list"' in html
    assert 'id="document-detail"' in html
    assert 'id="decisions-open"' in html
    assert 'id="decisions-dialog"' in html
    assert 'id="decisions-list"' in html
    assert 'id="decision-detail"' in html
    assert "openDocuments" in html
    assert "renderDocumentsList" in html
    assert "openDecisions" in html
    assert "renderDecisionsList" in html
    assert "/api/docs" in html
    assert "/api/decisions" in html


def test_browser_board_html_exposes_readonly_task_detail_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="task-dialog"' in html
    assert 'data-task-details="TASK-1"' in html
    assert "openTaskDetails" in html
    assert "Acceptance Criteria" in html
    assert "/api/tasks/" in html


def test_browser_board_html_exposes_orchestration_readonly_visibility(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="queue-category-filter"' in html
    assert "queue-badge" in html
    assert 'id="task-dialog-queue-category"' in html
    assert 'id="task-dialog-run-history"' in html
    assert "renderRunHistoryEvents" in html
    assert "orchestration_claim" not in html
    assert "orchestration_release" not in html
    assert "orchestration_transition" not in html


def test_browser_board_html_exposes_markdown_detail_sections(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="task-dialog-description-html"' in html
    assert 'id="task-dialog-implementation-notes"' in html
    assert 'id="task-dialog-final-summary"' in html
    assert "setHtml" in html
    assert "descriptionHtml" in html
    assert "implementationNotesHtml" in html
    assert "finalSummaryHtml" in html


def test_browser_markdown_preview_endpoint_returns_safe_rendered_html(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/markdown/preview",
            {"markdown": "## Preview\n\n<script>alert(1)</script>\n\n- item"},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200
    html = response["body"]["html"]
    assert "<h2>Preview</h2>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<li>item</li>" in html


def test_browser_markdown_preview_endpoint_renders_safe_links(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/markdown/preview",
            {"markdown": "[Docs](docs/setup.md) [Bad](javascript:alert(1))"},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200
    html = response["body"]["html"]
    assert '<a href="docs/setup.md">Docs</a>' in html
    assert '<a href="#">Bad</a>' in html
    assert "javascript:alert" not in html


def test_browser_markdown_preview_endpoint_does_not_render_links_inside_code_spans(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/markdown/preview",
            {"markdown": "`[Docs](docs/setup.md)` [Docs](docs/setup.md)"},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200
    html = response["body"]["html"]
    assert "<code>[Docs](docs/setup.md)</code>" in html
    assert html.count('<a href="docs/setup.md">Docs</a>') == 1


def test_browser_markdown_preview_endpoint_rejects_cross_origin(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/markdown/preview",
                {"markdown": "**Preview**"},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403


def test_browser_markdown_preview_endpoint_rejects_non_string_markdown(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/markdown/preview", {"markdown": ["not", "text"]})
    finally:
        service.shutdown()

    assert exc.value.code == 400


def test_browser_board_html_exposes_mermaid_renderer_hook(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert "renderMermaidDiagrams" in html
    # Mermaid is served from the local vendored asset by default (no CDN).
    assert 'data-mermaid-url="/assets/mermaid.min.js"' in html
    assert "cdn.jsdelivr.net" not in html
    assert 'querySelectorAll("[data-mermaid-diagram] .mermaid")' in html
    assert 'securityLevel: "strict"' in html
    assert "mermaidLoadPromise = null;\n        diagrams.forEach" in html


def test_browser_task_create_endpoint_creates_task_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks",
            {
                "title": "Browser created task",
                "status": "To Do",
                "description": "Created through the browser service.",
                "acceptanceCriteria": ["Visible on the board"],
                "assignees": ["codex"],
                "labels": ["browser"],
                "priority": "medium",
            },
        )
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert response["status"] == 201
    task = response["body"]["task"]
    assert lock_operations == [(repo, "browser_task_create")]
    assert task["id"] == "TASK-2"
    assert task["title"] == "Browser created task"
    assert task["description"] == "Created through the browser service."
    assert task["acceptanceCriteria"] == [
        {"checked": False, "itemId": "1", "text": "Visible on the board"}
    ]
    assert board["columns"]["To Do"][0]["id"] == "TASK-2"
    assert "title: Browser created task" in _created_task_file(repo).read_text(encoding="utf-8")


@pytest.mark.parametrize("source_kind", ["configured", "default", "local"])
def test_browser_task_endpoints_round_trip_every_exact_assignable_status(tmp_path, source_kind):
    repo = _copy_fixture_repo(tmp_path)
    exact_status = " Intentional "
    statuses = ["Configured"]
    default = "Ready"
    if source_kind == "configured":
        statuses.insert(0, exact_status)
    elif source_kind == "default":
        default = exact_status
    else:
        _replace_browser_fixture_task_status(repo, exact_status)
    project = _configure_statuses(repo, statuses, default=default)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        board = _get_json(f"{service.root_url}/api/board")
        for index, status in enumerate(board["assignableStatuses"], start=2):
            created = _post_json_response(
                f"{service.root_url}/api/tasks",
                {"title": f"Exact status {index}", "status": status},
            )
            edited = _post_json_response(
                f"{service.root_url}/api/tasks/TASK-{index}/edit",
                {"status": status},
            )
            detail = _get_json(f"{service.root_url}/api/tasks/TASK-{index}")
            assert created["body"]["task"]["status"] == status
            assert edited["body"]["task"]["status"] == status
            assert detail["status"] == status
    finally:
        service.shutdown()

    repository = MutableRepository(project)
    assert exact_status in board["assignableStatuses"]
    assert [repository.get_task(f"TASK-{index}").status for index in range(2, 2 + len(board["assignableStatuses"]))] == board[
        "assignableStatuses"
    ]


@pytest.mark.parametrize("status_value", ["", " \t ", None, 17])
@pytest.mark.parametrize("operation", ["create", "edit"])
def test_browser_task_endpoints_reject_explicit_invalid_status_without_mutation(
    tmp_path,
    operation,
    status_value,
):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, ["To Do", "In Progress"], default="To Do")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        url = f"{service.root_url}/api/tasks"
        payload = {"title": "Must not exist", "status": status_value}
        if operation == "edit":
            url = f"{service.root_url}/api/tasks/TASK-1/edit"
            payload = {"status": status_value}
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(url, payload)
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _backlog_snapshot(repo) == before


def test_browser_task_endpoints_distinguish_omitted_status(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    exact_default = " Default "
    project = _configure_statuses(repo, ["Configured"], default=exact_default)
    original_status = MutableRepository(project).get_task("TASK-1").status

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        created = _post_json_response(
            f"{service.root_url}/api/tasks",
            {"title": "Default status"},
        )
        edited = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/edit",
            {"title": "Status preserved"},
        )
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert created["body"]["task"]["status"] == exact_default
    assert edited["body"]["task"]["status"] == original_status
    assert detail["status"] == original_status
    assert MutableRepository(project).get_task("TASK-2").status == exact_default


def test_browser_task_create_endpoint_rejects_invalid_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = sorted(path.name for path in (repo / "backlog" / "tasks").glob("*.md"))

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks", {"title": "   "})
    finally:
        service.shutdown()

    assert exc.value.code == 400
    after = sorted(path.name for path in (repo / "backlog" / "tasks").glob("*.md"))
    assert after == before


def test_browser_task_create_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = sorted(path.name for path in (repo / "backlog" / "tasks").glob("*.md"))

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks",
                {"title": "Rejected browser task"},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    after = sorted(path.name for path in (repo / "backlog" / "tasks").glob("*.md"))
    assert after == before


def test_browser_board_html_exposes_task_create_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="task-create-dialog"' in html
    assert 'id="task-create-form"' in html
    assert 'name="title"' in html
    assert 'name="status"' in html
    assert 'name="description"' in html
    assert "submitTaskCreate" in html
    assert "/api/tasks" in html


def test_browser_task_edit_endpoint_updates_owned_fields_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/edit",
            {
                "title": "Browser edited task",
                "status": "To Do",
                "description": "Edited through the browser service.",
                "acceptanceCriteria": ["Edited criterion"],
            },
        )
        board = _get_json(f"{service.root_url}/api/board")
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert response["status"] == 200
    task = response["body"]["task"]
    assert lock_operations == [(repo, "browser_task_edit")]
    assert task["id"] == "TASK-1"
    assert task["title"] == "Browser edited task"
    assert task["status"] == "To Do"
    assert task["description"] == "Edited through the browser service."
    assert task["acceptanceCriteria"] == [
        {"checked": False, "itemId": "1", "text": "Edited criterion"}
    ]
    assert detail["title"] == "Browser edited task"
    assert board["columns"]["In Progress"] == []
    assert board["columns"]["To Do"][0]["id"] == "TASK-1"
    edited_source = _task_file(repo).read_text(encoding="utf-8")
    assert "title: Browser edited task" in edited_source
    assert "status: To Do" in edited_source
    assert "Edited through the browser service." in edited_source
    assert "- [ ] #1 Edited criterion" in edited_source
    assert "Preserve completed acceptance criteria raw line" not in edited_source


def test_browser_task_edit_endpoint_updates_rich_markdown_sections_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/edit",
            {
                "implementationNotes": "## Browser notes\n\n- **Keep** parser sections",
                "finalSummary": "Browser summary with `code`.",
            },
        )
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert lock_operations == [(repo, "browser_task_edit")]
    assert response["body"]["task"]["implementationNotes"] == "## Browser notes\n\n- **Keep** parser sections"
    assert response["body"]["task"]["finalSummary"] == "Browser summary with `code`."
    assert "<h2>Browser notes</h2>" in detail["implementationNotesHtml"]
    assert "<strong>Keep</strong>" in detail["implementationNotesHtml"]
    assert "<code>code</code>" in detail["finalSummaryHtml"]
    edited_source = _task_file(repo).read_text(encoding="utf-8")
    assert "## Browser notes" in edited_source
    assert "Browser summary with `code`." in edited_source


def test_browser_task_edit_endpoint_updates_metadata_fields_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/edit",
            {
                "assignees": ["codex", "reviewer"],
                "labels": ["browser", "metadata"],
                "priority": "high",
                "milestone": "Release 1",
            },
        )
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert lock_operations == [(repo, "browser_task_edit")]
    assert response["body"]["task"]["assignees"] == ["codex", "reviewer"]
    assert response["body"]["task"]["labels"] == ["browser", "metadata"]
    assert response["body"]["task"]["priority"] == "high"
    assert response["body"]["task"]["milestone"] == "Release 1"
    assert detail["assignees"] == ["codex", "reviewer"]
    assert detail["labels"] == ["browser", "metadata"]
    assert board["columns"]["In Progress"][0]["priority"] == "high"
    edited_source = _task_file(repo).read_text(encoding="utf-8")
    assert "assignee:\n- codex\n- reviewer" in edited_source
    assert "labels:\n- browser\n- metadata" in edited_source
    assert "priority: high" in edited_source
    assert "milestone: Release 1" in edited_source


def test_browser_task_edit_endpoint_clears_milestone_with_empty_string(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _post_json_response(f"{service.root_url}/api/tasks/TASK-1/edit", {"milestone": "Release 1"})
        response = _post_json_response(f"{service.root_url}/api/tasks/TASK-1/edit", {"milestone": ""})
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["body"]["task"]["milestone"] is None
    assert detail["milestone"] is None
    assert "milestone:" not in _task_file(repo).read_text(encoding="utf-8")


def test_browser_task_edit_endpoint_rejects_invalid_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/edit",
                {"title": "", "acceptanceCriteria": "Edited criterion"},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_task_edit_endpoint_rejects_invalid_metadata_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/edit",
                {"title": "Should not write", "assignees": "codex"},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_task_edit_endpoint_rejects_invalid_rich_section_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/edit",
                {"title": "Should not write", "implementationNotes": ["not", "a", "string"]},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_task_edit_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/edit",
                {"title": "Rejected browser edit"},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_board_html_exposes_task_edit_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="task-edit-dialog"' in html
    assert 'id="task-edit-form"' in html
    assert 'data-task-edit="TASK-1"' in html
    edit_form = html.split('<form class="task-form" id="task-edit-form">', maxsplit=1)[1].split(
        '<div class="form-actions">',
        maxsplit=1,
    )[0]
    edit_submit = html.split("async function submitTaskEdit", maxsplit=1)[1].split(
        "async function submitTaskChecklistState",
        maxsplit=1,
    )[0]
    assert 'name="title"' in html
    assert 'name="status"' in html
    assert 'name="description"' in html
    assert 'name="acceptanceCriteria"' in html
    assert 'name="implementationNotes"' in html
    assert 'name="finalSummary"' in html
    assert 'name="assignees"' in edit_form
    assert 'name="labels"' in edit_form
    assert 'name="priority"' in edit_form
    assert 'name="milestone"' in edit_form
    assert "taskEditForm.elements.implementationNotes.value" in html
    assert "taskEditForm.elements.finalSummary.value" in html
    assert "taskEditForm.elements.assignees.value" in html
    assert "taskEditForm.elements.labels.value" in html
    assert "taskEditForm.elements.priority.value" in html
    assert "taskEditForm.elements.milestone.value" in html
    assert "implementationNotes: String(data.get(\"implementationNotes\") || \"\")" in edit_submit
    assert "finalSummary: String(data.get(\"finalSummary\") || \"\")" in edit_submit
    assert "assignees: metadataList(data.get(\"assignees\"))" in edit_submit
    assert "labels: metadataList(data.get(\"labels\"))" in edit_submit
    assert "priority: String(data.get(\"priority\") || \"\")" in edit_submit
    assert "milestone: String(data.get(\"milestone\") || \"\")" in edit_submit
    assert "submitTaskEdit" in html
    assert "/api/tasks/" in html


def test_browser_board_html_exposes_markdown_edit_toolbar(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert html.count('data-markdown-toolbar="true"') >= 4
    assert html.count('data-markdown-input="true"') >= 4
    assert 'data-markdown-field="description"' in html
    assert 'data-markdown-field="implementationNotes"' in html
    assert 'data-markdown-field="finalSummary"' in html
    for command in ("bold", "italic", "code", "bullet", "numbered", "heading", "link"):
        assert f'data-markdown-command="{command}"' in html
    assert "function applyMarkdownFormat(textarea, command)" in html
    assert "document.querySelectorAll(\"[data-markdown-command]\")" in html
    assert "textarea.setSelectionRange" in html


def test_browser_board_html_exposes_markdown_edit_preview_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert html.count('data-markdown-editor="true"') >= 4
    assert html.count('data-markdown-mode="edit"') >= 4
    assert html.count('data-markdown-mode="preview"') >= 4
    assert html.count('data-markdown-preview-for=') >= 4
    assert 'data-markdown-preview-for="task-create-description"' in html
    assert 'data-markdown-preview-for="task-edit-description"' in html
    assert 'data-markdown-preview-for="task-edit-implementation-notes"' in html
    assert 'data-markdown-preview-for="task-edit-final-summary"' in html
    assert "async function renderMarkdownPreview(textarea)" in html
    assert "function showMarkdownPreview(textarea)" in html
    assert "function showMarkdownEdit(textarea)" in html
    assert 'fetch("/api/markdown/preview"' in html


def test_browser_board_html_exposes_markdown_rich_editor_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert html.count('data-markdown-mode="rich"') >= 4
    assert html.count('contenteditable="true"') >= 4
    assert html.count('data-markdown-rich-for=') >= 4
    assert 'data-markdown-rich-for="task-create-description"' in html
    assert 'data-markdown-rich-for="task-edit-description"' in html
    assert 'data-markdown-rich-for="task-edit-implementation-notes"' in html
    assert 'data-markdown-rich-for="task-edit-final-summary"' in html
    assert "function markdownToRichHtml(markdown)" in html
    assert "function richHtmlToMarkdown(root)" in html
    assert "function showMarkdownRich(textarea)" in html
    assert "function syncRichEditorToTextarea(textarea)" in html


def test_browser_board_html_syncs_markdown_rich_editor_before_submit_and_preview(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert "function syncAllRichEditors(root)" in html
    assert "syncRichEditorToTextarea(textarea);" in html
    assert "syncAllRichEditors(taskCreateForm);" in html
    assert "syncAllRichEditors(taskEditForm);" in html
    preview_block = html[html.index("function showMarkdownPreview(textarea)") : html.index("function showMarkdownEdit(textarea)")]
    assert "syncRichEditorToTextarea(textarea);" in preview_block


def test_browser_task_archive_endpoint_archives_task_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(f"{service.root_url}/api/tasks/TASK-1/archive", {})
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert response["status"] == 200
    task = response["body"]["task"]
    assert lock_operations == [(repo, "browser_task_archive")]
    assert task["id"] == "TASK-1"
    assert task["path"] == "backlog/archive/tasks/task-1 - Example-task.md"
    assert board["columns"]["In Progress"] == []
    assert _archived_task_file(repo).is_file()
    assert not _task_file_exists(repo)


def test_browser_task_archive_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/archive",
                {},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert _task_file(repo).read_text(encoding="utf-8") == before
    assert not _archived_task_file(repo).exists()


def test_browser_task_archive_endpoint_returns_not_found_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks/TASK-404/archive", {})
    finally:
        service.shutdown()

    assert exc.value.code == 404
    assert _task_file(repo).read_text(encoding="utf-8") == before
    assert not _archived_task_file(repo).exists()


def test_browser_board_html_exposes_task_archive_confirmation_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="task-archive-dialog"' in html
    assert 'data-task-archive="TASK-1"' in html
    assert 'id="task-archive-confirm"' in html
    assert 'id="task-archive-cancel"' in html
    assert "submitTaskArchive" in html
    assert "/api/tasks/" in html


def test_browser_task_checklist_endpoint_updates_acceptance_criteria_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/checklist",
            {"section": "acceptanceCriteria", "index": 2, "checked": True},
        )
        task = _get_json(f"{service.root_url}/api/tasks/TASK-1")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert lock_operations == [(repo, "browser_task_checklist")]
    assert response["body"]["task"]["mutable"] is True
    assert response["body"]["task"]["acceptanceCriteria"][1]["checked"] is True
    assert task["mutable"] is True
    assert task["acceptanceCriteria"][1]["checked"] is True
    assert "- [x] #2 Preserve incomplete acceptance criteria raw line" in _task_file(repo).read_text(encoding="utf-8")


def test_browser_task_checklist_rejects_newer_branch_winner_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    _install_newer_branch_task(monkeypatch, repo)
    before = _backlog_snapshot(repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/checklist",
                {"section": "acceptanceCriteria", "index": 2, "checked": True},
            )
        error = json.loads(exc.value.read().decode("utf-8"))
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert "read only" in error["error"].casefold()
    assert lock_operations == [(repo, "browser_task_checklist")]
    assert _backlog_snapshot(repo) == before


@pytest.mark.parametrize(
    ("suffix", "payload", "operation"),
    [
        ("edit", {"title": "Rejected edit"}, "browser_task_edit"),
        ("archive", {}, "browser_task_archive"),
        ("status", {"status": "Done"}, "browser_task_status"),
    ],
)
def test_browser_rejects_other_newer_branch_winner_mutations_under_project_lock(
    tmp_path,
    monkeypatch,
    suffix,
    payload,
    operation,
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    _install_newer_branch_task(monkeypatch, repo)
    before = _backlog_snapshot(repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks/TASK-1/{suffix}", payload)
        error = json.loads(exc.value.read().decode("utf-8"))
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert "read only" in error["error"].casefold()
    assert lock_operations == [(repo, operation)]
    assert _backlog_snapshot(repo) == before


@pytest.mark.parametrize(
    ("suffix", "payload", "operation"),
    [
        (
            "checklist",
            {"section": "acceptanceCriteria", "index": 1, "checked": True},
            "browser_task_checklist",
        ),
        ("edit", {"title": "Rejected edit"}, "browser_task_edit"),
        ("archive", {}, "browser_task_archive"),
        ("status", {"status": "Done"}, "browser_task_status"),
    ],
)
def test_browser_rejects_branch_only_task_mutations_under_project_lock(
    tmp_path,
    monkeypatch,
    suffix,
    payload,
    operation,
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    _install_branch_only_task(monkeypatch)
    before = _backlog_snapshot(repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks/TASK-99/{suffix}", payload)
        error = json.loads(exc.value.read().decode("utf-8"))
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert "read only" in error["error"].casefold()
    assert lock_operations == [(repo, operation)]
    assert _backlog_snapshot(repo) == before


def test_browser_task_checklist_endpoint_unchecks_acceptance_criteria(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/checklist",
            {"section": "acceptanceCriteria", "index": 1, "checked": False},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["body"]["task"]["acceptanceCriteria"][0]["checked"] is False
    assert "- [ ] #1 Preserve completed acceptance criteria raw line" in _task_file(repo).read_text(encoding="utf-8")


def test_browser_task_checklist_endpoint_updates_definition_of_done_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/tasks/TASK-1/checklist",
            {"section": "definitionOfDone", "index": 2, "checked": True},
        )
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert lock_operations == [(repo, "browser_task_checklist")]
    assert response["body"]["task"]["definitionOfDone"][1]["checked"] is True
    assert "- [x] #2 Verification recorded" in _task_file(repo).read_text(encoding="utf-8")


def test_browser_task_checklist_endpoint_rejects_invalid_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/checklist",
                {"section": "acceptanceCriteria", "index": 0, "checked": True},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_task_checklist_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/checklist",
                {"section": "definitionOfDone", "index": 2, "checked": True},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_dod_defaults_endpoint_returns_configured_defaults(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    replace_definition_of_done_defaults(project, ["Tests pass", "Docs updated"])

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _get_json(f"{service.root_url}/api/settings/dod-defaults")
    finally:
        service.shutdown()

    assert response == {"items": ["Tests pass", "Docs updated"]}


def test_browser_dod_defaults_update_endpoint_writes_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/settings/dod-defaults",
            {"items": [" Tests pass ", "", "Docs updated"]},
        )
    finally:
        service.shutdown()

    assert response == {"status": 200, "body": {"items": ["Tests pass", "Docs updated"]}}
    assert lock_operations == [(repo, "browser_dod_defaults_update")]
    assert get_definition_of_done_defaults(discover_project(Path.cwd(), explicit_cwd=repo)) == [
        "Tests pass",
        "Docs updated",
    ]


def test_browser_dod_defaults_update_rejects_invalid_payload_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    replace_definition_of_done_defaults(project, ["Tests pass"])
    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        invalid_payloads = (
            {"items": "Tests pass"},
            {"items": ["Docs updated", 7]},
        )
        for payload in invalid_payloads:
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post_json(f"{service.root_url}/api/settings/dod-defaults", payload)
            assert exc.value.code == 400
            assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before
    finally:
        service.shutdown()


def test_browser_dod_defaults_update_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    replace_definition_of_done_defaults(project, ["Tests pass"])
    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/dod-defaults",
                {"items": ["Docs updated"]},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before


def test_browser_config_settings_endpoint_returns_safe_values(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "defaultAssignee", "codex")
    set_config_value(project, "defaultPort", "6543")
    set_config_value(project, "autoOpenBrowser", "false")
    set_config_value(project, "autoCommit", "true")
    set_config_value(project, "remoteOperations", "true")
    set_config_value(project, "checkActiveBranches", "true")
    set_config_value(project, "activeBranchDays", "14")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _get_json(f"{service.root_url}/api/settings/config")
    finally:
        service.shutdown()

    assert response == {
        "settings": {
            "activeBranchDays": 14,
            "autoCommit": True,
            "autoOpenBrowser": False,
            "checkActiveBranches": True,
            "dateFormat": "yyyy-mm-dd",
            "defaultAssignee": "codex",
            "defaultPort": 6543,
            "defaultStatus": "To Do",
            "defaultStatusKey": "to do",
            "includeDatetimeInDates": True,
            "projectName": "basic-fixture",
            "remoteOperations": True,
            "statusRows": [
                {"name": "To Do", "taskCount": 0},
                {"name": "In Progress", "taskCount": 1},
                {"name": "Done", "taskCount": 0},
            ],
            "statusKeys": ["to do", "in progress", "done"],
            "statuses": ["To Do", "In Progress", "Done"],
            "zeroPaddedIds": None,
        }
    }


def test_browser_status_key_endpoint_uses_python_casefold_identity(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        values = ["ı", "i", "ẞ", "ß", "SS", "ΟΣ", "οσ", "ſ", "s", "Café", "Cafe\u0301"]
        keys = {
            value: _get_json(
                f"{service.root_url}/api/settings/status-key?value={urllib.parse.quote(value)}"
            )["key"]
            for value in values
        }
    finally:
        service.shutdown()

    assert keys["ı"] != keys["i"]
    assert keys["ẞ"] == keys["ß"] == keys["SS"]
    assert keys["ΟΣ"] == keys["οσ"]
    assert keys["ſ"] == keys["s"]
    assert keys["Café"] == keys["Cafe\u0301"]


def test_browser_config_status_rows_count_only_active_local_tasks(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, ["Ready", "In Progress", "Done"], default="Ready")
    set_config_value(project, "checkActiveBranches", "true")
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    archived = MutableRepository(project).create_task(title="Archived task", status="Ready")
    MutableRepository(project).archive_task(archived.id)
    completed = MutableRepository(project).create_task(title="Completed task", status="Done")
    MutableRepository(project).complete_task(completed.id)
    _install_branch_only_task(monkeypatch)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        settings = _get_json(f"{service.root_url}/api/settings/config")["settings"]
    finally:
        service.shutdown()

    assert settings["statuses"] == ["Ready", "In Progress", "Done"]
    assert settings["statusRows"] == [
        {"name": "Ready", "taskCount": 0},
        {"name": "In Progress", "taskCount": 1},
        {"name": "Done", "taskCount": 0},
    ]


@pytest.mark.parametrize("configured_statuses", [None, []])
def test_browser_config_status_rows_derive_task_order_and_append_default(tmp_path, configured_statuses):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, configured_statuses, default="Ready")
    MutableRepository(project).create_task(title="Review task", status="Review")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        settings = _get_json(f"{service.root_url}/api/settings/config")["settings"]
    finally:
        service.shutdown()

    assert settings["statuses"] == []
    assert settings["statusRows"] == [
        {"name": "In Progress", "taskCount": 1},
        {"name": "Review", "taskCount": 1},
        {"name": "Ready", "taskCount": 0},
    ]


def test_browser_config_status_rows_append_default_case_insensitively(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, None, default="in progress")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        rows = _get_json(f"{service.root_url}/api/settings/config")["settings"]["statusRows"]
    finally:
        service.shutdown()

    assert rows == [{"name": "In Progress", "taskCount": 1}]


def test_browser_config_status_rows_deduplicate_and_count_unicode_equivalents(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, None, default="Ready")
    _replace_browser_fixture_task_status(repo, "Café")
    MutableRepository(project).create_task(title="Equivalent status", status="Cafe\u0301")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        rows = _get_json(f"{service.root_url}/api/settings/config")["settings"]["statusRows"]
    finally:
        service.shutdown()

    assert rows == [
        {"name": "Café", "taskCount": 2},
        {"name": "Ready", "taskCount": 0},
    ]


def test_browser_config_status_rows_contain_default_when_project_has_no_active_tasks(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, None, default="Ready")
    _task_file(repo).unlink()

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        rows = _get_json(f"{service.root_url}/api/settings/config")["settings"]["statusRows"]
    finally:
        service.shutdown()

    assert rows == [{"name": "Ready", "taskCount": 0}]


def test_browser_config_status_rows_use_fresh_config_project_snapshot(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    observed_defaults = []
    original_repository = browser_service.MutableRepository

    def recording_repository(project, **kwargs):
        observed_defaults.append(project.config.default_status)
        return original_repository(project, **kwargs)

    monkeypatch.setattr(browser_service, "MutableRepository", recording_repository)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    set_config_value(project, "defaultStatus", "Ready")
    try:
        settings = _get_json(f"{service.root_url}/api/settings/config")["settings"]
    finally:
        service.shutdown()

    assert settings["defaultStatus"] == "Ready"
    assert observed_defaults == ["Ready"]


def test_browser_config_settings_update_endpoint_writes_safe_values_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/settings/config",
            {
                "settings": {
                    "projectName": "Browser project",
                    "defaultAssignee": "codex",
                    "defaultStatus": "Ready",
                    "dateFormat": "yyyy-mm-dd",
                    "includeDatetimeInDates": False,
                    "defaultPort": 6543,
                    "autoOpenBrowser": False,
                    "zeroPaddedIds": 4,
                    "autoCommit": True,
                    "remoteOperations": True,
                    "checkActiveBranches": True,
                    "activeBranchDays": 14,
                    "statuses": ["Ready", "In Progress", "Done"],
                }
            },
        )
        updated = _get_json(f"{service.root_url}/api/settings/config")
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["body"]["settings"]["projectName"] == "Browser project"
    assert response["body"]["settings"]["defaultPort"] == 6543
    assert response["body"]["settings"]["autoCommit"] is True
    assert response["body"]["settings"]["remoteOperations"] is True
    assert response["body"]["settings"]["checkActiveBranches"] is True
    assert response["body"]["settings"]["activeBranchDays"] == 14
    assert response["body"]["settings"]["statuses"] == ["Ready", "In Progress", "Done"]
    assert updated["settings"]["includeDatetimeInDates"] is False
    assert updated["settings"]["zeroPaddedIds"] == 4
    assert updated["settings"]["autoCommit"] is True
    assert updated["settings"]["remoteOperations"] is True
    assert updated["settings"]["checkActiveBranches"] is True
    assert updated["settings"]["activeBranchDays"] == 14
    assert lock_operations == [(repo, "browser_config_settings_update")]


def test_browser_config_settings_update_refreshes_server_project(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        _post_json_response(
            f"{service.root_url}/api/settings/config",
            {"settings": {"statuses": ["Ready", "In Progress", "Done"], "defaultStatus": "Ready"}},
        )
        board = _get_json(f"{service.root_url}/api/board")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert board["statuses"][:3] == ["Ready", "In Progress", "Done"]
    assert '<option value="Ready" selected>Ready</option>' in html


def test_browser_config_status_pair_trims_and_canonicalizes_submitted_values(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"statuses": [" Ready ", " In Progress "], "defaultStatus": "ready"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["statuses"] == ["Ready", "In Progress"]
    assert response["settings"]["defaultStatus"] == "Ready"


@pytest.mark.parametrize(
    "statuses",
    [
        [],
        ["", " "],
        ["To Do", "to do", "In Progress"],
        ["Café", "Cafe\u0301", "In Progress"],
    ],
)
def test_browser_config_status_pair_rejects_empty_or_duplicate_statuses_without_writing(
    tmp_path, monkeypatch, statuses
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    config_path = repo / "backlog" / "config.yml"
    before = config_path.read_bytes()

    from backlog_py.browser import service as browser_service

    writes = []
    original_set_config_values = browser_service.set_config_values

    def tracking_set_config_values(project, updates):
        writes.append(dict(updates))
        return original_set_config_values(project, updates)

    monkeypatch.setattr(browser_service, "set_config_values", tracking_set_config_values)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/config",
                {"settings": {"statuses": statuses, "defaultStatus": "To Do"}},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert config_path.read_bytes() == before
    assert writes == []


def test_browser_config_status_pair_accepts_unicode_equivalent_in_use_status(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, ["Café"], default="Café")
    _replace_browser_fixture_task_status(repo, "Café")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"statuses": ["Cafe\u0301"], "defaultStatus": "Cafe\u0301"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["statuses"] == ["Cafe\u0301"]
    assert response["settings"]["defaultStatus"] == "Cafe\u0301"
    assert response["settings"]["statusRows"] == [{"name": "Cafe\u0301", "taskCount": 1}]


@pytest.mark.parametrize(
    "statuses",
    [
        ["To Do", "Done"],
        ["In Progress", "Done"],
    ],
)
def test_browser_config_status_pair_rejects_removing_in_use_or_default_status_without_writing(
    tmp_path, monkeypatch, statuses
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    config_path = repo / "backlog" / "config.yml"
    before = config_path.read_bytes()

    from backlog_py.browser import service as browser_service

    writes = []
    original_set_config_values = browser_service.set_config_values

    def tracking_set_config_values(project, updates):
        writes.append(dict(updates))
        return original_set_config_values(project, updates)

    monkeypatch.setattr(browser_service, "set_config_values", tracking_set_config_values)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/config",
                {"settings": {"statuses": statuses}},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 409
    assert config_path.read_bytes() == before
    assert writes == []


def test_browser_config_status_pair_canonicalizes_only_default_update(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"defaultStatus": "done"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["defaultStatus"] == "Done"


def test_browser_config_status_pair_rejects_only_default_outside_configured_statuses(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    config_path = repo / "backlog" / "config.yml"
    before = config_path.read_bytes()

    from backlog_py.browser import service as browser_service

    writes = []
    original_set_config_values = browser_service.set_config_values

    def tracking_set_config_values(project, updates):
        writes.append(dict(updates))
        return original_set_config_values(project, updates)

    monkeypatch.setattr(browser_service, "set_config_values", tracking_set_config_values)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/config",
                {"settings": {"defaultStatus": "Ready"}},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 409
    assert config_path.read_bytes() == before
    assert writes == []


@pytest.mark.parametrize("configured_statuses", [None, []])
def test_browser_config_status_pair_accepts_only_default_without_configured_statuses(
    tmp_path, configured_statuses
):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, configured_statuses, default="To Do")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"defaultStatus": "Ready"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["defaultStatus"] == "Ready"
    assert response["settings"]["statuses"] == []


def test_browser_config_status_pair_validation_skips_unrelated_updates(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, ["To Do"], default="Missing")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"projectName": "Status-independent update"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["projectName"] == "Status-independent update"
    assert response["settings"]["defaultStatus"] == "Missing"


def test_browser_config_status_pair_validation_uses_fresh_config_project_snapshot(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    observed_defaults = []
    original_repository = browser_service.MutableRepository

    def recording_repository(project, **kwargs):
        observed_defaults.append(project.config.default_status)
        return original_repository(project, **kwargs)

    monkeypatch.setattr(browser_service, "MutableRepository", recording_repository)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    set_config_value(project, "statuses", json.dumps(["Ready", "In Progress"]))
    set_config_value(project, "defaultStatus", "Ready")
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {"settings": {"statuses": ["Ready", "In Progress"], "defaultStatus": "Ready"}},
        )
    finally:
        service.shutdown()

    assert response["settings"]["defaultStatus"] == "Ready"
    assert observed_defaults == ["Ready", "Ready"]


def test_browser_config_settings_update_calls_atomic_writer_once_and_refreshes_project(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    writes = []
    original_set_config_values = browser_service.set_config_values

    def tracking_set_config_values(project, updates):
        writes.append(dict(updates))
        return original_set_config_values(project, updates)

    monkeypatch.setattr(browser_service, "set_config_values", tracking_set_config_values)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    original_server_project = service.server.project
    try:
        response = _post_json(
            f"{service.root_url}/api/settings/config",
            {
                "settings": {
                    "projectName": "Atomic browser update",
                    "statuses": ["Ready", "In Progress", "Done"],
                    "defaultStatus": "ready",
                }
            },
        )
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert writes == [
        {
            "projectName": "Atomic browser update",
            "statuses": json.dumps(["Ready", "In Progress", "Done"]),
            "defaultStatus": "Ready",
        }
    ]
    assert service.server.project is not original_server_project
    assert response["settings"]["projectName"] == "Atomic browser update"
    assert response["settings"]["defaultStatus"] == "Ready"
    assert board["project"]["name"] == "Atomic browser update"
    assert board["statuses"][:3] == ["Ready", "In Progress", "Done"]


def test_browser_config_settings_concurrent_responses_keep_request_snapshot_and_latest_project(
    tmp_path, monkeypatch
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    first_write_released = threading.Event()
    release_first_response = threading.Event()
    original_lock = browser_service.with_project_write_lock

    def interleaving_lock(project, operation, fn):
        result = original_lock(project, operation, fn)
        if operation == "browser_config_settings_update" and result.config.project_name == "First request":
            first_write_released.set()
            assert release_first_response.wait(timeout=5)
        return result

    monkeypatch.setattr(browser_service, "with_project_write_lock", interleaving_lock)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    first_response = None
    second_response = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                _post_json,
                f"{service.root_url}/api/settings/config",
                {"settings": {"projectName": "First request"}},
            )
            try:
                assert first_write_released.wait(timeout=5)
                second = executor.submit(
                    _post_json,
                    f"{service.root_url}/api/settings/config",
                    {"settings": {"projectName": "Second request"}},
                )
                second_response = second.result(timeout=5)
            finally:
                release_first_response.set()
            first_response = first.result(timeout=5)
    finally:
        service.shutdown()

    assert first_response["settings"]["projectName"] == "First request"
    assert second_response["settings"]["projectName"] == "Second request"
    assert service.server.project.config.project_name == "Second request"
    assert yaml.safe_load((repo / "backlog" / "config.yml").read_text(encoding="utf-8"))["projectName"] == (
        "Second request"
    )


@pytest.mark.parametrize(
    ("unsafe_key", "unsafe_value"),
    [
        ("onStatusChange", "echo unsafe"),
        ("bypassGitHooks", True),
    ],
)
def test_browser_config_settings_update_rejects_unsafe_git_and_shell_settings_without_mutation(
    tmp_path, unsafe_key, unsafe_value
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/config",
                {"settings": {unsafe_key: unsafe_value, "projectName": "Mutated"}},
            )
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before


def test_browser_config_settings_update_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/settings/config",
                {"settings": {"projectName": "Rejected browser settings"}},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before


def test_browser_board_html_exposes_task_checklist_state_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'data-checklist-section="acceptanceCriteria"' in html
    assert 'data-checklist-section="definitionOfDone"' in html
    assert "submitTaskChecklistState" in html
    assert "/checklist" in html
    render_checklist = html.split("function renderChecklist", maxsplit=1)[1].split(
        "function renderRunHistoryEvents", maxsplit=1
    )[0]
    assert "(id, items, section, mutable)" in render_checklist
    assert "checkbox.disabled = !mutable;" in render_checklist
    assert 'if (mutable) checkbox.addEventListener("change", submitTaskChecklistState);' in render_checklist
    detail = html.split("async function openTaskDetails", maxsplit=1)[1].split(
        "async function openTaskEdit", maxsplit=1
    )[0]
    assert detail.count("task.mutable === true") == 2
    checklist_submit = html.split("async function submitTaskChecklistState", maxsplit=1)[1].split(
        "async function submitTaskArchive", maxsplit=1
    )[0]
    assert checklist_submit.count("payload.task.mutable === true") == 2


def test_browser_board_html_exposes_dod_defaults_settings_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="dod-defaults-open"' in html
    assert 'id="dod-defaults-dialog"' in html
    assert 'id="dod-defaults-form"' in html
    assert 'name="items"' in html
    assert "openDodDefaultsSettings" in html
    assert "submitDodDefaultsSettings" in html
    assert "/api/settings/dod-defaults" in html


def test_browser_board_html_exposes_general_settings_dialog(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'id="config-settings-open"' in html
    assert 'id="config-settings-dialog"' in html
    assert 'id="config-settings-form"' in html
    assert 'name="projectName"' in html
    assert 'name="defaultPort"' in html
    assert 'id="config-status-rows"' in html
    assert 'name="autoCommit"' in html
    assert 'name="remoteOperations"' in html
    assert 'name="checkActiveBranches"' in html
    assert 'name="activeBranchDays"' in html
    assert "configSettingsForm.elements.autoCommit.checked" in html
    assert "activeBranchDays: Number(data.get(\"activeBranchDays\") || 0)" in html
    assert "openConfigSettings" in html
    assert "submitConfigSettings" in html
    assert "/api/settings/config" in html


def test_browser_general_settings_uses_structured_status_editor(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    settings = html.split('id="config-settings-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    script = html.split("<script>", maxsplit=1)[1].split("</script>", maxsplit=1)[0]

    assert '<label for="config-default-status">Default status</label>' in settings
    assert '<select id="config-default-status" name="defaultStatus" required></select>' in settings
    assert '<fieldset class="status-editor">' in settings
    assert 'id="config-status-rows"' in settings
    assert 'id="config-status-add"' in settings
    assert 'id="config-status-add-button"' in settings
    assert 'id="config-status-message" role="status" aria-live="polite"' in settings
    assert 'textarea name="statuses"' not in settings

    assert "let statusRows = [];" in script
    assert "const statusKeyByName = new Map();" in script
    assert 'fetch(`/api/settings/status-key?value=${encodeURIComponent(name)}`)' in script
    assert "const exact = statusRows.find((row) => row.name === raw);" in script
    assert "function renderStatusRows()" in script
    assert "container.replaceChildren();" in script
    assert 'usage.textContent = row.taskCount === 1 ? "1 task" : `${row.taskCount} tasks`;' in script
    assert 'up.setAttribute("aria-label", `Move ${row.name} up`);' in script
    assert 'down.setAttribute("aria-label", `Move ${row.name} down`);' in script
    assert 'remove.setAttribute("aria-label", `Remove ${row.name}`);' in script
    assert 'configStatusAddButton?.addEventListener("click", addStatusFromInput);' in script
    assert 'configStatusAddInput?.addEventListener("keydown"' in script
    assert 'if (event.key !== "Enter") return;' in script
    assert 'statuses: statusRows.map((row) => row.name)' in script
    assert 'showConfigStatusMessage(await responseErrorMessage(response, "Unable to save project settings"));' in script
    loader = script.split("async function openConfigSettings", maxsplit=1)[1].split(
        "async function openDodDefaultsSettings", maxsplit=1
    )[0]
    assert loader.index("if (generation !== configSettingsLoadGeneration) return;") < loader.index(
        "if (!response.ok)"
    )


def test_browser_structured_status_editor_is_responsive_and_keyboard_visible(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    focus = html.split(".status-editor :focus-visible {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    responsive = html.split("@media (max-width: 720px)", maxsplit=1)[1]

    assert "outline: 2px solid var(--accent);" in focus
    assert "outline-offset: 2px;" in focus
    assert ".status-row-state" in html
    assert ".status-row," in responsive
    assert ".status-add-row" in responsive
    assert "grid-template-columns: 1fr;" in responsive
    assert ".status-add-row button:disabled" in html


def test_browser_structured_status_state_adds_reorders_and_safely_removes():
    result = _run_board_javascript_harness(
        """
statusRows = [
  {name: "To Do", taskCount: 0},
  {name: "Café", taskCount: 2},
  {name: "Done", taskCount: 0},
];
configDefaultStatus = "To Do";
globalThis.fetch = async (url) => {
  const value = decodeURIComponent(String(url).split("?value=")[1] || "");
  return {ok: true, json: async () => ({key: statusKey(value)})};
};
fakeConfigStatusAddInput.value = "  Review  ";
await fakeConfigStatusAddButton.listeners.click();
const added = statusRows.some((row) => row.name === "Review");
fakeConfigStatusAddInput.value = "Cafe\\u0301";
let enterPrevented = false;
await fakeConfigStatusAddInput.listeners.keydown({
  key: "Enter",
  preventDefault() { enterPrevented = true; },
});
const duplicate = statusRows.filter((row) => statusKey(row.name) === statusKey("Café")).length > 1;
const moved = moveStatus(3, -2);
const removedDefault = removeStatus(0);
const removedInUse = removeStatus(2);
const removedFree = removeStatus(3);
return {
  added,
  duplicate,
  germanEquivalent: statusKey("Straße") === statusKey("STRASSE"),
  sigmaEquivalent: statusKey("ΟΣ") === statusKey("οσ"),
  longSEquivalent: statusKey("ſ") === statusKey("s"),
  enterPrevented,
  inputFocused: Boolean(fakeConfigStatusAddInput.focused),
  moved,
  removedDefault,
  removedInUse,
  removedFree,
  rows: statusRows,
  message: configStatus.textContent,
};
"""
    )

    assert result == {
        "added": True,
        "duplicate": False,
        "germanEquivalent": True,
        "sigmaEquivalent": True,
        "longSEquivalent": True,
        "enterPrevented": True,
        "inputFocused": True,
        "moved": True,
        "removedDefault": False,
        "removedInUse": False,
        "removedFree": True,
        "rows": [
            {"name": "To Do", "taskCount": 0},
            {"name": "Review", "taskCount": 0},
            {"name": "Café", "taskCount": 2},
        ],
        "message": "",
    }


def test_browser_structured_status_submit_keeps_state_and_renders_server_error():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "Done", taskCount: 0}, {name: "Ready", taskCount: 0}];
const form = {
  _data: {
    projectName: "Project",
    defaultAssignee: "",
    defaultStatus: "Ready",
    dateFormat: "yyyy-mm-dd",
    defaultPort: "6420",
    activeBranchDays: "30",
    zeroPaddedIds: "",
  },
  elements: {
    defaultStatus: {value: "Ready"},
    includeDatetimeInDates: {checked: false},
    autoOpenBrowser: {checked: false},
    remoteOperations: {checked: false},
    checkActiveBranches: {checked: true},
    autoCommit: {checked: false},
  },
};
const submitter = {disabled: false};
let request = null;
globalThis.fetch = async (url, options) => {
  request = {url, options};
  return {ok: false, json: async () => ({error: "Status is still in use"})};
};
await submitConfigSettings({preventDefault() {}, currentTarget: form, submitter});
return {
  url: request.url,
  body: JSON.parse(request.options.body),
  disabled: submitter.disabled,
  message: configStatus.textContent,
  rows: statusRows,
};
"""
    )

    assert result["url"] == "/api/settings/config"
    assert result["body"]["settings"]["statuses"] == ["Done", "Ready"]
    assert result["body"]["settings"]["defaultStatus"] == "Ready"
    assert result["disabled"] is False
    assert result["message"] == "Status is still in use"
    assert result["rows"] == [
        {"name": "Done", "taskCount": 0},
        {"name": "Ready", "taskCount": 0},
    ]


def test_browser_structured_status_submit_preserves_nonfirst_casefold_default():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "Unrelated", taskCount: 0}, {name: "ΟΣ", taskCount: 0}];
configDefaultStatus = canonicalStatusName("οσ");
const unmatchedDefault = canonicalStatusName("Missing");
const form = {
  _data: {
    projectName: "Project",
    defaultAssignee: "",
    dateFormat: "yyyy-mm-dd",
    defaultPort: "6420",
    activeBranchDays: "30",
    zeroPaddedIds: "",
  },
  elements: {
    defaultStatus: {value: configDefaultStatus},
    includeDatetimeInDates: {checked: false},
    autoOpenBrowser: {checked: false},
    remoteOperations: {checked: false},
    checkActiveBranches: {checked: true},
    autoCommit: {checked: false},
  },
};
let request = null;
globalThis.fetch = async (url, options) => {
  request = {url, options};
  return {ok: false, json: async () => ({error: "Keep open"})};
};
await submitConfigSettings({preventDefault() {}, currentTarget: form, submitter: {disabled: false}});
return {
  canonicalDefault: configDefaultStatus,
  unmatchedDefault,
  submitted: JSON.parse(request.options.body).settings.defaultStatus,
};
"""
    )

    assert result == {
        "canonicalDefault": "ΟΣ",
        "unmatchedDefault": "Missing",
        "submitted": "ΟΣ",
    }


def test_browser_structured_status_submit_preserves_exact_dotless_i_default():
    result = _run_board_javascript_harness(
        """
statusRows = [
  {name: "Unrelated", taskCount: 0, key: "unrelated"},
  {name: "ı", taskCount: 0, key: "ı"},
  {name: "i", taskCount: 0, key: "i"},
];
configDefaultStatus = canonicalStatusName("i", "i");
configDefaultStatusKey = "i";
const form = {
  _data: {
    projectName: "Project",
    defaultAssignee: "",
    dateFormat: "yyyy-mm-dd",
    defaultPort: "6420",
    activeBranchDays: "30",
    zeroPaddedIds: "",
  },
  elements: {
    defaultStatus: {value: configDefaultStatus},
    includeDatetimeInDates: {checked: false},
    autoOpenBrowser: {checked: false},
    remoteOperations: {checked: false},
    checkActiveBranches: {checked: true},
    autoCommit: {checked: false},
  },
};
let request = null;
globalThis.fetch = async (url, options) => {
  request = {url, options};
  return {ok: false, json: async () => ({error: "Keep open"})};
};
await submitConfigSettings({preventDefault() {}, currentTarget: form, submitter: {disabled: false}});
return {
  canonicalDefault: configDefaultStatus,
  submitted: JSON.parse(request.options.body).settings.defaultStatus,
};
"""
    )

    assert result == {"canonicalDefault": "i", "submitted": "i"}


def test_browser_structured_status_rejects_sigma_and_long_s_duplicates():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "ΟΣ", taskCount: 0}, {name: "ſ", taskCount: 0}];
globalThis.fetch = async (url) => {
  const value = decodeURIComponent(String(url).split("?value=")[1] || "");
  return {ok: true, json: async () => ({key: statusKey(value)})};
};
const sigmaRejected = !await addStatus("οσ");
const longSRejected = !await addStatus("s");
return {sigmaRejected, longSRejected, rows: statusRows};
"""
    )

    assert result == {
        "sigmaRejected": True,
        "longSRejected": True,
        "rows": [{"name": "ΟΣ", "taskCount": 0}, {"name": "ſ", "taskCount": 0}],
    }


def test_browser_structured_status_uses_server_casefold_keys_for_duplicates():
    result = _run_board_javascript_harness(
        """
statusRows = [
  {name: "ı", taskCount: 0},
  {name: "ß", taskCount: 0},
  {name: "ΟΣ", taskCount: 0},
  {name: "ſ", taskCount: 0},
  {name: "Straße", taskCount: 0},
  {name: "Café", taskCount: 0},
];
statusKeyByName.set("ı", "ı");
statusKeyByName.set("ß", "ss");
statusKeyByName.set("ΟΣ", "οσ");
statusKeyByName.set("ſ", "s");
statusKeyByName.set("Straße", "strasse");
statusKeyByName.set("Café", "café");
const keys = new Map([
  ["i", "i"],
  ["ẞ", "ss"],
  ["SS", "ss"],
  ["οσ", "οσ"],
  ["s", "s"],
  ["STRASSE", "strasse"],
  ["Cafe\\u0301", "café"],
]);
globalThis.fetch = async (url) => {
  const value = decodeURIComponent(String(url).split("?value=")[1] || "");
  return {ok: true, json: async () => ({key: keys.get(value)})};
};
const dotlessDistinct = await addStatus("i");
const sharpSRejected = !await addStatus("ẞ");
const ssRejected = !await addStatus("SS");
const sigmaRejected = !await addStatus("οσ");
const longSRejected = !await addStatus("s");
const strasseRejected = !await addStatus("STRASSE");
const accentRejected = !await addStatus("Cafe\\u0301");
return {
  dotlessDistinct,
  sharpSRejected,
  ssRejected,
  sigmaRejected,
  longSRejected,
  strasseRejected,
  accentRejected,
  names: statusRows.map((row) => row.name),
};
"""
    )

    assert result == {
        "dotlessDistinct": True,
        "sharpSRejected": True,
        "ssRejected": True,
        "sigmaRejected": True,
        "longSRejected": True,
        "strasseRejected": True,
        "accentRejected": True,
        "names": ["ı", "ß", "ΟΣ", "ſ", "Straße", "Café", "i"],
    }


def test_browser_status_add_serializes_lookup_and_preserves_newer_input():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "Ready", taskCount: 0}];
let calls = 0;
let releaseLookup;
let markLookupStarted;
const lookupStarted = new Promise((resolve) => { markLookupStarted = resolve; });
globalThis.fetch = async () => {
  calls += 1;
  markLookupStarted();
  return new Promise((resolve) => { releaseLookup = resolve; });
};
fakeConfigStatusAddInput.value = "Review";
const pending = fakeConfigStatusAddButton.listeners.click();
await lookupStarted;
fakeConfigStatusAddInput.value = "Newer input";
await fakeConfigStatusAddButton.listeners.click();
releaseLookup({ok: true, json: async () => ({key: "review"})});
await pending;
return {
  calls,
  input: fakeConfigStatusAddInput.value,
  disabled: Boolean(fakeConfigStatusAddInput.disabled || fakeConfigStatusAddButton.disabled),
  names: statusRows.map((row) => row.name),
};
"""
    )

    assert result == {
        "calls": 1,
        "input": "Newer input",
        "disabled": False,
        "names": ["Ready", "Review"],
    }


def test_browser_status_add_refocuses_after_duplicate_and_lookup_errors():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "Ready", taskCount: 0}];
statusKeyByName.set("Ready", "ready");

let enterPrevented = false;
fakeConfigStatusAddInput.value = "READY";
fakeConfigStatusAddInput.focused = true;
globalThis.fetch = async () => ({ok: true, json: async () => ({key: "ready"})});
await fakeConfigStatusAddInput.listeners.keydown({
  key: "Enter",
  preventDefault() { enterPrevented = true; },
});
const duplicate = {
  focused: Boolean(fakeConfigStatusAddInput.focused),
  input: fakeConfigStatusAddInput.value,
  message: configStatus.textContent,
};

fakeConfigStatusAddInput.value = "HTTP error";
globalThis.fetch = async () => ({ok: false, json: async () => ({error: "Lookup failed"})});
await fakeConfigStatusAddInput.listeners.keydown({key: "Enter", preventDefault() {}});
const httpError = {
  focused: Boolean(fakeConfigStatusAddInput.focused),
  input: fakeConfigStatusAddInput.value,
  message: configStatus.textContent,
};

fakeConfigStatusAddInput.value = "Network error";
globalThis.fetch = async () => { throw new Error("Network failed"); };
await fakeConfigStatusAddButton.listeners.click();
const networkError = {
  focused: Boolean(fakeConfigStatusAddInput.focused),
  input: fakeConfigStatusAddInput.value,
  message: configStatus.textContent,
};
return {enterPrevented, duplicate, httpError, networkError};
"""
    )

    assert result == {
        "enterPrevented": True,
        "duplicate": {
            "focused": True,
            "input": "READY",
            "message": "A status named “READY” already exists.",
        },
        "httpError": {"focused": True, "input": "HTTP error", "message": "Lookup failed"},
        "networkError": {"focused": True, "input": "Network error", "message": "Network failed"},
    }


def test_browser_status_add_keeps_lookup_error_and_ignores_stale_reopen():
    result = _run_board_javascript_harness(
        """
statusRows = [{name: "Ready", taskCount: 0}];
fakeConfigStatusAddInput.value = "Broken";
globalThis.fetch = async () => ({ok: false, json: async () => ({error: "Lookup failed"})});
await fakeConfigStatusAddButton.listeners.click();
const afterError = {
  focused: Boolean(fakeConfigStatusAddInput.focused),
  input: fakeConfigStatusAddInput.value,
  message: configStatus.textContent,
  names: statusRows.map((row) => row.name),
};

let call = 0;
let releaseLookup;
let markLookupStarted;
const lookupStarted = new Promise((resolve) => { markLookupStarted = resolve; });
globalThis.fetch = async () => {
  call += 1;
  if (call === 1) {
    markLookupStarted();
    return new Promise((resolve) => { releaseLookup = resolve; });
  }
  return {
    ok: true,
    json: async () => ({
      settings: {
        defaultStatus: "Fresh",
        defaultStatusKey: "fresh",
        statusRows: [{name: "Fresh", taskCount: 0}],
        statusKeys: ["fresh"],
      },
    }),
  };
};
fakeConfigStatusAddInput.value = "Stale";
const staleAdd = fakeConfigStatusAddButton.listeners.click();
await lookupStarted;
fakeConfigStatusAddInput.focused = false;
await openConfigSettings();
releaseLookup({ok: true, json: async () => ({key: "stale"})});
await staleAdd;
const staleFocused = Boolean(fakeConfigStatusAddInput.focused);

let releaseClosedLookup;
let markClosedLookupStarted;
const closedLookupStarted = new Promise((resolve) => { markClosedLookupStarted = resolve; });
globalThis.fetch = async () => {
  markClosedLookupStarted();
  return new Promise((resolve) => { releaseClosedLookup = resolve; });
};
fakeConfigStatusAddInput.value = "Closed";
fakeConfigStatusAddInput.focused = true;
const closedAdd = fakeConfigStatusAddButton.listeners.click();
await closedLookupStarted;
fakeConfigSettingsDialog.close();
releaseClosedLookup({ok: true, json: async () => ({key: "closed"})});
await closedAdd;
return {
  afterError,
  defaultStatus: configDefaultStatus,
  focused: Boolean(fakeConfigStatusAddInput.focused),
  staleFocused,
  names: statusRows.map((row) => row.name),
  message: configStatus.textContent,
};
"""
    )

    assert result == {
        "afterError": {
            "focused": True,
            "input": "Broken",
            "message": "Lookup failed",
            "names": ["Ready"],
        },
        "defaultStatus": "Fresh",
        "focused": False,
        "staleFocused": False,
        "names": ["Fresh", "Closed"],
        "message": "",
    }


def test_browser_settings_open_ignores_stale_delayed_success_and_error_bodies():
    result = _run_board_javascript_harness(
        """
let call = 0;
let releaseFirstSuccess;
let markFirstSuccessStarted;
const firstSuccessStarted = new Promise((resolve) => { markFirstSuccessStarted = resolve; });
globalThis.fetch = async () => {
  call += 1;
  if (call === 1) {
    return {
      ok: true,
      json: () => {
        markFirstSuccessStarted();
        return new Promise((resolve) => { releaseFirstSuccess = resolve; });
      },
    };
  }
  return {
    ok: true,
    json: async () => ({settings: {defaultStatus: "New", statusRows: [{name: "New", taskCount: 0}]}}),
  };
};
const staleSuccess = openConfigSettings();
await firstSuccessStarted;
await openConfigSettings();
releaseFirstSuccess({settings: {defaultStatus: "Old", statusRows: [{name: "Old", taskCount: 0}]}});
await staleSuccess;
const afterSuccess = {defaultStatus: configDefaultStatus, rows: statusRows};

let releaseFirstError;
let markFirstErrorStarted;
const firstErrorStarted = new Promise((resolve) => { markFirstErrorStarted = resolve; });
globalThis.fetch = async () => {
  call += 1;
  if (call === 3) {
    return {
      ok: false,
      json: () => {
        markFirstErrorStarted();
        return new Promise((resolve) => { releaseFirstError = resolve; });
      },
    };
  }
  return {
    ok: true,
    json: async () => ({settings: {defaultStatus: "Latest", statusRows: [{name: "Latest", taskCount: 0}]}}),
  };
};
const staleError = openConfigSettings();
await firstErrorStarted;
await openConfigSettings();
releaseFirstError({error: "Stale failure"});
await staleError;
return {
  afterSuccess,
  afterError: {defaultStatus: configDefaultStatus, rows: statusRows},
  message: configStatus.textContent,
};
"""
    )

    assert result == {
        "afterSuccess": {"defaultStatus": "New", "rows": [{"name": "New", "taskCount": 0}]},
        "afterError": {"defaultStatus": "Latest", "rows": [{"name": "Latest", "taskCount": 0}]},
        "message": "",
    }


def test_browser_task_payload_includes_ordinal(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before = _get_json(f"{service.root_url}/api/board")
        repository.replace_task_frontmatter_values("TASK-1", {"ordinal": 4200})
        after = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert before["columns"]["In Progress"][0]["ordinal"] is None
    assert after["columns"]["In Progress"][0]["ordinal"] == 4200


def test_browser_sort_endpoint_persists_full_column_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    older = repository.create_task(title="Older task", task_id="TASK-2", status="In Progress")
    repository.replace_task_frontmatter_values(older.id, {"created_date": "2026-01-01"})
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/tasks/sort",
            {"status": "In Progress", "sort": "created", "direction": "asc"},
        )
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert response == {
        "status": "In Progress",
        "sort": "created",
        "direction": "asc",
        "taskIds": ["TASK-2", "TASK-1"],
        "changedCount": 2,
    }
    assert lock_operations == [(repo, "browser_task_sort")]
    assert [task["id"] for task in board["columns"]["In Progress"]] == ["TASK-2", "TASK-1"]
    assert [task["ordinal"] for task in board["columns"]["In Progress"]] == [1000, 2000]
    stored = {task.id: task.parsed.frontmatter.get("ordinal") for task in MutableRepository(project).list_tasks()}
    assert stored["TASK-2"] == 1000
    assert stored["TASK-1"] == 2000


def test_browser_sort_endpoint_ignores_current_board_filters(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.create_task(
        title="Only matching label",
        task_id="TASK-2",
        status="To Do",
        labels=["only-one-task"],
        ordinal=9000,
    )
    repository.create_task(
        title="Other label",
        task_id="TASK-3",
        status="To Do",
        labels=["different"],
        ordinal=8000,
    )

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/tasks/sort?labels=only-one-task",
            {"status": "To Do", "sort": "priority", "direction": None},
        )
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert response["taskIds"] == ["TASK-2", "TASK-3"]
    assert response["changedCount"] == 2
    assert [task["id"] for task in board["columns"]["To Do"]] == ["TASK-2", "TASK-3"]
    assert [task["ordinal"] for task in board["columns"]["To Do"]] == [1000, 2000]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"sort": "priority", "direction": None},
        {"status": 7, "sort": "priority", "direction": None},
        {"status": "   ", "sort": "priority", "direction": None},
        {"status": "To Do", "direction": None},
        {"status": "To Do", "sort": 7, "direction": None},
        {"status": "To Do", "sort": "title", "direction": None},
        {"status": "To Do", "sort": "created"},
        {"status": "To Do", "sort": "created", "direction": None},
        {"status": "To Do", "sort": "created", "direction": "sideways"},
        {"status": "To Do", "sort": "created", "direction": 7},
        {"status": "To Do", "sort": "created", "direction": []},
        {"status": "To Do", "sort": "created", "direction": {}},
        {"status": "To Do", "sort": "priority", "direction": "asc"},
        {"status": "To Do", "sort": "priority", "direction": False},
    ],
)
def test_browser_sort_endpoint_rejects_invalid_requests_before_lock(tmp_path, monkeypatch, payload):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_sources(repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    def tracking_lock(project, operation, fn):
        lock_operations.append(operation)
        return fn()

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks/sort", payload)
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert lock_operations == []
    assert _task_sources(repo) == before


def test_browser_sort_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_sources(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        for url, payload in (
            ("/api/tasks/sort", {"status": "In Progress", "sort": "priority", "direction": None}),
            ("/api/tasks/TASK-1/status", {"status": "Done"}),
        ):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post_json(f"{service.root_url}{url}", payload, origin="https://example.com")
            assert exc.value.code == 403
    finally:
        service.shutdown()

    assert _task_sources(repo) == before


def test_browser_sort_endpoint_hides_unexpected_repository_error(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    def fail_sort(self, status, *, sort, direction=None):
        raise OSError("private detail")

    monkeypatch.setattr(browser_service.MutableRepository, "sort_tasks", fail_sort)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/sort",
                {"status": "In Progress", "sort": "priority", "direction": None},
            )
        body = json.loads(exc.value.read().decode("utf-8"))
    finally:
        service.shutdown()

    assert exc.value.code == 500
    assert body == {"error": "Internal server error"}
    assert "private detail" not in json.dumps(body)


def test_browser_status_move_uses_ordinal_aware_append(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.create_task(title="First target", task_id="TASK-2", status="To Do")
    repository.create_task(title="Second target", task_id="TASK-3", status="To Do")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/tasks/TASK-1/status",
            {"status": "To Do"},
        )
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert set(response) == {"task"}
    assert response["task"]["id"] == "TASK-1"
    assert [task["id"] for task in board["columns"]["To Do"]] == ["TASK-2", "TASK-3", "TASK-1"]
    assert [task["ordinal"] for task in board["columns"]["To Do"]] == [1000, 2000, 3000]
    stored = {task.id: task.parsed.frontmatter.get("ordinal") for task in MutableRepository(project).list_tasks()}
    assert stored == {"TASK-2": 1000, "TASK-3": 2000, "TASK-1": 3000}


def test_configured_active_branch_only_column_omits_sort_controls(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service
    from backlog_py.core import repository as repository_module
    from backlog_py.runtime.git import GitTaskSnapshot

    def branch_task(task_id, title):
        return GitTaskSnapshot(
            ref="refs/heads/feature",
            relative_path=f"backlog/tasks/{task_id.lower()} - {title}.md",
            source=f"---\nid: {task_id}\ntitle: {title}\nstatus: Done\n---\n",
            committed_at=1.0,
        )

    monkeypatch.setattr(
        repository_module,
        "list_active_branch_task_snapshots",
        lambda project: [branch_task("TASK-20", "Branch one"), branch_task("TASK-21", "Branch two")],
    )

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        payload = _get_json(f"{service.root_url}/api/board")
        detail = _get_json(f"{service.root_url}/api/tasks/TASK-20")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    branch_tasks = payload["columns"]["Done"]
    assert [task["mutable"] for task in branch_tasks] == [False, False]
    assert detail["id"] == "TASK-20"
    assert detail["mutable"] is False
    branch_column = html.split('data-status="Done"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert "TASK-20" in branch_column
    assert "TASK-21" in branch_column
    assert "column-sort" not in branch_column
    assert branch_column.count('draggable="false"') == 2
    assert "data-task-edit" not in branch_column
    assert "data-task-archive" not in branch_column


def test_newer_branch_winner_is_readonly_while_local_winner_remains_mutable(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.create_task(title="Local companion", task_id="TASK-2", status="In Progress")

    from backlog_py.browser import service as browser_service

    _install_newer_branch_task(monkeypatch, repo)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        payload = _get_json(f"{service.root_url}/api/board")
        branch_detail = _get_json(f"{service.root_url}/api/tasks/TASK-1")
        local_detail = _get_json(f"{service.root_url}/api/tasks/TASK-2")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    tasks = {task["id"]: task for task in payload["columns"]["In Progress"]}
    assert tasks["TASK-1"]["title"] == "Newer branch winner"
    assert tasks["TASK-1"]["mutable"] is False
    assert tasks["TASK-2"]["mutable"] is True
    assert branch_detail["title"] == "Newer branch winner"
    assert branch_detail["mutable"] is False
    assert local_detail["title"] == "Local companion"
    assert local_detail["mutable"] is True
    column = html.split('data-status="In Progress"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    branch_card = column.split('data-task-id="TASK-1"', maxsplit=1)[1].split("</article>", maxsplit=1)[0]
    local_card = column.split('data-task-id="TASK-2"', maxsplit=1)[1].split("</article>", maxsplit=1)[0]
    assert 'draggable="false"' in branch_card
    assert "data-task-edit" not in branch_card
    assert "data-task-archive" not in branch_card
    assert 'draggable="true"' in local_card
    assert 'data-task-edit="TASK-2"' in local_card
    assert 'data-task-archive="TASK-2"' in local_card
    assert "column-sort" not in column


def test_browser_board_html_exposes_sort_controls_only_for_multi_task_local_columns(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.create_task(title="First sortable task", task_id="TASK-2", status="To Do")
    repository.create_task(title="Second sortable task", task_id="TASK-3", status="To Do")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    todo_column = html.split('data-status="To Do"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    one_task_column = html.split('data-status="In Progress"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert '<details class="column-sort">' in todo_column
    assert "<summary>Sort</summary>" in todo_column
    assert '<button type="button" data-sort="priority">Priority</button>' in todo_column
    assert (
        '<button type="button" data-sort="created" data-direction="asc">Oldest</button>'
        in todo_column
    )
    assert (
        '<button type="button" data-sort="created" data-direction="desc">Newest</button>'
        in todo_column
    )
    assert 'role="menu"' not in todo_column
    assert "column-sort" not in one_task_column


def test_browser_board_status_region_is_live_before_main_and_accessible_when_empty(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    marker = '<div id="board-status" class="board-status" role="status" aria-live="polite"></div>'
    assert marker in html
    assert html.index(marker) < html.index("<main")

    empty_rule = html.split(".board-status:empty {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "display:" not in empty_rule
    assert "visibility:" not in empty_rule
    assert "content-visibility:" not in empty_rule
    assert "margin: 0;" in empty_rule
    assert "border: 0;" in empty_rule
    assert "padding: 0;" in empty_rule


def test_browser_sort_controls_use_visible_board_status_and_preserve_query_on_success(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    sort_handler = html.split("async function sortColumn", maxsplit=1)[1].split(
        'document.querySelectorAll("[data-sort]")', maxsplit=1
    )[0]
    drop_handler = html.split('column.addEventListener("drop"', maxsplit=1)[1].split(
        "if (!connectBoardRevisionEvents())", maxsplit=1
    )[0]
    assert 'button.closest("[data-status]")' in sort_handler
    assert "column.dataset.status" in sort_handler
    assert "button.dataset.sort" in sort_handler
    assert "button.dataset.direction || null" in sort_handler
    assert 'fetch("/api/tasks/sort"' in sort_handler
    assert "body: JSON.stringify({status, sort, direction})" in sort_handler
    assert "button.disabled = true" in sort_handler
    assert "button.disabled = false" in sort_handler
    assert "showBoardMessage" in sort_handler
    assert "catch (error)" in sort_handler
    assert "window.location.reload()" in sort_handler
    assert "showBoardMessage" in drop_handler
    assert "catch (error)" in drop_handler
    assert "console.error(await response.text())" not in drop_handler


def test_browser_sort_controls_are_responsive_and_keyboard_visible(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert ".column-sort-actions {" in html
    assert "flex-wrap: wrap;" in html
    focus_selector = (
        ".column-sort summary:focus-visible,\n"
        "    .column-sort-actions button:focus-visible {"
    )
    focus_rule = html.split(focus_selector, maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "outline: 2px solid var(--accent);" in focus_rule
    assert "outline-offset: 2px;" in focus_rule
    responsive = html.split("@media (max-width: 720px)", maxsplit=1)[1]
    assert ".queue-filter" in responsive
    assert ".column-sort-actions button" in responsive


def test_browser_status_move_endpoint_updates_task_under_project_lock(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    lock_operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        payload = _post_json(f"{service.root_url}/api/tasks/TASK-1/status", {"status": "Done"})
        board = _get_json(f"{service.root_url}/api/board")
    finally:
        service.shutdown()

    assert lock_operations == [(repo, "browser_task_status")]
    assert payload["task"]["id"] == "TASK-1"
    assert payload["task"]["status"] == "Done"
    assert board["columns"]["In Progress"] == []
    assert board["columns"]["Done"][0]["id"] == "TASK-1"
    assert "status: Done" in _task_file(repo).read_text(encoding="utf-8")


def test_browser_status_move_endpoint_rejects_invalid_status_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/tasks/TASK-1/status", {"status": "Blocked"})
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_status_move_endpoint_rejects_cross_origin_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/status",
                {"status": "Done"},
                origin="https://example.com",
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_status_move_endpoint_requires_origin_header_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    before = _task_file(repo).read_text(encoding="utf-8")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/tasks/TASK-1/status",
                {"status": "Done"},
                omit_origin=True,
            )
    finally:
        service.shutdown()

    assert exc.value.code == 403
    assert _task_file(repo).read_text(encoding="utf-8") == before


def test_browser_board_html_exposes_drag_and_drop_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'data-status="In Progress"' in html
    assert 'data-task-id="TASK-1"' in html
    assert 'draggable="true"' in html
    assert "dragstart" in html
    assert "/api/tasks/" in html


def test_browser_service_rejects_occupied_port(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(OSError):
            start_browser_service(project, host="127.0.0.1", port=service.port)
    finally:
        service.shutdown()


def test_browser_service_rejects_non_loopback_host(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    with pytest.raises(ValueError, match="loopback"):
        start_browser_service(project, host="0.0.0.0", port=0)


@pytest.mark.parametrize("name", ["Release / Windows", r"Release \\ Windows", "Café % ready"])
def test_legacy_milestone_api_key_is_one_safe_reversible_segment(name):
    from backlog_py.browser import service as browser_service

    key = browser_service._legacy_milestone_key(name)

    assert "/" not in key and "\\" not in key and "%" not in key
    assert browser_service._legacy_name_from_key(key) == name


def test_current_milestone_api_key_round_trips_canonically(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    record = MilestoneService(project).add_milestone("Release")

    from backlog_py.browser import service as browser_service

    key = browser_service._milestone_api_key(record)

    assert key == "m-1"
    assert browser_service._milestone_reference_from_key(key) == "m-1"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "1",
        "M-1",
        "m-01",
        "m-١",
        "legacy-",
        "legacy-YQ=",
        "legacy-YQ==",
        "legacy-%%%%",
        "legacy-_w",
    ],
)
def test_milestone_key_rejects_malformed_and_noncanonical_tokens(key):
    from backlog_py.browser import service as browser_service

    with pytest.raises(ValueError, match="milestone key"):
        browser_service._milestone_reference_from_key(key)


def test_browser_milestone_endpoint_lists_active_and_archived_payloads_and_local_reference_counts(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestones = MilestoneService(project)
    current = milestones.add_milestone("Release", "Current scope.", due_date="2026-09-30T17:00Z")
    MutableRepository(project).edit_task("TASK-1", milestone=current.id)
    repository = MutableRepository(project)
    repository.create_task(title="Numeric reference", milestone="1")
    repository.create_task(title="Title reference", milestone="Release")
    repository.create_task(title="Stem reference", milestone=current.path.stem)
    ignored = repository.create_task(title="Completed reference", milestone="m-1")
    repository.edit_task(ignored.id, status="Done")
    repository.complete_task(ignored.id)
    milestones.archive_milestone(current.id or "")
    _write_browser_legacy_milestone(repo, "Release / Windows", filename="windows.md")
    MutableRepository(project).create_task(title="Legacy reference", milestone="Release / Windows")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _get_json(f"{service.root_url}/api/milestones")
    finally:
        service.shutdown()

    assert isinstance(response, dict)
    records = {record["title"]: record for record in response["milestones"]}
    assert set(records) == {"Release", "Release / Windows"}
    assert records["Release"] == {
        "key": "m-1",
        "selectionKey": "archive/milestones/m-1 - release.md",
        "id": "m-1",
        "title": "Release",
        "name": "Release",
        "dueDate": "2026-09-30 17:00",
        "description": "Current scope.",
        "format": "current",
        "path": "archive/milestones/m-1 - release.md",
        "archived": True,
        "taskReferenceCount": 4,
    }
    legacy = records["Release / Windows"]
    assert legacy["key"] == "legacy-UmVsZWFzZSAvIFdpbmRvd3M"
    assert legacy["id"] is None
    assert legacy["format"] == "legacy"
    assert legacy["archived"] is False
    assert legacy["taskReferenceCount"] == 1


def test_browser_milestone_list_counts_references_against_one_supplied_snapshot(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    service = MilestoneService(project)
    service.add_milestone("Alpha")
    service.add_milestone("Beta")
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone="m-1")
    repository.create_task(title="Alpha alias", milestone="Alpha")
    repository.create_task(title="Beta alias", milestone="2")
    records = service.list_milestones(include_archived=True)
    list_calls = []

    def list_once(self, *, include_archived=False):
        list_calls.append(include_archived)
        return records if len(list_calls) == 1 else []

    monkeypatch.setattr(MilestoneService, "list_milestones", list_once)

    from backlog_py.browser import service as browser_service

    payload = browser_service._milestone_list_payload(project)

    assert list_calls == [True]
    assert {item["title"]: item["taskReferenceCount"] for item in payload["milestones"]} == {
        "Alpha": 2,
        "Beta": 1,
    }


def test_browser_duplicate_legacy_route_keys_have_stable_unique_selection_keys_and_ambiguous_counts(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    active = _write_browser_legacy_milestone(repo, "Shared", filename="active.md")
    archived = repo / "backlog" / "archive" / "milestones" / "archived.md"
    archived.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(active, archived)
    MutableRepository(project).edit_task("TASK-1", milestone="Shared")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        first = _get_json(f"{service.root_url}/api/milestones")["milestones"]
        second = _get_json(f"{service.root_url}/api/milestones")["milestones"]
    finally:
        service.shutdown()

    assert first == second
    assert len({item["key"] for item in first}) == 1
    assert {item["selectionKey"] for item in first} == {
        "milestones/active.md",
        "archive/milestones/archived.md",
    }
    assert {item["taskReferenceCount"] for item in first} == {0}


def test_browser_milestone_endpoints_use_exact_project_lock_operations(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    from backlog_py.browser import service as browser_service

    original_lock = browser_service.with_project_write_lock

    def tracking_lock(project, operation, fn):
        operations.append(operation)
        return original_lock(project, operation, fn)

    monkeypatch.setattr(browser_service, "with_project_write_lock", tracking_lock)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        assert _get_json(f"{service.root_url}/api/milestones") == {"milestones": []}
        created = _post_json_response(
            f"{service.root_url}/api/milestones",
            {"title": "Alpha", "description": "First", "dueDate": "2026-10-01T09:30"},
        )
        edited = _post_json_response(
            f"{service.root_url}/api/milestones/m-1/edit",
            {"title": "Beta"},
        )
        archived = _post_json_response(f"{service.root_url}/api/milestones/m-1/archive", {})
        disposable = _post_json_response(f"{service.root_url}/api/milestones", {"title": "Disposable"})
        removed = _post_json_response(f"{service.root_url}/api/milestones/m-2/remove", {})
    finally:
        service.shutdown()

    assert created["status"] == 201
    assert created["body"]["milestone"]["format"] == "current"
    assert edited["body"]["milestone"]["title"] == "Beta"
    assert edited["body"]["milestone"]["description"] == "First"
    assert edited["body"]["milestone"]["dueDate"] == "2026-10-01 09:30"
    assert archived["body"]["milestone"]["archived"] is True
    assert disposable["status"] == 201
    assert removed["body"]["milestone"]["title"] == "Disposable"
    assert operations == [
        "browser_milestone_create",
        "browser_milestone_edit",
        "browser_milestone_archive",
        "browser_milestone_create",
        "browser_milestone_remove",
    ]


def test_browser_milestone_edit_updates_only_present_fields_and_clears_empty_due_date(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone(
        "Alpha",
        "Original description.",
        due_date="2026-10-01 09:30",
    )

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        title_only = _post_json(
            f"{service.root_url}/api/milestones/m-1/edit",
            {"title": "Beta", "unknownFutureField": "ignored"},
        )["milestone"]
        due_date_only = _post_json(
            f"{service.root_url}/api/milestones/m-1/edit",
            {"dueDate": ""},
        )["milestone"]
    finally:
        service.shutdown()

    assert title_only["title"] == "Beta"
    assert title_only["description"] == "Original description."
    assert title_only["dueDate"] == "2026-10-01 09:30"
    assert due_date_only["title"] == "Beta"
    assert due_date_only["description"] == "Original description."
    assert due_date_only["dueDate"] is None


@pytest.mark.parametrize("payload", [{}, {"unknownFutureField": "ignored"}], ids=["empty", "unknown-only"])
def test_browser_milestone_edit_rejects_payload_without_supported_fields(tmp_path, payload):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Alpha")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/milestones/m-1/edit", payload)
    finally:
        service.shutdown()

    with exc.value:
        assert exc.value.code == 400
        assert json.loads(exc.value.read().decode("utf-8")) == {
            "error": "Request body must include title, description, or dueDate"
        }
    assert _backlog_snapshot(repo) == before


def test_browser_legacy_milestone_key_edits_special_name_without_treating_it_as_a_path(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    path = _write_browser_legacy_milestone(repo, "Release / Windows", filename="windows.md")

    from backlog_py.browser import service as browser_service

    key = browser_service._legacy_milestone_key("Release / Windows")
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/milestones/{key}/edit",
            {"description": "Updated safely."},
        )
    finally:
        service.shutdown()

    assert response["milestone"]["name"] == "Release / Windows"
    assert response["milestone"]["description"] == "Updated safely."
    assert path.exists()
    assert not (repo / "Release").exists()


@pytest.mark.parametrize("due_date", ["2026-10-01T09:30", "not-a-date"])
def test_browser_legacy_milestone_edit_rejects_due_date_without_mutation(tmp_path, due_date):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    path = _write_browser_legacy_milestone(repo, "Legacy", filename="legacy.md")
    before = path.read_bytes()

    from backlog_py.browser import service as browser_service

    key = browser_service._legacy_milestone_key("Legacy")
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/milestones/{key}/edit",
                {"title": "Renamed", "description": "Changed.", "dueDate": due_date},
            )
    finally:
        service.shutdown()

    with exc.value:
        assert exc.value.code == 400
        assert json.loads(exc.value.read().decode("utf-8")) == {
            "error": "Legacy milestones do not support dueDate"
        }
    assert path.read_bytes() == before
    assert sorted(item.name for item in path.parent.glob("*.md")) == ["legacy.md"]


def test_browser_legacy_milestone_edit_without_due_date_preserves_legacy_format(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    original = _write_browser_legacy_milestone(repo, "Legacy", filename="legacy.md")

    from backlog_py.browser import service as browser_service

    key = browser_service._legacy_milestone_key("Legacy")
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json(
            f"{service.root_url}/api/milestones/{key}/edit",
            {"title": "Renamed Legacy", "description": "Changed safely."},
        )["milestone"]
    finally:
        service.shutdown()

    assert response["format"] == "legacy"
    assert response["id"] is None
    assert response["title"] == "Renamed Legacy"
    assert response["description"] == "Changed safely."
    assert response["dueDate"] is None
    assert not original.exists()
    assert (repo / "backlog" / response["path"]).is_file()


def test_browser_milestone_remove_without_policy_is_allowed_when_unreferenced(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    added = MilestoneService(project).add_milestone("Unused")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(f"{service.root_url}/api/milestones/m-1/remove", {})
    finally:
        service.shutdown()

    assert response["status"] == 200
    assert response["body"]["milestone"]["taskReferenceCount"] == 0
    assert not added.path.exists()


@pytest.mark.parametrize("task_handling", ["keep", "clear"])
def test_browser_referenced_milestone_remove_requires_explicit_valid_policy(tmp_path, task_handling):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    added = MilestoneService(project).add_milestone("Used")
    MutableRepository(project).edit_task("TASK-1", milestone="Used")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _post_json_response(
            f"{service.root_url}/api/milestones/m-1/remove",
            {"taskHandling": task_handling},
        )
    finally:
        service.shutdown()

    source = _task_file(repo).read_text(encoding="utf-8")
    assert response["status"] == 200
    assert not added.path.exists()
    if task_handling == "clear":
        assert "milestone:" not in source
    else:
        assert "milestone: Used" in source


def test_browser_referenced_milestone_remove_without_policy_returns_409_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Used")
    MutableRepository(project).edit_task("TASK-1", milestone="1")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/milestones/m-1/remove", {})
    finally:
        service.shutdown()

    assert exc.value.code == 409
    assert _backlog_snapshot(repo) == before


@pytest.mark.parametrize(
    "task_handling",
    [[], {}, True, 1, 1.5, "delete"],
    ids=["array", "object", "bool", "integer", "number", "invalid-string"],
)
def test_browser_milestone_remove_rejects_non_string_and_invalid_task_handling_without_mutation(
    tmp_path,
    task_handling,
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Used")
    MutableRepository(project).edit_task("TASK-1", milestone="m-1")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"{service.root_url}/api/milestones/m-1/remove",
                {"taskHandling": task_handling},
            )
    finally:
        service.shutdown()

    with exc.value:
        assert exc.value.code == 400
    assert _backlog_snapshot(repo) == before


@pytest.mark.parametrize(
    ("path", "payload", "expected_status"),
    [
        ("/api/milestones", {"title": "Release", "dueDate": "not-a-date"}, 400),
        ("/api/milestones/m-1/edit", {"dueDate": "2026-09-01"}, 400),
        ("/api/milestones/m-999/edit", {"title": "Missing"}, 404),
        ("/api/milestones/legacy-/edit", {"title": "Invalid"}, 400),
        ("/api/milestones/m-01/archive", {}, 400),
        ("/api/milestones/m-1/remove", {"taskHandling": "delete"}, 400),
    ],
)
def test_browser_milestone_endpoint_maps_validation_and_missing_errors_without_mutation(
    tmp_path,
    path,
    payload,
    expected_status,
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Release")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}{path}", payload)
    finally:
        service.shutdown()

    assert exc.value.code == expected_status
    assert _backlog_snapshot(repo) == before


def test_browser_milestone_duplicate_and_ambiguous_references_return_409_without_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Release")

    from backlog_py.browser import service as browser_service

    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before_duplicate = _backlog_snapshot(repo)
        with pytest.raises(urllib.error.HTTPError) as duplicate:
            _post_json(f"{service.root_url}/api/milestones", {"title": "Release"})
        assert duplicate.value.code == 409
        assert _backlog_snapshot(repo) == before_duplicate

        _write_browser_legacy_milestone(repo, "Ambiguous", filename="first.md")
        _write_browser_legacy_milestone(repo, "Ambiguous", filename="second.md")
        before_ambiguous = _backlog_snapshot(repo)
        key = browser_service._legacy_milestone_key("Ambiguous")
        with pytest.raises(urllib.error.HTTPError) as ambiguous:
            _post_json(f"{service.root_url}/api/milestones/{key}/edit", {"description": "No"})
        assert ambiguous.value.code == 409
        assert _backlog_snapshot(repo) == before_ambiguous
    finally:
        service.shutdown()


@pytest.mark.parametrize("key_kind", ["current", "legacy"])
def test_browser_stale_milestone_key_cannot_mutate_a_different_record_format(tmp_path, key_kind):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser import service as browser_service

    if key_kind == "current":
        _write_browser_legacy_milestone(repo, "m-1", filename="legacy.md")
        key = "m-1"
    else:
        MilestoneService(project).add_milestone("Former legacy")
        key = browser_service._legacy_milestone_key("Former legacy")
    before = _backlog_snapshot(repo)
    service = browser_service.start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/milestones/{key}/edit", {"description": "Wrong target"})
    finally:
        service.shutdown()

    assert exc.value.code == 404
    assert _backlog_snapshot(repo) == before


def test_browser_milestone_reference_counts_only_uniquely_resolved_aliases(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Shared")
    MutableRepository(project).edit_task("TASK-1", milestone="m-1")
    MutableRepository(project).create_task(title="Ambiguous reference", milestone="Shared")
    _write_browser_legacy_milestone(repo, "Shared", filename="legacy.md")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        milestones = _get_json(f"{service.root_url}/api/milestones")["milestones"]
    finally:
        service.shutdown()

    current = next(record for record in milestones if record["format"] == "current")
    legacy = next(record for record in milestones if record["format"] == "legacy")
    assert current["taskReferenceCount"] == 1
    assert legacy["taskReferenceCount"] == 0


def test_browser_milestone_archive_rejects_invalid_json_before_mutation(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Release")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    request = urllib.request.Request(
        f"{service.root_url}/api/milestones/m-1/archive",
        data=b"not-json",
        headers={
            "Content-Type": "application/json",
            "Origin": service.root_url.rstrip("/"),
        },
        method="POST",
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=2)
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert _backlog_snapshot(repo) == before


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/milestones", {"title": "Blocked"}),
        ("/api/milestones/m-1/edit", {"title": "Blocked"}),
        ("/api/milestones/m-1/archive", {}),
        ("/api/milestones/m-1/remove", {}),
    ],
)
@pytest.mark.parametrize("rejection", ["origin", "missing_origin", "host"])
def test_browser_milestone_mutations_reject_cross_origin_and_foreign_host_without_mutation(
    tmp_path,
    path,
    payload,
    rejection,
):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone("Release")
    before = _backlog_snapshot(repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        if rejection == "origin":
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post_json(f"{service.root_url}{path}", payload, origin="https://attacker.example")
            status = exc.value.code
        elif rejection == "missing_origin":
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post_json(f"{service.root_url}{path}", payload, omit_origin=True)
            status = exc.value.code
        else:
            response = _raw_browser_request(
                service,
                "POST",
                path,
                host_header=f"attacker.example:{service.port}",
                origin=f"http://attacker.example:{service.port}",
                payload=payload,
            )
            status = response["status"]
    finally:
        service.shutdown()

    assert status == 403
    assert _backlog_snapshot(repo) == before


def test_browser_milestone_get_rejects_foreign_host(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        response = _raw_browser_request(
            service,
            "GET",
            "/api/milestones",
            host_header=f"attacker.example:{service.port}",
        )
    finally:
        service.shutdown()

    assert response["status"] == 403


def test_board_combines_queue_milestone_and_any_match_repeated_labels(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    milestone = MilestoneService(project).add_milestone("Release 2")
    repository.edit_task("TASK-1", labels=["Frontend"], milestone=milestone.id)
    repository.create_task(
        title="Urgent match",
        task_id="TASK-2",
        status="In Progress",
        labels=["URGENT"],
        milestone="Release 2",
    )
    repository.create_task(
        title="Wrong queue",
        task_id="TASK-3",
        status="To Do",
        labels=["frontend"],
        milestone="1",
    )
    repository.create_task(
        title="Wrong milestone",
        task_id="TASK-4",
        status="In Progress",
        labels=["frontend"],
        milestone="unknown",
    )
    repository.create_task(
        title="Wrong label",
        task_id="TASK-5",
        status="In Progress",
        labels=["backend"],
        milestone=milestone.id,
    )

    from backlog_py.browser.service import start_browser_service

    query = urllib.parse.urlencode(
        [
            ("queueCategory", "in_workflow"),
            ("milestone", "Release 2"),
            ("labels", "frontend"),
            ("labels", "urgent"),
        ]
    )
    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        payload = _get_json(f"{service.root_url}/api/board?{query}")
        html = _get_text(f"{service.root_url}/?{query}")
    finally:
        service.shutdown()

    visible_ids = {task["id"] for tasks in payload["columns"].values() for task in tasks}
    assert visible_ids == {"TASK-1", "TASK-2"}
    assert payload["queueCategoryFilter"] == "in_workflow"
    assert payload["milestoneFilter"] == "Release 2"
    assert payload["labelFilters"] == ["frontend", "urgent"]
    assert payload["visibleTaskCount"] == 2
    assert payload["totalTaskCount"] == 5
    assert '<option value="in_workflow" selected>In Workflow</option>' in html
    assert '<option value="Release 2" selected>Release 2</option>' in html
    assert 'name="labels" value="frontend" checked' in html
    assert 'name="labels" value="URGENT" checked' in html
    assert ReadOnlyRepository(project).list_tasks(labels=["frontend", "urgent"]) == []


def test_filter_choices_come_from_unfiltered_board(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    first = MilestoneService(project).add_milestone("First release")
    second = MilestoneService(project).add_milestone("Second release")
    repository.edit_task("TASK-1", labels=["visible"], milestone=first.id)
    repository.create_task(
        title="Hidden choice source",
        task_id="TASK-2",
        status="To Do",
        labels=["hidden"],
        milestone=second.id,
    )

    from backlog_py.browser.service import build_board_payload

    payload = build_board_payload(project, milestone_filter=first.id, label_filters=["visible"])

    assert payload["availableLabels"] == ["hidden", "visible"]
    choices = {choice["value"]: choice["label"] for choice in payload["milestoneChoices"]}
    assert choices == {first.id: "First release", second.id: "Second release"}


def test_board_resolves_milestones_against_one_shared_snapshot(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    service = MilestoneService(project)
    milestone = service.add_milestone("Release")
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone=milestone.id)
    repository.create_task(title="Alias", task_id="TASK-2", milestone=milestone.title)
    records = service.list_milestones(include_archived=True)
    list_calls = []

    def list_once(self, *, include_archived=False):
        list_calls.append(include_archived)
        return records if len(list_calls) == 1 else []

    monkeypatch.setattr(MilestoneService, "list_milestones", list_once)

    from backlog_py.browser.service import build_board_payload

    tasks = [task for column in build_board_payload(project)["columns"].values() for task in column]

    assert list_calls == [True]
    assert {task["milestoneTitle"] for task in tasks} == {"Release"}


def test_filtering_does_not_change_revision(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestone = MilestoneService(project).add_milestone("Release")
    MutableRepository(project).edit_task("TASK-1", labels=["frontend"], milestone=milestone.id)

    from backlog_py.browser.service import build_board_payload

    revisions = {
        build_board_payload(project)["revision"],
        build_board_payload(project, queue_category_filter="claimed")["revision"],
        build_board_payload(project, milestone_filter="Release")["revision"],
        build_board_payload(project, label_filters=["FRONTEND"])["revision"],
    }

    assert len(revisions) == 1


def test_board_payload_refreshes_one_config_snapshot_after_external_change(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    stale_project = discover_project(Path.cwd(), explicit_cwd=repo)
    from backlog_py.browser import service as browser_service

    before = browser_service.build_board_payload(stale_project)
    config_path = repo / "backlog" / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["projectName"] = "refreshed-project"
    raw["statuses"] = ["Ready", "Review"]
    raw["defaultStatus"] = "Ready"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    from backlog_py.core import repository as repository_module

    load_calls = []
    real_load_config = browser_service.load_config

    def tracked_load_config(path):
        load_calls.append(path)
        return real_load_config(path)

    def fail_nested_config_load(path):
        pytest.fail(f"board repository reloaded config instead of reusing its snapshot: {path}")

    monkeypatch.setattr(browser_service, "load_config", tracked_load_config)
    monkeypatch.setattr(repository_module, "load_config", fail_nested_config_load)

    payload = browser_service.build_board_payload(stale_project)

    assert load_calls == [config_path]
    assert payload["project"]["name"] == "refreshed-project"
    assert payload["defaultStatus"] == "Ready"
    assert payload["statuses"] == ["Ready", "Review", "In Progress"]
    assert payload["assignableStatuses"] == ["Ready", "Review", "In Progress"]
    assert payload["revision"] != before["revision"]


def test_running_browser_uses_one_fresh_config_for_api_and_html(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    stale_project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import build_board_payload, start_browser_service

    service = start_browser_service(stale_project, host="127.0.0.1", port=0)
    try:
        config_path = repo / "backlog" / "config.yml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["projectName"] = "refreshed-project"
        raw["statuses"] = ["Ready", "Review"]
        raw["defaultStatus"] = "Ready"
        config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        expected = build_board_payload(discover_project(Path.cwd(), explicit_cwd=repo))

        payload = _get_json(f"{service.root_url}/api/board")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert payload["project"]["name"] == "refreshed-project"
    assert payload["defaultStatus"] == "Ready"
    assert payload["statuses"] == ["Ready", "Review", "In Progress"]
    assert payload["assignableStatuses"] == ["Ready", "Review", "In Progress"]
    assert payload["revision"] == expected["revision"]
    assert "<h1>refreshed-project</h1>" in html
    assert '<option value="Ready" selected>Ready</option>' in html
    assert 'data-status="Ready"' in html
    assert 'data-status="Review"' in html
    assert 'data-status="Done"' not in html


def test_milestone_filter_resolves_current_title_numeric_and_archived_aliases(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestones = MilestoneService(project)
    current = milestones.add_milestone("Release 2")
    archived = milestones.add_milestone("Release 1")
    milestones.archive_milestone(archived.id or "")
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone=current.id)
    repository.create_task(title="Current title", task_id="TASK-2", milestone=current.title)
    repository.create_task(title="Current numeric", task_id="TASK-3", milestone="1")
    repository.create_task(title="Archived id", task_id="TASK-4", milestone=archived.id)
    repository.create_task(title="Archived title", task_id="TASK-5", milestone=archived.title)

    from backlog_py.browser.service import build_board_payload

    def visible_ids(reference):
        payload = build_board_payload(project, milestone_filter=reference)
        return {task["id"] for tasks in payload["columns"].values() for task in tasks}

    assert visible_ids(current.id) == {"TASK-1", "TASK-2", "TASK-3"}
    assert visible_ids(current.title) == {"TASK-1", "TASK-2", "TASK-3"}
    assert visible_ids("1") == {"TASK-1", "TASK-2", "TASK-3"}
    assert visible_ids(archived.id) == {"TASK-4", "TASK-5"}
    assert visible_ids(archived.title) == {"TASK-4", "TASK-5"}


def test_unknown_milestone_reference_remains_filterable(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone="raw-value")
    repository.create_task(title="Other unknown", task_id="TASK-2", milestone="other-value")

    from backlog_py.browser.service import build_board_payload

    payload = build_board_payload(project, milestone_filter="raw-value")
    visible = [task for tasks in payload["columns"].values() for task in tasks]

    assert [task["id"] for task in visible] == ["TASK-1"]
    assert {(choice["value"], choice["label"]) for choice in payload["milestoneChoices"]} == {
        ("other-value", "Unknown: other-value"),
        ("raw-value", "Unknown: raw-value"),
    }


def test_task_payload_exposes_resolved_milestone_display_and_ordinal(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestones = MilestoneService(project)
    current = milestones.add_milestone("Release 2")
    archived = milestones.add_milestone("Release 1")
    milestones.archive_milestone(archived.id or "")
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone=current.id, ordinal=1200)
    repository.create_task(title="Archived", task_id="TASK-2", milestone=archived.title, ordinal=1300)
    repository.create_task(title="Unknown", task_id="TASK-3", milestone="raw-value", ordinal=1400)

    from backlog_py.browser.service import build_board_payload

    tasks = {
        task["id"]: task
        for column in build_board_payload(project)["columns"].values()
        for task in column
    }

    assert (tasks["TASK-1"]["ordinal"], tasks["TASK-1"]["milestoneTitle"]) == (1200, "Release 2")
    assert tasks["TASK-1"]["milestoneArchived"] is False
    assert tasks["TASK-1"]["milestoneUnknown"] is False
    assert tasks["TASK-2"]["milestoneTitle"] == "Release 1"
    assert tasks["TASK-2"]["milestoneArchived"] is True
    assert tasks["TASK-2"]["milestoneUnknown"] is False
    assert tasks["TASK-3"]["milestoneTitle"] is None
    assert tasks["TASK-3"]["milestoneArchived"] is False
    assert tasks["TASK-3"]["milestoneUnknown"] is True


def test_task_card_shows_milestone_and_label_overflow_badges(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestone = MilestoneService(project).add_milestone("Release 2")
    MutableRepository(project).edit_task(
        "TASK-1", labels=["alpha", "beta", "gamma"], milestone=milestone.id
    )

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    task = html.split('data-task-id="TASK-1"', maxsplit=1)[1].split("</article>", maxsplit=1)[0]

    assert '<span class="badge milestone-badge">Release 2</span>' in task
    assert '<span class="badge label-badge">alpha</span>' in task
    assert '<span class="badge label-badge">beta</span>' in task
    assert '<span class="badge label-overflow">+1</span>' in task
    assert "gamma" not in task


def test_task_details_show_resolved_milestone_state_not_raw_id(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestones = MilestoneService(project)
    current = milestones.add_milestone("Release 2")
    archived = milestones.add_milestone("Release 1")
    milestones.archive_milestone(archived.id or "")
    repository = MutableRepository(project)
    repository.edit_task("TASK-1", milestone=current.id)
    repository.create_task(title="Archived", task_id="TASK-2", milestone=archived.id)
    repository.create_task(title="Unknown", task_id="TASK-3", milestone="raw-value")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        current_task = _get_json(f"{service.root_url}/api/tasks/TASK-1")
        archived_task = _get_json(f"{service.root_url}/api/tasks/TASK-2")
        unknown_task = _get_json(f"{service.root_url}/api/tasks/TASK-3")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert current_task["milestoneTitle"] == "Release 2"
    assert archived_task["milestoneTitle"] == "Release 1"
    assert archived_task["milestoneArchived"] is True
    assert unknown_task["milestoneUnknown"] is True
    assert "function milestoneDetailText(task)" in html
    assert "Unknown: ${task.milestone}" in html
    assert "${title} (archived)" in html
    assert "function selectTaskMilestone(task)" in html
    assert 'document.createElement("option")' in html
    assert 'option.dataset.storedMilestone = "true"' in html
    assert "Unknown: ${raw}" in html
    assert "${title} (stored as ${raw})" in html
    details = html.split("async function openTaskDetails", maxsplit=1)[1].split(
        "async function openTaskEdit", maxsplit=1
    )[0]
    assert 'setText("task-dialog-milestone", milestoneDetailText(task))' in details
    assert 'setText("task-dialog-milestone", task.milestone)' not in details


def test_task_edit_preserves_exact_status_missing_from_current_options(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    helper = html.split("function selectTaskStatus(task)", maxsplit=1)[1].split(
        "function selectTaskMilestone(task)", maxsplit=1
    )[0]
    edit = html.split("async function openTaskEdit", maxsplit=1)[1].split(
        "async function openTaskArchive", maxsplit=1
    )[0]

    assert 'select.querySelectorAll("[data-stored-status]")' in helper
    assert 'option.dataset.storedStatus = "true"' in helper
    assert 'const option = document.createElement("option")' in helper
    assert "option.textContent = raw" in helper
    assert "option.value === raw" in helper
    assert "innerHTML" not in helper
    assert "selectTaskStatus(task);" in edit
    assert "taskEditForm.elements.status.value = task.status" not in edit


@pytest.mark.parametrize("statuses", [None, [], ["Configured"]])
def test_browser_status_options_separate_rendered_and_assignable_columns(tmp_path, statuses):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, statuses, default="Ready")

    from backlog_py.browser.service import build_board_payload, render_board_html

    payload = build_board_payload(project)
    html = render_board_html(project)

    expected_assignable = ["Ready", "In Progress"] if not statuses else ["Configured", "Ready", "In Progress"]
    assert payload["assignableStatuses"] == expected_assignable
    assert set(payload["statuses"]) == set(expected_assignable)
    assert payload["columns"]["Ready"] == []
    assert '<option value="Ready" selected>Ready</option>' in html


@pytest.mark.parametrize("source_kind", ["configured", "default", "local"])
def test_browser_hides_whitespace_only_status_options_from_each_source(tmp_path, source_kind):
    repo = _copy_fixture_repo(tmp_path)
    statuses = ["Configured"]
    default = "Ready"
    if source_kind == "configured":
        statuses.insert(0, "   ")
    elif source_kind == "default":
        default = "   "
    else:
        _replace_browser_fixture_task_status(repo, "   ")
    project = _configure_statuses(repo, statuses, default=default)

    from backlog_py.browser.service import build_board_payload, render_board_html

    payload = build_board_payload(project)
    html = render_board_html(project)

    assert "   " in payload["statuses"]
    assert "   " not in payload["assignableStatuses"]
    assert all(str(status).strip() for status in payload["assignableStatuses"])
    blank_column = html.split('data-status="   "', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert 'data-assignable="false"' in blank_column.split(">", maxsplit=1)[0]
    assert "Read only" in blank_column
    create_form = html.split('id="task-create-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    edit_form = html.split('id="task-edit-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    assert '<option value="   ">' not in create_form
    assert '<option value="   ">' not in edit_form


@pytest.mark.parametrize("source_kind", ["configured", "default", "local"])
def test_browser_preserves_exact_nonblank_status_spelling_from_each_source(tmp_path, source_kind):
    repo = _copy_fixture_repo(tmp_path)
    exact_status = " Intentional "
    statuses = ["Configured"]
    default = "Ready"
    if source_kind == "configured":
        statuses.insert(0, exact_status)
    elif source_kind == "default":
        default = exact_status
    else:
        _replace_browser_fixture_task_status(repo, exact_status)
    project = _configure_statuses(repo, statuses, default=default)

    from backlog_py.browser.service import build_board_payload, render_board_html

    payload = build_board_payload(project)
    html = render_board_html(project)
    repository = MutableRepository(project)

    assert exact_status in payload["assignableStatuses"]
    assert all(repository._status_is_assignable(str(status)) for status in payload["assignableStatuses"])
    exact_column = html.split(f'data-status="{exact_status}"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert 'data-assignable="true"' in exact_column.split(">", maxsplit=1)[0]
    create_form = html.split('id="task-create-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    edit_form = html.split('id="task-edit-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    assert f'<option value="{exact_status}"' in create_form
    assert f'<option value="{exact_status}"' in edit_form


def test_default_only_column_accepts_browser_drop(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, [], default="Ready")

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before = _get_json(f"{service.root_url}/api/board")
        html = _get_text(service.root_url)
        moved = _post_json(f"{service.root_url}/api/tasks/TASK-1/status", {"status": "Ready"})
    finally:
        service.shutdown()

    assert before["columns"]["Ready"] == []
    assert "Ready" in before["assignableStatuses"]
    ready_column = html.split('data-status="Ready"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert 'data-assignable="true"' in ready_column.split(">", maxsplit=1)[0]
    assert moved["task"]["status"] == "Ready"
    assert '<option value="Ready" selected>Ready</option>' in html


def test_active_branch_only_status_is_rendered_read_only(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = _configure_statuses(repo, ["To Do", "In Progress"], default="To Do")

    from backlog_py.browser import service as browser_service
    from backlog_py.core import repository as repository_module
    from backlog_py.runtime.git import GitTaskSnapshot

    monkeypatch.setattr(
        repository_module,
        "list_active_branch_task_snapshots",
        lambda project: [
            GitTaskSnapshot(
                ref="refs/heads/feature",
                relative_path="backlog/tasks/task-99 - branch-only.md",
                source="---\nid: TASK-99\ntitle: Branch only\nstatus: Review\n---\n",
                committed_at=1.0,
            )
        ],
    )

    payload = browser_service.build_board_payload(project)
    html = browser_service.render_board_html(project)

    assert "Review" in payload["statuses"]
    assert payload["columns"]["Review"][0]["id"] == "TASK-99"
    assert "Review" not in payload["assignableStatuses"]
    review_column = html.split('data-status="Review"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert 'data-assignable="false"' in review_column.split(">", maxsplit=1)[0]
    assert "Read only" in review_column
    assert 'draggable="false"' in review_column
    assert '.task[draggable="false"]' in html
    assert 'data-task-details="TASK-99"' in review_column
    assert "data-task-edit" not in review_column
    assert "data-task-archive" not in review_column
    assert 'document.querySelectorAll(\'[data-status][data-assignable="true"]\')' in html
    create_form = html.split('id="task-create-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    edit_form = html.split('id="task-edit-form"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]
    assert '<option value="Review">Review</option>' not in create_form
    assert '<option value="Review">Review</option>' not in edit_form


def test_browser_filter_form_and_assignment_controls_are_native_accessible_and_complete(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    milestone = MilestoneService(project).add_milestone("Release 2")
    MutableRepository(project).edit_task("TASK-1", labels=["one", "two"], milestone=milestone.id)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project, milestone_filter=milestone.id, label_filters=["one"])

    assert '<form class="board-filter" method="get">' in html
    assert 'id="queue-category-filter"' in html
    assert 'id="milestone-filter"' in html
    assert '<details class="label-filter">' in html
    assert html.count('name="labels"') >= 3
    assert 'href="/"' in html
    assert "1 / 1 tasks" in html
    assert "No matching tasks" in html
    assert '<select class="task-form-select" name="milestone">' in html
    assert '<option value="m-1">Release 2</option>' in html
    assert ".board-filter :focus-visible" in html
    label_css = html.split(".label-filter-options {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "max-height:" in label_css
    assert "overflow-y: auto;" in label_css
    assert "max-inline-size:" in label_css
    assert "min-inline-size:" in label_css
    label_row_css = html.split(".label-filter-options label {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "overflow-wrap: anywhere;" in label_row_css
    responsive = html.split("@media (max-width: 720px)", maxsplit=1)[1]
    assert ".board-filter" in responsive


def test_board_filter_preserves_escaped_stale_queue_and_label_controls(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(
        project,
        queue_category_filter='stale<&"',
        label_filters=['missing<&"', "second-missing"],
    )
    filter_markup = html.split('<form class="board-filter"', maxsplit=1)[1].split(
        "</form>", maxsplit=1
    )[0]

    assert '<option value="stale&lt;&amp;&quot;" selected>Unknown: stale&lt;&amp;&quot;</option>' in filter_markup
    assert 'name="labels" value="missing&lt;&amp;&quot;" checked' in filter_markup
    assert "Unknown: missing&lt;&amp;&quot;" in filter_markup
    assert 'name="labels" value="second-missing" checked' in filter_markup
    assert "Unknown: second-missing" in filter_markup
    assert "Labels (2)" in filter_markup
    assert 'stale<&"' not in filter_markup
    assert 'missing<&"' not in filter_markup


def test_browser_create_submission_sends_visible_metadata_fields(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    create_submit = html.split("async function submitTaskCreate", maxsplit=1)[1].split(
        "async function submitTaskEdit", maxsplit=1
    )[0]

    assert 'priority: String(data.get("priority") || "")' in create_submit
    assert 'milestone: String(data.get("milestone") || "")' in create_submit
    assert 'assignees: metadataList(data.get("assignees"))' in create_submit
    assert 'labels: metadataList(data.get("labels"))' in create_submit


def test_browser_milestone_dialog_exposes_management_controls_and_accessible_states(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    dialog = html.split('id="milestones-dialog"', maxsplit=1)[1].split("</dialog>", maxsplit=1)[0]

    assert 'id="milestones-open"' in html
    assert 'aria-labelledby="milestones-title"' in html
    assert 'id="milestone-create-form"' in dialog
    assert 'name="title"' in dialog
    assert 'name="dueDate" type="datetime-local"' in dialog
    assert 'name="description"' in dialog
    assert 'id="milestone-active-list"' in dialog
    assert '<details class="milestone-archived">' in dialog
    assert '<summary>Archived</summary>' in dialog
    assert 'id="milestone-archived-list"' in dialog
    assert 'id="milestone-editor"' in dialog
    assert 'id="milestone-edit-form"' in dialog
    assert 'id="milestone-archive"' in dialog
    assert 'id="milestone-remove"' in dialog
    assert 'id="milestone-remove-options"' in dialog
    assert 'name="taskHandling" value="keep"' in dialog
    assert 'name="taskHandling" value="clear"' in dialog
    assert 'id="milestone-message"' in dialog
    assert 'role="status" aria-live="polite"' in dialog


def test_browser_milestone_dialog_exposes_metadata_and_readonly_archive_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    dialog = html.split('id="milestones-dialog"', maxsplit=1)[1].split("</dialog>", maxsplit=1)[0]
    selector = html.split("function selectMilestone", maxsplit=1)[1].split(
        "async function submitMilestoneCreate", maxsplit=1
    )[0]

    assert 'id="milestone-editor-id"' in dialog
    assert 'id="milestone-editor-format"' in dialog
    assert 'id="milestone-editor-path"' in dialog
    assert 'id="milestone-editor-references"' in dialog
    assert 'id="milestone-archived-note"' in dialog
    assert "record.archived" in selector
    assert "elements.title.readOnly = readOnly" in selector
    assert "elements.description.readOnly = readOnly" in selector
    assert 'archiveButton.hidden = readOnly' in selector
    assert 'removeButton.hidden = readOnly' in selector
    assert 'editSubmit.hidden = readOnly' in selector
    assert 'dueDate.disabled = readOnly || record.format === "legacy"' in selector


def test_browser_milestone_dialog_explains_disabled_legacy_due_date_visibly(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    dialog = html.split('id="milestones-dialog"', maxsplit=1)[1].split("</dialog>", maxsplit=1)[0]
    selector = html.split("function selectMilestone", maxsplit=1)[1].split(
        "async function submitMilestoneCreate", maxsplit=1
    )[0]

    assert 'aria-describedby="milestone-legacy-due-note"' in dialog
    assert 'id="milestone-legacy-due-note"' in dialog
    assert "Legacy milestones do not support due dates." in dialog
    assert 'legacyDueNote.hidden = record.format !== "legacy"' in selector
    assert "dueDate.title" not in selector


def test_browser_milestone_dialog_uses_unique_selection_key_and_route_key(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    lists = html.split("function renderMilestoneLists", maxsplit=1)[1].split(
        "function selectMilestone", maxsplit=1
    )[0]
    actions = html.split("async function submitMilestoneEdit", maxsplit=1)[1].split(
        'document.getElementById("task-create-open")', maxsplit=1
    )[0]

    assert "record.selectionKey" in lists
    assert "selectMilestone(record.selectionKey)" in lists
    assert "record.key" not in lists
    assert "selectedMilestoneKey === record.selectionKey" in html
    assert "encodeURIComponent(selected.key)" in actions
    assert "encodeURIComponent(selected.selectionKey)" not in actions
    assert ".innerHTML" not in lists
    assert ".textContent" in lists


def test_browser_milestone_loads_apply_only_latest_response():
    result = _run_board_javascript_harness(
        """
        const pending = [];
        const applied = [];
        fetch = () => new Promise((resolve) => pending.push(resolve));
        selectedMilestoneKey = "new";
        renderMilestoneLists = () => applied.push(milestones.map((record) => record.selectionKey));
        selectMilestone = () => {};
        const older = loadMilestones();
        const newer = loadMilestones();
        pending[1]({
          ok: true,
          json: async () => ({milestones: [{selectionKey: "new"}]}),
        });
        await newer;
        pending[0]({
          ok: true,
          json: async () => ({milestones: [{selectionKey: "old"}]}),
        });
        await older;
        return {
          applied,
          finalKeys: milestones.map((record) => record.selectionKey),
          selectedMilestoneKey,
        };
        """
    )

    assert result == {
        "applied": [["new"]],
        "finalKeys": ["new"],
        "selectedMilestoneKey": "new",
    }


def test_stale_milestone_load_error_does_not_replace_newer_state_or_message():
    result = _run_board_javascript_harness(
        """
        const pending = [];
        fetch = () => new Promise((resolve) => pending.push(resolve));
        renderMilestoneLists = () => {};
        selectMilestone = () => {};
        milestoneStatus.textContent = "Newest result";
        const older = loadMilestones();
        const newer = loadMilestones();
        pending[1]({ok: true, json: async () => ({milestones: []})});
        await newer;
        pending[0]({ok: false, json: async () => ({error: "Old failure"})});
        await older;
        return {message: milestoneStatus.textContent};
        """
    )

    assert result == {"message": "Newest result"}


@pytest.mark.parametrize(
    "completed_action",
    ["Created Alpha", "Saved Alpha", "Archived Alpha", "Removed Alpha"],
)
def test_milestone_completed_mutation_reports_failed_list_refresh(completed_action):
    result = _run_board_javascript_harness(
        f"""
        fetch = async () => ({{
          ok: false,
          json: async () => ({{error: "Reload failed"}}),
        }});
        const loaded = await loadMilestones({json.dumps(completed_action)});
        return {{loaded, message: milestoneStatus.textContent}};
        """
    )

    assert result == {
        "loaded": False,
        "message": f"{completed_action}, but the list could not be refreshed: Reload failed",
    }


def test_browser_milestone_dialog_handles_mutation_policies_and_inline_errors(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    actions = html.split("async function submitMilestoneCreate", maxsplit=1)[1].split(
        'document.getElementById("task-create-open")', maxsplit=1
    )[0]

    assert 'fetch("/api/milestones"' in actions
    assert '}/edit`' in actions
    assert '}/archive`' in actions
    assert '}/remove`' in actions
    assert 'taskReferenceCount > 0' in html
    assert 'taskHandling: policy.value' in actions
    assert 'const body = selected.taskReferenceCount > 0 ?' in actions
    assert "showMilestoneMessage" in actions
    assert "responseErrorMessage" in actions
    assert "setMilestoneBusy(control, true)" in actions
    assert "setMilestoneBusy(control, false)" in actions
    assert "querySelectorAll" not in actions
    assert actions.count("const completedAction =") == 4
    assert actions.count("await loadMilestones(completedAction)") == 4


def test_browser_milestone_dialog_has_focus_scroll_and_responsive_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    dialog_rule = html.split("dialog {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    body_rule = html.split(".dialog-body {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    responsive = html.split("@media (max-width: 720px)", maxsplit=1)[1]

    assert "max-height:" in dialog_rule
    assert "overflow: hidden;" in dialog_rule
    assert "max-height:" in body_rule
    assert "overflow-y: auto;" in body_rule
    assert ".milestone-manager :focus-visible" in html
    assert ".milestone-manager {" in responsive
    assert "grid-template-columns: 1fr;" in responsive
    assert "@keyframes" not in html
    assert "animation:" not in html


def test_browser_pending_revision_is_deferred_until_final_dialog_close(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import render_board_html

    html = render_board_html(project)
    handler = html.split("function handleBoardRevision", maxsplit=1)[1].split(
        "async function pollBoardRevision", maxsplit=1
    )[0]
    close_handler = html.split('document.querySelectorAll("dialog")', maxsplit=1)[1]

    assert html.count('let pendingBoardRevision = "";') == 1
    assert "if (!nextRevision || nextRevision === currentBoardRevision) return;" in handler
    assert "if (hasOpenDialog())" in handler
    assert "pendingBoardRevision = nextRevision;" in handler
    assert "window.location.reload();" in handler
    assert 'dialog.addEventListener("close"' in close_handler
    assert "if (pendingBoardRevision && !hasOpenDialog()) window.location.reload();" in close_handler


def test_milestone_edit_datetime_local_round_trip_is_lossless(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MilestoneService(project).add_milestone(
        "Release",
        "Scope.",
        due_date="2026-09-30 17:00",
    )

    from backlog_py.browser.service import render_board_html, start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        before = _get_json(f"{service.root_url}/api/milestones")["milestones"][0]
        control_value = before["dueDate"].replace(" ", "T")[:16]
        _post_json(
            f"{service.root_url}/api/milestones/{before['key']}/edit",
            {
                "title": before["title"],
                "description": before["description"],
                "dueDate": control_value,
            },
        )
        after = _get_json(f"{service.root_url}/api/milestones")["milestones"][0]
    finally:
        service.shutdown()

    html = render_board_html(project)
    assert control_value == "2026-09-30T17:00"
    assert after["dueDate"] == "2026-09-30 17:00"
    assert "function dueDateInputValue(value)" in html
    assert 'String(value).replace(" ", "T").slice(0, 16)' in html
    assert 'dueDate: String(data.get("dueDate") || "")' in html


def test_browser_command_uses_config_default_port_and_no_open(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "defaultPort", "45678")
    calls = []

    def fake_run_browser_service_foreground(project, *, host, port, open_browser):
        calls.append(
            {
                "project_root": project.root,
                "host": host,
                "port": port,
                "open_browser": open_browser,
            }
        )

    monkeypatch.setattr("backlog_py.cli.main.run_browser_service_foreground", fake_run_browser_service_foreground)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "browser", "--no-open"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "project_root": repo,
            "host": "127.0.0.1",
            "port": 45678,
            "open_browser": False,
        }
    ]


def test_browser_command_port_option_overrides_config_and_auto_open(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    calls = []

    def fake_run_browser_service_foreground(project, *, host, port, open_browser):
        calls.append((project.root, host, port, open_browser))

    monkeypatch.setattr("backlog_py.cli.main.run_browser_service_foreground", fake_run_browser_service_foreground)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "browser", "--port", "45679"])

    assert result.exit_code == 0, result.output
    assert calls == [(repo, "127.0.0.1", 45679, True)]


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _run_board_javascript_harness(test_source: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for executable browser JavaScript contracts")
    payload = json.dumps(
        {
            "boardSource": Path("src/backlog_py/browser/assets/board.js").read_text(encoding="utf-8"),
            "testSource": test_source,
        }
    )
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const milestoneStatus = {textContent: ""};
const configStatus = {textContent: ""};
const fakeConfigStatusAddInput = {
  _disabled: false,
  value: "",
  listeners: {},
  get disabled() { return this._disabled; },
  set disabled(value) {
    this._disabled = Boolean(value);
    if (this._disabled) this.focused = false;
  },
  addEventListener(type, listener) { this.listeners[type] = listener; },
  focus() { if (!this.disabled) this.focused = true; },
};
const fakeConfigStatusAddButton = {
  listeners: {},
  addEventListener(type, listener) { this.listeners[type] = listener; },
};
const fakeConfigSettingsDialog = {
  open: true,
  close() { this.open = false; },
  showModal() { this.open = true; },
  setAttribute(name) { if (name === "open") this.open = true; },
};
const document = {
  getElementById: (id) => ({
    "milestone-message": milestoneStatus,
    "config-status-message": configStatus,
    "config-status-add": fakeConfigStatusAddInput,
    "config-status-add-button": fakeConfigStatusAddButton,
    "config-settings-dialog": fakeConfigSettingsDialog,
  })[id] || null,
  querySelector: (selector) => selector === "[data-board-revision]"
    ? {dataset: {boardRevision: "", mermaidUrl: "", mermaidSri: ""}}
    : null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const window = {
  document,
  setInterval: () => 1,
  clearInterval: () => {},
  location: {reload: () => {}},
};
const context = vm.createContext({
  console,
  document,
  window,
  milestoneStatus,
  configStatus,
  fakeConfigStatusAddInput,
  fakeConfigStatusAddButton,
  fakeConfigSettingsDialog,
  HTMLSelectElement: class {},
  HTMLTextAreaElement: class {},
  FormData: class {
    constructor(form) { this.form = form; }
    get(name) { return this.form._data[name] ?? null; }
  },
  Event: class {},
  Node: {TEXT_NODE: 3, ELEMENT_NODE: 1},
});
vm.runInContext(payload.boardSource, context, {filename: "board.js"});
vm.runInContext(`(async () => {${payload.testSource}})()`, context)
  .then((result) => process.stdout.write(JSON.stringify(result)))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _configure_statuses(repo: Path, statuses: list[str] | None, *, default: str):
    config_path = repo / "backlog" / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if statuses is None:
        raw.pop("statuses", None)
    else:
        raw["statuses"] = statuses
    raw["defaultStatus"] = default
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _replace_browser_fixture_task_status(repo: Path, status: str) -> None:
    path = _task_file(repo)
    source = path.read_text(encoding="utf-8")
    replaced = source.replace("status: In Progress", f"status: {yaml.safe_dump(status).strip()}")
    assert replaced != source
    path.write_text(replaced, encoding="utf-8")


def _install_newer_branch_task(monkeypatch, repo: Path) -> None:
    from backlog_py.core import repository as repository_module
    from backlog_py.runtime.git import GitTaskSnapshot

    local_path = _task_file(repo)
    branch_source = local_path.read_text(encoding="utf-8").replace(
        "title: Example task", "title: Newer branch winner"
    )
    relative_path = local_path.relative_to(repo).as_posix()
    monkeypatch.setattr(
        repository_module,
        "current_task_snapshot_timestamps",
        lambda project, paths: {path: 1.0 for path in paths},
    )
    monkeypatch.setattr(
        repository_module,
        "list_active_branch_task_snapshots",
        lambda project: [
            GitTaskSnapshot(
                ref="refs/heads/feature",
                relative_path=relative_path,
                source=branch_source,
                committed_at=2.0,
            )
        ],
    )


def _install_branch_only_task(monkeypatch) -> None:
    from backlog_py.core import repository as repository_module
    from backlog_py.runtime.git import GitTaskSnapshot

    monkeypatch.setattr(
        repository_module,
        "list_active_branch_task_snapshots",
        lambda project: [
            GitTaskSnapshot(
                ref="refs/heads/feature",
                relative_path="backlog/tasks/task-99 - branch-only.md",
                source=(
                    "---\nid: TASK-99\ntitle: Branch only\nstatus: In Progress\n---\n"
                    "\n## Acceptance Criteria\n<!-- AC:BEGIN -->\n"
                    "- [ ] #1 Branch criterion\n<!-- AC:END -->\n"
                ),
                committed_at=2.0,
            )
        ],
    )


def _write_browser_legacy_milestone(repo: Path, name: str, *, filename: str) -> Path:
    path = repo / "backlog" / "milestones" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\n---\n\nLegacy scope.\n", encoding="utf-8")
    return path


def _backlog_snapshot(repo: Path) -> dict[str, bytes]:
    backlog = repo / "backlog"
    return {
        path.relative_to(backlog).as_posix(): path.read_bytes()
        for path in sorted(backlog.rglob("*"))
        if path.is_file()
    }


def _raw_browser_request(
    service,
    method: str,
    path: str,
    *,
    host_header: str,
    origin: str | None = None,
    payload: object | None = None,
) -> dict[str, object]:
    connection = http.client.HTTPConnection(service.host, service.port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host_header)
        if origin is not None:
            connection.putheader("Origin", origin)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        with connection.getresponse() as response:
            return {"status": response.status, "body": response.read().decode("utf-8")}
    finally:
        connection.close()


def _task_sources(repo: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted((repo / "backlog" / "tasks").glob("*.md"))
    }


def _task_file(repo: Path) -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob("task-1 -*.md"))
    assert len(matches) == 1
    return matches[0]


def _task_file_exists(repo: Path) -> bool:
    return bool(list((repo / "backlog" / "tasks").glob("task-1 -*.md")))


def _archived_task_file(repo: Path) -> Path:
    return repo / "backlog" / "archive" / "tasks" / "task-1 - Example-task.md"


def _created_task_file(repo: Path) -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob("task-2 -*.md"))
    assert len(matches) == 1
    return matches[0]


def _get_json(url: str) -> object:
    return json.loads(_get_text(url))


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read().decode("utf-8")


def _get_response_text(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return {
            "status": response.status,
            "contentType": response.headers.get("Content-Type"),
            "body": response.read().decode("utf-8"),
        }


def _get_response_bytes(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return {
            "status": response.status,
            "contentType": response.headers.get("Content-Type"),
            "body": response.read(),
        }


def _post_json(url: str, payload: object, *, origin: str | None = None, omit_origin: bool = False) -> object:
    return _post_json_response(url, payload, origin=origin, omit_origin=omit_origin)["body"]


def _post_json_response(
    url: str,
    payload: object,
    *,
    origin: str | None = None,
    omit_origin: bool = False,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if origin is None and not omit_origin:
        # Browsers always send Origin on POST; mirror that by default so tests
        # exercise the same code path the board does.
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return {
            "status": response.status,
            "body": json.loads(response.read().decode("utf-8")),
        }
