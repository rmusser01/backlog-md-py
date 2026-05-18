import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.storage.config import set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


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


def _created_task_file(repo: Path) -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob("task-2 -*.md"))
    assert len(matches) == 1
    return matches[0]


def _get_json(url: str) -> object:
    return json.loads(_get_text(url))


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read().decode("utf-8")


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
