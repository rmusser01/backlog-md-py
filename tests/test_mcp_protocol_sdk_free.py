import ast
import json
import shutil
from pathlib import Path

import pytest

from backlog_py.mcp.catalog import list_tools
from backlog_py.mcp.protocol import (
    McpRequestContext,
    handle_jsonrpc_message,
    handle_jsonrpc_text,
)
from backlog_py.orchestration import parse_run_history


def _tool_properties(name):
    return _tool_schema(name)["properties"]


def _tool_schema(name):
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    tool = next(tool for tool in response["result"]["tools"] if tool["name"] == name)
    return tool["inputSchema"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: mcp-orchestration-test\n", encoding="utf-8")
    _task_path(repo).write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Example\n"
        "status: To Do\n"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    return repo


def _task_path(repo: Path) -> Path:
    return repo / "backlog" / "tasks" / "task-1 - Example.md"


def _call_tool(name: str, arguments: dict[str, object]):
    return handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def _tool_payload(response: dict[str, object]) -> dict[str, object]:
    return json.loads(response["result"]["content"][0]["text"])


def _assert_orchestration_contract(payload: dict[str, object], *, version: int, category: str) -> None:
    assert payload["taskId"] == "TASK-1"
    assert payload["path"] == "backlog/tasks/task-1 - Example.md"
    assert payload["version"] == version
    assert payload["eventId"].startswith("run-")
    assert payload["runHistoryEventIds"][-1] == payload["eventId"]
    assert payload["queueCategory"] == category
    assert payload["validationIssues"] == []


def _handler_accepted_arguments():
    source = Path("src/backlog_py/mcp/tools.py").read_text()
    module = ast.parse(source)
    tool_names = {tool["name"] for tool in list_tools()}

    class AcceptedArgumentVisitor(ast.NodeVisitor):
        def __init__(self):
            self.owner_stack = []
            self.names_by_tool = {name: set() for name in tool_names}

        @property
        def owner(self):
            return self.owner_stack[-1] if self.owner_stack else None

        def visit_FunctionDef(self, node):
            owner = node.name if node.name in tool_names else self.owner
            self.owner_stack.append(owner)
            if node.name in tool_names:
                for arg in [*node.args.args, *node.args.kwonlyargs]:
                    if arg.arg != "project":
                        self.names_by_tool[node.name].add(arg.arg)
            self.generic_visit(node)
            self.owner_stack.pop()

        def visit_Call(self, node):
            owner = self.owner
            if owner:
                if isinstance(node.func, ast.Name) and node.func.id in {
                    "_get_alias",
                    "_combined_optional_string_list",
                }:
                    for arg in node.args[1:]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.names_by_tool[owner].add(arg.value)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "kwargs"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    self.names_by_tool[owner].add(node.args[0].value)
            self.generic_visit(node)

    visitor = AcceptedArgumentVisitor()
    visitor.visit(module)
    return visitor.names_by_tool


def test_initialize_returns_server_capabilities():
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test"}},
        }
    )

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "backlog-md-py"
    assert response["result"]["capabilities"]["tools"] == {}
    assert response["result"]["capabilities"]["resources"] == {}


def test_ping_returns_empty_result():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "ping", "method": "ping"})

    assert response == {"jsonrpc": "2.0", "id": "ping", "result": {}}


def test_tools_list_contains_existing_task_search_tool():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})

    tools = response["result"]["tools"]
    names = {tool["name"] for tool in tools}
    project_status = next(tool for tool in tools if tool["name"] == "project_status")
    task_search = next(tool for tool in tools if tool["name"] == "task_search")
    task_view = next(tool for tool in tools if tool["name"] == "task_view")
    task_create = next(tool for tool in tools if tool["name"] == "task_create")
    assert "project_status" in names
    assert "task_search" in names
    assert "task_create" in names
    assert "project" in project_status["inputSchema"]["properties"]
    assert "project" not in project_status["inputSchema"]["required"]
    assert task_search["inputSchema"]["type"] == "object"
    assert "project" not in task_search["inputSchema"]["required"]
    assert task_view["inputSchema"]["required"] == ["task_id"]
    assert "id" in task_create["inputSchema"]["properties"]
    assert "status" in task_create["inputSchema"]["properties"]
    assert "parentTaskId" in task_create["inputSchema"]["properties"]
    assert "milestone" in task_create["inputSchema"]["properties"]
    assert "ordinal" in task_create["inputSchema"]["properties"]
    assert "references" in task_create["inputSchema"]["properties"]
    assert "documentation" in task_create["inputSchema"]["properties"]
    assert "modifiedFiles" in task_create["inputSchema"]["properties"]
    assert "implementationPlan" in task_create["inputSchema"]["properties"]
    assert "finalSummary" in task_create["inputSchema"]["properties"]


def test_tools_list_advertises_task_edit_acceptance_criteria_fields():
    properties = _tool_properties("task_edit")
    assert "acceptanceCriteria" in properties
    assert "acceptanceCriteriaAdd" in properties
    assert "acceptanceCriteriaSet" in properties
    assert "clearPriority" in properties
    assert "clearMilestone" in properties
    assert "ordinal" in properties
    assert "milestone" in properties
    assert "references" in properties
    assert "addReferences" in properties
    assert "documentation" in properties
    assert "addDocumentation" in properties


def test_tools_list_advertises_task_read_filter_fields():
    task_list = _tool_properties("task_list")
    task_search = _tool_properties("task_search")

    assert {
        "status",
        "limit",
        "assignee",
        "labels",
        "priority",
        "milestone",
        "parentTaskId",
        "search",
    }.issubset(task_list)
    assert {"query", "limit", "status", "priority", "modified_files", "modifiedFiles"}.issubset(task_search)


def test_tools_list_advertises_task_mutation_metadata_fields():
    task_create = _tool_properties("task_create")
    task_edit = _tool_properties("task_edit")

    assert {
        "description",
        "notes",
        "acceptanceCriteria",
        "definitionOfDone",
        "definitionOfDoneAdd",
        "disableDefinitionOfDoneDefaults",
        "dependencies",
        "assignee",
        "labels",
        "priority",
        "onStatusChange",
    }.issubset(task_create)
    assert {
        "title",
        "description",
        "implementationPlan",
        "planAppend",
        "planClear",
        "notes",
        "appendNotes",
        "definitionOfDoneAdd",
        "finalSummary",
        "finalSummaryAppend",
        "finalSummaryClear",
        "checkAc",
        "checkDod",
        "uncheckAc",
        "uncheckDod",
        "acceptanceCriteriaRemove",
        "definitionOfDoneRemove",
        "dependencies",
        "assignee",
        "labels",
        "priority",
        "status",
        "onStatusChange",
        "removeReferences",
        "removeDocumentation",
        "modifiedFiles",
    }.issubset(task_edit)


def test_tools_list_advertises_document_and_milestone_optional_fields():
    assert {"query", "limit"}.issubset(_tool_properties("document_list"))
    assert "metadata" in _tool_properties("document_create")
    assert {"title", "content"}.issubset(_tool_properties("document_update"))
    assert "description" in _tool_properties("milestone_add")
    assert "update_tasks" in _tool_properties("milestone_rename")
    assert "clear_tasks" in _tool_properties("milestone_remove")


def test_tools_list_advertises_orchestration_record_run_fields():
    schema = _tool_schema("orchestration_record_run")
    properties = schema["properties"]

    assert {
        "task_id",
        "taskId",
        "actor",
        "result",
        "summary",
        "files",
        "verification",
        "idempotencyKey",
        "idempotency_key",
        "expectedVersion",
        "expected_version",
        "stateUpdate",
        "state_update",
    }.issubset(properties)
    assert "result" in schema["required"]
    assert "task_id" not in schema["required"]
    required_sets = [set(item.get("required", [])) for item in schema["anyOf"]]
    assert {"task_id"} in required_sets
    assert {"taskId"} in required_sets
    conditional_contract = json.dumps(schema["allOf"], sort_keys=True)
    assert "stateUpdate" in conditional_contract
    assert "state_update" in conditional_contract
    assert "expectedVersion" in conditional_contract
    assert "expected_version" in conditional_contract


def test_tools_list_advertises_orchestration_workflow_tools():
    tools = {tool["name"]: tool for tool in list_tools()}

    expected_names = {
        "orchestration_status",
        "orchestration_queue",
        "orchestration_eligible",
        "orchestration_claims",
        "orchestration_stale_leases",
        "orchestration_claim_task",
        "orchestration_release_task",
        "orchestration_transition_task",
        "orchestration_split_task",
    }
    assert expected_names.issubset(tools)
    for name in expected_names:
        assert "project" in tools[name]["inputSchema"]["properties"]
    claim_properties = tools["orchestration_claim_task"]["inputSchema"]["properties"]
    release_properties = tools["orchestration_release_task"]["inputSchema"]["properties"]
    transition_properties = tools["orchestration_transition_task"]["inputSchema"]["properties"]
    assert {"task_id", "taskId", "actor", "expectedVersion", "expected_version", "idempotencyKey"}.issubset(
        claim_properties
    )
    assert {"task_id", "taskId", "actor", "expectedVersion", "expected_version", "reason"}.issubset(
        release_properties
    )
    assert {"task_id", "taskId", "toStatus", "to_status", "actor", "expectedVersion", "expected_version"}.issubset(
        transition_properties
    )
    split_properties = tools["orchestration_split_task"]["inputSchema"]["properties"]
    assert {
        "task_id",
        "taskId",
        "mode",
        "items",
        "actor",
        "expectedVersion",
        "expected_version",
        "idempotencyKey",
        "idempotency_key",
        "inheritDependencies",
        "inherit_dependencies",
        "linkSequence",
        "link_sequence",
        "transitionToStatus",
        "transition_to_status",
        "reason",
    }.issubset(split_properties)


def test_tools_list_advertises_all_handler_accepted_argument_names():
    properties_by_tool = {tool["name"]: set(tool["inputSchema"]["properties"]) for tool in list_tools()}

    missing = {
        name: sorted(accepted_names - properties_by_tool[name])
        for name, accepted_names in _handler_accepted_arguments().items()
        if accepted_names - properties_by_tool[name]
    }

    assert missing == {}


def test_resources_list_contains_workflow_resources():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "resources", "method": "resources/list"})

    resources = response["result"]["resources"]
    uris = {resource["uri"] for resource in resources}
    assert "backlog://workflow/overview" in uris
    assert "backlog://docs/task-workflow" in uris
    assert "backlog://init-required" in uris


def test_resources_read_returns_text_content():
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "resource",
            "method": "resources/read",
            "params": {"uri": "backlog://workflow/overview"},
        }
    )

    contents = response["result"]["contents"]
    assert contents[0]["uri"] == "backlog://workflow/overview"
    assert contents[0]["mimeType"] == "text/markdown"
    assert "Backlog.md" in contents[0]["text"]


def test_tools_call_uses_project_hint_when_project_argument_is_omitted():
    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": "task_view", "arguments": {"task_id": "TASK-1"}},
        },
        context=McpRequestContext(project_hint="tests/fixtures/repos/basic"),
    )

    content = response["result"]["content"]
    assert response["result"]["isError"] is False
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"])["id"] == "TASK-1"


def test_tools_call_explicit_project_overrides_project_hint(tmp_path):
    hinted_repo = tmp_path / "hinted"
    explicit_repo = tmp_path / "explicit"

    shutil.copytree("tests/fixtures/repos/basic", hinted_repo)
    shutil.copytree("tests/fixtures/repos/basic", explicit_repo)
    task_path = explicit_repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("title: Example task", "title: Explicit project task"),
        encoding="utf-8",
    )

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {
                "name": "task_view",
                "arguments": {"project": str(explicit_repo), "task_id": "TASK-1"},
            },
        },
        context=McpRequestContext(project_hint=str(hinted_repo)),
    )

    content = response["result"]["content"]
    assert json.loads(content[0]["text"])["title"] == "Explicit project task"


def test_tools_call_orchestration_record_run_appends_history(tmp_path):
    repo = _repo(tmp_path)

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {
                "name": "orchestration_record_run",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "actor": "codex",
                    "result": "succeeded",
                    "summary": "done",
                    "files": ["src/backlog_py/mcp/tools.py"],
                    "verification": ["pytest tests/test_mcp_protocol_sdk_free.py"],
                    "expectedVersion": 0,
                    "stateUpdate": {"statusKey": "inprogress", "reviewState": "pending"},
                },
            },
        }
    )

    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["taskId"] == "TASK-1"
    assert payload["path"] == "backlog/tasks/task-1 - Example.md"
    assert payload["version"] == 1
    assert payload["eventId"].startswith("run-")
    assert payload["runHistoryEventIds"] == [payload["eventId"]]
    assert payload["queueCategory"] == "in_workflow"
    assert payload["validationIssues"] == []

    parsed = parse_run_history(_task_path(repo).read_text(encoding="utf-8"))
    assert [event.event_id for event in parsed.events] == [payload["eventId"]]


def test_tools_call_orchestration_claim_release_transition_happy_path(tmp_path):
    repo = _repo(tmp_path)

    claim = _tool_payload(
        _call_tool(
            "orchestration_claim_task",
            {
                "project": str(repo),
                "taskId": "TASK-1",
                "actor": "codex",
                "expectedVersion": 0,
                "idempotencyKey": "claim-task-1",
            },
        )
    )
    assert claim["taskId"] == "TASK-1"
    _assert_orchestration_contract(claim, version=1, category="claimed")

    release = _tool_payload(
        _call_tool(
            "orchestration_release_task",
            {
                "project": str(repo),
                "task_id": "TASK-1",
                "actor": "codex",
                "expected_version": 1,
                "reason": "handoff",
            },
        )
    )
    # Release returns the task to a claimable status, so it is eligible again.
    _assert_orchestration_contract(release, version=2, category="eligible")
    assert len(release["runHistoryEventIds"]) == 2

    transition = _tool_payload(
        _call_tool(
            "orchestration_transition_task",
            {
                "project": str(repo),
                "task_id": "TASK-1",
                "toStatus": "inprogress",
                "actor": "codex",
                "expectedVersion": 2,
            },
        )
    )
    _assert_orchestration_contract(transition, version=3, category="in_workflow")
    assert len(transition["runHistoryEventIds"]) == 3


def test_tools_call_orchestration_split_task_creates_children(tmp_path):
    repo = _repo(tmp_path)

    payload = _tool_payload(
        _call_tool(
            "orchestration_split_task",
            {
                "project": str(repo),
                "taskId": "TASK-1",
                "mode": "child",
                "items": [{"title": "Add parser coverage"}, {"title": "Update docs"}],
                "actor": "codex",
                "expectedVersion": 0,
                "idempotencyKey": "split-task-1",
            },
        )
    )

    _assert_orchestration_contract(payload, version=1, category="eligible")
    assert payload["createdTaskIds"] == ["TASK-1.1", "TASK-1.2"]
    assert payload["parentEventId"] == payload["eventId"]
    parsed = parse_run_history(_task_path(repo).read_text(encoding="utf-8"))
    assert parsed.events[0].type == "split_task"
    assert parsed.events[0].split_mode == "child"


def test_tools_call_orchestration_claim_conflict_reports_current_state(tmp_path):
    repo = _repo(tmp_path)
    first = _call_tool(
        "orchestration_claim_task",
        {
            "project": str(repo),
            "task_id": "TASK-1",
            "actor": "agent-a",
            "expectedVersion": 0,
        },
    )
    assert "result" in first, first

    response = _call_tool(
        "orchestration_claim_task",
        {
            "project": str(repo),
            "task_id": "TASK-1",
            "actor": "agent-b",
            "expectedVersion": 1,
        },
    )

    payload = _tool_payload(response)
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 1
    assert payload["path"] == "backlog/tasks/task-1 - Example.md"
    assert payload["eventId"] is None
    assert payload["queueCategory"] == "claimed"
    assert payload["validationIssues"] == []
    assert payload["conflict"]["type"] == "OrchestrationLeaseConflict"
    assert payload["conflict"]["details"]["actual_version"] == 1
    assert payload["conflict"]["details"]["lease_owner"] == "agent-a"


def test_tools_call_orchestration_release_and_transition_conflicts_report_current_state(tmp_path):
    repo = _repo(tmp_path)
    first = _call_tool(
        "orchestration_claim_task",
        {
            "project": str(repo),
            "task_id": "TASK-1",
            "actor": "agent-a",
            "expectedVersion": 0,
        },
    )
    assert "result" in first, first

    release = _tool_payload(
        _call_tool(
            "orchestration_release_task",
            {
                "project": str(repo),
                "task_id": "TASK-1",
                "actor": "agent-b",
                "expectedVersion": 1,
            },
        )
    )
    transition = _tool_payload(
        _call_tool(
            "orchestration_transition_task",
            {
                "project": str(repo),
                "task_id": "TASK-1",
                "toStatus": "review",
                "actor": "agent-b",
                "expectedVersion": 1,
            },
        )
    )

    for payload in (release, transition):
        assert payload["taskId"] == "TASK-1"
        assert payload["path"] == "backlog/tasks/task-1 - Example.md"
        assert payload["version"] == 1
        assert payload["eventId"] is None
        assert payload["queueCategory"] == "claimed"
        assert payload["validationIssues"] == []
        assert payload["conflict"]["type"] == "OrchestrationLeaseConflict"
        assert payload["conflict"]["details"]["actual_version"] == 1
        assert payload["conflict"]["details"]["lease_owner"] == "agent-a"


def test_tools_call_orchestration_record_run_reports_version_conflict(tmp_path):
    repo = _repo(tmp_path)

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {
                "name": "orchestration_record_run",
                "arguments": {
                    "project": str(repo),
                    "task_id": "TASK-1",
                    "actor": "codex",
                    "result": "succeeded",
                    "expectedVersion": 2,
                    "stateUpdate": {"statusKey": "inprogress"},
                },
            },
        }
    )

    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 0
    assert payload["eventId"] is None
    assert payload["runHistoryEventIds"] == []
    assert payload["queueCategory"] == "eligible"
    assert payload["conflict"]["type"] == "OrchestrationVersionConflict"
    assert payload["conflict"]["details"]["expected_version"] == 2
    assert payload["conflict"]["details"]["actual_version"] == 0


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        (
            {"task_id": "TASK-1", "actor": 123, "result": "succeeded"},
            "actor",
        ),
        (
            {
                "task_id": "TASK-1",
                "result": "succeeded",
                "expectedVersion": "abc",
                "stateUpdate": {"statusKey": "inprogress"},
            },
            "expectedVersion",
        ),
    ],
)
def test_tools_call_orchestration_record_run_rejects_invalid_argument_types(tmp_path, arguments, field):
    repo = _repo(tmp_path)
    arguments = {"project": str(repo), **arguments}

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": "orchestration_record_run", "arguments": arguments},
        }
    )

    assert response["error"]["code"] == -32602
    assert field in response["error"]["message"]


def test_invalid_jsonrpc_version_returns_invalid_request_error():
    response = handle_jsonrpc_message({"jsonrpc": "1.0", "id": 1, "method": "ping"})

    assert response["id"] == 1
    assert response["error"]["code"] == -32600


def test_unknown_method_returns_method_not_found():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "unknown"})

    assert response["error"]["code"] == -32601
    assert "unknown" in response["error"]["message"]


def test_invalid_params_returns_invalid_params_error():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {}})

    assert response["error"]["code"] == -32602
    assert "uri" in response["error"]["message"]


def test_notification_returns_none():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "method": "ping"})

    assert response is None


def test_batch_request_returns_responses_in_order():
    response = handle_jsonrpc_message(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )

    assert [item["id"] for item in response] == [1, 2]


def test_handle_jsonrpc_text_renders_response_json():
    response_text = handle_jsonrpc_text('{"jsonrpc":"2.0","id":1,"method":"ping"}')

    assert json.loads(response_text) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_handle_jsonrpc_text_returns_none_for_notification():
    assert handle_jsonrpc_text('{"jsonrpc":"2.0","method":"ping"}') is None


def test_handle_jsonrpc_text_reports_parse_error():
    response_text = handle_jsonrpc_text("{not json")
    response = json.loads(response_text)

    assert response["id"] is None
    assert response["error"]["code"] == -32700
