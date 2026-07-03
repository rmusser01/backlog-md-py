"""Follow-up: not-found signals use NotFoundError so a genuine KeyError bug
inside a handler is not masked as a clean 'not found' result."""
from __future__ import annotations

from pathlib import Path

import pytest

from backlog_py.core.errors import NotFoundError
from backlog_py.core.init import init_project
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp.protocol import handle_jsonrpc_message


def _project(tmp_path: Path):
    return init_project(tmp_path, no_git=True).project


def _call_tool(project_root: Path, name: str, **arguments):
    arguments["project"] = str(project_root)
    return handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": "call", "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
    )


def test_notfounderror_is_keyerror_subclass():
    assert issubclass(NotFoundError, KeyError)


def test_get_task_missing_raises_notfounderror(tmp_path):
    project = _project(tmp_path)
    repo = MutableRepository(project)
    with pytest.raises(NotFoundError):
        repo.get_task("TASK-404")


def test_internal_keyerror_is_not_masked_as_tool_error(tmp_path, monkeypatch):
    project = _project(tmp_path)

    class _FakeTool:
        def handler(self, project, **kwargs):
            raise KeyError("internal dict bug")

    monkeypatch.setattr("backlog_py.mcp.protocol.tool_by_name", lambda name: _FakeTool())

    response = _call_tool(project.root, "whatever")

    # A genuine KeyError bug must surface as a protocol-level internal error,
    # not a clean isError "not found" tool result.
    assert "error" in response, response
    assert response["error"]["code"] == -32603


def test_missing_task_still_returns_tool_error(tmp_path):
    project = _project(tmp_path)
    MutableRepository(project).create_task(title="T")
    response = _call_tool(project.root, "task_view", task_id="TASK-404")
    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is True
