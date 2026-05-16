from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backlog_py import __version__
from backlog_py.mcp.catalog import (
    list_resources,
    list_tools,
    project_from_argument,
    read_resource_content,
    tool_by_name,
)

JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class McpRequestContext:
    """Optional request context for SDK-free MCP dispatch."""

    project_hint: str | None = None
    client_id: str | None = None
    session_id: str | None = None


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def result_response(request_id: object, result: object) -> dict[str, object]:
    """Return a JSON-RPC success response."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, object]:
    """Return a JSON-RPC error response."""
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def handle_jsonrpc_message(
    payload: object,
    *,
    context: McpRequestContext | None = None,
) -> dict[str, object] | list[dict[str, object]] | None:
    """Handle one parsed JSON-RPC message or batch."""
    if isinstance(payload, list):
        if not payload:
            return error_response(None, INVALID_REQUEST, "Invalid Request")
        responses = [_handle_single_message(item, context=context) for item in payload]
        filtered = [response for response in responses if response is not None]
        return filtered or None
    return _handle_single_message(payload, context=context)


def handle_jsonrpc_text(text: str, *, context: McpRequestContext | None = None) -> str | None:
    """Handle JSON-RPC text and render a compact JSON response."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return json.dumps(error_response(None, PARSE_ERROR, "Parse error", {"detail": str(exc)}), sort_keys=True)
    response = handle_jsonrpc_message(payload, context=context)
    if response is None:
        return None
    return json.dumps(response, sort_keys=True)


def _handle_single_message(
    payload: object,
    *,
    context: McpRequestContext | None,
) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return error_response(None, INVALID_REQUEST, "Invalid Request")

    request_id = payload.get("id")
    has_id = "id" in payload
    try:
        _validate_request(payload)
        result = _dispatch(str(payload["method"]), _params(payload), context=context or McpRequestContext())
    except JsonRpcError as exc:
        if not has_id:
            return None
        return error_response(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:
        if not has_id:
            return None
        return error_response(request_id, INTERNAL_ERROR, "Internal error", {"detail": str(exc)})

    if not has_id:
        return None
    return result_response(request_id, result)


def _validate_request(payload: dict[str, object]) -> None:
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcError(INVALID_REQUEST, "Invalid Request: jsonrpc must be '2.0'")
    if not isinstance(payload.get("method"), str):
        raise JsonRpcError(INVALID_REQUEST, "Invalid Request: method must be a string")
    if "params" in payload and not isinstance(payload["params"], (dict, list)):
        raise JsonRpcError(INVALID_REQUEST, "Invalid Request: params must be an object or array")


def _params(payload: dict[str, object]) -> dict[str, object]:
    raw_params = payload.get("params", {})
    if isinstance(raw_params, list):
        raise JsonRpcError(INVALID_PARAMS, "Positional params are not supported")
    return dict(raw_params)


def _dispatch(method: str, params: dict[str, object], *, context: McpRequestContext) -> object:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "backlog-md-py", "version": __version__},
        }
    if method == "notifications/initialized":
        return {}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": list_tools()}
    if method == "resources/list":
        return {"resources": list_resources()}
    if method == "resources/read":
        return _read_resource(params)
    if method == "tools/call":
        return _call_tool(params, context=context)
    raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _read_resource(params: dict[str, object]) -> dict[str, object]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise JsonRpcError(INVALID_PARAMS, "resources/read requires string param 'uri'")
    try:
        return {"contents": [read_resource_content(uri)]}
    except KeyError as exc:
        raise JsonRpcError(INVALID_PARAMS, str(exc)) from exc


def _call_tool(params: dict[str, object], *, context: McpRequestContext) -> dict[str, object]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise JsonRpcError(INVALID_PARAMS, "tools/call requires string param 'name'")
    raw_arguments = params.get("arguments", {})
    if not isinstance(raw_arguments, dict):
        raise JsonRpcError(INVALID_PARAMS, "tools/call param 'arguments' must be an object")

    arguments = dict(raw_arguments)
    if "project" not in arguments and context.project_hint is not None:
        arguments["project"] = context.project_hint
    project_path = arguments.pop("project", None)
    if not isinstance(project_path, str) or not project_path:
        raise JsonRpcError(INVALID_PARAMS, "tools/call arguments require string field 'project'")

    try:
        tool = tool_by_name(name)
    except KeyError as exc:
        raise JsonRpcError(INVALID_PARAMS, str(exc)) from exc

    try:
        result = tool.handler(project_from_argument(project_path), **arguments)
    except TypeError as exc:
        raise JsonRpcError(INVALID_PARAMS, str(exc)) from exc
    return {
        "content": [{"type": "text", "text": _tool_text(result)}],
        "isError": False,
    }


def _tool_text(result: object) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, sort_keys=True)
