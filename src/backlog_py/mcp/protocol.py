from __future__ import annotations

import datetime
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from backlog_py import __version__
from backlog_py.core.decisions import DecisionMutationError
from backlog_py.core.documents import DocumentMutationError
from backlog_py.core.errors import NotFoundError
from backlog_py.core.milestones import MilestoneMutationError
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import TaskMutationError
from backlog_py.mcp.catalog import (
    list_resources,
    list_tools,
    project_from_argument,
    read_resource_content,
    tool_by_name,
)
from backlog_py.mcp.tools import McpArgumentError
from backlog_py.runtime.locks import LockTimeoutError
from backlog_py.security.paths import PathContainmentError

# Exceptions that mean "the tool ran but the operation failed" (as opposed to a
# malformed request). These become MCP tool-error results, not JSON-RPC -32603.
# NotFoundError (a KeyError subclass) is used instead of bare KeyError so an
# accidental dict[missing] bug inside a handler still surfaces as -32603.
_TOOL_EXECUTION_ERRORS = (
    NotFoundError,
    TaskMutationError,
    MilestoneMutationError,
    DecisionMutationError,
    DocumentMutationError,
    PathContainmentError,
    LockTimeoutError,
)

JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# MCP reserves -32002 for "resource not found" (JSON-RPC leaves -32000..-32099
# to the application).
RESOURCE_NOT_FOUND = -32002

# Absolute filesystem paths must never reach a client: they expose the local
# layout of the machine running the server. Relative paths (backlog/tasks/...)
# and URIs (backlog://init-required) are meaningful to the caller, so the
# leading separator must not be preceded by a word character, '.', ':' or '/'.
# Requires at least one path segment after the root, so a bare "/" used as a
# separator in prose ("To Do / In Progress") or markup ("</div>") is left alone.
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w.:/])(?:[A-Za-z]:[\\/]|/)[\w.\-]+(?:[\\/][^\s'\"]*)?"
)

_INIT_REQUIRED_MESSAGE = (
    "No Backlog.md project was found for the requested project directory. "
    "Read the backlog://init-required resource for setup guidance, then retry "
    "with an initialized project."
)


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
        return error_response(request_id, INTERNAL_ERROR, "Internal error", {"detail": _redact_paths(str(exc))})

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
        raise JsonRpcError(RESOURCE_NOT_FOUND, _tool_error_message(exc)) from exc


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
        project = project_from_argument(project_path)
    except FileNotFoundError:
        # The caller pointed at a directory that is not inside a Backlog.md
        # project. That is actionable guidance, not a server fault, so answer
        # with the setup resource instead of a -32603 echoing the path.
        return _tool_error_result(_INIT_REQUIRED_MESSAGE)

    _reject_unbindable_arguments(tool.handler, project, arguments)
    try:
        result = tool.handler(project, **arguments)
    except McpArgumentError as exc:
        raise JsonRpcError(INVALID_PARAMS, _tool_error_message(exc)) from exc
    except _TOOL_EXECUTION_ERRORS as exc:
        # The tool ran but the operation failed (not found, invalid mutation,
        # lock timeout, ...). Per MCP this is a tool error result, not a
        # protocol-level -32603, and the message must not leak absolute paths.
        return _tool_error_result(_tool_error_message(exc))
    return {
        "content": [{"type": "text", "text": _tool_text(result)}],
        "isError": False,
    }


def _reject_unbindable_arguments(
    handler: Callable[..., Any],
    project: BacklogProject,
    arguments: dict[str, object],
) -> None:
    """Reject unknown or missing tool arguments before the handler runs.

    Binding first keeps caller mistakes at -32602 while letting a TypeError
    raised from inside a handler stay a -32603 server bug.
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):  # pragma: no cover - builtins have no signature
        return
    try:
        signature.bind(project, **arguments)
    except TypeError as exc:
        raise JsonRpcError(INVALID_PARAMS, _tool_error_message(exc)) from exc


def _tool_error_result(message: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_error_message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return _redact_paths(str(exc.args[0]))
    message = _redact_paths(str(exc)).strip()
    return message or exc.__class__.__name__


def _redact_paths(message: str) -> str:
    """Replace absolute filesystem paths so errors cannot leak the local layout."""
    return _ABSOLUTE_PATH_PATTERN.sub("<path>", message)


def _tool_text(result: object) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, sort_keys=True, default=_json_default)


def _json_default(value: object) -> object:
    # YAML parses unquoted frontmatter dates/times into date/datetime objects,
    # which json.dumps cannot serialize. Render them as ISO-8601 strings.
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
