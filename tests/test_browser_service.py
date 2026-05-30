import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.storage.config import get_definition_of_done_defaults, replace_definition_of_done_defaults, set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_browser_service_module_does_not_embed_full_board_assets():
    source = Path("src/backlog_py/browser/service.py").read_text(encoding="utf-8")

    assert "<style>" not in source
    assert "<script>" not in source
    assert "let draggedTaskId = null;" not in source


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
    assert "mermaid.esm.min.mjs" in html
    assert 'querySelectorAll("[data-mermaid-diagram] .mermaid")' in html
    assert 'securityLevel: "strict"' in html
    assert "mermaidModulePromise = null;\n        diagrams.forEach" in html


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
    assert response["body"]["task"]["acceptanceCriteria"][1]["checked"] is True
    assert task["acceptanceCriteria"][1]["checked"] is True
    assert "- [x] #2 Preserve incomplete acceptance criteria raw line" in _task_file(repo).read_text(encoding="utf-8")


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
            "includeDatetimeInDates": True,
            "projectName": "basic-fixture",
            "remoteOperations": True,
            "statuses": ["To Do", "In Progress", "Done"],
            "zeroPaddedIds": None,
        }
    }


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
    assert '<option value="Ready">Ready</option>' in html


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
    assert 'name="statuses"' in html
    assert 'name="autoCommit"' in html
    assert 'name="remoteOperations"' in html
    assert 'name="checkActiveBranches"' in html
    assert 'name="activeBranchDays"' in html
    assert "configSettingsForm.elements.autoCommit.checked" in html
    assert "activeBranchDays: Number(data.get(\"activeBranchDays\") || 0)" in html
    assert "openConfigSettings" in html
    assert "submitConfigSettings" in html
    assert "/api/settings/config" in html


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


def _post_json(url: str, payload: object, *, origin: str | None = None) -> object:
    return _post_json_response(url, payload, origin=origin)["body"]


def _post_json_response(url: str, payload: object, *, origin: str | None = None) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
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
