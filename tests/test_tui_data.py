import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from backlog_py.core.decisions import DecisionService
from backlog_py.core.documents import DocumentService
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.daemon.lifecycle import DaemonNotRunningError
from backlog_py.storage.project import discover_project
from backlog_py.tui.data import (
    DaemonBoardDataSource,
    DaemonMcpClient,
    DaemonMutationError,
    LocalBoardDataSource,
    create_board_data_source,
)
from backlog_py.tui import models as tui_models
from backlog_py.tui.models import CreateTaskInput, EditTaskInput


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_local_data_source_loads_board_and_mutates_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.data.with_project_write_lock", fake_lock)
    source = LocalBoardDataSource(project)

    snapshot = source.load_board()
    created = source.create_task(CreateTaskInput(title="New card", status="To Do", labels=("tui",)))
    moved = source.move_task(created.id, "Done")
    edited = source.edit_task(
        created.id,
        EditTaskInput(
            title="Edited card",
            status="In Progress",
            description="Edited description",
            priority=None,
            clear_priority=True,
            assignees=("codex",),
            labels=("tui", "metadata"),
            milestone=None,
            clear_milestone=True,
            dependencies=("TASK-1",),
        ),
    )
    archived = source.archive_task(created.id)

    assert snapshot.source == "local"
    assert snapshot.columns["In Progress"][0].id == "TASK-1"
    assert created.labels == ("tui",)
    assert moved.status == "Done"
    assert edited.title == "Edited card"
    assert edited.status == "In Progress"
    assert edited.priority is None
    assert edited.assignees == ("codex",)
    assert edited.labels == ("tui", "metadata")
    assert edited.milestone is None
    assert edited.dependencies == ("TASK-1",)
    assert archived.id == created.id
    assert operations == [
        (repo, "tui_task_create"),
        (repo, "tui_task_move"),
        (repo, "tui_task_edit"),
        (repo, "tui_task_archive"),
    ]


def test_local_data_source_toggles_checklist_items_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.data.with_project_write_lock", fake_lock)
    source = LocalBoardDataSource(project)

    unchecked = source.set_checklist_item("TASK-1", "AC", 1, checked=False)
    checked = source.set_checklist_item("TASK-1", "DOD", 1, checked=True)

    assert unchecked.acceptance_criteria[0].checked is False
    assert checked.definition_of_done[0].checked is True
    assert operations == [
        (repo, "tui_task_checklist"),
        (repo, "tui_task_checklist"),
    ]


def test_local_source_task_path_returns_validated_absolute_project_path(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    path = LocalBoardDataSource(project).task_path("TASK-1")

    assert path.is_absolute()
    assert path.is_relative_to(repo)
    assert path.name == "task-1 - Example-task.md"


def test_local_data_source_searches_tasks_documents_and_decisions(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    task = MutableRepository(project).create_task(title="Shared needle task", description="TUI search target")
    document = DocumentService(project).create_document(
        "guides/search.md",
        title="Shared needle guide",
        content="Document search target",
    )
    decision = DecisionService(project).create_decision("Shared needle decision", status="accepted")

    results = LocalBoardDataSource(project).search("shared needle", limit=10)

    assert [(result.kind, result.identifier, result.title) for result in results] == [
        ("task", task.id, "Shared needle task"),
        ("document", document.path_relative, "Shared needle guide"),
        ("decision", decision.id, "Shared needle decision"),
    ]
    assert results[0].task_id == task.id
    assert results[1].task_id is None
    assert results[2].task_id is None


def test_local_data_source_updates_safe_settings_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.data.with_project_write_lock", fake_lock)
    source = LocalBoardDataSource(project)
    settings_input = tui_models.SettingsInput(
        project_name="TUI project",
        default_assignee="codex",
        default_status="Ready",
        date_format="dd/mm/yyyy",
        include_datetime_in_dates=False,
        default_port=6543,
        auto_open_browser=False,
        zero_padded_ids=4,
        auto_commit=True,
        remote_operations=True,
        check_active_branches=True,
        active_branch_days=14,
        statuses=("Ready", "In Progress", "Done"),
    )

    updated = source.update_settings(settings_input)

    assert updated.project_name == "TUI project"
    assert updated.default_assignee == "codex"
    assert updated.default_status == "Ready"
    assert updated.date_format == "dd/mm/yyyy"
    assert updated.include_datetime_in_dates is False
    assert updated.default_port == 6543
    assert updated.auto_open_browser is False
    assert updated.zero_padded_ids == 4
    assert updated.auto_commit is True
    assert updated.remote_operations is True
    assert updated.check_active_branches is True
    assert updated.active_branch_days == 14
    assert updated.statuses == ("Ready", "In Progress", "Done")
    assert source.project.config.project_name == "TUI project"
    assert source.load_board().statuses == ("Ready", "In Progress", "Done")
    assert operations == [(repo, "tui_config_settings_update")]


def test_local_data_source_rejects_invalid_settings_without_partial_mutation(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    source = LocalBoardDataSource(project)
    before = project.config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Project name is required"):
        source.update_settings(
            tui_models.SettingsInput(
                project_name="",
                default_assignee=None,
                default_status="Ready",
                date_format="dd/mm/yyyy",
                include_datetime_in_dates=False,
                default_port=6543,
                auto_open_browser=False,
                zero_padded_ids=4,
                auto_commit=True,
                remote_operations=True,
                check_active_branches=True,
                active_branch_days=14,
                statuses=(),
            )
        )

    assert project.config_path.read_text(encoding="utf-8") == before


def test_local_data_source_updates_dod_defaults_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.data.with_project_write_lock", fake_lock)
    source = LocalBoardDataSource(project)

    updated = source.update_definition_of_done_defaults(
        tui_models.DefinitionOfDoneDefaultsInput(items=("Tests pass", "Docs updated"))
    )

    assert updated.items == ("Tests pass", "Docs updated")
    assert source.project.config.definition_of_done == ["Tests pass", "Docs updated"]
    assert source.load_definition_of_done_defaults().items == ("Tests pass", "Docs updated")
    assert operations == [(repo, "tui_dod_defaults_update")]


def test_local_data_source_rejects_invalid_dod_defaults_without_partial_mutation(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    source = LocalBoardDataSource(project)
    before = project.config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Definition of Done defaults must be strings"):
        source.update_definition_of_done_defaults(
            tui_models.DefinitionOfDoneDefaultsInput(items=("Tests pass", object()))  # type: ignore[arg-type]
        )

    assert project.config_path.read_text(encoding="utf-8") == before


def test_daemon_data_source_loads_board_with_task_view_hydration():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    relative_path = task.path.relative_to(project.root).as_posix()
    daemon = _FakeMcpDaemon(
        {
            "task_board": {
                "In Progress": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "description": task.description,
                        "path": relative_path,
                    }
                ]
            },
            "task_view": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "description": task.description,
                "path": relative_path,
                "raw_source": task.raw_source,
            },
        }
    )
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        snapshot = source.load_board()
    finally:
        daemon.shutdown()

    assert snapshot.source == "daemon"
    assert snapshot.statuses == ("To Do", "In Progress", "Done")
    assert snapshot.columns["To Do"] == ()
    assert snapshot.columns["Done"] == ()
    assert snapshot.columns["In Progress"][0].acceptance_criteria[0].checked is True
    assert [(request["tool"], request["arguments"].get("task_id")) for request in daemon.tool_calls] == [
        ("task_board", None),
        ("task_view", "TASK-1"),
    ]


def test_daemon_data_source_orders_columns_by_configured_statuses():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    relative_path = task.path.relative_to(project.root).as_posix()
    daemon = _FakeMcpDaemon(
        {
            "task_board": {
                "Done": [],
                "In Progress": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "description": task.description,
                        "path": relative_path,
                    }
                ],
                "To Do": [],
            },
            "task_view": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "description": task.description,
                "path": relative_path,
                "raw_source": task.raw_source,
            },
        }
    )
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        snapshot = source.load_board()
    finally:
        daemon.shutdown()

    assert snapshot.statuses == ("To Do", "In Progress", "Done")
    assert tuple(snapshot.columns) == ("To Do", "In Progress", "Done")
    assert snapshot.columns["In Progress"][0].id == "TASK-1"


def test_daemon_mutations_call_expected_tools_and_return_task_views():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    detail = {
        "id": task.id,
        "title": task.title,
        "status": "Done",
        "description": task.description,
        "path": task.path.relative_to(project.root).as_posix(),
        "raw_source": task.raw_source,
    }
    daemon = _FakeMcpDaemon(
        {
            "task_create": {**detail, "id": "TASK-2", "title": "New card", "status": "To Do"},
            "task_edit": detail,
            "task_archive": detail,
        }
    )
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        created = source.create_task(
            CreateTaskInput(
                title="New card",
                status="To Do",
                priority="high",
                assignees=("alice", "bob"),
                labels=("tui",),
                milestone="Release 1",
                dependencies=("TASK-1",),
                acceptance_criteria=("Works",),
                definition_of_done_add=("Tests pass",),
                description="Description body",
            )
        )
        moved = source.move_task("TASK-1", "Done")
        edited = source.edit_task(
            "TASK-1",
            EditTaskInput(
                title="Edited card",
                status="In Progress",
                description="Edited description",
                priority=None,
                clear_priority=True,
                assignees=("codex",),
                labels=("tui", "metadata"),
                milestone=None,
                clear_milestone=True,
                dependencies=("TASK-2",),
            ),
        )
        archived = source.archive_task("TASK-1")
    finally:
        daemon.shutdown()

    assert created.id == "TASK-2"
    assert moved.status == "Done"
    assert edited.id == "TASK-1"
    assert archived.id == "TASK-1"
    assert [(request["tool"], request["arguments"]) for request in daemon.tool_calls] == [
        (
            "task_create",
            {
                "project": str(project.root),
                "title": "New card",
                "status": "To Do",
                "description": "Description body",
                "priority": "high",
                "assignees": ["alice", "bob"],
                "labels": ["tui"],
                "milestone": "Release 1",
                "dependencies": ["TASK-1"],
                "acceptanceCriteria": ["Works"],
                "definitionOfDoneAdd": ["Tests pass"],
            },
        ),
        ("task_edit", {"project": str(project.root), "task_id": "TASK-1", "status": "Done"}),
        (
            "task_edit",
            {
                "project": str(project.root),
                "task_id": "TASK-1",
                "title": "Edited card",
                "status": "In Progress",
                "description": "Edited description",
                "clearPriority": True,
                "assignees": ["codex"],
                "labels": ["tui", "metadata"],
                "milestone": None,
                "dependencies": ["TASK-2"],
            },
        ),
        ("task_archive", {"project": str(project.root), "task_id": "TASK-1"}),
    ]


def test_daemon_data_source_toggles_checklist_items_with_task_edit_arguments():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    detail = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "description": task.description,
        "path": task.path.relative_to(project.root).as_posix(),
        "raw_source": task.raw_source,
    }
    daemon = _FakeMcpDaemon({"task_edit": detail})
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        unchecked = source.set_checklist_item("TASK-1", "AC", 1, checked=False)
        checked = source.set_checklist_item("TASK-1", "DOD", 1, checked=True)
    finally:
        daemon.shutdown()

    assert unchecked.id == "TASK-1"
    assert checked.id == "TASK-1"
    assert [(request["tool"], request["arguments"]) for request in daemon.tool_calls] == [
        ("task_edit", {"project": str(project.root), "task_id": "TASK-1", "uncheckAc": [1]}),
        ("task_edit", {"project": str(project.root), "task_id": "TASK-1", "checkDod": [1]}),
    ]


def test_daemon_mutation_failure_does_not_fall_back_to_local_mode(monkeypatch):
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    daemon = _FakeMcpDaemon({"task_edit": RuntimeError("boom")})
    monkeypatch.setattr(
        "backlog_py.tui.data.MutableRepository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local fallback should not run")),
    )
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        with pytest.raises(DaemonMutationError):
            source.move_task("TASK-1", "Done")
    finally:
        daemon.shutdown()


def test_daemon_client_rejects_non_json_tool_text():
    daemon = _FakeMcpDaemon({"task_board": _RawToolText("not json")})
    try:
        client = DaemonMcpClient(endpoint=daemon.endpoint, token="secret")
        with pytest.raises(RuntimeError, match="valid JSON"):
            client.call_tool("task_board", {"project": str(FIXTURE_REPO)})
    finally:
        daemon.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:18765/mcp",
        "http://192.168.1.2:18765/mcp",
        "file:///tmp/daemon.sock",
    ],
)
def test_daemon_endpoint_validation_rejects_non_loopback_http(endpoint):
    with pytest.raises(ValueError, match="loopback HTTP"):
        DaemonMcpClient(endpoint=endpoint, token="secret")


def test_daemon_task_path_uses_payload_path_and_validates_containment():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    daemon = _FakeMcpDaemon(
        {
            "task_view": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "description": task.description,
                "path": task.path.relative_to(project.root).as_posix(),
                "raw_source": task.raw_source,
            }
        }
    )
    try:
        path = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret").task_path("TASK-1")
    finally:
        daemon.shutdown()

    assert path.is_absolute()
    assert path.is_relative_to(project.root)
    assert path.name == "task-1 - Example-task.md"


def test_factory_uses_healthy_daemon_without_starting_one(monkeypatch):
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    monkeypatch.setattr(
        "backlog_py.tui.data.daemon_status",
        lambda: _status("http://127.0.0.1:18765/mcp", "secret"),
    )

    source = create_board_data_source(project)

    assert isinstance(source, DaemonBoardDataSource)


def test_factory_falls_back_to_local_when_daemon_is_not_running(monkeypatch):
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    monkeypatch.setattr(
        "backlog_py.tui.data.daemon_status",
        lambda: (_ for _ in ()).throw(DaemonNotRunningError("Daemon not running")),
    )

    source = create_board_data_source(project)

    assert isinstance(source, LocalBoardDataSource)


def _status(endpoint: str, token: str):
    return SimpleNamespace(record=SimpleNamespace(endpoint=endpoint, token=token))


class _FakeMcpDaemon:
    def __init__(self, responses):
        self.responses = responses
        self.tool_calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.responses = self.responses
        self.server.tool_calls = self.tool_calls
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}/mcp"

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _handler(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                params = body["params"]
                tool_name = params["name"]
                arguments = dict(params.get("arguments") or {})
                self.server.tool_calls.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "tool": tool_name,
                        "arguments": arguments,
                    }
                )
                response = self.server.responses[tool_name]
                if isinstance(response, RuntimeError):
                    payload = {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {"code": -32603, "message": str(response)},
                    }
                elif isinstance(response, _RawToolText):
                    payload = {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "content": [{"type": "text", "text": response.text}],
                            "isError": False,
                        },
                    }
                else:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(response, sort_keys=True)}],
                            "isError": False,
                        },
                    }
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                _ = format, args

        return Handler


class _RawToolText:
    def __init__(self, text):
        self.text = text
