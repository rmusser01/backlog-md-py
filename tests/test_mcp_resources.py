import shutil
from pathlib import Path

import pytest

import backlog_py.mcp as mcp
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp import server as mcp_server
from backlog_py.mcp import tools as mcp_tools
from backlog_py.mcp.resources import read_resource
from backlog_py.mcp.tools import task_board, task_edit, task_search, task_view
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
    assert "task_create(project, ordinal=None" in content
    assert "task_edit(project, task_id, ordinal=None" in content


def test_task_list_returns_fixture_backed_readonly_dicts():
    assert hasattr(mcp_tools, "task_list")

    results = mcp_tools.task_list(_project())

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


def test_task_list_honors_status_and_limit():
    assert hasattr(mcp_tools, "task_list")

    assert mcp_tools.task_list(_project(), status="To Do") == []
    assert mcp_tools.task_list(_project(), limit=0) == []


def test_task_list_honors_frontmatter_metadata_filters(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.edit_task(
        "TASK-1",
        assignees=["Codex"],
        labels=["Parser", "UI"],
        priority="high",
        milestone="Release 1",
    )
    repository.create_task(
        title="Documentation task",
        task_id="TASK-2",
        status="To Do",
        assignees=["reviewer"],
        labels=["docs"],
        priority="low",
        milestone="Release 2",
    )

    assert [task["id"] for task in mcp_tools.task_list(project, status="in progress")] == ["TASK-1"]
    assert [task["id"] for task in mcp_tools.task_list(project, assignee="codex")] == ["TASK-1"]
    assert [task["id"] for task in mcp_tools.task_list(project, labels=["parser", "ui"])] == ["TASK-1"]
    assert [task["id"] for task in mcp_tools.task_list(project, priority="HIGH")] == ["TASK-1"]
    assert [task["id"] for task in mcp_tools.task_list(project, milestone="release 1")] == ["TASK-1"]


def test_task_list_and_create_honor_parent_task_id(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    child = mcp_tools.task_create(project, title="MCP child task", parentTaskId="1")
    MutableRepository(project).create_task(title="MCP sibling task", task_id="TASK-2")

    assert child["id"] == "TASK-1.1"
    assert child["parentTaskId"] == "TASK-1"
    assert [task["id"] for task in mcp_tools.task_list(project, parentTaskId="TASK-1")] == ["TASK-1.1"]


def test_task_list_search_honors_parent_task_id(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    mcp_tools.task_create(project, title="Parented searchable task", parentTaskId="1")
    MutableRepository(project).create_task(title="Unparented searchable task", task_id="TASK-2")

    result = mcp_tools.task_list(project, search="searchable", parentTaskId="1")

    assert [task["id"] for task in result] == ["TASK-1.1"]


def test_task_list_honors_search_filter(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).create_task(
        title="Searchable MCP task",
        task_id="TASK-2",
        status="To Do",
        description="Needle content",
    )

    results = mcp_tools.task_list(project, search="needle")

    assert [task["id"] for task in results] == ["TASK-2"]


def test_task_board_returns_status_grouped_fixture_rows():
    assert hasattr(mcp_tools, "task_board")

    result = task_board(_project())

    assert list(result) == ["To Do", "In Progress", "Done"]
    assert result["To Do"] == []
    assert result["Done"] == []
    assert result["In Progress"] == [
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


def test_task_search_honors_status_priority_and_modified_file_filters(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.edit_task(
        "TASK-1",
        priority="high",
        modified_files=["src/components/Button.tsx"],
    )
    repository.create_task(
        title="Server task",
        task_id="TASK-2",
        status="To Do",
        priority="low",
        modified_files=["src/server/index.py"],
    )

    assert [task["id"] for task in task_search(project, "task", status="to do")] == ["TASK-2"]
    assert [task["id"] for task in task_search(project, "task", priority="HIGH")] == ["TASK-1"]
    assert [task["id"] for task in task_search(project, modified_files=["components/button"])] == ["TASK-1"]
    assert [task["id"] for task in task_search(project, modifiedFiles=["SERVER"])] == ["TASK-2"]


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


def test_task_archive_moves_task_to_archive_through_mcp_tool(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    result = mcp_tools.task_archive(project, "TASK-1")

    assert result["id"] == "TASK-1"
    assert result["path"] == "backlog/archive/tasks/task-1 - Example-task.md"
    assert not (repo / "backlog" / "tasks" / "task-1 - Example-task.md").exists()
    assert (repo / "backlog" / "archive" / "tasks" / "task-1 - Example-task.md").is_file()


def test_task_complete_moves_done_task_to_completed_through_mcp_tool(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).edit_task("TASK-1", status="Done")

    result = mcp_tools.task_complete(project, "TASK-1")

    assert result["id"] == "TASK-1"
    assert result["status"] == "Done"
    assert result["path"] == "backlog/completed/task-1 - Example-task.md"
    assert not (repo / "backlog" / "tasks" / "task-1 - Example-task.md").exists()
    assert (repo / "backlog" / "completed" / "task-1 - Example-task.md").is_file()
    assert [task["id"] for task in task_search(project, "fixture")] == ["TASK-1"]


def test_create_server_registers_resources_and_tools_with_fastmcp_adapter():
    fake_server = mcp_server.create_server(fastmcp_cls=FakeFastMCP)

    assert fake_server.name == "backlog-md-py"
    assert fake_server.resources["backlog://workflow/overview"]() == read_resource("backlog://workflow/overview")
    assert fake_server.resources["backlog://docs/task-workflow"]() == read_resource("backlog://docs/task-workflow")
    assert "task_board" in fake_server.tools
    assert "task_archive" in fake_server.tools
    assert "task_complete" in fake_server.tools
    assert "task_list" in fake_server.tools
    assert "task_search" in fake_server.tools
    assert "definition_of_done_defaults_upsert" in fake_server.tools

    board = fake_server.tools["task_board"](project=str(FIXTURE_REPO))
    assert board["In Progress"][0]["id"] == "TASK-1"

    listed = fake_server.tools["task_list"](project=str(FIXTURE_REPO), status="In Progress")
    assert listed[0]["id"] == "TASK-1"

    result = fake_server.tools["task_search"](project=str(FIXTURE_REPO), query="parser preservation")

    assert result[0]["id"] == "TASK-1"


def test_create_server_reports_missing_sdk_when_no_adapter_is_provided(monkeypatch):
    def missing_fastmcp():
        raise RuntimeError("MCP SDK is not installed. Install backlog-md-py[mcp] to run the MCP server.")

    monkeypatch.setattr(mcp_server, "_load_fastmcp", missing_fastmcp)

    with pytest.raises(RuntimeError, match=r"Install backlog-md-py\[mcp\]"):
        mcp_server.create_server()


def test_mcp_package_exports_document_milestone_and_dod_tools():
    assert mcp.task_archive.__name__ == "task_archive"
    assert mcp.task_complete.__name__ == "task_complete"
    assert mcp.task_board.__name__ == "task_board"
    assert mcp.task_list.__name__ == "task_list"
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
