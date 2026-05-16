import json

from backlog_py.mcp.protocol import (
    McpRequestContext,
    handle_jsonrpc_message,
    handle_jsonrpc_text,
)


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
    task_search = next(tool for tool in tools if tool["name"] == "task_search")
    assert "task_search" in names
    assert "task_create" in names
    assert task_search["inputSchema"]["type"] == "object"
    assert "project" in task_search["inputSchema"]["required"]


def test_tools_list_advertises_task_edit_acceptance_criteria_fields():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})

    task_edit = next(tool for tool in response["result"]["tools"] if tool["name"] == "task_edit")
    properties = task_edit["inputSchema"]["properties"]
    assert "acceptanceCriteria" in properties
    assert "acceptanceCriteriaAdd" in properties
    assert "acceptanceCriteriaSet" in properties


def test_resources_list_contains_workflow_resources():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "resources", "method": "resources/list"})

    resources = response["result"]["resources"]
    uris = {resource["uri"] for resource in resources}
    assert "backlog://workflow/overview" in uris
    assert "backlog://docs/task-workflow" in uris


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
