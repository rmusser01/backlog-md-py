from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import unquote, urlparse

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError, TaskRecord
from backlog_py.runtime.locks import with_project_write_lock

_LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))


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


def create_browser_server(*, project: BacklogProject, host: str, port: int) -> BrowserThreadingHTTPServer:
    """Create a loopback browser HTTP server without starting it."""
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("Browser service only supports loopback hosts")
    server = BrowserThreadingHTTPServer((host, port), _BrowserHttpHandler)
    server.project = project
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
    repository = ReadOnlyRepository(project)
    board = repository.board()
    return {
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


class _BrowserHttpHandler(BaseHTTPRequestHandler):
    server: BrowserThreadingHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "projectName": self.server.project.config.project_name})
            return
        if path == "/api/board":
            self._send_json(HTTPStatus.OK, build_board_payload(self.server.project))
            return
        if path in {"", "/", "/index.html"}:
            self._send_html(HTTPStatus.OK, render_board_html(self.server.project))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
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


def render_board_html(project: BacklogProject) -> str:
    """Render a kanban board with drag-and-drop status movement."""
    payload = build_board_payload(project)
    project_name = escape(project.config.project_name)
    columns_obj = payload["columns"]
    columns = columns_obj if isinstance(columns_obj, dict) else {}
    column_markup = "\n".join(
        _render_column(str(status), tasks)
        for status, tasks in columns.items()
    )
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
    .empty {{
      color: var(--muted);
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 12px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{project_name}</h1>
    <div class="subtitle">Backlog.md board with drag-and-drop status movement</div>
  </header>
  <main class="board">
{column_markup}
  </main>
  <script>
    let draggedTaskId = null;
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
    return f"""      <article class="task" data-task-id="{task_id}" draggable="true">
        <div class="task-id">{task_id}</div>
        <div class="task-title">{title}</div>
      </article>"""


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
    }


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


def _status_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("Request body field status must be a non-empty string")
    return status
