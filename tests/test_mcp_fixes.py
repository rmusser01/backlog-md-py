"""Regression tests for inherited MCP-server bugs."""
from __future__ import annotations

from pathlib import Path

from backlog_py.core.documents import DocumentService
from backlog_py.core.init import init_project
from backlog_py.mcp.protocol import handle_jsonrpc_message


def _project(tmp_path: Path):
    return init_project(tmp_path, no_git=True).project


def _call_tool(project_root: Path, name: str, **arguments):
    arguments["project"] = str(project_root)
    return handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


# --- #7: unquoted dates in frontmatter crash JSON serialization -------------

def test_document_view_with_unquoted_date_does_not_error(tmp_path):
    project = _project(tmp_path)
    doc = DocumentService(project).create_document(
        path="notes", title="Notes", content="Body", metadata={"date": "2026-07-02"}
    )
    # Rewrite the on-disk date unquoted, the way a human or another tool would.
    text = doc.path.read_text(encoding="utf-8").replace("date: '2026-07-02'", "date: 2026-07-02")
    assert "date: 2026-07-02" in text
    doc.path.write_text(text, encoding="utf-8")

    response = _call_tool(project.root, "document_view", path_or_id=doc.id)

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False


# --- #10/M8: missing required fields create junk files ----------------------

def _is_error_response(response) -> bool:
    return "error" in response or bool(response.get("result", {}).get("isError"))


def test_task_create_without_title_is_rejected(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "task_create")

    assert _is_error_response(response), response
    created = list((project.backlog_dir / "tasks").glob("*.md"))
    assert created == [], f"junk task file created: {created}"


def test_document_create_without_title_is_rejected(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "document_create", path="notes", content="body")

    assert _is_error_response(response), response
    created = list((project.backlog_dir / "docs").glob("*.md"))
    assert created == [], f"junk document file created: {created}"


def test_document_create_allows_empty_content(tmp_path):
    project = _project(tmp_path)

    response = _call_tool(project.root, "document_create", path="notes", title="Notes", content="")

    assert "error" not in response, response.get("error")
    assert response["result"]["isError"] is False
