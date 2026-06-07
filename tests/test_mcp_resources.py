import json
import shutil
from pathlib import Path

import pytest

import backlog_py.mcp as mcp
from backlog_py.core.repository import MutableRepository
from backlog_py.runtime.locks import ProjectWriteLock
from backlog_py.mcp import server as mcp_server
from backlog_py.mcp import tools as mcp_tools
from backlog_py.mcp.protocol import handle_jsonrpc_message
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
    assert "project_status(project, recentLimit=5)" in content
    assert "task_create(project, id=None, ordinal=None" in content
    assert "task_edit(project, task_id, ordinal=None" in content
    assert "backlog://init-required" in content


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


def test_project_status_reports_counts_recent_activity_and_project_locks(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    repository = MutableRepository(project)
    repository.create_task(title="New coordination task", task_id="TASK-2", status="To Do")
    repository.edit_task("TASK-2", title="Recently updated coordination task")

    with ProjectWriteLock(project.root, operation="task_edit").acquire(timeout=0.1):
        status = mcp_tools.project_status(project)

    assert status["projectRoot"] == str(repo.resolve())
    assert status["backlogDir"] == str((repo / "backlog").resolve())
    assert status["configPath"] == str((repo / "backlog" / "config.yml").resolve())
    assert status["taskCounts"] == {
        "active": 2,
        "completed": 0,
        "total": 2,
        "byStatus": {"In Progress": 1, "To Do": 1},
    }
    assert status["recentActivity"][0]["id"] == "TASK-2"
    assert status["recentActivity"][0]["timestampField"] == "updated_date"
    assert status["recentActivity"][1]["id"] == "TASK-1"
    assert status["recentActivity"][1]["timestamp"] == "2026-05-10 10:00"
    assert status["locks"][0]["active"] is True
    assert status["locks"][0]["operation"] == "task_edit"
    assert status["locks"][0]["project_root"] == str(repo.resolve())


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


def test_task_workflow_resource_contains_lifecycle_guidance():
    content = read_resource("backlog://docs/task-workflow")

    assert "# Backlog.md Task Workflow" in content
    assert "Search before creating" in content
    assert "Task creation" in content
    assert "Task execution" in content
    assert "Task finalization" in content


def test_init_required_resource_explains_setup_path():
    content = read_resource("backlog://init-required")

    assert "# Backlog.md Project Initialization Required" in content
    assert "No Backlog.md config" in content
    assert "backlog-py --cwd" in content
    assert "init" in content


def test_task_workflow_resource_is_distinct_from_overview():
    overview = read_resource("backlog://workflow/overview")
    workflow = read_resource("backlog://docs/task-workflow")

    assert workflow != overview


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


def test_sdk_free_tools_call_task_edit_passes_title_to_safe_core(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "task_edit",
                "arguments": {"project": str(repo), "task_id": "TASK-1", "title": "SDK-free renamed task"},
            },
        }
    )

    result = json.loads(response["result"]["content"][0]["text"])
    assert result["title"] == "SDK-free renamed task"
    assert (repo / "backlog" / "tasks" / "task-1 - SDK-free-renamed-task.md").is_file()


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


def test_create_server_returns_sdk_free_stdio_facade_with_ignored_legacy_args():
    server = mcp_server.create_server(object(), legacy_adapter=object())

    assert server.name == "backlog-md-py"
    assert mcp_server.is_mcp_sdk_available() is False


def test_mcp_package_exports_document_milestone_and_dod_tools():
    assert mcp.project_status.__name__ == "project_status"
    assert mcp.task_archive.__name__ == "task_archive"
    assert mcp.task_complete.__name__ == "task_complete"
    assert mcp.task_board.__name__ == "task_board"
    assert mcp.task_list.__name__ == "task_list"
    assert mcp.document_create.__name__ == "document_create"
    assert mcp.milestone_add.__name__ == "milestone_add"
    assert mcp.definition_of_done_defaults_get.__name__ == "definition_of_done_defaults_get"
