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


def test_browser_board_html_exposes_live_refresh_polling_contract(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert 'data-board-revision="' in html
    assert "pollBoardRevision" in html
    assert "hasOpenDialog" in html
    assert "setInterval" in html
    assert "/api/board" in html


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
    assert 'name="title"' in html
    assert 'name="status"' in html
    assert 'name="description"' in html
    assert 'name="acceptanceCriteria"' in html
    assert "submitTaskEdit" in html
    assert "/api/tasks/" in html


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
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(f"{service.root_url}/api/settings/dod-defaults", {"items": "Tests pass"})
    finally:
        service.shutdown()

    assert exc.value.code == 400
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before


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
