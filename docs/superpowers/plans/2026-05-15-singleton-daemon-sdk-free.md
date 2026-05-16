# SDK-Free Singleton Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single local `backlog-md-py` daemon that multiple agents can use without spawning multiple Backlog.md process trees, while replacing the MCP SDK dependency with an SDK-free JSON-RPC MCP adapter.

**Architecture:** Implement shared runtime primitives first: state directories, runtime files, and cross-process project write locks. Then replace the FastMCP stdio path with a lightweight protocol adapter modeled after the relevant `tldw_server2` MCP JSON-RPC patterns, add daemon lifecycle commands, and expose either a direct loopback HTTP MCP endpoint or an SDK-free stdio shim that forwards to the singleton daemon.

**Tech Stack:** Python 3.11+ stdlib (`dataclasses`, `json`, `hashlib`, `contextlib`, `http.server` or `socketserver`, `subprocess`, `threading`, `fcntl`/`msvcrt`), existing Click CLI, existing Loguru, existing PyYAML, pytest. Do not use the Python `mcp` package or any MCP SDK.

---

## File Structure

- Create `src/backlog_py/runtime/__init__.py`
  - Public exports for state paths, runtime records, and lock helpers.
- Create `src/backlog_py/runtime/state.py`
  - State directory resolution, runtime-file read/write/delete helpers, token-safe status rendering, and log path allocation.
- Create `src/backlog_py/runtime/locks.py`
  - Cross-process daemon lock, project write locks, init-root locks, lock metadata, and timeout errors.
- Create `src/backlog_py/runtime/mutations.py`
  - Checked inventory of known project mutation surfaces and helpers for lock operation names.
- Create `src/backlog_py/mcp/protocol.py`
  - SDK-free JSON-RPC 2.0 request parsing, response rendering, batch handling, method dispatch, and MCP method handlers.
- Create `src/backlog_py/mcp/catalog.py`
  - SDK-free tool/resource metadata for existing Backlog.md MCP resources and tools.
- Create `src/backlog_py/mcp/stdio_server.py`
  - New implementation for `backlog-py-mcp`, with direct stdio mode and optional daemon-forwarding mode.
- Create `src/backlog_py/mcp/http_server.py`
  - Loopback JSON-RPC HTTP endpoint used by the singleton daemon.
- Create `src/backlog_py/daemon/__init__.py`
  - Public daemon lifecycle exports.
- Create `src/backlog_py/daemon/lifecycle.py`
  - `status`, `ensure`, `start`, `stop`, stale runtime cleanup, duplicate prevention, and process health checks.
- Create `src/backlog_py/daemon/service.py`
  - Foreground daemon service wiring: runtime file, HTTP server, active request tracking, and graceful shutdown.
- Modify `src/backlog_py/mcp/server.py`
  - Replace FastMCP-based implementation with a compatibility wrapper around `backlog_py.mcp.stdio_server`.
- Modify `src/backlog_py/mcp/tools.py`
  - Wrap write-capable MCP functions with shared project write locks.
- Modify `src/backlog_py/cli/main.py`
  - Add `daemon` command group and wrap CLI mutation paths with project/root locks.
- Modify `src/backlog_py/core/board_export.py`
  - Replace direct `Path.write_text` project writes with atomic write helpers.
- Modify `pyproject.toml`
  - Remove the `mcp` optional dependency if no other test/docs path still needs it; keep script names stable.
- Modify docs that reference `backlog-md-py[mcp]` or `--extra mcp`
  - Update setup and validation docs to reflect SDK-free MCP support.
- Test files:
  - Create `tests/test_runtime_state.py`
  - Create `tests/test_runtime_locks.py`
  - Create `tests/test_mutation_inventory.py`
  - Create `tests/test_mcp_protocol_sdk_free.py`
  - Create `tests/test_mcp_stdio_sdk_free.py`
  - Create `tests/test_daemon_lifecycle.py`
  - Create `tests/test_daemon_http_server.py`
  - Modify existing CLI/MCP mutation tests where lock assertions fit better with existing fixtures.

## Task 1: Runtime State Directories and Runtime Files

**Files:**
- Create: `src/backlog_py/runtime/__init__.py`
- Create: `src/backlog_py/runtime/state.py`
- Test: `tests/test_runtime_state.py`

- [ ] **Step 1: Write failing state-directory tests**

Add tests for override and platform defaults:

```python
def test_state_dir_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    assert resolve_state_dir() == tmp_path / "state"


def test_state_layout_creates_expected_subdirectories(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    layout = ensure_state_layout()

    assert layout.runtime_dir.is_dir()
    assert layout.locks_dir.is_dir()
    assert layout.logs_dir.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev python -m pytest tests/test_runtime_state.py -q`

Expected: FAIL because `backlog_py.runtime.state` does not exist.

- [ ] **Step 3: Implement state layout and runtime record model**

Create frozen dataclasses:

```python
@dataclass(frozen=True)
class StateLayout:
    root: Path
    runtime_dir: Path
    locks_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class RuntimeRecord:
    pid: int
    host: str
    port: int
    endpoint: str
    token: str
    started_at: str
    version: str
    log_path: Path
```

Implement:

- `resolve_state_dir(env=os.environ, platform=sys.platform, home=Path.home()) -> Path`
- `ensure_state_layout() -> StateLayout`
- `runtime_record_path(layout: StateLayout) -> Path`
- `write_runtime_record(record: RuntimeRecord, layout: StateLayout) -> None`
- `read_runtime_record(layout: StateLayout) -> RuntimeRecord | None`
- `runtime_status(record: RuntimeRecord) -> dict[str, object]` that omits token material
- `allocate_log_path(layout: StateLayout) -> Path`

Use `os.open(..., mode=0o600)` for token-bearing runtime files where possible.

- [ ] **Step 4: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_runtime_state.py -q`

Expected: PASS.

- [ ] **Step 5: Commit runtime state slice**

```bash
git add src/backlog_py/runtime tests/test_runtime_state.py
git commit -m "Add daemon runtime state helpers"
```

## Task 2: Cross-Process Locks and Mutation Inventory

**Files:**
- Create: `src/backlog_py/runtime/locks.py`
- Create: `src/backlog_py/runtime/mutations.py`
- Modify: `src/backlog_py/runtime/__init__.py`
- Test: `tests/test_runtime_locks.py`
- Test: `tests/test_mutation_inventory.py`

- [ ] **Step 1: Write failing lock tests**

Cover canonical lock keys, same-project serialization, different-project independence, timeout errors, and stale metadata behavior:

```python
def test_project_lock_key_uses_resolved_project_root(tmp_path):
    project = _project(tmp_path)
    link = tmp_path.parent / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)

    assert project_lock_key(project.root) == project_lock_key(link)


def test_lock_metadata_does_not_block_after_release(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    lock = ProjectWriteLock(tmp_path / "repo", operation="task_create")

    with lock.acquire(timeout=0.1):
        metadata_path = lock.metadata_path
        assert metadata_path.is_file()

    with lock.acquire(timeout=0.1):
        assert metadata_path.is_file()
```

- [ ] **Step 2: Write failing mutation inventory test**

Create an inventory test that must list current write surfaces:

```python
def test_mutation_inventory_covers_known_write_surfaces():
    names = {surface.name for surface in MUTATION_SURFACES}

    assert {
        "init_project",
        "task_create",
        "task_edit",
        "task_archive",
        "task_complete",
        "cleanup_complete_done",
        "draft_create",
        "draft_promote",
        "draft_demote",
        "draft_archive",
        "document_create",
        "document_update",
        "decision_create",
        "milestone_add",
        "milestone_rename",
        "milestone_remove",
        "milestone_archive",
        "config_set",
        "definition_of_done_defaults_upsert",
        "agents_update_instructions",
        "board_export_file",
        "board_export_readme",
    } <= names
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_runtime_locks.py tests/test_mutation_inventory.py -q`

Expected: FAIL because lock and inventory modules do not exist.

- [ ] **Step 4: Implement lock primitives**

Implement:

- `project_lock_key(project_root: Path) -> str`
- `init_lock_key(target_root: Path) -> str`
- `ProjectWriteLock`
- `DaemonRuntimeLock`
- `LockTimeoutError`
- `with_project_write_lock(project: BacklogProject, operation: str, fn: Callable[[], T]) -> T`
- `with_init_lock(target_root: Path, operation: str, fn: Callable[[], T]) -> T`

Use stdlib locking:

- POSIX: `fcntl.flock(file_handle, LOCK_EX | LOCK_NB)`
- Windows: `msvcrt.locking`

If Windows locking cannot be implemented cleanly in the first pass, keep the API cross-platform and mark the Windows branch with a focused test skip plus a tracked plan note before committing.

- [ ] **Step 5: Implement mutation inventory**

Create:

```python
@dataclass(frozen=True)
class MutationSurface:
    name: str
    entrypoints: tuple[str, ...]
    lock_scope: Literal["project", "init-root", "out-of-scope"]
    rationale: str
```

Populate `MUTATION_SURFACES` with the current CLI/MCP write surfaces. Add `mutation_by_name(name: str) -> MutationSurface`.

- [ ] **Step 6: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_runtime_locks.py tests/test_mutation_inventory.py -q`

Expected: PASS.

- [ ] **Step 7: Commit lock primitives**

```bash
git add src/backlog_py/runtime tests/test_runtime_locks.py tests/test_mutation_inventory.py
git commit -m "Add shared project write locks"
```

## Task 3: Lock CLI Mutation Paths and Make Board Exports Atomic

**Files:**
- Modify: `src/backlog_py/cli/main.py`
- Modify: `src/backlog_py/core/board_export.py`
- Test: `tests/test_cli_locking.py`
- Test: `tests/test_cli_readonly.py`

- [ ] **Step 1: Write failing CLI lock coverage tests**

Use monkeypatching to prove representative write commands enter the expected lock:

```python
@pytest.mark.parametrize(
    ("args", "operation"),
    [
        (("task", "create", "Locked task"), "task_create"),
        (("task", "edit", "TASK-1", "--title", "Updated"), "task_edit"),
        (("task", "archive", "TASK-1"), "task_archive"),
        (("draft", "create", "Draft"), "draft_create"),
        (("decision", "create", "Decision"), "decision_create"),
        (("config", "set", "defaultStatus", "To Do"), "config_set"),
        (("board", "export", "status.md", "--force"), "board_export_file"),
    ],
)
def test_cli_write_commands_acquire_project_lock(repo, monkeypatch, args, operation):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)

    result = CliRunner().invoke(main, ["--cwd", str(repo), *args])

    assert result.exit_code == 0, result.output
    assert operation in seen
```

Add a separate test for `init` using `with_init_lock`.

- [ ] **Step 2: Write failing atomic board export test**

Monkeypatch `_atomic_write_text` in `backlog_py.core.board_export` and assert both export paths use it.

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_cli_locking.py tests/test_cli_readonly.py::test_board_export_writes_markdown_report tests/test_cli_readonly.py::test_board_export_readme_updates_marker_section -q`

Expected: FAIL because CLI commands do not call lock helpers and board export uses direct `Path.write_text`.

- [ ] **Step 4: Wrap CLI mutations**

Import lock helpers in `src/backlog_py/cli/main.py`:

```python
from backlog_py.runtime.locks import with_init_lock, with_project_write_lock
```

Use small local helpers:

```python
def _locked_write(ctx: click.Context, operation: str, fn: Callable[[], T]) -> T:
    return with_project_write_lock(_project(ctx), operation, fn)


def _locked_init(ctx: click.Context, operation: str, fn: Callable[[], T]) -> T:
    return with_init_lock(_cwd(ctx), operation, fn)
```

Wrap every write surface listed in `MUTATION_SURFACES`. Keep read-only commands unlocked.

- [ ] **Step 5: Convert board export writes to atomic helpers**

Use the existing `_atomic_write_text` helper from `backlog_py.core.repository` for generated project files and README marker updates. Preserve existing content behavior.

- [ ] **Step 6: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_cli_locking.py tests/test_cli_readonly.py -q`

Expected: PASS.

- [ ] **Step 7: Commit CLI locking**

```bash
git add src/backlog_py/cli/main.py src/backlog_py/core/board_export.py tests/test_cli_locking.py tests/test_cli_readonly.py
git commit -m "Lock CLI project mutations"
```

## Task 4: SDK-Free MCP Protocol and Catalog

**Files:**
- Create: `src/backlog_py/mcp/protocol.py`
- Create: `src/backlog_py/mcp/catalog.py`
- Modify: `src/backlog_py/mcp/resources.py`
- Test: `tests/test_mcp_protocol_sdk_free.py`

- [ ] **Step 1: Write failing protocol tests**

Cover the minimal SDK-free MCP surface:

```python
def test_initialize_returns_server_capabilities():
    response = handle_jsonrpc_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "test"}},
    })

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "backlog-md-py"
    assert response["result"]["capabilities"]["tools"] == {}


def test_tools_list_contains_existing_task_search_tool():
    response = handle_jsonrpc_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "task_search" in names
```

Also test:

- `ping`
- `resources/list`
- `resources/read` for `backlog://workflow/overview`
- invalid JSON-RPC version
- unknown method
- notification returns `None`
- batch request returns list preserving response order

- [ ] **Step 2: Run protocol tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_mcp_protocol_sdk_free.py -q`

Expected: FAIL because `backlog_py.mcp.protocol` does not exist.

- [ ] **Step 3: Implement JSON-RPC envelope and errors**

Implement plain dict parsing, not Pydantic:

```python
JSONRPC_VERSION = "2.0"


def result_response(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: object, code: int, message: str, data: object | None = None) -> dict[str, object]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}
```

Use JSON-RPC standard error codes: parse `-32700`, invalid request `-32600`, method not found `-32601`, invalid params `-32602`, internal `-32603`.

- [ ] **Step 4: Implement catalog metadata**

Map existing resources/tools to MCP-compatible metadata. Use stable names matching current public Python functions, for example `task_search`, `task_create`, `document_create`, and `definition_of_done_defaults_upsert`.

Each tool should include:

- `name`
- `description`
- `inputSchema` with JSON Schema object shape

Keep schemas minimal but accurate enough for Codex.

- [ ] **Step 5: Implement method dispatch**

Implement:

- `handle_jsonrpc_message(payload: object, *, context: McpRequestContext | None = None) -> dict | list | None`
- `handle_jsonrpc_text(text: str, *, context: McpRequestContext | None = None) -> str | None`
- `McpRequestContext(project_hint: str | None = None, client_id: str | None = None, session_id: str | None = None)`

Use `tldw_server2`'s `MCPRequest`/`MCPResponse`/handler pattern only as a behavioral reference.

- [ ] **Step 6: Run protocol tests**

Run: `uv run --extra dev python -m pytest tests/test_mcp_protocol_sdk_free.py -q`

Expected: PASS.

- [ ] **Step 7: Commit protocol slice**

```bash
git add src/backlog_py/mcp/protocol.py src/backlog_py/mcp/catalog.py src/backlog_py/mcp/resources.py tests/test_mcp_protocol_sdk_free.py
git commit -m "Add SDK-free MCP protocol"
```

## Task 5: Replace FastMCP Stdio Server and Remove MCP SDK Dependency

**Files:**
- Create: `src/backlog_py/mcp/stdio_server.py`
- Modify: `src/backlog_py/mcp/server.py`
- Modify: `src/backlog_py/mcp/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/integration.md`
- Modify: `docs/cutover-validation.md`
- Modify: `docs/cutover-validation-results-2026-05-13.md`
- Test: `tests/test_mcp_stdio_sdk_free.py`
- Test: existing docs/reference tests if any fail after dependency removal

- [x] **Step 1: Write failing stdio tests**

Test the command path without importing `mcp`:

```python
def test_create_server_no_longer_requires_mcp_sdk(monkeypatch):
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "mcp" else original_find_spec(name),
    )

    server = create_server()

    assert server.name == "backlog-md-py"
```

Add a subprocess-style test for one initialize request:

```python
def test_stdio_server_handles_initialize_line(monkeypatch, capsys):
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    stdout = io.StringIO()

    run_stdio(stdin=stdin, stdout=stdout)

    assert '"serverInfo"' in stdout.getvalue()
```

- [x] **Step 2: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_mcp_stdio_sdk_free.py -q`

Expected: FAIL because `server.py` still imports FastMCP when creating a server.

- [x] **Step 3: Implement SDK-free stdio server**

Implement:

- `SdkFreeMcpServer(name: str)`
- `create_server() -> SdkFreeMcpServer`
- `run_stdio(stdin=sys.stdin, stdout=sys.stdout) -> None`
- `main() -> None`

Input format: one JSON-RPC JSON object per line. Output format: one JSON-RPC JSON object per line for requests; no output for notifications.

- [x] **Step 4: Remove MCP SDK dependency**

Removed the optional `mcp` extra from `pyproject.toml`; no empty compatibility
extra was retained.

- [x] **Step 5: Update docs**

Docs now use the SDK-free install/test path:

```bash
uv pip install -e ".[dev]"
uv run --extra dev python -m pytest tests -v
```

- [x] **Step 6: Run focused and baseline tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_mcp_protocol_sdk_free.py tests/test_mcp_stdio_sdk_free.py tests/test_agent_critical_matrix.py -q
```

Expected: PASS.

- [x] **Step 7: Commit SDK-free stdio slice**

```bash
git add src/backlog_py/mcp pyproject.toml README.md docs tests/test_mcp_stdio_sdk_free.py
git commit -m "Replace MCP SDK stdio server"
```

## Task 6: Lock MCP Mutations

**Files:**
- Modify: `src/backlog_py/mcp/tools.py`
- Modify: `src/backlog_py/mcp/protocol.py`
- Test: `tests/test_mcp_protocol_sdk_free.py`
- Test: `tests/test_mcp_tools_locking.py`

- [ ] **Step 1: Write failing MCP lock tests**

Use monkeypatching around `with_project_write_lock`:

```python
@pytest.mark.parametrize(
    ("tool_name", "arguments", "operation"),
    [
        ("task_create", {"project": "{repo}", "title": "MCP task"}, "mcp_task_create"),
        ("task_edit", {"project": "{repo}", "task_id": "TASK-1", "title": "Updated"}, "mcp_task_edit"),
        ("document_create", {"project": "{repo}", "path": "notes/a.md", "title": "A", "content": ""}, "mcp_document_create"),
        ("milestone_add", {"project": "{repo}", "name": "Alpha"}, "mcp_milestone_add"),
        ("definition_of_done_defaults_upsert", {"project": "{repo}", "items": ["Tests"]}, "mcp_definition_of_done_defaults_upsert"),
    ],
)
def test_mcp_write_tools_acquire_project_lock(repo, monkeypatch, tool_name, arguments, operation):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.mcp.tools.with_project_write_lock", fake_with_project_lock)
    arguments = {key: (str(repo) if value == "{repo}" else value) for key, value in arguments.items()}

    response = handle_jsonrpc_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })

    assert "result" in response
    assert operation in seen
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_mcp_tools_locking.py -q`

Expected: FAIL because MCP write tools do not acquire locks.

- [ ] **Step 3: Wrap MCP write tools**

Import `with_project_write_lock` in `mcp/tools.py`. Use a helper:

```python
def _locked(project: BacklogProject, operation: str, fn: Callable[[], T]) -> T:
    return with_project_write_lock(project, operation, fn)
```

Wrap only writes. Keep `task_list`, `task_search`, `task_view`, `document_list`, `document_view`, `milestone_list`, and `definition_of_done_defaults_get` unlocked.

- [ ] **Step 4: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_mcp_tools_locking.py tests/test_mcp_protocol_sdk_free.py -q`

Expected: PASS.

- [ ] **Step 5: Commit MCP locking**

```bash
git add src/backlog_py/mcp tests/test_mcp_tools_locking.py
git commit -m "Lock MCP project mutations"
```

## Task 7: Daemon Lifecycle Commands

**Files:**
- Create: `src/backlog_py/daemon/__init__.py`
- Create: `src/backlog_py/daemon/lifecycle.py`
- Create: `src/backlog_py/daemon/service.py`
- Modify: `src/backlog_py/cli/main.py`
- Test: `tests/test_daemon_lifecycle.py`

- [x] **Step 1: Write failing daemon CLI tests**

Use `CliRunner` and fake process runners:

```python
def test_daemon_status_reports_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    result = CliRunner().invoke(main, ["daemon", "status"])

    assert result.exit_code != 0
    assert "not running" in result.output.lower()


def test_daemon_status_json_omits_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    write_runtime_record(_record(token="secret-token"), ensure_state_layout())

    result = CliRunner().invoke(main, ["daemon", "status", "--json"])

    assert result.exit_code == 0
    assert "secret-token" not in result.output
    assert '"endpoint"' in result.output
```

- [x] **Step 2: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_daemon_lifecycle.py -q`

Expected: FAIL because daemon commands do not exist.

- [x] **Step 3: Implement lifecycle helpers**

Implement:

- `daemon_status() -> DaemonStatus`
- `daemon_ensure(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> DaemonStatus`
- `daemon_start(...) -> DaemonStatus`
- `daemon_stop(force: bool = False, timeout: float = 5.0) -> None`
- `is_pid_alive(pid: int) -> bool`
- stale runtime cleanup

Also verified that graceful stop timeouts keep the runtime record intact instead of allowing duplicate daemon startup after a failed stop.

`daemon_start` should launch:

```bash
python -m backlog_py daemon run --foreground --host 127.0.0.1 --port <port>
```

Capture stdout/stderr to the allocated daemon log path.

- [x] **Step 4: Add Click commands**

Add group:

- `backlog-py daemon status [--json]`
- `backlog-py daemon ensure [--json]`
- `backlog-py daemon start [--host HOST] [--port PORT] [--json]`
- `backlog-py daemon stop [--force]`
- `backlog-py daemon run --foreground [--host HOST] [--port PORT]`

Keep `run` hidden if desired, but tests should call it directly.

- [x] **Step 5: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_daemon_lifecycle.py -q`

Expected: PASS.

- [x] **Step 6: Commit daemon lifecycle**

```bash
git add src/backlog_py/daemon src/backlog_py/cli/main.py tests/test_daemon_lifecycle.py
git commit -m "Add singleton daemon lifecycle commands"
```

## Task 8: SDK-Free Loopback HTTP Endpoint and Stdio Forwarding Shim

**Files:**
- Create: `src/backlog_py/mcp/http_server.py`
- Modify: `src/backlog_py/mcp/stdio_server.py`
- Modify: `src/backlog_py/daemon/service.py`
- Test: `tests/test_daemon_http_server.py`
- Test: `tests/test_mcp_stdio_sdk_free.py`

- [x] **Step 1: Write failing HTTP endpoint tests**

Use a foreground server in a background thread with a temp state dir:

```python
def test_http_endpoint_requires_daemon_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    service = start_test_daemon_http_server(token="secret")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_request(service.endpoint, {"jsonrpc": "2.0", "id": 1, "method": "ping"}))

    assert exc.value.code == 401
```

Add success test with `Authorization: Bearer secret`, plus batch request test.

- [x] **Step 2: Write failing stdio forwarding test**

Start a fake HTTP daemon and run:

```python
run_stdio(stdin=io.StringIO(request_line), stdout=stdout, daemon_endpoint=fake.endpoint, token="secret")
```

Assert the stdout response is the fake daemon response and that no repository/tool code runs in the shim.

- [x] **Step 3: Run tests to verify failure**

Run: `uv run --extra dev python -m pytest tests/test_daemon_http_server.py tests/test_mcp_stdio_sdk_free.py -q`

Expected: FAIL because HTTP endpoint and forwarding shim are not implemented.

- [x] **Step 4: Implement HTTP JSON-RPC endpoint**

Use stdlib `ThreadingHTTPServer` unless tests show Codex requires a different HTTP shape. The endpoint should support:

- `GET /health`
- `GET /status`
- `POST /mcp`
- JSON object or JSON array body
- `Authorization: Bearer <runtime token>`
- `Mcp-Session-Id` response header for initialize requests if no session id was supplied

Keep the body protocol delegated to `backlog_py.mcp.protocol`.

- [x] **Step 5: Implement stdio forwarding mode**

`backlog-py-mcp` behavior:

1. If daemon endpoint/token are configured or runtime status is healthy, forward each stdio JSON-RPC request to `POST /mcp`.
2. If no daemon is healthy, either fail with a clear daemon-unavailable error or run direct SDK-free stdio mode based on a documented flag.

Default for multi-agent Codex migration should be forwarding mode, so per-client shims stay lightweight.

- [x] **Step 6: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_daemon_http_server.py tests/test_mcp_stdio_sdk_free.py tests/test_mcp_protocol_sdk_free.py -q`

Expected: PASS.

- [x] **Step 7: Commit HTTP/shim slice**

```bash
git add src/backlog_py/mcp/http_server.py src/backlog_py/mcp/stdio_server.py src/backlog_py/daemon/service.py tests/test_daemon_http_server.py tests/test_mcp_stdio_sdk_free.py
git commit -m "Add SDK-free daemon MCP endpoint"
```

## Task 9: Migration Docs, Validation, and Local Process Smoke

**Files:**
- Modify: `README.md`
- Modify: `docs/integration.md`
- Modify: `docs/cutover-validation.md`
- Create: `docs/singleton-daemon.md`
- Test: docs command snippets where practical

- [x] **Step 1: Write docs update**

Document:

- why `backlog-py-mcp` is SDK-free,
- `backlog-py daemon start/status/ensure/stop`,
- local Codex config options,
- rollback to direct stdio mode,
- how to verify one daemon process,
- limitations: official Node/native Backlog.md binary does not honor Python locks.

- [x] **Step 2: Run docs/reference grep checks**

Run:

```bash
rg -n "extra mcp|\\[mcp\\]|FastMCP|MCP SDK|mcp>=|backlog mcp start" README.md docs pyproject.toml src tests
```

Expected: only intentional historical/rollback references remain.

- [x] **Step 3: Run full package tests**

Run:

```bash
uv run --extra dev python -m pytest tests -v
```

Expected: PASS.

- [x] **Step 4: Run Bandit**

Run:

```bash
uv run --extra dev python -m bandit -r src -f json -o /tmp/bandit_backlog_py_daemon.json
```

Expected: PASS or no new high/medium findings in touched daemon/protocol code.

- [x] **Step 5: Build and check package**

Run:

```bash
uv build --out-dir /tmp/backlog-md-py-daemon-dist
uv run --extra dev python -m twine check /tmp/backlog-md-py-daemon-dist/*
```

Expected: PASS.

- [x] **Step 6: Run local process smoke**

Run:

```bash
backlog-py daemon start
backlog-py daemon status --json
backlog-py-mcp
ps -ef | rg "backlog|backlog-py|backlog.md-darwin-arm64"
backlog-py daemon stop
```

Expected:

- exactly one Python daemon process during the smoke,
- no `backlog.md-darwin-arm64/backlog mcp start` child process from the new path,
- `backlog-py-mcp` does not import or require the Python `mcp` package.

Result: isolated daemon smoke started one Python `backlog_py daemon run` process,
`backlog-py-mcp` initialized through the daemon, and `daemon stop` removed that
process. Host `ps` output still showed many pre-existing
`backlog.md-darwin-arm64/backlog mcp start` processes from outside this new
path; they were not spawned by the Python singleton smoke.

- [x] **Step 7: Commit docs and validation**

```bash
git add README.md docs
git commit -m "Document singleton daemon migration"
```

## Execution Notes

- Keep each task as a separate commit.
- Do not add a new runtime dependency unless a task explicitly proves stdlib is insufficient.
- Do not use the Python `mcp` package or any MCP SDK.
- If Codex cannot consume the direct loopback HTTP endpoint, keep the daemon HTTP endpoint as the single service and use the SDK-free stdio forwarding shim as the Codex-facing config.
- If the transport smoke contradicts this plan, stop after the spike and patch the spec/plan before continuing.
