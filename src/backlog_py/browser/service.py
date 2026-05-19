from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import webbrowser
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import unquote, urlparse

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError, TaskRecord
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.storage.config import (
    get_definition_of_done_defaults,
    load_config,
    replace_definition_of_done_defaults,
    set_config_value,
)

_LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
_BROWSER_CONFIG_SETTING_KEYS = frozenset(
    (
        "autoOpenBrowser",
        "dateFormat",
        "defaultAssignee",
        "defaultPort",
        "defaultStatus",
        "includeDatetimeInDates",
        "projectName",
        "statuses",
        "zeroPaddedIds",
    )
)


@dataclass(frozen=True)
class BrowserService:
    """Background browser service used by tests."""

    server: "BrowserThreadingHTTPServer"
    thread: threading.Thread
    host: str
    port: int
    root_url: str

    def shutdown(self) -> None:
        """Stop the background HTTP service."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class BrowserThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the Backlog project context."""

    project: BacklogProject
    request_log: deque[dict[str, object]]
    request_log_limit: int
    request_log_lock: threading.Lock


def create_browser_server(*, project: BacklogProject, host: str, port: int) -> BrowserThreadingHTTPServer:
    """Create a loopback browser HTTP server without starting it."""
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("Browser service only supports loopback hosts")
    server = BrowserThreadingHTTPServer((host, port), _BrowserHttpHandler)
    server.project = project
    server.request_log_limit = 50
    server.request_log = deque(maxlen=server.request_log_limit)
    server.request_log_lock = threading.Lock()
    return server


def start_browser_service(project: BacklogProject, *, host: str = "127.0.0.1", port: int) -> BrowserService:
    """Start a background browser service."""
    server = create_browser_server(project=project, host=host, port=port)
    thread = threading.Thread(target=server.serve_forever, name="backlog-md-py-browser", daemon=True)
    thread.start()
    actual_host, actual_port = server.server_address[:2]
    root_url = _root_url(str(actual_host), int(actual_port))
    return BrowserService(
        server=server,
        thread=thread,
        host=str(actual_host),
        port=int(actual_port),
        root_url=root_url,
    )


def run_browser_service_foreground(
    project: BacklogProject,
    *,
    host: str = "127.0.0.1",
    port: int,
    open_browser: bool,
) -> None:
    """Run the loopback browser service until interrupted."""
    server = create_browser_server(project=project, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    root_url = _root_url(str(actual_host), int(actual_port))
    print(f"Serving Backlog.md browser at {root_url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(root_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_board_payload(project: BacklogProject) -> dict[str, object]:
    """Return a JSON-serializable board snapshot for the browser service."""
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    board = repository.board()
    payload: dict[str, object] = {
        "project": {
            "name": project.config.project_name,
            "root": str(project.root),
            "backlogDir": str(project.backlog_dir),
        },
        "statuses": list(board.keys()),
        "columns": {
            status: [_task_payload(task, project=project) for task in tasks]
            for status, tasks in board.items()
        },
    }
    payload["revision"] = _board_revision(payload)
    return payload


def _service_status_payload(server: BrowserThreadingHTTPServer) -> dict[str, object]:
    host, port = server.server_address[:2]
    return {
        "ok": True,
        "projectName": server.project.config.project_name,
        "projectRoot": str(server.project.root),
        "backlogDir": str(server.project.backlog_dir),
        "host": str(host),
        "port": int(port),
        "rootUrl": _root_url(str(host), int(port)),
        "shutdownSupported": True,
    }


def _service_requests_payload(server: BrowserThreadingHTTPServer) -> dict[str, object]:
    with server.request_log_lock:
        requests = list(server.request_log)
    return {
        "limit": server.request_log_limit,
        "requests": requests,
    }


def _record_service_request(
    server: BrowserThreadingHTTPServer,
    *,
    method: str,
    raw_path: str,
    status: HTTPStatus,
    content_type: str,
) -> None:
    entry = {
        "method": method,
        "path": urlparse(raw_path).path or "/",
        "status": int(status),
        "contentType": content_type,
        "timestamp": _utc_timestamp(),
    }
    with server.request_log_lock:
        server.request_log.append(entry)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _schedule_server_shutdown(server: BrowserThreadingHTTPServer) -> None:
    thread = threading.Thread(
        target=server.shutdown,
        name="backlog-md-py-browser-shutdown",
        daemon=True,
    )
    thread.start()


class _BrowserHttpHandler(BaseHTTPRequestHandler):
    server: BrowserThreadingHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "projectName": self.server.project.config.project_name})
            return
        if path == "/api/service/status":
            self._send_json(HTTPStatus.OK, _service_status_payload(self.server))
            return
        if path == "/api/service/requests":
            self._send_json(HTTPStatus.OK, _service_requests_payload(self.server))
            return
        if path == "/api/board":
            self._send_json(HTTPStatus.OK, build_board_payload(self.server.project))
            return
        if path == "/api/settings/config":
            self._send_json(
                HTTPStatus.OK,
                {"settings": _config_settings_payload(load_config(self.server.project.config_path))},
            )
            return
        if path == "/api/settings/dod-defaults":
            self._send_json(
                HTTPStatus.OK,
                {"items": get_definition_of_done_defaults(self.server.project)},
            )
            return
        task_id = _task_detail_endpoint_task_id(path)
        if task_id is not None:
            try:
                task = ReadOnlyRepository(self.server.project).get_task(task_id)
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Task not found: {task_id}"})
                return
            self._send_json(HTTPStatus.OK, _task_detail_payload(task, project=self.server.project))
            return
        if path in {"", "/", "/index.html"}:
            self._send_html(HTTPStatus.OK, render_board_html(self.server.project))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/service/shutdown":
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "message": "Shutdown scheduled"})
            _schedule_server_shutdown(self.server)
            return

        if path == "/api/settings/config":
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                settings = _config_settings_from_payload(self._read_json_body())

                def update_project() -> BacklogProject:
                    project = self.server.project
                    for key, value in settings.items():
                        set_config_value(project, key, value)
                    return BacklogProject(
                        root=project.root,
                        backlog_dir=project.backlog_dir,
                        config_path=project.config_path,
                        config=load_config(project.config_path),
                    )

                self.server.project = with_project_write_lock(
                    self.server.project,
                    "browser_config_settings_update",
                    update_project,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"settings": _config_settings_payload(self.server.project.config)})
            return

        if path == "/api/settings/dod-defaults":
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                items = _dod_defaults_items_from_payload(self._read_json_body())
                config = with_project_write_lock(
                    self.server.project,
                    "browser_dod_defaults_update",
                    lambda: replace_definition_of_done_defaults(self.server.project, items),
                )
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"items": config.definition_of_done or []})
            return

        if path in {"/api/tasks", "/api/tasks/"}:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                create_kwargs = _task_create_kwargs_from_payload(self._read_json_body())
                task = with_project_write_lock(
                    self.server.project,
                    "browser_task_create",
                    lambda: MutableRepository(self.server.project).create_task(**create_kwargs),
                )
            except (json.JSONDecodeError, TaskMutationError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.CREATED, {"task": _task_detail_payload(task, project=self.server.project)})
            return

        edit_task_id = _task_edit_endpoint_task_id(path)
        if edit_task_id is not None:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                edit_kwargs = _task_edit_kwargs_from_payload(self._read_json_body())
                task = with_project_write_lock(
                    self.server.project,
                    "browser_task_edit",
                    lambda: MutableRepository(self.server.project).edit_task(edit_task_id, **edit_kwargs),
                )
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Task not found: {edit_task_id}"})
                return
            except (json.JSONDecodeError, TaskMutationError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"task": _task_detail_payload(task, project=self.server.project)})
            return

        checklist_task_id = _task_checklist_endpoint_task_id(path)
        if checklist_task_id is not None:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                checklist_kwargs = _task_checklist_kwargs_from_payload(self._read_json_body())
                task = with_project_write_lock(
                    self.server.project,
                    "browser_task_checklist",
                    lambda: MutableRepository(self.server.project).edit_task(checklist_task_id, **checklist_kwargs),
                )
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Task not found: {checklist_task_id}"})
                return
            except (json.JSONDecodeError, TaskMutationError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"task": _task_detail_payload(task, project=self.server.project)})
            return

        archive_task_id = _task_archive_endpoint_task_id(path)
        if archive_task_id is not None:
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                task = with_project_write_lock(
                    self.server.project,
                    "browser_task_archive",
                    lambda: MutableRepository(self.server.project).archive_task(archive_task_id),
                )
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Task not found: {archive_task_id}"})
                return
            except (TaskMutationError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"task": _task_detail_payload(task, project=self.server.project)})
            return

        task_id = _status_endpoint_task_id(path)
        if task_id is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return

        try:
            status = _status_from_payload(self._read_json_body())
            task = with_project_write_lock(
                self.server.project,
                "browser_task_status",
                lambda: MutableRepository(self.server.project).edit_task(task_id, status=status),
            )
        except KeyError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Task not found: {task_id}"})
            return
        except (json.JSONDecodeError, TaskMutationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self._send_json(HTTPStatus.OK, {"task": _task_payload(task, project=self.server.project)})

    def log_message(self, format: str, *args: object) -> None:
        _ = format, args

    def _read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = self.headers.get("Host")
        if host is None:
            return False
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS and parsed.netloc == host

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        self._send_text(status, json.dumps(payload, sort_keys=True), content_type="application/json")

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        self._send_text(status, html, content_type="text/html; charset=utf-8")

    def _send_text(self, status: HTTPStatus, text: str, *, content_type: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        _record_service_request(
            self.server,
            method=self.command,
            raw_path=self.path,
            status=status,
            content_type=content_type,
        )


def render_board_html(project: BacklogProject) -> str:
    """Render a browser board with basic task creation, editing, and status movement."""
    payload = build_board_payload(project)
    project_name = escape(project.config.project_name)
    board_revision = escape(str(payload.get("revision", "")))
    columns_obj = payload["columns"]
    columns = columns_obj if isinstance(columns_obj, dict) else {}
    column_markup = "\n".join(
        _render_column(str(status), tasks)
        for status, tasks in columns.items()
    )
    select_tag = "select"
    status_options = _render_status_options(project.config.statuses or [])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{project_name} - Backlog.md</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f5;
      --text: #171717;
      --muted: #66615a;
      --panel: #ffffff;
      --border: #d9d6cf;
      --accent: #245c73;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #151515;
        --text: #f3f0e8;
        --muted: #b7b1a7;
        --panel: #202020;
        --border: #3c3934;
        --accent: #82bfd1;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--border);
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      margin-top: 4px;
    }}
    .board {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
      padding: 20px 24px 24px;
      align-items: start;
    }}
    .column {{
      min-width: 0;
    }}
    .column h2 {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin: 0 0 10px;
      font-size: 14px;
      letter-spacing: 0;
    }}
    .count {{
      color: var(--muted);
      font-weight: 500;
    }}
    .task {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 10px;
      cursor: grab;
    }}
    .column.drag-over .empty,
    .column.drag-over .task {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .task-id {{
      color: var(--accent);
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .task-title {{
      margin-top: 4px;
      overflow-wrap: anywhere;
    }}
    .task-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .badge {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 1px 7px;
      background: color-mix(in srgb, var(--panel) 80%, var(--bg));
    }}
    .task-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    button {{
      color: inherit;
      font: inherit;
    }}
    .primary-button {{
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: var(--panel);
      padding: 6px 11px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .details-button,
    .dialog-close,
    .secondary-button {{
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      padding: 4px 9px;
      cursor: pointer;
    }}
    .details-button:hover,
    .dialog-close:hover,
    .secondary-button:hover {{
      border-color: var(--accent);
    }}
    .empty {{
      color: var(--muted);
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 12px;
    }}
    dialog {{
      max-width: min(720px, calc(100vw - 32px));
      width: 720px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      padding: 0;
    }}
    dialog::backdrop {{
      background: rgb(0 0 0 / 0.35);
    }}
    .dialog-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      border-bottom: 1px solid var(--border);
      padding: 16px;
    }}
    .dialog-title {{
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }}
    .dialog-body {{
      padding: 16px;
    }}
    .dialog-meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px 16px;
      margin: 0 0 16px;
      color: var(--muted);
    }}
    .dialog-meta dt {{
      font-weight: 650;
      color: var(--text);
    }}
    .dialog-meta dd {{
      margin: 0;
    }}
    .dialog-section {{
      border-top: 1px solid var(--border);
      padding-top: 12px;
      margin-top: 12px;
    }}
    .dialog-section h3 {{
      margin: 0 0 6px;
      font-size: 13px;
      text-transform: uppercase;
      color: var(--muted);
      letter-spacing: 0;
    }}
    .dialog-section p {{
      margin: 0;
      white-space: pre-wrap;
    }}
    .markdown-body > :first-child {{
      margin-top: 0;
    }}
    .markdown-body > :last-child {{
      margin-bottom: 0;
    }}
    .markdown-body h1,
    .markdown-body h2,
    .markdown-body h3 {{
      margin: 0 0 8px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .markdown-body p {{
      margin: 0 0 10px;
      white-space: normal;
    }}
    .markdown-body ul {{
      margin: 0 0 10px;
      padding-left: 20px;
    }}
    .markdown-body code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.94em;
    }}
    .markdown-code {{
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      margin: 0 0 10px;
      padding: 10px;
      overflow-x: auto;
      white-space: pre;
    }}
    .dialog-section ul {{
      margin: 0;
      padding-left: 20px;
    }}
    .checklist-item {{
      margin: 4px 0;
    }}
    .checklist-item label {{
      display: flex;
      gap: 8px;
      align-items: start;
    }}
    .checklist-item input {{
      margin-top: 3px;
    }}
    .task-form {{
      display: grid;
      gap: 12px;
    }}
    .task-form label {{
      display: grid;
      gap: 4px;
      font-weight: 650;
    }}
    .task-form input,
    .task-form .task-form-select,
    .task-form textarea {{
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
      font: inherit;
      padding: 7px 9px;
    }}
    .task-form textarea {{
      min-height: 88px;
      resize: vertical;
    }}
    .task-form input[type="checkbox"] {{
      width: auto;
      justify-self: start;
    }}
    .form-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }}
    .header-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{project_name}</h1>
      <div class="subtitle">Backlog.md board with task creation, editing, and drag-and-drop status movement</div>
    </div>
    <div class="header-actions">
      <button class="secondary-button" type="button" id="service-status-open">Service</button>
      <button class="secondary-button" type="button" id="config-settings-open">Project settings</button>
      <button class="secondary-button" type="button" id="dod-defaults-open">Definition of Done</button>
      <button class="primary-button" type="button" id="task-create-open">New task</button>
    </div>
  </header>
  <main class="board" data-board-revision="{board_revision}">
{column_markup}
  </main>
  <dialog id="task-create-dialog" aria-labelledby="task-create-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="task-create-title">New task</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <form class="task-form" id="task-create-form">
        <label>Title
          <input name="title" autocomplete="off" required>
        </label>
        <label>Status
          <{select_tag} class="task-form-select" name="status">{status_options}</{select_tag}>
        </label>
        <label>Description
          <textarea name="description"></textarea>
        </label>
        <label>Acceptance Criteria
          <textarea name="acceptanceCriteria"></textarea>
        </label>
        <div class="form-actions">
          <button class="secondary-button" type="button" id="task-create-cancel">Cancel</button>
          <button class="primary-button" type="submit">Create</button>
        </div>
      </form>
    </div>
  </dialog>
  <dialog id="task-edit-dialog" aria-labelledby="task-edit-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="task-edit-title">Edit task</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <form class="task-form" id="task-edit-form">
        <label>Title
          <input name="title" autocomplete="off" required>
        </label>
        <label>Status
          <{select_tag} class="task-form-select" name="status">{status_options}</{select_tag}>
        </label>
        <label>Description
          <textarea name="description"></textarea>
        </label>
        <label>Acceptance Criteria
          <textarea name="acceptanceCriteria"></textarea>
        </label>
        <label>Implementation Notes
          <textarea name="implementationNotes"></textarea>
        </label>
        <label>Final Summary
          <textarea name="finalSummary"></textarea>
        </label>
        <div class="form-actions">
          <button class="secondary-button" type="button" id="task-edit-cancel">Cancel</button>
          <button class="primary-button" type="submit">Save</button>
        </div>
      </form>
    </div>
  </dialog>
  <dialog id="task-archive-dialog" aria-labelledby="task-archive-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="task-archive-title">Archive task</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <p>Archive <strong id="task-archive-name"></strong>?</p>
      <div class="form-actions">
        <button class="secondary-button" type="button" id="task-archive-cancel">Cancel</button>
        <button class="primary-button" type="button" id="task-archive-confirm">Archive</button>
      </div>
    </div>
  </dialog>
  <dialog id="config-settings-dialog" aria-labelledby="config-settings-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="config-settings-title">Project settings</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <form class="task-form" id="config-settings-form">
        <label>Project name
          <input name="projectName" autocomplete="off" required>
        </label>
        <label>Default assignee
          <input name="defaultAssignee" autocomplete="off">
        </label>
        <label>Default status
          <input name="defaultStatus" autocomplete="off" required>
        </label>
        <label>Date format
          <input name="dateFormat" autocomplete="off" required>
        </label>
        <label>Default browser port
          <input name="defaultPort" type="number" min="1" max="65535" step="1" required>
        </label>
        <label>Zero-padded ID width
          <input name="zeroPaddedIds" type="number" min="0" step="1">
        </label>
        <label>Statuses
          <textarea name="statuses"></textarea>
        </label>
        <label>
          <input name="includeDatetimeInDates" type="checkbox">
          Include time in dates
        </label>
        <label>
          <input name="autoOpenBrowser" type="checkbox">
          Open browser automatically
        </label>
        <div class="form-actions">
          <button class="secondary-button" type="button" id="config-settings-cancel">Cancel</button>
          <button class="primary-button" type="submit">Save</button>
        </div>
      </form>
    </div>
  </dialog>
  <dialog id="dod-defaults-dialog" aria-labelledby="dod-defaults-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="dod-defaults-title">Definition of Done defaults</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <form class="task-form" id="dod-defaults-form">
        <label>Definition of Done
          <textarea name="items"></textarea>
        </label>
        <div class="form-actions">
          <button class="secondary-button" type="button" id="dod-defaults-cancel">Cancel</button>
          <button class="primary-button" type="submit">Save</button>
        </div>
      </form>
    </div>
  </dialog>
  <dialog id="service-status-dialog" aria-labelledby="service-status-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="service-status-title">Browser service</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <dl class="dialog-meta">
        <div><dt>Project</dt><dd id="service-status-project"></dd></div>
        <div><dt>Project root</dt><dd id="service-status-root"></dd></div>
        <div><dt>Backlog directory</dt><dd id="service-status-backlog"></dd></div>
        <div><dt>Host</dt><dd id="service-status-host"></dd></div>
        <div><dt>Port</dt><dd id="service-status-port"></dd></div>
        <div><dt>URL</dt><dd id="service-status-url"></dd></div>
      </dl>
      <p id="service-status-message"></p>
      <section class="dialog-section">
        <h3>Recent requests</h3>
        <ul id="service-request-log"></ul>
      </section>
      <div class="form-actions">
        <button class="secondary-button" type="button" id="service-status-refresh">Refresh</button>
        <button class="primary-button" type="button" id="service-shutdown-confirm">Stop server</button>
      </div>
    </div>
  </dialog>
  <dialog id="task-dialog" aria-labelledby="task-dialog-title">
    <div class="dialog-header">
      <h2 class="dialog-title" id="task-dialog-title">Task details</h2>
      <form method="dialog">
        <button class="dialog-close" type="submit">Close</button>
      </form>
    </div>
    <div class="dialog-body">
      <dl class="dialog-meta">
        <div><dt>Status</dt><dd id="task-dialog-status"></dd></div>
        <div><dt>File</dt><dd id="task-dialog-path"></dd></div>
        <div><dt>Created</dt><dd id="task-dialog-created"></dd></div>
        <div><dt>Updated</dt><dd id="task-dialog-updated"></dd></div>
        <div><dt>Priority</dt><dd id="task-dialog-priority"></dd></div>
        <div><dt>Assignees</dt><dd id="task-dialog-assignees"></dd></div>
        <div><dt>Labels</dt><dd id="task-dialog-labels"></dd></div>
        <div><dt>Milestone</dt><dd id="task-dialog-milestone"></dd></div>
      </dl>
      <section class="dialog-section">
        <h3>Description</h3>
        <div class="markdown-body" id="task-dialog-description-html"></div>
      </section>
      <section class="dialog-section">
        <h3>Implementation Notes</h3>
        <div class="markdown-body" id="task-dialog-implementation-notes"></div>
      </section>
      <section class="dialog-section">
        <h3>Final Summary</h3>
        <div class="markdown-body" id="task-dialog-final-summary"></div>
      </section>
      <section class="dialog-section">
        <h3>Acceptance Criteria</h3>
        <ul id="task-dialog-acceptance" data-checklist-section="acceptanceCriteria"></ul>
      </section>
      <section class="dialog-section">
        <h3>Definition of Done</h3>
        <ul id="task-dialog-dod" data-checklist-section="definitionOfDone"></ul>
      </section>
    </div>
  </dialog>
  <script>
    let draggedTaskId = null;
    const taskDialog = document.getElementById("task-dialog");
    const taskCreateDialog = document.getElementById("task-create-dialog");
    const taskCreateForm = document.getElementById("task-create-form");
    const taskEditDialog = document.getElementById("task-edit-dialog");
    const taskEditForm = document.getElementById("task-edit-form");
    const taskArchiveDialog = document.getElementById("task-archive-dialog");
    const taskArchiveConfirm = document.getElementById("task-archive-confirm");
    const configSettingsDialog = document.getElementById("config-settings-dialog");
    const configSettingsForm = document.getElementById("config-settings-form");
    const dodDefaultsDialog = document.getElementById("dod-defaults-dialog");
    const dodDefaultsForm = document.getElementById("dod-defaults-form");
    const serviceStatusDialog = document.getElementById("service-status-dialog");
    const serviceShutdownConfirm = document.getElementById("service-shutdown-confirm");
    const boardElement = document.querySelector("[data-board-revision]");
    const boardRefreshIntervalMs = 5000;
    let currentBoardRevision = boardElement?.dataset.boardRevision || "";
    let boardRefreshInFlight = false;

    function setText(id, value) {{
      const element = document.getElementById(id);
      if (element) element.textContent = value || "—";
    }}

    function setHtml(id, value) {{
      const element = document.getElementById(id);
      if (element) element.innerHTML = value || '<p class="markdown-empty">No content</p>';
    }}

    function hasOpenDialog() {{
      return Boolean(document.querySelector("dialog[open]"));
    }}

    async function pollBoardRevision() {{
      if (!currentBoardRevision || boardRefreshInFlight) return;
      boardRefreshInFlight = true;
      try {{
        const response = await fetch("/api/board", {{
          headers: {{"Accept": "application/json"}},
          cache: "no-store",
        }});
        if (!response.ok) {{
          console.error(await response.text());
          return;
        }}
        const payload = await response.json();
        if (payload.revision && payload.revision !== currentBoardRevision && !hasOpenDialog()) {{
          window.location.reload();
        }}
      }} catch (error) {{
        console.error(error);
      }} finally {{
        boardRefreshInFlight = false;
      }}
    }}

    function renderChecklist(id, items, section) {{
      const list = document.getElementById(id);
      if (!list) return;
      list.replaceChildren();
      if (!items || items.length === 0) {{
        const empty = document.createElement("li");
        empty.textContent = "No items";
        list.appendChild(empty);
        return;
      }}
      items.forEach((item, index) => {{
        const li = document.createElement("li");
        li.className = "checklist-item";
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = Boolean(item.checked);
        checkbox.setAttribute("data-checklist-section", section);
        checkbox.setAttribute("data-checklist-index", String(index + 1));
        checkbox.addEventListener("change", submitTaskChecklistState);
        const text = document.createElement("span");
        const itemId = item.itemId ? `#${{item.itemId}} ` : "";
        text.textContent = `${{itemId}}${{item.text}}`;
        label.appendChild(checkbox);
        label.appendChild(text);
        li.appendChild(label);
        list.appendChild(li);
      }});
    }}

    function checklistText(items) {{
      return (items || []).map((item) => item.text || "").filter(Boolean).join("\\n");
    }}

    async function openTaskDetails(taskId) {{
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}`);
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      const task = await response.json();
      if (taskDialog) taskDialog.dataset.taskId = task.id;
      setText("task-dialog-title", `${{task.id}} - ${{task.title}}`);
      setText("task-dialog-status", task.status);
      setText("task-dialog-path", task.path);
      setText("task-dialog-created", task.createdDate);
      setText("task-dialog-updated", task.updatedDate);
      setText("task-dialog-priority", task.priority);
      setText("task-dialog-assignees", (task.assignees || []).join(", "));
      setText("task-dialog-labels", (task.labels || []).join(", "));
      setText("task-dialog-milestone", task.milestone);
      setHtml("task-dialog-description-html", task.descriptionHtml);
      setHtml("task-dialog-implementation-notes", task.implementationNotesHtml);
      setHtml("task-dialog-final-summary", task.finalSummaryHtml);
      renderChecklist("task-dialog-acceptance", task.acceptanceCriteria, "acceptanceCriteria");
      renderChecklist("task-dialog-dod", task.definitionOfDone, "definitionOfDone");
      if (taskDialog && taskDialog.showModal) taskDialog.showModal();
      else if (taskDialog) taskDialog.setAttribute("open", "open");
    }}

    async function openTaskEdit(taskId) {{
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}`);
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      const task = await response.json();
      if (!taskEditForm) return;
      taskEditForm.dataset.taskId = task.id;
      taskEditForm.elements.title.value = task.title || "";
      taskEditForm.elements.status.value = task.status || "";
      taskEditForm.elements.description.value = task.description || "";
      taskEditForm.elements.acceptanceCriteria.value = checklistText(task.acceptanceCriteria);
      taskEditForm.elements.implementationNotes.value = task.implementationNotes || "";
      taskEditForm.elements.finalSummary.value = task.finalSummary || "";
      setText("task-edit-title", `${{task.id}} - Edit task`);
      if (taskEditDialog && taskEditDialog.showModal) taskEditDialog.showModal();
      else if (taskEditDialog) taskEditDialog.setAttribute("open", "open");
    }}

    async function openTaskArchive(taskId) {{
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}`);
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      const task = await response.json();
      if (taskArchiveConfirm) taskArchiveConfirm.dataset.taskId = task.id;
      setText("task-archive-title", `${{task.id}} - Archive task`);
      setText("task-archive-name", task.title);
      if (taskArchiveDialog && taskArchiveDialog.showModal) taskArchiveDialog.showModal();
      else if (taskArchiveDialog) taskArchiveDialog.setAttribute("open", "open");
    }}

    function openTaskCreate() {{
      if (taskCreateForm) taskCreateForm.reset();
      if (taskCreateDialog && taskCreateDialog.showModal) taskCreateDialog.showModal();
      else if (taskCreateDialog) taskCreateDialog.setAttribute("open", "open");
    }}

    async function openConfigSettings() {{
      const response = await fetch("/api/settings/config");
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      const payload = await response.json();
      const settings = payload.settings || {{}};
      if (configSettingsForm) {{
        configSettingsForm.elements.projectName.value = settings.projectName || "";
        configSettingsForm.elements.defaultAssignee.value = settings.defaultAssignee || "";
        configSettingsForm.elements.defaultStatus.value = settings.defaultStatus || "";
        configSettingsForm.elements.dateFormat.value = settings.dateFormat || "";
        configSettingsForm.elements.defaultPort.value = settings.defaultPort || "";
        configSettingsForm.elements.zeroPaddedIds.value = settings.zeroPaddedIds || "";
        configSettingsForm.elements.statuses.value = (settings.statuses || []).join("\\n");
        configSettingsForm.elements.includeDatetimeInDates.checked = Boolean(settings.includeDatetimeInDates);
        configSettingsForm.elements.autoOpenBrowser.checked = Boolean(settings.autoOpenBrowser);
      }}
      if (configSettingsDialog && configSettingsDialog.showModal) configSettingsDialog.showModal();
      else if (configSettingsDialog) configSettingsDialog.setAttribute("open", "open");
    }}

    async function openDodDefaultsSettings() {{
      const response = await fetch("/api/settings/dod-defaults");
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      const payload = await response.json();
      if (dodDefaultsForm) {{
        dodDefaultsForm.elements.items.value = (payload.items || []).join("\\n");
      }}
      if (dodDefaultsDialog && dodDefaultsDialog.showModal) dodDefaultsDialog.showModal();
      else if (dodDefaultsDialog) dodDefaultsDialog.setAttribute("open", "open");
    }}

    async function refreshServiceStatus() {{
      const response = await fetch("/api/service/status", {{
        headers: {{"Accept": "application/json"}},
        cache: "no-store",
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return false;
      }}
      const status = await response.json();
      setText("service-status-project", status.projectName);
      setText("service-status-root", status.projectRoot);
      setText("service-status-backlog", status.backlogDir);
      setText("service-status-host", status.host);
      setText("service-status-port", String(status.port || ""));
      setText("service-status-url", status.rootUrl);
      setText("service-status-message", status.shutdownSupported ? "Shutdown is available from this local browser session." : "");
      return true;
    }}

    function renderServiceRequestLog(requests) {{
      const list = document.getElementById("service-request-log");
      if (!list) return;
      list.replaceChildren();
      if (!requests || requests.length === 0) {{
        const empty = document.createElement("li");
        empty.textContent = "No requests recorded";
        list.appendChild(empty);
        return;
      }}
      requests.slice(-10).reverse().forEach((request) => {{
        const item = document.createElement("li");
        item.textContent = `${{request.timestamp}} ${{request.method}} ${{request.path}} ${{request.status}}`;
        list.appendChild(item);
      }});
    }}

    async function refreshServiceRequests() {{
      const response = await fetch("/api/service/requests", {{
        headers: {{"Accept": "application/json"}},
        cache: "no-store",
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return false;
      }}
      const payload = await response.json();
      renderServiceRequestLog(payload.requests || []);
      return true;
    }}

    async function refreshServicePanel() {{
      const loaded = await refreshServiceStatus();
      if (!loaded) return false;
      await refreshServiceRequests();
      return true;
    }}

    async function openServiceStatus() {{
      const loaded = await refreshServicePanel();
      if (!loaded) return;
      if (serviceShutdownConfirm) serviceShutdownConfirm.disabled = false;
      if (serviceStatusDialog && serviceStatusDialog.showModal) serviceStatusDialog.showModal();
      else if (serviceStatusDialog) serviceStatusDialog.setAttribute("open", "open");
    }}

    async function submitServiceShutdown(event) {{
      event.preventDefault();
      const response = await fetch("/api/service/shutdown", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{}}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      if (serviceShutdownConfirm) serviceShutdownConfirm.disabled = true;
      setText("service-status-message", "Server is stopping.");
    }}

    async function submitTaskCreate(event) {{
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const criteria = String(data.get("acceptanceCriteria") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/tasks", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          title: String(data.get("title") || ""),
          status: String(data.get("status") || ""),
          description: String(data.get("description") || ""),
          acceptanceCriteria: criteria,
          implementationNotes: String(data.get("implementationNotes") || ""),
          finalSummary: String(data.get("finalSummary") || ""),
        }}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      window.location.reload();
    }}

    async function submitTaskEdit(event) {{
      event.preventDefault();
      const form = event.currentTarget;
      const taskId = form.dataset.taskId;
      if (!taskId) return;
      const data = new FormData(form);
      const criteria = String(data.get("acceptanceCriteria") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}/edit`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          title: String(data.get("title") || ""),
          status: String(data.get("status") || ""),
          description: String(data.get("description") || ""),
          acceptanceCriteria: criteria,
        }}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      window.location.reload();
    }}

    async function submitTaskChecklistState(event) {{
      const checkbox = event.currentTarget;
      const taskId = taskDialog?.dataset.taskId;
      const section = checkbox?.dataset.checklistSection;
      const index = Number(checkbox?.dataset.checklistIndex);
      if (!taskId || !section || !Number.isInteger(index)) return;
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}/checklist`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{section, index, checked: checkbox.checked}}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        checkbox.checked = !checkbox.checked;
        return;
      }}
      const payload = await response.json();
      setText("task-dialog-updated", payload.task.updatedDate);
      renderChecklist("task-dialog-acceptance", payload.task.acceptanceCriteria, "acceptanceCriteria");
      renderChecklist("task-dialog-dod", payload.task.definitionOfDone, "definitionOfDone");
    }}

    async function submitTaskArchive(event) {{
      event.preventDefault();
      const taskId = taskArchiveConfirm?.dataset.taskId;
      if (!taskId) return;
      const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}/archive`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{}}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      window.location.reload();
    }}

    async function submitConfigSettings(event) {{
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const statuses = String(data.get("statuses") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/settings/config", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          settings: {{
            projectName: String(data.get("projectName") || ""),
            defaultAssignee: String(data.get("defaultAssignee") || ""),
            defaultStatus: String(data.get("defaultStatus") || ""),
            dateFormat: String(data.get("dateFormat") || ""),
            defaultPort: Number(data.get("defaultPort") || 0),
            zeroPaddedIds: String(data.get("zeroPaddedIds") || ""),
            statuses,
            includeDatetimeInDates: Boolean(form.elements.includeDatetimeInDates?.checked),
            autoOpenBrowser: Boolean(form.elements.autoOpenBrowser?.checked),
          }},
        }}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      window.location.reload();
    }}

    async function submitDodDefaultsSettings(event) {{
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const items = String(data.get("items") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/settings/dod-defaults", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{items}}),
      }});
      if (!response.ok) {{
        console.error(await response.text());
        return;
      }}
      window.location.reload();
    }}

    document.getElementById("task-create-open")?.addEventListener("click", openTaskCreate);
    document.getElementById("task-create-cancel")?.addEventListener("click", () => taskCreateDialog?.close());
    taskCreateForm?.addEventListener("submit", submitTaskCreate);
    document.getElementById("task-edit-cancel")?.addEventListener("click", () => taskEditDialog?.close());
    taskEditForm?.addEventListener("submit", submitTaskEdit);
    document.getElementById("task-archive-cancel")?.addEventListener("click", () => taskArchiveDialog?.close());
    taskArchiveConfirm?.addEventListener("click", submitTaskArchive);
    document.getElementById("config-settings-open")?.addEventListener("click", openConfigSettings);
    document.getElementById("config-settings-cancel")?.addEventListener("click", () => configSettingsDialog?.close());
    configSettingsForm?.addEventListener("submit", submitConfigSettings);
    document.getElementById("dod-defaults-open")?.addEventListener("click", openDodDefaultsSettings);
    document.getElementById("dod-defaults-cancel")?.addEventListener("click", () => dodDefaultsDialog?.close());
    dodDefaultsForm?.addEventListener("submit", submitDodDefaultsSettings);
    document.getElementById("service-status-open")?.addEventListener("click", openServiceStatus);
    document.getElementById("service-status-refresh")?.addEventListener("click", refreshServicePanel);
    serviceShutdownConfirm?.addEventListener("click", submitServiceShutdown);

    document.querySelectorAll("[data-task-details]").forEach((button) => {{
      button.addEventListener("click", (event) => {{
        event.stopPropagation();
        openTaskDetails(button.dataset.taskDetails);
      }});
    }});
    document.querySelectorAll("[data-task-edit]").forEach((button) => {{
      button.addEventListener("click", (event) => {{
        event.stopPropagation();
        openTaskEdit(button.dataset.taskEdit);
      }});
    }});
    document.querySelectorAll("[data-task-archive]").forEach((button) => {{
      button.addEventListener("click", (event) => {{
        event.stopPropagation();
        openTaskArchive(button.dataset.taskArchive);
      }});
    }});

    document.querySelectorAll("[data-task-id]").forEach((task) => {{
      task.addEventListener("dragstart", (event) => {{
        draggedTaskId = task.dataset.taskId;
        event.dataTransfer.setData("text/plain", draggedTaskId);
      }});
    }});
    document.querySelectorAll("[data-status]").forEach((column) => {{
      column.addEventListener("dragover", (event) => {{
        event.preventDefault();
        column.classList.add("drag-over");
      }});
      column.addEventListener("dragleave", () => column.classList.remove("drag-over"));
      column.addEventListener("drop", async (event) => {{
        event.preventDefault();
        column.classList.remove("drag-over");
        const taskId = event.dataTransfer.getData("text/plain") || draggedTaskId;
        const status = column.dataset.status;
        if (!taskId || !status) return;
        const response = await fetch(`/api/tasks/${{encodeURIComponent(taskId)}}/status`, {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{status}}),
        }});
        if (!response.ok) {{
          console.error(await response.text());
          return;
        }}
        window.location.reload();
      }});
    }});
    window.setInterval(pollBoardRevision, boardRefreshIntervalMs);
    document.addEventListener("visibilitychange", () => {{
      if (!document.hidden) pollBoardRevision();
    }});
  </script>
</body>
</html>
"""


def _render_column(status: str, raw_tasks: object) -> str:
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    task_markup = "\n".join(_render_task(task) for task in tasks)
    if not task_markup:
        task_markup = '      <div class="empty">No tasks</div>'
    return f"""    <section class="column" data-status="{escape(status)}">
      <h2><span>{escape(status)}</span><span class="count">{len(tasks)}</span></h2>
{task_markup}
    </section>"""


def _render_task(raw_task: object) -> str:
    task = raw_task if isinstance(raw_task, dict) else {}
    task_id = escape(str(task.get("id", "")))
    title = escape(str(task.get("title", "")))
    priority = _metadata_string(task.get("priority"))
    assignees = task.get("assignees")
    labels = task.get("labels")
    meta = _render_task_meta(
        priority=priority,
        assignees=assignees if isinstance(assignees, list) else [],
        labels=labels if isinstance(labels, list) else [],
    )
    return f"""      <article class="task" data-task-id="{task_id}" draggable="true">
        <div class="task-id">{task_id}</div>
        <div class="task-title">{title}</div>
{meta}
        <div class="task-actions"><button class="details-button" type="button" data-task-details="{task_id}">Details</button><button class="details-button" type="button" data-task-edit="{task_id}">Edit</button><button class="details-button" type="button" data-task-archive="{task_id}">Archive</button></div>
      </article>"""


def _board_revision(payload: Mapping[str, object]) -> str:
    revision_source = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(revision_source.encode("utf-8")).hexdigest()


def _render_status_options(statuses: list[str]) -> str:
    return "".join(f'<option value="{escape(status)}">{escape(status)}</option>' for status in statuses)


def _task_payload(task: TaskRecord, *, project: BacklogProject) -> dict[str, object]:
    frontmatter = task.parsed.frontmatter
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "path": str(task.path.relative_to(project.root)),
        "assignees": _metadata_list(frontmatter.get("assignee")),
        "labels": _metadata_list(frontmatter.get("labels")),
        "priority": _metadata_string(frontmatter.get("priority")),
        "milestone": _metadata_string(frontmatter.get("milestone")),
        "createdDate": _metadata_string(frontmatter.get("created_date")),
        "updatedDate": _metadata_string(frontmatter.get("updated_date")),
    }


def _task_detail_payload(task: TaskRecord, *, project: BacklogProject) -> dict[str, object]:
    payload = _task_payload(task, project=project)
    description = task.description or task.body.strip()
    implementation_notes = _section_content(task, "IMPLEMENTATION_NOTES")
    final_summary = _section_content(task, "FINAL_SUMMARY")
    payload.update(
        {
            "description": description,
            "descriptionHtml": _markdown_to_html(description),
            "implementationNotes": implementation_notes,
            "implementationNotesHtml": _markdown_to_html(implementation_notes),
            "finalSummary": final_summary,
            "finalSummaryHtml": _markdown_to_html(final_summary),
            "acceptanceCriteria": _checklist_payload(task, "AC"),
            "definitionOfDone": _checklist_payload(task, "DOD"),
        }
    )
    return payload


def _checklist_payload(task: TaskRecord, marker: str) -> list[dict[str, object]]:
    return [
        {
            "checked": item.checked,
            "itemId": item.item_id,
            "text": item.text,
        }
        for item in task.parsed.checklists.get(marker, [])
    ]


def _section_content(task: TaskRecord, section_name: str) -> str:
    section = task.parsed.sections.get(section_name)
    return "" if section is None else section.content.strip()


def _markdown_to_html(text: str) -> str:
    source = text.strip()
    if not source:
        return '<p class="markdown-empty">No content</p>'

    blocks: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    code_language = ""
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            paragraph = " ".join(paragraph_lines).strip()
            if paragraph:
                blocks.append(f"<p>{_render_inline_markdown(paragraph)}</p>")
            paragraph_lines.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items.clear()

    def flush_text_blocks() -> None:
        flush_paragraph()
        flush_list()

    def flush_code_block() -> None:
        language_class = _markdown_language_class(code_language)
        code = escape("\n".join(code_lines))
        blocks.append(f'<pre class="markdown-code{language_class}"><code>{code}</code></pre>')
        code_lines.clear()

    for line in source.splitlines():
        stripped = line.strip()
        if in_code_block:
            if stripped.startswith("```"):
                flush_code_block()
                in_code_block = False
                code_language = ""
            else:
                code_lines.append(line)
            continue

        if stripped.startswith("```"):
            flush_text_blocks()
            in_code_block = True
            code_language = stripped[3:].strip().split(maxsplit=1)[0] if stripped[3:].strip() else ""
            continue

        if not stripped:
            flush_text_blocks()
            continue

        heading = _markdown_heading(stripped)
        if heading is not None:
            flush_text_blocks()
            level, content = heading
            blocks.append(f"<h{level}>{_render_inline_markdown(content)}</h{level}>")
            continue

        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            list_items.append(stripped[2:].strip())
            continue

        flush_list()
        paragraph_lines.append(stripped)

    if in_code_block:
        flush_code_block()
    flush_text_blocks()
    return "\n".join(blocks)


def _markdown_heading(line: str) -> tuple[int, str] | None:
    marker_length = len(line) - len(line.lstrip("#"))
    if marker_length < 1 or marker_length > 3:
        return None
    if len(line) <= marker_length or line[marker_length] != " ":
        return None
    content = line[marker_length:].strip()
    if not content:
        return None
    return marker_length, content


def _markdown_language_class(language: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "", language.strip().lower())
    return f" language-{normalized}" if normalized else ""


def _render_inline_markdown(text: str) -> str:
    rendered = escape(text)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", rendered)
    return rendered


def _render_task_meta(*, priority: str | None, assignees: list[object], labels: list[object]) -> str:
    badges: list[str] = []
    if priority:
        badges.append(f'<span class="badge">Priority: {escape(priority)}</span>')
    for assignee in assignees[:2]:
        badges.append(f'<span class="badge">@{escape(str(assignee).lstrip("@"))}</span>')
    for label in labels[:2]:
        badges.append(f'<span class="badge">{escape(str(label))}</span>')
    if not badges:
        return ""
    return f'        <div class="task-meta">{"".join(badges)}</div>'


def _metadata_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _metadata_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _root_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def _status_endpoint_task_id(path: str) -> str | None:
    prefix = "/api/tasks/"
    suffix = "/status"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    encoded_task_id = path[len(prefix) : -len(suffix)]
    if not encoded_task_id:
        return None
    return unquote(encoded_task_id)


def _task_edit_endpoint_task_id(path: str) -> str | None:
    prefix = "/api/tasks/"
    suffix = "/edit"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    encoded_task_id = path[len(prefix) : -len(suffix)]
    if not encoded_task_id or "/" in encoded_task_id:
        return None
    return unquote(encoded_task_id)


def _task_archive_endpoint_task_id(path: str) -> str | None:
    prefix = "/api/tasks/"
    suffix = "/archive"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    encoded_task_id = path[len(prefix) : -len(suffix)]
    if not encoded_task_id or "/" in encoded_task_id:
        return None
    return unquote(encoded_task_id)


def _task_checklist_endpoint_task_id(path: str) -> str | None:
    prefix = "/api/tasks/"
    suffix = "/checklist"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    encoded_task_id = path[len(prefix) : -len(suffix)]
    if not encoded_task_id or "/" in encoded_task_id:
        return None
    return unquote(encoded_task_id)


def _task_detail_endpoint_task_id(path: str) -> str | None:
    prefix = "/api/tasks/"
    if not path.startswith(prefix):
        return None
    encoded_task_id = path[len(prefix):]
    if not encoded_task_id or "/" in encoded_task_id:
        return None
    return unquote(encoded_task_id)


def _status_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("Request body field status must be a non-empty string")
    return status


def _task_create_kwargs_from_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    title = _required_string_field(payload, "title")
    create_kwargs: dict[str, object] = {"title": title}
    for payload_key, repository_key in (
        ("status", "status"),
        ("description", "description"),
        ("priority", "priority"),
        ("milestone", "milestone"),
    ):
        value = _optional_string_field(payload, payload_key)
        if value is not None:
            create_kwargs[repository_key] = value
    for payload_key, repository_key in (
        ("acceptanceCriteria", "acceptance_criteria"),
        ("definitionOfDone", "definition_of_done"),
        ("assignees", "assignees"),
        ("labels", "labels"),
    ):
        value = _optional_string_list_field(payload, payload_key)
        if value is not None:
            create_kwargs[repository_key] = value
    return create_kwargs


def _task_edit_kwargs_from_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    edit_kwargs: dict[str, object] = {}
    for payload_key, repository_key in (("title", "title"), ("status", "status")):
        if payload_key in payload:
            edit_kwargs[repository_key] = _required_string_field(payload, payload_key)
    for payload_key, repository_key in (
        ("description", "description"),
        ("implementationNotes", "notes"),
        ("finalSummary", "final_summary"),
    ):
        if payload_key in payload:
            edit_kwargs[repository_key] = _required_text_field(payload, payload_key)
    value = _optional_string_list_field(payload, "acceptanceCriteria")
    if value is not None:
        edit_kwargs["acceptance_criteria"] = value
    if not edit_kwargs:
        raise ValueError("Request body must include at least one editable field")
    return edit_kwargs


def _task_checklist_kwargs_from_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    section = payload.get("section")
    if section not in {"acceptanceCriteria", "definitionOfDone"}:
        raise ValueError("Request body field section must be acceptanceCriteria or definitionOfDone")
    index = payload.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError("Request body field index must be a positive integer")
    checked = payload.get("checked")
    if not isinstance(checked, bool):
        raise ValueError("Request body field checked must be a boolean")
    if section == "acceptanceCriteria":
        return {"check_ac" if checked else "uncheck_ac": [index]}
    return {"check_dod" if checked else "uncheck_dod": [index]}


def _dod_defaults_items_from_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return _required_string_list_field(payload, "items")


def _config_settings_payload(config: BacklogConfig) -> dict[str, object]:
    return {
        "autoOpenBrowser": config.auto_open_browser,
        "dateFormat": config.date_format,
        "defaultAssignee": config.default_assignee,
        "defaultPort": config.default_port,
        "defaultStatus": config.default_status,
        "includeDatetimeInDates": config.include_datetime_in_dates,
        "projectName": config.project_name,
        "statuses": list(config.statuses or []),
        "zeroPaddedIds": config.zero_padded_ids,
    }


def _config_settings_from_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Request body field settings must be a JSON object")

    updates: dict[str, str] = {}
    for raw_key, raw_value in settings.items():
        if not isinstance(raw_key, str) or raw_key not in _BROWSER_CONFIG_SETTING_KEYS:
            raise ValueError(f"Unsupported browser config setting: {raw_key}")
        if raw_key in {"projectName", "defaultStatus", "dateFormat"}:
            updates[raw_key] = _required_string_setting(raw_value, raw_key)
        elif raw_key == "defaultAssignee":
            updates[raw_key] = _optional_string_setting(raw_value, raw_key)
        elif raw_key in {"autoOpenBrowser", "includeDatetimeInDates"}:
            updates[raw_key] = _boolean_setting(raw_value, raw_key)
        elif raw_key == "defaultPort":
            updates[raw_key] = _port_setting(raw_value, raw_key)
        elif raw_key == "zeroPaddedIds":
            updates[raw_key] = _zero_padded_ids_setting(raw_value, raw_key)
        elif raw_key == "statuses":
            updates[raw_key] = _statuses_setting(raw_value, raw_key)
    if not updates:
        raise ValueError("Request body field settings must include at least one setting")
    return updates


def _required_string_setting(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Request body setting {field} must be a non-empty string")
    return value.strip()


def _optional_string_setting(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Request body setting {field} must be a string")
    return value.strip()


def _boolean_setting(value: object, field: str) -> str:
    if not isinstance(value, bool):
        raise ValueError(f"Request body setting {field} must be a boolean")
    return "true" if value else "false"


def _port_setting(value: object, field: str) -> str:
    parsed = _integer_setting(value, field)
    if parsed < 1 or parsed > 65535:
        raise ValueError(f"Request body setting {field} must be a valid port number (1-65535)")
    return str(parsed)


def _zero_padded_ids_setting(value: object, field: str) -> str:
    if value is None:
        return "0"
    if isinstance(value, str) and not value.strip():
        return "0"
    parsed = _integer_setting(value, field)
    if parsed < 0:
        raise ValueError(f"Request body setting {field} must be a non-negative integer")
    return str(parsed)


def _integer_setting(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Request body setting {field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError as exc:
            raise ValueError(f"Request body setting {field} must be an integer") from exc
    raise ValueError(f"Request body setting {field} must be an integer")


def _statuses_setting(value: object, field: str) -> str:
    if isinstance(value, str):
        separator = "\n" if "\n" in value else ","
        raw_items: list[object] = value.split(separator)
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError(f"Request body setting {field} must be a list of strings")

    statuses: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            raise ValueError(f"Request body setting {field} must be a list of strings")
        status = item.strip()
        if status:
            statuses.append(status)
    if not statuses:
        raise ValueError(f"Request body setting {field} must include at least one status")
    return json.dumps(statuses)


def _required_string_field(payload: dict[object, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Request body field {field} must be a non-empty string")
    return value.strip()


def _required_text_field(payload: dict[object, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Request body field {field} must be a string")
    return value.strip()


def _required_string_list_field(payload: dict[object, object], field: str) -> list[str]:
    if field not in payload:
        raise ValueError(f"Request body field {field} must be a list of strings")
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"Request body field {field} must be a list of strings")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"Request body field {field} must be a list of strings")
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _optional_string_field(payload: dict[object, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Request body field {field} must be a string")
    normalized = value.strip()
    return normalized or None


def _optional_string_list_field(payload: dict[object, object], field: str) -> list[str] | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"Request body field {field} must be a list of strings")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"Request body field {field} must be a list of strings")
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized
