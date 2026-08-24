"""Regression tests for inherited MCP-server bugs."""
from __future__ import annotations

import inspect
import io
import json
from pathlib import Path

import pytest

import backlog_py.core.repository as repository_module
from backlog_py.core.documents import DocumentService
from backlog_py.core.init import init_project
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp import protocol as protocol_module
from backlog_py.mcp import tools as tool_registry
from backlog_py.mcp.catalog import TOOL_DEFINITIONS, ToolDefinition, list_tools
from backlog_py.mcp.http_server import start_mcp_http_server
from backlog_py.mcp.protocol import McpRequestContext, handle_jsonrpc_message
from backlog_py.mcp.stdio_server import _inject_project_hint, run_stdio
from backlog_py.orchestration import OrchestrationService, OrchestrationValidationError


def _project(tmp_path: Path):
    return init_project(tmp_path, no_git=True).project


def _result_text(response) -> str:
    """The text payload of a tools/call response."""
    return response["result"]["content"][0]["text"]


def _call_tool(project_root: Path, name: str, /, **arguments):
    arguments["project"] = str(project_root)
    return handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


# --- #9: domain errors surface as -32603 and leak paths ---------------------

def test_task_view_missing_returns_tool_error_not_internal_error(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "task_view", task_id="TASK-999")

    assert "error" not in response, response.get("error")
    result = response["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "not found" in text.casefold()
    assert str(project.root) not in text, "tool error leaked the absolute project path"


def test_task_edit_unknown_status_returns_tool_error(tmp_path):
    from backlog_py.core.repository import MutableRepository

    project = _project(tmp_path)
    MutableRepository(project).create_task(title="Task")

    response = _call_tool(project.root, "task_edit", task_id="TASK-1", status="Nonsense")

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is True


# --- #7: unquoted dates in frontmatter crash JSON serialization -------------

def test_document_view_with_unquoted_date_does_not_error(tmp_path):
    project = _project(tmp_path)
    doc = DocumentService(project).create_document(
        path="notes", title="Notes", content="Body", metadata={"date": "2026-07-02"}
    )
    # Rewrite the on-disk date unquoted, the way a human or another tool would.
    text = doc.path.read_text(encoding="utf-8").replace("date: '2026-07-02'", "date: 2026-07-02")
    assert "date: 2026-07-02" in text
    doc.path.write_text(text, encoding="utf-8")

    response = _call_tool(project.root, "document_view", path_or_id=doc.id)

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False


# --- #10/M8: missing required fields create junk files ----------------------

def _is_error_response(response) -> bool:
    return "error" in response or bool(response.get("result", {}).get("isError"))


def test_task_create_without_title_is_rejected(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "task_create")

    assert _is_error_response(response), response
    created = list((project.backlog_dir / "tasks").glob("*.md"))
    assert created == [], f"junk task file created: {created}"


def test_document_create_without_title_is_rejected(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "document_create", path="notes", content="body")

    assert _is_error_response(response), response
    created = list((project.backlog_dir / "docs").glob("*.md"))
    assert created == [], f"junk document file created: {created}"


def test_document_create_allows_empty_content(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "document_create", path="notes", title="Notes", content="")

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False


# --- #8: daemon forwarding drops the local project hint ---------------------

def test_stdio_forwarding_injects_local_project_hint(tmp_path):
    project = _project(tmp_path)
    # A daemon HTTP server created without a project context (as the real
    # daemon is): its own project_hint is None.
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "project_status", "arguments": {}},
            }
        )
        stdout = io.StringIO()
        run_stdio(
            stdin=io.StringIO(request + "\n"),
            stdout=stdout,
            context=McpRequestContext(project_hint=str(project.root)),
            daemon_endpoint=service.endpoint,
            token="secret",
        )
    finally:
        service.shutdown()

    response = json.loads(stdout.getvalue().strip())
    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False


# --- F1: document_update silently drops metadata and directory --------------

def _frontmatter(project, path_or_id: str) -> dict:
    return DocumentService(project).view_document(path_or_id).frontmatter


def test_document_update_round_trips_metadata_to_disk(tmp_path):
    project = _project(tmp_path)
    document = DocumentService(project).create_document(path="notes", title="Notes", content="Body")

    response = _call_tool(project.root, "document_update", path_or_id=document.id, metadata={"type": "guide"})

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False
    assert _frontmatter(project, document.id)["type"] == "guide"


def test_document_update_metadata_none_deletes_key(tmp_path):
    project = _project(tmp_path)
    document = DocumentService(project).create_document(
        path="notes", title="Notes", content="Body", metadata={"type": "guide"}
    )

    response = _call_tool(project.root, "document_update", path_or_id=document.id, metadata={"type": None})

    assert "error" not in response, response.get("error")
    assert "type" not in _frontmatter(project, document.id)


def test_document_update_type_and_tags_match_cli_metadata_semantics(tmp_path):
    project = _project(tmp_path)
    document = DocumentService(project).create_document(path="notes", title="Notes", content="Body")

    response = _call_tool(
        project.root,
        "document_update",
        path_or_id=document.id,
        type="guide",
        tags="alpha, beta",
    )

    assert "error" not in response, response.get("error")
    frontmatter = _frontmatter(project, document.id)
    assert frontmatter["type"] == "guide"
    assert frontmatter["tags"] == ["alpha", "beta"]


def test_document_update_moves_document_with_directory(tmp_path):
    project = _project(tmp_path)
    document = DocumentService(project).create_document(path="notes", title="Notes", content="Body")

    response = _call_tool(project.root, "document_update", path_or_id=document.id, directory="guides")

    assert "error" not in response, response.get("error")
    assert DocumentService(project).view_document(document.id).path_relative == "guides/notes.md"
    assert not document.path.exists()


def test_document_update_schema_declares_metadata_and_directory():
    schema = next(tool for tool in list_tools() if tool["name"] == "document_update")["inputSchema"]

    assert {"metadata", "directory", "type", "tags"}.issubset(schema["properties"])


# --- F2: non-project path returns -32603 instead of actionable guidance -----

def test_tool_call_outside_a_project_points_at_init_required(tmp_path):
    outside = tmp_path / "not-a-project"
    outside.mkdir()

    response = _call_tool(outside, "task_board")

    assert "error" not in response, response.get("error")
    result = response["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "backlog://init-required" in text
    assert str(outside) not in text, "init-required guidance leaked the absolute path"


def _stub_tool(handler) -> ToolDefinition:
    return ToolDefinition("task_board", "stub", {}, handler)


def test_internal_errors_do_not_leak_absolute_paths(tmp_path, monkeypatch):
    project = _project(tmp_path)
    secret = tmp_path / "secret" / "workspace"

    def boom(_project, **_kwargs):
        raise RuntimeError(f"exploded while reading {secret}")

    monkeypatch.setattr(protocol_module, "tool_by_name", lambda _name: _stub_tool(boom))

    response = _call_tool(project.root, "task_board")

    assert response["error"]["code"] == -32603
    assert str(secret) not in json.dumps(response), "internal error leaked an absolute path"


# --- F3: daemon forwarding changes tools/call with no 'arguments' key -------

def test_inject_project_hint_adds_missing_arguments_object():
    text = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "task_board"}})

    injected = json.loads(_inject_project_hint(text, "/repo"))

    assert injected["params"]["arguments"] == {"project": "/repo"}


def test_tools_call_without_arguments_key_matches_local_and_forwarded_paths(tmp_path):
    project = _project(tmp_path)
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "task_board"}}
    context = McpRequestContext(project_hint=str(project.root))

    local = handle_jsonrpc_message(message, context=context)
    assert "error" not in local, local.get("error")
    assert local["result"]["isError"] is False

    service = start_mcp_http_server(host="127.0.0.1", port=0, token="secret")
    try:
        stdout = io.StringIO()
        run_stdio(
            stdin=io.StringIO(json.dumps(message) + "\n"),
            stdout=stdout,
            context=context,
            daemon_endpoint=service.endpoint,
            token="secret",
        )
    finally:
        service.shutdown()

    forwarded = json.loads(stdout.getvalue().strip())
    assert "error" not in forwarded, forwarded.get("error")
    assert forwarded["result"]["isError"] is False


# --- F4: read tools trigger a synchronous git fetch --------------------------

@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("project_status", {}),
        ("task_board", {}),
        ("task_list", {}),
        ("task_search", {"query": "Example"}),
        ("task_view", {"task_id": "TASK-1"}),
    ],
)
def test_mcp_read_tools_do_not_refresh_remote_refs(tmp_path, monkeypatch, tool_name, arguments):
    project = _project(tmp_path)
    MutableRepository(project).create_task(title="Example")
    fetches = []
    monkeypatch.setattr(repository_module, "maybe_fetch_remote_refs", lambda project: fetches.append(project))

    response = _call_tool(project.root, tool_name, **arguments)

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False
    assert fetches == [], f"{tool_name} performed a remote ref refresh"


# --- F5: broad except TypeError misclassifies internal bugs ------------------

def test_task_search_coerces_numeric_string_limit(tmp_path):
    project = _project(tmp_path)
    MutableRepository(project).create_task(title="Example")

    response = _call_tool(project.root, "task_search", query="Example", limit="10")

    assert "error" not in response, response.get("error")
    rows = json.loads(response["result"]["content"][0]["text"])
    assert [row["title"] for row in rows] == ["Example"]


def test_task_search_rejects_non_numeric_limit(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "task_search", query="Example", limit="ten")

    assert response["error"]["code"] == -32602
    assert "limit" in response["error"]["message"]


def test_internal_type_error_is_not_reported_as_invalid_params(tmp_path, monkeypatch):
    project = _project(tmp_path)

    def boom(_project, **_kwargs):
        raise TypeError("'<=' not supported between instances of 'str' and 'int'")

    monkeypatch.setattr(protocol_module, "tool_by_name", lambda _name: _stub_tool(boom))

    response = _call_tool(project.root, "task_board")

    assert response["error"]["code"] == -32603


# --- F6: schema/handler disagreement on additionalProperties ----------------

def _handler_accepts_extra_keys(handler) -> bool:
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(handler).parameters.values()
    )


def test_tool_schemas_are_strict_exactly_where_handlers_are_strict():
    schemas = {tool["name"]: tool["inputSchema"] for tool in list_tools()}

    mismatched = {
        tool.name
        for tool in TOOL_DEFINITIONS
        if schemas[tool.name]["additionalProperties"] is not _handler_accepts_extra_keys(tool.handler)
    }

    assert mismatched == set()


def test_unknown_argument_for_strict_tool_is_invalid_params(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "milestone_add", name="Alpha", bogus="x")

    assert response["error"]["code"] == -32602


# --- F7: misc consistency ---------------------------------------------------

def test_resources_read_unknown_uri_returns_resource_not_found():
    response = handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": "resource", "method": "resources/read", "params": {"uri": "backlog://nope"}}
    )

    assert response["error"]["code"] == -32002
    assert "backlog://nope" in response["error"]["message"], "path redaction mangled the resource URI"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Separators in prose and markup are not paths; they must survive intact.
        ("Unknown status. Valid: To Do / In Progress", "Unknown status. Valid: To Do / In Progress"),
        ("unexpected </div> in the body", "unexpected </div> in the body"),
        # Absolute paths must be masked *entirely*, whatever characters the
        # leading component happens to use. A partial mask still leaks layout.
        ("failed at /version~1", "failed at <path>"),
        ("failed at /tmp~user/x", "failed at <path>"),
        ("failed at /home/robert/proj/backlog/tasks/task-1.md", "failed at <path>"),
        ("failed at C:\\Users\\robert\\proj", "failed at <path>"),
        # Relative paths and URIs are meaningful to the caller and stay readable.
        ("backlog/tasks/task-1.md is invalid", "backlog/tasks/task-1.md is invalid"),
        ("backlog://init-required", "backlog://init-required"),
    ],
)
def test_redact_paths_masks_whole_absolute_paths_without_mangling_prose(message, expected):
    assert protocol_module._redact_paths(message) == expected


def test_invalid_params_message_names_the_tool(tmp_path):
    """inspect.Signature.bind does not know the callable's name; we must add it."""
    project = _project(tmp_path)

    response = _call_tool(project.root, "task_view", task_id="TASK-1", bogus="x")

    assert response["error"]["code"] == -32602
    message = response["error"]["message"]
    assert "task_view" in message, message
    assert "bogus" in message, message


def test_task_edit_reloads_project_config_like_task_create(tmp_path):
    project = _project(tmp_path)
    MutableRepository(project).create_task(title="Example")
    project.config_path.write_text(
        project.config_path.read_text(encoding="utf-8").replace("- Done\n", "- Done\n- Blocked\n"),
        encoding="utf-8",
    )

    detail = tool_registry.task_edit(project, "TASK-1", status="Blocked")

    assert detail["status"] == "Blocked"


def test_orchestration_validation_error_is_reported_as_a_conflict(tmp_path, monkeypatch):
    project = _project(tmp_path)
    MutableRepository(project).create_task(title="Example")

    def raise_validation(*_args, **_kwargs):
        raise OrchestrationValidationError("Task orchestration metadata is invalid", details={"field": "result"})

    monkeypatch.setattr(OrchestrationService, "record_run", raise_validation)

    payload = tool_registry.orchestration_record_run(project, task_id="TASK-1", actor="codex", result="succeeded")

    assert payload["conflict"]["type"] == "OrchestrationValidationError"


def test_a_write_is_visible_to_the_next_read_through_the_scan_cache(tmp_path):
    """The MCP server is long-lived, so a cached scan must never outlive a write.

    Tool calls each re-read every task file -- 1.5s per call on a 2310-task
    project -- so reads now share one scan. That is only safe if a mutation
    through the same server is visible to the very next read.
    """
    project = _project(tmp_path)

    _call_tool(tmp_path, "task_create", title="First task")
    before = _call_tool(tmp_path, "task_list")
    _call_tool(tmp_path, "task_create", title="Second task")
    after = _call_tool(tmp_path, "task_list")

    titles_before = _result_text(before)
    titles_after = _result_text(after)
    assert "Second task" not in titles_before
    assert "Second task" in titles_after, "a cached scan hid a task this server just created"

    task_id = json.loads(titles_after)[0]["id"]
    _call_tool(tmp_path, "task_edit", task_id=task_id, title="Renamed by the same server")
    assert "Renamed by the same server" in _result_text(_call_tool(tmp_path, "task_list")), (
        "a cached scan hid an edit this server just made"
    )
    assert project.root == tmp_path


def test_an_edit_made_outside_the_server_is_picked_up(tmp_path):
    """Files change under the server too -- an editor, another agent, a merge."""
    _project(tmp_path)
    _call_tool(tmp_path, "task_create", title="Original title")

    task_file = next((tmp_path / "backlog" / "tasks").glob("*.md"))
    task_file.write_text(
        task_file.read_text(encoding="utf-8").replace("Original title", "Changed on disk"),
        encoding="utf-8",
    )

    assert "Changed on disk" in _result_text(_call_tool(tmp_path, "task_list"))
