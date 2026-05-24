import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.daemon.lifecycle import DaemonNotRunningError
from backlog_py.storage.project import discover_project
from backlog_py.tui.data import (
    DaemonBoardDataSource,
    DaemonMcpClient,
    DaemonMutationError,
    LocalBoardDataSource,
    create_board_data_source,
)
from backlog_py.tui.models import CreateTaskInput


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
    archived = source.archive_task(created.id)

    assert snapshot.source == "local"
    assert snapshot.columns["In Progress"][0].id == "TASK-1"
    assert created.labels == ("tui",)
    assert moved.status == "Done"
    assert archived.id == created.id
    assert operations == [
        (repo, "tui_task_create"),
        (repo, "tui_task_move"),
        (repo, "tui_task_archive"),
    ]


def test_local_source_task_path_returns_validated_absolute_project_path(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    path = LocalBoardDataSource(project).task_path("TASK-1")

    assert path.is_absolute()
    assert path.is_relative_to(repo)
    assert path.name == "task-1 - Example-task.md"


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
    assert snapshot.statuses == ("In Progress",)
    assert snapshot.columns["In Progress"][0].acceptance_criteria[0].checked is True
    assert [(request["tool"], request["arguments"].get("task_id")) for request in daemon.tool_calls] == [
        ("task_board", None),
        ("task_view", "TASK-1"),
    ]


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
        archived = source.archive_task("TASK-1")
    finally:
        daemon.shutdown()

    assert created.id == "TASK-2"
    assert moved.status == "Done"
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
        ("task_archive", {"project": str(project.root), "task_id": "TASK-1"}),
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
