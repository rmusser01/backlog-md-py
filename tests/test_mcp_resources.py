import shutil
from pathlib import Path

import pytest

import backlog_py.mcp as mcp
from backlog_py.mcp import server as mcp_server
from backlog_py.mcp.resources import read_resource
from backlog_py.mcp.tools import task_edit, task_search, task_view
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _project():
    return discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)


def test_workflow_overview_resource_returns_task_workflow_guidance():
    content = read_resource("backlog://workflow/overview")

    assert "Backlog.md" in content
    assert "task" in content.casefold()
    assert "safe mutation" in content.casefold()
    assert "document_create" in content
    assert "milestone_add" in content
    assert "definition_of_done_defaults_get" in content


def test_task_workflow_resource_alias_matches_overview():
    overview = read_resource("backlog://workflow/overview")
    alias = read_resource("backlog://docs/task-workflow")

    assert alias == overview


def test_unknown_resource_uri_raises_clear_error():
    with pytest.raises(KeyError, match="Unsupported Backlog MCP resource"):
        read_resource("backlog://unknown")


def test_task_search_returns_fixture_backed_readonly_dicts():
    results = task_search(_project(), "parser preservation")

    assert results == [
        {
            "id": "TASK-1",
            "title": "Example task",
            "status": "In Progress",
            "description": (
                "Implement a fixture that exercises parser preservation behavior.\n"
                "This paragraph must remain untouched by a no-op render."
            ),
            "path": "backlog/tasks/task-1 - Example-task.md",
        }
    ]


def test_task_search_honors_limit():
    assert task_search(_project(), "", limit=0) == []


def test_task_view_returns_fixture_backed_readonly_dict():
    result = task_view(_project(), "task-1")

    assert result["id"] == "TASK-1"
    assert result["title"] == "Example task"
    assert result["status"] == "In Progress"
    assert "Implement a fixture" in result["description"]
    assert result["path"] == "backlog/tasks/task-1 - Example-task.md"
    assert "Trailing unowned body content" in result["raw_source"]


def test_fastmcp_task_edit_passes_title_to_safe_core(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    fake_server = mcp_server.create_server(fastmcp_cls=FakeFastMCP)

    result = fake_server.tools["task_edit"](project=str(repo), task_id="TASK-1", title="FastMCP renamed task")

    assert result["title"] == "FastMCP renamed task"
    assert (repo / "backlog" / "tasks" / "task-1 - FastMCP-renamed-task.md").is_file()


def test_create_server_registers_resources_and_tools_with_fastmcp_adapter():
    fake_server = mcp_server.create_server(fastmcp_cls=FakeFastMCP)

    assert fake_server.name == "backlog-md-py"
    assert fake_server.resources["backlog://workflow/overview"]() == read_resource("backlog://workflow/overview")
    assert fake_server.resources["backlog://docs/task-workflow"]() == read_resource("backlog://docs/task-workflow")
    assert "task_search" in fake_server.tools
    assert "definition_of_done_defaults_upsert" in fake_server.tools

    result = fake_server.tools["task_search"](project=str(FIXTURE_REPO), query="parser preservation")

    assert result[0]["id"] == "TASK-1"


def test_create_server_reports_missing_sdk_when_no_adapter_is_provided(monkeypatch):
    def missing_fastmcp():
        raise RuntimeError("MCP SDK is not installed. Install backlog-md-py[mcp] to run the MCP server.")

    monkeypatch.setattr(mcp_server, "_load_fastmcp", missing_fastmcp)

    with pytest.raises(RuntimeError, match=r"Install backlog-md-py\[mcp\]"):
        mcp_server.create_server()


def test_mcp_package_exports_document_milestone_and_dod_tools():
    assert mcp.document_create.__name__ == "document_create"
    assert mcp.milestone_add.__name__ == "milestone_add"
    assert mcp.definition_of_done_defaults_get.__name__ == "definition_of_done_defaults_get"


class FakeFastMCP:
    def __init__(self, name: str):
        self.name = name
        self.resources = {}
        self.tools = {}
        self.ran = False

    def resource(self, uri: str):
        def decorator(function):
            self.resources[uri] = function
            return function

        return decorator

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator

    def run(self) -> None:
        self.ran = True
