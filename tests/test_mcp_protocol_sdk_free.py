import ast
import json
import shutil
from pathlib import Path

from backlog_py.mcp.catalog import list_tools
from backlog_py.mcp.protocol import (
    McpRequestContext,
    handle_jsonrpc_message,
    handle_jsonrpc_text,
)


def _tool_properties(name):
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    tool = next(tool for tool in response["result"]["tools"] if tool["name"] == name)
    return tool["inputSchema"]["properties"]


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
