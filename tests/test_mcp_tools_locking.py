import shutil
from pathlib import Path

import pytest

from backlog_py.core.documents import DocumentService
from backlog_py.core.milestones import MilestoneService
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp.protocol import handle_jsonrpc_message
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, target)
    return target


@pytest.mark.parametrize(
    ("prepare", "tool_name", "arguments", "operation"),
    [
        (lambda repo: None, "task_create", {"project": "{repo}", "title": "MCP task"}, "mcp_task_create"),
        (
            lambda repo: None,
            "task_edit",
            {"project": "{repo}", "task_id": "TASK-1", "title": "Updated"},
            "mcp_task_edit",
        ),
        (
            lambda repo: None,
            "task_archive",
            {"project": "{repo}", "task_id": "TASK-1"},
            "mcp_task_archive",
        ),
        (
            lambda repo: MutableRepository.from_path(repo).edit_task("TASK-1", status="Done"),
            "task_complete",
            {"project": "{repo}", "task_id": "TASK-1"},
            "mcp_task_complete",
        ),
        (
            lambda repo: None,
            "document_create",
            {"project": "{repo}", "path": "notes/a.md", "title": "A", "content": ""},
            "mcp_document_create",
        ),
        (
            lambda repo: DocumentService(_project(repo)).create_document("notes/a.md", title="A", content=""),
            "document_update",
            {"project": "{repo}", "path_or_id": "notes/a.md", "title": "Updated"},
            "mcp_document_update",
        ),
        (
            lambda repo: None,
            "milestone_add",
            {"project": "{repo}", "name": "Alpha"},
            "mcp_milestone_add",
        ),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            "milestone_rename",
            {"project": "{repo}", "old_name": "Alpha", "new_name": "Beta"},
            "mcp_milestone_rename",
        ),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            "milestone_remove",
            {"project": "{repo}", "name": "Alpha"},
            "mcp_milestone_remove",
        ),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            "milestone_archive",
            {"project": "{repo}", "name": "Alpha"},
            "mcp_milestone_archive",
        ),
        (
            lambda repo: None,
            "definition_of_done_defaults_upsert",
            {"project": "{repo}", "items": ["Tests"]},
            "mcp_definition_of_done_defaults_upsert",
        ),
    ],
)
def test_mcp_write_tools_acquire_project_lock(repo, monkeypatch, prepare, tool_name, arguments, operation):
    prepare(repo)
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.mcp.tools.with_project_write_lock", fake_with_project_lock)
    resolved_arguments = {key: (str(repo) if value == "{repo}" else value) for key, value in arguments.items()}

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": resolved_arguments},
        }
    )

    assert "result" in response, response
    assert operation in seen


def test_mcp_read_tools_do_not_acquire_project_lock(repo, monkeypatch):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.mcp.tools.with_project_write_lock", fake_with_project_lock)

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "task_view", "arguments": {"project": str(repo), "task_id": "TASK-1"}},
        }
    )

    assert "result" in response, response
    assert seen == []


def test_mcp_orchestration_record_run_uses_service_lock_not_tool_wrapper(repo, monkeypatch):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.mcp.tools.with_project_write_lock", fake_with_project_lock)

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "orchestration_record_run",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "actor": "codex",
                    "result": "succeeded",
                    "summary": "recorded",
                },
            },
        }
    )

    assert "result" in response, response
    assert seen == []


def test_mcp_orchestration_workflow_mutations_use_service_lock_not_tool_wrapper(repo, monkeypatch):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.mcp.tools.with_project_write_lock", fake_with_project_lock)

    claim = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "orchestration_claim_task",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "actor": "codex",
                    "expectedVersion": 0,
                },
            },
        }
    )
    release = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "orchestration_release_task",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "actor": "codex",
                    "expectedVersion": 1,
                },
            },
        }
    )
    transition = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "orchestration_transition_task",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "toStatus": "review",
                    "actor": "codex",
                    "expectedVersion": 2,
                },
            },
        }
    )

    assert "result" in claim, claim
    assert "result" in release, release
    assert "result" in transition, transition
    assert seen == []


@pytest.mark.parametrize("field_name", ["acceptanceCriteria", "acceptance_criteria"])
def test_mcp_task_edit_acceptance_criteria_alias_adds_items(repo, field_name):
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "task_edit",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    field_name: ["Helper-populated criterion"],
                },
            },
        }
    )

    assert "result" in response, response
    task_source = (repo / "backlog" / "tasks" / "task-1 - Example-task.md").read_text(encoding="utf-8")
    assert "- [ ] #4 Helper-populated criterion" in task_source


def test_mcp_task_edit_acceptance_criteria_set_replaces_items(repo):
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "task_edit",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "acceptanceCriteriaSet": ["Replacement criterion"],
                },
            },
        }
    )

    assert "result" in response, response
    task_source = (repo / "backlog" / "tasks" / "task-1 - Example-task.md").read_text(encoding="utf-8")
    assert "- [ ] #1 Replacement criterion" in task_source
    assert "Preserve completed acceptance criteria raw line" not in task_source


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)
