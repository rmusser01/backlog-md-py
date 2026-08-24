from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
import re
import threading
import webbrowser
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from importlib.resources import files
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger

from backlog_py.core.decisions import DecisionRecord, DecisionService
from backlog_py.core.documents import DocumentMutationError, DocumentRecord, DocumentService
from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError, TaskRecord
from backlog_py.orchestration import (
    OrchestrationQueueItem,
    OrchestrationService,
    OrchestrationRunEvent,
    ValidationIssue,
    parse_run_history,
)
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.security.http import LOOPBACK_HOSTNAMES, host_header_is_loopback
from backlog_py.storage.config import (
    get_definition_of_done_defaults,
    load_config,
    normalize_definition_of_done_defaults,
    replace_definition_of_done_defaults,
    set_config_value,
)

# Sourced from the shared helper so the browser and MCP servers cannot drift
# on what counts as loopback.
_LOOPBACK_HOSTS = LOOPBACK_HOSTNAMES
# Cap request bodies so an oversized Content-Length cannot exhaust memory, and
# bound the socket so a slow/idle client cannot pin a handler thread forever.
_MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 30
# Mermaid diagram support. By default the board loads a locally vendored,
# self-contained UMD build served from this process, so no third-party request
# is ever made (privacy-respecting). Override with a URL (e.g. a CDN or a newer
# local copy) or set the env var to an empty string to disable rendering.
_BROWSER_MERMAID_ASSET_PATH = "/assets/mermaid.min.js"
_BROWSER_MERMAID_DEFAULT_URL = _BROWSER_MERMAID_ASSET_PATH
_BROWSER_MERMAID_URL_ENV = "BACKLOG_PY_BROWSER_MERMAID_URL"
# Optional subresource-integrity digest (e.g. "sha384-...") applied only when a
# remote Mermaid URL is configured; the CSP is widened to that origin too.
_BROWSER_MERMAID_SRI_ENV = "BACKLOG_PY_BROWSER_MERMAID_SRI"
# Hosts/ports that may appear verbatim in a Content-Security-Policy source.
# Either a registered name / IPv4 literal, or a bracketed IPv6 literal such as
# `[::1]:8080` - a perfectly ordinary local Mermaid server. Rejecting those used
# to drop the origin from script-src while still emitting the URL as the script
# `src`, so diagrams failed with a CSP violation rather than a clear error.
# Neither alternative can contain a space or a `;`, so no extra CSP source or
# directive can be injected through the env var.
_CSP_SOURCE_PATTERN = re.compile(r"(?:[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]+)?")
# One or more space-separated integrity digests. The digest body accepts base64
# and base64url (`-`/`_`, which some CDNs publish) plus the spec's optional
# `?option` suffix. Option characters are restricted to an unambiguous subset of
# VCHAR: the value is HTML-escaped before it reaches the attribute, but there is
# no reason to accept quotes or angle brackets here in the first place.
_SRI_DIGEST_SOURCE = r"sha(?:256|384|512)-[A-Za-z0-9+/_-]+={0,2}(?:\?[A-Za-z0-9._~%+/=-]*)?"
_SRI_PATTERN = re.compile(rf"{_SRI_DIGEST_SOURCE}(?: {_SRI_DIGEST_SOURCE})*")
_BOARD_REVISION_RETRY_MS = 5000
_BROWSER_CONFIG_SETTING_KEYS = frozenset(
    (
        "activeBranchDays",
        "autoCommit",
        "autoOpenBrowser",
        "checkActiveBranches",
        "dateFormat",
        "defaultAssignee",
        "defaultPort",
        "defaultStatus",
        "includeDatetimeInDates",
        "projectName",
        "remoteOperations",
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
    shutdown_in_progress: bool
    shutdown_lock: threading.Lock
    shutdown_requested_at: str | None


def create_browser_server(*, project: BacklogProject, host: str, port: int) -> BrowserThreadingHTTPServer:
    """Create a loopback browser HTTP server without starting it."""
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("Browser service only supports loopback hosts")
    server = BrowserThreadingHTTPServer((host, port), _BrowserHttpHandler)
    server.project = project
    server.request_log_limit = 50
    server.request_log = deque(maxlen=server.request_log_limit)
    server.request_log_lock = threading.Lock()
    server.shutdown_in_progress = False
    server.shutdown_requested_at = None
    server.shutdown_lock = threading.Lock()
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


def build_board_payload(project: BacklogProject, *, queue_category_filter: str | None = None) -> dict[str, object]:
    """Return a JSON-serializable board snapshot for the browser service."""
    repository = ReadOnlyRepository(project, refresh_remote_refs=False)
    board = repository.board()
    # Reuse the scan just done: the queue would otherwise build its own
    # repository and parse every task file a second time, on every request.
    queue_report = OrchestrationService(project).queue(repository=repository)
    queue_items = {item.task_id.casefold(): item for item in queue_report.items}
    category_filter = _normalize_queue_category_filter(queue_category_filter)
    unfiltered_columns = {
        status: [
            _task_payload(task, project=project, queue_item=queue_items.get(task.id.casefold()))
            for task in tasks
        ]
        for status, tasks in board.items()
    }
    payload: dict[str, object] = {
        "project": {
            "name": project.config.project_name,
            "root": str(project.root),
            "backlogDir": str(project.backlog_dir),
        },
        "statuses": list(board.keys()),
        "queueCategories": sorted(queue_report.by_category),
        "queueCategoryFilter": category_filter,
        "columns": {
            status: [task for task in tasks if _matches_queue_category_payload(task, category_filter)]
            for status, tasks in unfiltered_columns.items()
        },
    }
    payload["revision"] = _board_revision({**payload, "queueCategoryFilter": None, "columns": unfiltered_columns})
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
        **_shutdown_state_payload(server),
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


def _shutdown_state_payload(server: BrowserThreadingHTTPServer) -> dict[str, object]:
    with server.shutdown_lock:
        return {
            "shutdownInProgress": server.shutdown_in_progress,
            "shutdownRequestedAt": server.shutdown_requested_at,
        }


def _request_server_shutdown(server: BrowserThreadingHTTPServer) -> bool:
    with server.shutdown_lock:
        already_scheduled = server.shutdown_in_progress
        if server.shutdown_requested_at is None:
            server.shutdown_requested_at = _utc_timestamp()
        server.shutdown_in_progress = True
    if not already_scheduled:
        _schedule_server_shutdown(server)
    return already_scheduled


class _BrowserHttpHandler(BaseHTTPRequestHandler):
    server: BrowserThreadingHTTPServer
    timeout = _REQUEST_TIMEOUT_SECONDS

    def do_GET(self) -> None:
        try:
            if not self._host_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            self._handle_get()
        except Exception:
            # A single unreadable file or bad config must not drop the
            # connection with a bare traceback; return a 500 error page.
            logger.exception("Unhandled error serving GET {}", self.path)
            self._safe_send_error()

    def do_HEAD(self) -> None:
        # Same routing and same headers as GET; the body is suppressed in
        # `_send_text` / `_send_cached_asset` / `_send_board_event`. Without a
        # handler, `BaseHTTPRequestHandler` answers `curl -I` with a bare 501
        # that never reaches the Host check or the security headers.
        self.do_GET()

    def do_OPTIONS(self) -> None:
        self._reject_unsupported_method()

    def do_PUT(self) -> None:
        self._reject_unsupported_method()

    def do_PATCH(self) -> None:
        self._reject_unsupported_method()

    def do_DELETE(self) -> None:
        self._reject_unsupported_method()

    def _reject_unsupported_method(self) -> None:
        """Answer an unsupported verb through the normal response pipeline.

        The stdlib's fallback (`send_error(501)` straight out of
        `handle_one_request`) runs before any `do_*` method, so it skipped both
        the Host check and every security header. Verbs with no handler at all
        still land there, which is why `send_response` is overridden as well.
        """
        if not self._host_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "Method not allowed"},
            extra_headers=(("Allow", "GET, POST"),),
        )

    def send_response(self, code: int, message: str | None = None) -> None:
        """Emit the security headers on *every* response, including errors.

        `send_error` (unknown verb, malformed request line, oversized header
        block) builds its response without going through the senders below, so
        hooking the one call they all share is what makes "applied on every
        request" true rather than aspirational.
        """
        super().send_response(code, message)
        self._send_security_headers()

    def _send_readonly_listing_error(self, subject: str, exc: BaseException) -> None:
        """Return a well-formed error for a readonly route instead of a bare 500.

        A single unreadable or malformed file should describe itself rather
        than turning the whole panel into an opaque "Internal server error".
        """
        logger.warning("Failed to render browser {} view: {}", subject, exc)
        self._send_json(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"error": f"Unable to read {subject}: {exc}"},
        )

    def _safe_send_error(self) -> None:
        try:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})
        except Exception:
            logger.debug("Failed to send fallback browser error response", exc_info=True)

    def _handle_get(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/favicon.ico":
            self._send_empty(HTTPStatus.NO_CONTENT, content_type="image/x-icon")
            return
        if path == _BROWSER_MERMAID_ASSET_PATH:
            self._send_cached_asset(
                _vendored_mermaid_source(),
                content_type="application/javascript; charset=utf-8",
            )
            return
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "projectName": self.server.project.config.project_name})
            return
        if path == "/api/service/status":
            self._send_json(HTTPStatus.OK, _service_status_payload(self.server))
            return
        if path == "/api/service/requests":
            self._send_json(HTTPStatus.OK, _service_requests_payload(self.server))
            return
        if path == "/api/board/events":
            self._send_board_event()
            return
        if path == "/api/board":
            self._send_json(
                HTTPStatus.OK,
                build_board_payload(
                    self.server.project,
                    queue_category_filter=_query_value(parsed_url.query, "queueCategory"),
                ),
            )
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
        if path in {"/api/docs", "/api/docs/"}:
            try:
                payload = _document_list_payload(self.server.project)
            except Exception as exc:  # defence in depth: one bad doc must not kill the panel
                self._send_readonly_listing_error("documents", exc)
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        document_id = _document_endpoint_id(path)
        if document_id is not None:
            try:
                document = DocumentService(self.server.project).view_document(document_id)
            except DocumentMutationError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid document path"})
                return
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Document not found: {document_id}"})
                return
            except Exception as exc:
                self._send_readonly_listing_error(f"document {document_id}", exc)
                return
            self._send_json(HTTPStatus.OK, _document_detail_payload(document))
            return
        if path in {"/api/decisions", "/api/decisions/"}:
            try:
                payload = _decision_list_payload(self.server.project)
            except Exception as exc:
                self._send_readonly_listing_error("decisions", exc)
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        decision_id = _decision_endpoint_id(path)
        if decision_id is not None:
            try:
                decision = DecisionService(self.server.project).view_decision(decision_id)
            except KeyError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Decision not found: {decision_id}"})
                return
            except Exception as exc:
                self._send_readonly_listing_error(f"decision {decision_id}", exc)
                return
            self._send_json(HTTPStatus.OK, _decision_detail_payload(decision))
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
            self._send_html(
                HTTPStatus.OK,
                render_board_html(
                    self.server.project,
                    queue_category_filter=_query_value(parsed_url.query, "queueCategory"),
                ),
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return
        path = urlparse(self.path).path
        if path == "/api/markdown/preview":
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            try:
                payload = self._read_json_body()
                if not isinstance(payload, dict) or not isinstance(payload.get("markdown"), str):
                    raise ValueError("markdown must be a string")
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"html": _markdown_to_html(payload["markdown"])})
            return

        if path == "/api/service/shutdown":
            if not self._origin_allowed():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
                return
            already_scheduled = _request_server_shutdown(self.server)
            self._send_json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "message": "Shutdown already scheduled" if already_scheduled else "Shutdown scheduled",
                    "shutdownInProgress": True,
                    "alreadyScheduled": already_scheduled,
                },
            )
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
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            raise ValueError("Invalid Content-Length header")
        if length < 0:
            raise ValueError("Invalid Content-Length header")
        if length > _MAX_REQUEST_BODY_BYTES:
            raise ValueError("Request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _host_allowed(self) -> bool:
        """Reject requests whose Host is not this service's loopback authority.

        Browsers never send Origin on GET, so the Host header is the only
        defence against DNS rebinding: a page on ``attacker.example.com`` that
        resolves to 127.0.0.1 would otherwise be able to read the whole
        backlog. The hostname must be a loopback literal and the port must be
        the bound port (an absent port means the HTTP default, port 80).
        """
        return host_header_is_loopback(
            self.headers.get("Host"), int(self.server.server_address[1])
        )

    def _origin_allowed(self) -> bool:
        """Require a same-origin loopback Origin on every mutating request.

        Browsers always send Origin on POST, so requiring it costs the board
        nothing while denying unauthenticated local processes (and any tool
        that forgets the header) write access to the project.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return False
        host = self.headers.get("Host")
        if host is None:
            return False
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS and parsed.netloc == host

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, object],
        *,
        extra_headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._send_text(
            status,
            json.dumps(payload, sort_keys=True),
            content_type="application/json",
            extra_headers=extra_headers,
        )

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        self._send_text(status, html, content_type="text/html; charset=utf-8")

    def _send_empty(self, status: HTTPStatus, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", "0")
        self.end_headers()
        _record_service_request(
            self.server,
            method=self.command,
            raw_path=self.path,
            status=status,
            content_type=content_type,
        )

    def _send_board_event(self) -> None:
        shutdown_state = _shutdown_state_payload(self.server)
        if shutdown_state["shutdownInProgress"]:
            event = _board_shutdown_sse_event(shutdown_state)
        else:
            payload = build_board_payload(self.server.project)
            event = _board_revision_sse_event(str(payload.get("revision", "")))
        data = event.encode("utf-8")
        content_type = "text/event-stream; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._write_body(data)
        _record_service_request(
            self.server,
            method=self.command,
            raw_path=self.path,
            status=HTTPStatus.OK,
            content_type=content_type,
        )

    def _write_body(self, data: bytes) -> None:
        """Write a response body, except for HEAD, which is headers only."""
        if self.command == "HEAD":
            return
        self.wfile.write(data)

    def _send_security_headers(self) -> None:
        # Purely defensive, additive headers: block MIME sniffing, framing
        # (clickjacking of the Stop/Archive controls), and referrer leakage.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _content_security_policy())

    def _send_cached_asset(self, text: str, *, content_type: str) -> None:
        data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # The vendored asset is version-pinned and immutable.
        self.send_header("Cache-Control", "public, max-age=86400, immutable")
        self.end_headers()
        self._write_body(data)
        _record_service_request(
            self.server,
            method=self.command,
            raw_path=self.path,
            status=HTTPStatus.OK,
            content_type=content_type,
        )

    def _send_text(
        self,
        status: HTTPStatus,
        text: str,
        *,
        content_type: str,
        extra_headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self._write_body(data)
        _record_service_request(
            self.server,
            method=self.command,
            raw_path=self.path,
            status=status,
            content_type=content_type,
        )


def _load_browser_text_resource(*path_parts: str) -> str:
    """Load a packaged browser template or asset as UTF-8 text."""
    return files("backlog_py.browser").joinpath(*path_parts).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _vendored_mermaid_source() -> str:
    """Return the vendored Mermaid build, read once (it is a large, static asset)."""
    return _load_browser_text_resource("assets", "mermaid.min.js")


def render_board_html(project: BacklogProject, *, queue_category_filter: str | None = None) -> str:
    """Render a browser board with basic task creation, editing, and status movement."""
    payload = build_board_payload(project, queue_category_filter=queue_category_filter)
    project_name = escape(project.config.project_name)
    board_revision = escape(str(payload.get("revision", "")))
    columns_obj = payload["columns"]
    columns = columns_obj if isinstance(columns_obj, dict) else {}
    column_markup = "\n".join(
        _render_column(str(status), tasks)
        for status, tasks in columns.items()
    )
    task_create_description_editor = _render_markdown_editor(
        field_id="task-create-description",
        name="description",
        label="Description",
        data_field="description",
    )
    task_edit_description_editor = _render_markdown_editor(
        field_id="task-edit-description",
        name="description",
        label="Description",
        data_field="description",
    )
    task_edit_implementation_notes_editor = _render_markdown_editor(
        field_id="task-edit-implementation-notes",
        name="implementationNotes",
        label="Implementation Notes",
        data_field="implementationNotes",
    )
    task_edit_final_summary_editor = _render_markdown_editor(
        field_id="task-edit-final-summary",
        name="finalSummary",
        label="Final Summary",
        data_field="finalSummary",
    )
    select_tag = "select"
    status_options = _render_status_options(project.config.statuses or [])
    queue_filter = _render_queue_category_filter(
        categories=list(payload.get("queueCategories") or []),
        selected=_metadata_string(payload.get("queueCategoryFilter")),
    )
    escaped_mermaid_url = escape(_resolve_mermaid_url(), quote=True)
    html = _load_browser_text_resource("templates", "board.html").format_map(
        {
            "project_name": project_name,
            "board_revision": board_revision,
            "column_markup": column_markup,
            "task_create_description_editor": task_create_description_editor,
            "task_edit_description_editor": task_edit_description_editor,
            "task_edit_implementation_notes_editor": task_edit_implementation_notes_editor,
            "task_edit_final_summary_editor": task_edit_final_summary_editor,
            "select_tag": select_tag,
            "status_options": status_options,
            "queue_filter": queue_filter,
            "board_css": _load_browser_text_resource("assets", "board.css").rstrip("\n"),
            "board_js": _load_browser_text_resource("assets", "board.js").rstrip("\n"),
            "mermaid_url": escaped_mermaid_url,
        }
    )
    return _with_mermaid_integrity_attribute(html, escaped_url=escaped_mermaid_url)


def _with_mermaid_integrity_attribute(html: str, *, escaped_url: str) -> str:
    """Attach ``data-mermaid-sri`` next to ``data-mermaid-url`` when configured.

    The board template only exposes a ``{mermaid_url}`` placeholder, so the
    optional integrity value is attached to the same element here; the default
    (local, same-origin) asset renders exactly as before.
    """
    remote_origin = _mermaid_script_origin()
    if remote_origin is None:
        # Integrity only means something for a third-party build: gating the
        # same-origin vendored asset (or the disabled/empty URL) on a digest
        # the operator never set for it would silently break diagrams.
        return html
    integrity = _resolve_mermaid_integrity()
    if not integrity:
        _warn_unverified_remote_mermaid(remote_origin)
        return html
    marker = f'data-mermaid-url="{escaped_url}"'
    return html.replace(marker, f'{marker} data-mermaid-sri="{escape(integrity, quote=True)}"', 1)


def _warn_unverified_remote_mermaid(remote_origin: str) -> None:
    """Say out loud that third-party script is being loaded unverified.

    A missing digest and a rejected one both end up as "no integrity attribute",
    so without this a single typo in the digest downgrades a verified load to an
    unverified one with nothing in the logs to show for it.
    """
    raw = (os.environ.get(_BROWSER_MERMAID_SRI_ENV) or "").strip()
    reason = (
        f"no {_BROWSER_MERMAID_SRI_ENV} is set"
        if not raw
        else f"the configured {_BROWSER_MERMAID_SRI_ENV} value is malformed and was rejected"
    )
    logger.warning(
        "Loading Mermaid from remote origin {} without subresource integrity: {}. "
        "The board will execute whatever that origin serves. Set {} to a "
        "'sha384-<base64>' digest, or unset {} to use the vendored local build.",
        remote_origin,
        reason,
        _BROWSER_MERMAID_SRI_ENV,
        _BROWSER_MERMAID_URL_ENV,
    )


def _resolve_mermaid_integrity() -> str:
    """Return a validated subresource-integrity value for a remote Mermaid URL.

    Malformed values are ignored rather than emitted, so a bad env var can
    never break the attribute or inject markup. Callers must not read a ``""``
    result as "no integrity was asked for" - see
    ``_warn_unverified_remote_mermaid``, which reports both cases.
    """
    value = (os.environ.get(_BROWSER_MERMAID_SRI_ENV) or "").strip()
    if not value or not _SRI_PATTERN.fullmatch(value):
        return ""
    return value


def _resolve_mermaid_url() -> str:
    """Resolve the Mermaid module URL; empty string disables diagram rendering."""
    value = os.environ.get(_BROWSER_MERMAID_URL_ENV)
    if value is None:
        return _BROWSER_MERMAID_DEFAULT_URL
    return value.strip()


def _mermaid_script_origin() -> str | None:
    """Return the CSP source for a remotely configured Mermaid build.

    The default (and any other same-origin path) needs no extra source because
    ``script-src 'self'`` already covers it.
    """
    url = _resolve_mermaid_url()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if not _CSP_SOURCE_PATTERN.fullmatch(parsed.netloc):
        # Never let a hostile env value inject extra directives or sources.
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _content_security_policy() -> str:
    """Build the board's CSP, widened only for a configured remote Mermaid.

    The board inlines its own CSS/JS (``format_map`` into board.html), fetches
    same-origin JSON, opens an EventSource on /api/board/events and loads the
    vendored Mermaid build from /assets, so 'self' plus 'unsafe-inline' is the
    tightest policy that keeps the page working without a nonce pipeline.

    Be precise about what that buys, because ``'unsafe-inline'`` in
    ``script-src`` is doing most of the work here:

    * It does **not** meaningfully mitigate script *execution*. Any injected
      inline script element or event-handler attribute runs exactly as it would
      with no policy at all. Output escaping (``html.escape`` on every rendered
      value) remains the only defence against XSS on this page; treat the CSP as
      a second line, not the first.
    * What it does buy is *containment*. ``default-src 'none'`` plus
      ``connect-src 'self'`` means injected script cannot exfiltrate the backlog
      to a third-party host, and cannot pull in a remote payload: every fetch,
      XHR, EventSource, image, font and frame target must be same-origin (only
      an explicitly configured Mermaid origin is added, and only to
      ``script-src``).
    * ``frame-ancestors 'none'`` blocks clickjacking of the Stop/Archive
      controls, ``base-uri 'none'`` stops an injected base element from
      re-pointing every relative URL, and ``form-action 'self'`` stops a planted
      form from POSTing project contents elsewhere.

    Closing the execution gap needs a nonce (or hash) pipeline over board.html,
    which is a larger change than this policy.
    """
    script_sources = ["'self'", "'unsafe-inline'"]
    remote_origin = _mermaid_script_origin()
    if remote_origin is not None:
        script_sources.append(remote_origin)
    directives = (
        "default-src 'none'",
        f"script-src {' '.join(script_sources)}",
        # 'self' is redundant while the CSS is inlined, but the Mermaid bundle
        # already ships from /assets: the first stylesheet that follows it there
        # would otherwise be blocked by a policy that never allows same-origin
        # styles.
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
    return "; ".join(directives)


def _render_markdown_toolbar() -> str:
    return (
        '<span class="markdown-toolbar" data-markdown-toolbar="true" role="toolbar" '
        'aria-label="Markdown formatting">'
        '<button type="button" data-markdown-command="bold" aria-label="Bold"><strong>B</strong></button>'
        '<button type="button" data-markdown-command="italic" aria-label="Italic"><em>I</em></button>'
        '<button type="button" data-markdown-command="code" aria-label="Inline code">`</button>'
        '<button type="button" data-markdown-command="bullet" aria-label="Bullet list">-</button>'
        '<button type="button" data-markdown-command="numbered" aria-label="Numbered list">1.</button>'
        '<button type="button" data-markdown-command="heading" aria-label="Heading">H</button>'
        '<button type="button" data-markdown-command="link" aria-label="Link">Link</button>'
        "</span>"
    )


def _render_markdown_editor(*, field_id: str, name: str, label: str, data_field: str) -> str:
    escaped_id = escape(field_id)
    escaped_name = escape(name)
    escaped_label = escape(label)
    escaped_data_field = escape(data_field)
    toolbar = _render_markdown_toolbar()
    return f"""<div class="task-form-field markdown-editor" data-markdown-editor="true" data-markdown-mode="edit">
          <label for="{escaped_id}">{escaped_label}</label>
          <div class="markdown-editor-tabs" role="tablist" aria-label="{escaped_label} mode">
            <button type="button" role="tab" data-markdown-mode="edit" data-markdown-target="{escaped_id}" aria-selected="true">Edit</button>
            <button type="button" role="tab" data-markdown-mode="preview" data-markdown-target="{escaped_id}" aria-selected="false">Preview</button>
            <button type="button" role="tab" data-markdown-mode="rich" data-markdown-target="{escaped_id}" aria-selected="false">Rich</button>
          </div>
          {toolbar}
          <textarea id="{escaped_id}" name="{escaped_name}" data-markdown-input="true" data-markdown-field="{escaped_data_field}"></textarea>
          <div class="markdown-preview markdown-body" data-markdown-preview-for="{escaped_id}" hidden></div>
          <div class="markdown-rich-editor markdown-body" data-markdown-rich-for="{escaped_id}" contenteditable="true" hidden></div>
        </div>"""


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
    queue_category = _metadata_string(task.get("queueCategory"))
    meta = _render_task_meta(
        priority=priority,
        assignees=assignees if isinstance(assignees, list) else [],
        labels=labels if isinstance(labels, list) else [],
        queue_category=queue_category,
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


def _board_revision_sse_event(revision: str) -> str:
    payload = json.dumps({"revision": revision}, sort_keys=True)
    return f"retry: {_BOARD_REVISION_RETRY_MS}\nevent: revision\ndata: {payload}\n\n"


def _board_shutdown_sse_event(shutdown_state: Mapping[str, object]) -> str:
    payload = json.dumps(shutdown_state, sort_keys=True)
    return f"retry: {_BOARD_REVISION_RETRY_MS}\nevent: shutdown\ndata: {payload}\n\n"


def _render_status_options(statuses: list[str]) -> str:
    return "".join(f'<option value="{escape(status)}">{escape(status)}</option>' for status in statuses)


def _render_queue_category_filter(*, categories: list[object], selected: str | None) -> str:
    option_markup = ['<option value="">All queue states</option>']
    for category in categories:
        category_text = str(category)
        selected_attr = ' selected' if selected and selected == category_text else ""
        option_markup.append(
            f'<option value="{escape(category_text)}"{selected_attr}>{escape(_queue_category_label(category_text))}</option>'
        )
    return (
        '<form class="queue-filter" method="get">'
        '<label for="queue-category-filter">Queue</label>'
        f'<select id="queue-category-filter" name="queueCategory">{"".join(option_markup)}</select>'
        '<button class="secondary-button" type="submit">Filter</button>'
        "</form>"
    )


def _document_list_payload(project: BacklogProject) -> list[dict[str, object]]:
    return [_document_summary_payload(document) for document in DocumentService(project).list_documents()]


def _document_summary_payload(document: DocumentRecord) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "type": _metadata_string(document.frontmatter.get("type")),
        "path": document.path_relative,
        "tags": _metadata_list(document.frontmatter.get("tags")),
    }


def _document_detail_payload(document: DocumentRecord) -> dict[str, object]:
    payload = _document_summary_payload(document)
    payload.update(
        {
            "content": document.content,
            "contentHtml": _markdown_to_html(_document_content_for_html(document)),
        }
    )
    return payload


def _normalized_document_title(value: str) -> str:
    return " ".join(value.split())


def _document_content_for_html(document: DocumentRecord) -> str:
    lines = document.content.splitlines(keepends=True)
    if not lines:
        return document.content
    heading = lines[0].rstrip("\r\n")
    if not heading.startswith("# "):
        return document.content
    heading_text = heading[2:].strip()
    if not heading_text or _normalized_document_title(document.title) != _normalized_document_title(heading_text):
        return document.content
    return "".join(lines[1:]).lstrip("\r\n")


def _decision_list_payload(project: BacklogProject) -> list[dict[str, object]]:
    return [_decision_summary_payload(decision) for decision in DecisionService(project).list_decisions()]


def _decision_summary_payload(decision: DecisionRecord) -> dict[str, object]:
    return {
        "id": decision.id,
        "title": decision.title,
        "status": decision.status,
        "date": decision.date,
    }


def _decision_detail_payload(decision: DecisionRecord) -> dict[str, object]:
    payload = _decision_summary_payload(decision)
    payload.update(
        {
            "path": decision.path_relative,
            "context": decision.context,
            "contextHtml": _markdown_to_html(decision.context),
            "decision": decision.decision,
            "decisionHtml": _markdown_to_html(decision.decision),
            "consequences": decision.consequences,
            "consequencesHtml": _markdown_to_html(decision.consequences),
            "alternatives": decision.alternatives,
            "alternativesHtml": _markdown_to_html(decision.alternatives or ""),
        }
    )
    return payload


def _task_payload(
    task: TaskRecord,
    *,
    project: BacklogProject,
    queue_item: OrchestrationQueueItem | None = None,
) -> dict[str, object]:
    frontmatter = task.parsed.frontmatter
    payload: dict[str, object] = {
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
    if queue_item is None:
        queue_item = _queue_item_for_task(project, task.id)
    if queue_item is not None:
        payload.update(_queue_item_payload(queue_item))
    return payload


def _task_detail_payload(task: TaskRecord, *, project: BacklogProject) -> dict[str, object]:
    payload = _task_payload(task, project=project)
    description = task.description_or_legacy_body
    implementation_notes = _section_content(task, "IMPLEMENTATION_NOTES")
    final_summary = _section_content(task, "FINAL_SUMMARY")
    run_history = parse_run_history(task.raw_source)
    payload.update(
        {
            "description": description,
            "descriptionHtml": _markdown_to_html(description) if description else "",
            "implementationNotes": implementation_notes,
            "implementationNotesHtml": _markdown_to_html(implementation_notes),
            "finalSummary": final_summary,
            "finalSummaryHtml": _markdown_to_html(final_summary),
            "acceptanceCriteria": _checklist_payload(task, "AC"),
            "definitionOfDone": _checklist_payload(task, "DOD"),
            "runHistoryEvents": [_run_history_event_payload(event) for event in run_history.events],
            "runHistoryIssues": [
                _validation_issue_payload(ValidationIssue(issue.code, issue.message, issue.location or "run_history"))
                for issue in run_history.issues
            ],
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
        if _normalized_markdown_language(code_language) == "mermaid":
            blocks.append(
                '<div class="mermaid-diagram" data-mermaid-diagram="true">'
                f'<div class="mermaid">{code}</div>'
                "</div>"
            )
        else:
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
    normalized = _normalized_markdown_language(language)
    return f" language-{normalized}" if normalized else ""


def _normalized_markdown_language(language: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", language.strip().lower())


def _render_inline_markdown(text: str) -> str:
    rendered_parts: list[str] = []
    parts = re.split(r"(`[^`]+`)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("`") and part.endswith("`"):
            rendered_parts.append(f"<code>{escape(part[1:-1])}</code>")
            continue
        rendered_parts.append(_render_inline_markdown_segment(part))
    return "".join(rendered_parts)


def _render_inline_markdown_segment(text: str) -> str:
    rendered = escape(text)
    rendered = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _render_markdown_link, rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", rendered)
    return rendered


def _render_markdown_link(match: re.Match[str]) -> str:
    label = match.group(1)
    href = _safe_markdown_href(match.group(2))
    return f'<a href="{href}">{label}</a>'


def _safe_markdown_href(href: str) -> str:
    value = href.strip()
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", value)
    if scheme and scheme.group(1).lower() not in {"http", "https", "mailto"}:
        return "#"
    return value


def _render_task_meta(
    *,
    priority: str | None,
    assignees: list[object],
    labels: list[object],
    queue_category: str | None = None,
) -> str:
    badges: list[str] = []
    if queue_category:
        badges.append(
            f'<span class="badge queue-badge" data-queue-category="{escape(queue_category)}">'
            f'{escape(_queue_category_label(queue_category))}</span>'
        )
    if priority:
        badges.append(f'<span class="badge">Priority: {escape(priority)}</span>')
    for assignee in assignees[:2]:
        badges.append(f'<span class="badge">@{escape(str(assignee).lstrip("@"))}</span>')
    for label in labels[:2]:
        badges.append(f'<span class="badge">{escape(str(label))}</span>')
    if not badges:
        return ""
    return f'        <div class="task-meta">{"".join(badges)}</div>'


def _queue_item_for_task(project: BacklogProject, task_id: str) -> OrchestrationQueueItem | None:
    normalized = task_id.casefold()
    for item in OrchestrationService(project).queue(include_completed=True).items:
        if item.task_id.casefold() == normalized:
            return item
    return None


def _queue_item_payload(item: OrchestrationQueueItem) -> dict[str, object]:
    return {
        "orchestrationVersion": item.version,
        "effectiveStatus": item.effective_status,
        "queueCategory": item.category,
        "validationIssues": [_validation_issue_payload(issue) for issue in item.validation_issues],
        "runHistoryIssues": [_validation_issue_payload(issue) for issue in item.run_history_issues],
        "dependencyIds": list(item.dependency_ids),
        "leaseOwner": item.lease_owner,
        "leaseExpiresAt": item.lease_expires_at,
    }


def _validation_issue_payload(issue: ValidationIssue) -> dict[str, str]:
    return {
        "code": issue.code,
        "message": issue.message,
        "path": issue.path,
        "severity": issue.severity,
    }


def _run_history_event_payload(event: OrchestrationRunEvent) -> dict[str, object]:
    return {
        "eventId": event.event_id,
        "type": event.type,
        "actor": event.actor,
        "timestamp": event.timestamp,
        "result": event.result,
        "summary": event.summary,
        "taskId": event.task_id,
        "fromStatus": event.from_status,
        "toStatus": event.to_status,
        "splitMode": event.split_mode,
        "files": list(event.files),
        "verification": list(event.verification),
        "metadata": dict(event.metadata),
    }


def _normalize_queue_category_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _matches_queue_category_payload(task: object, category: str | None) -> bool:
    if category is None:
        return True
    if not isinstance(task, Mapping):
        return False
    task_category = _metadata_string(task.get("queueCategory"))
    return task_category is not None and task_category.casefold() == category.casefold()


def _queue_category_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _query_value(query: str, key: str) -> str | None:
    values = parse_qs(query, keep_blank_values=True).get(key)
    if not values:
        return None
    return values[-1]


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


def _endpoint_segment(path: str, prefix: str, suffix: str = "", *, allow_separators: bool = False) -> str | None:
    """Extract and percent-decode a single endpoint id from a request path.

    The decoded value is validated (decode first, *then* check), so an encoded
    separator such as ``%2F`` can never slip past the check the way it does
    with the classic check-then-decode ordering.
    """
    if not path.startswith(prefix):
        return None
    if suffix:
        if not path.endswith(suffix):
            return None
        encoded = path[len(prefix) : -len(suffix)]
    else:
        encoded = path[len(prefix) :]
    if not encoded or "/" in encoded:
        return None
    identifier = unquote(encoded)
    if not identifier or "\x00" in identifier:
        return None
    if not allow_separators and ("/" in identifier or "\\" in identifier):
        return None
    return identifier


def _status_endpoint_task_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/tasks/", "/status")


def _task_edit_endpoint_task_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/tasks/", "/edit")


def _task_archive_endpoint_task_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/tasks/", "/archive")


def _task_checklist_endpoint_task_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/tasks/", "/checklist")


def _document_endpoint_id(path: str) -> str | None:
    # Documents are addressable by id *or* by their backlog-relative path
    # (``/api/docs/guides%2Fsetup.md``), so decoded separators stay legal here;
    # containment is enforced by DocumentService._document_path.
    return _endpoint_segment(path, "/api/docs/", allow_separators=True)


def _decision_endpoint_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/decisions/")


def _task_detail_endpoint_task_id(path: str) -> str | None:
    return _endpoint_segment(path, "/api/tasks/")


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
    for payload_key, repository_key in (("assignees", "assignees"), ("labels", "labels")):
        value = _optional_string_list_field(payload, payload_key)
        if value is not None:
            edit_kwargs[repository_key] = value
    if "priority" in payload:
        value = _optional_string_field(payload, "priority")
        if value is not None:
            edit_kwargs["priority"] = value
    if "milestone" in payload:
        value = payload.get("milestone")
        if value is None:
            edit_kwargs["clear_milestone"] = True
        elif not isinstance(value, str):
            raise ValueError("Request body field milestone must be a string")
        elif value.strip():
            edit_kwargs["milestone"] = value.strip()
        else:
            edit_kwargs["clear_milestone"] = True
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
    if "items" not in payload:
        raise ValueError("Request body field items must be a list of strings")
    return normalize_definition_of_done_defaults(payload["items"])


def _config_settings_payload(config: BacklogConfig) -> dict[str, object]:
    return {
        "activeBranchDays": config.active_branch_days,
        "autoCommit": config.auto_commit,
        "autoOpenBrowser": config.auto_open_browser,
        "checkActiveBranches": config.check_active_branches,
        "dateFormat": config.date_format,
        "defaultAssignee": config.default_assignee,
        "defaultPort": config.default_port,
        "defaultStatus": config.default_status,
        "includeDatetimeInDates": config.include_datetime_in_dates,
        "projectName": config.project_name,
        "remoteOperations": config.remote_operations,
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
        elif raw_key in {
            "autoCommit",
            "autoOpenBrowser",
            "checkActiveBranches",
            "includeDatetimeInDates",
            "remoteOperations",
        }:
            updates[raw_key] = _boolean_setting(raw_value, raw_key)
        elif raw_key == "defaultPort":
            updates[raw_key] = _port_setting(raw_value, raw_key)
        elif raw_key == "activeBranchDays":
            updates[raw_key] = _positive_integer_setting(raw_value, raw_key)
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


def _positive_integer_setting(value: object, field: str) -> str:
    parsed = _integer_setting(value, field)
    if parsed < 1:
        raise ValueError(f"Request body setting {field} must be a positive integer")
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
