# Backlog.md Python Singleton Daemon Design

Date: 2026-05-15
Scope: `backlog-md-py`
Status: design review hardened; pending user approval before implementation planning

## Problem

Multiple Codex agents can currently launch the official Backlog.md MCP server as
separate `backlog mcp start` process trees. On this machine the global Codex MCP
configuration starts `backlog`, which spawns a Node wrapper and the native
`backlog.md-darwin-arm64/backlog` binary. Multiple long-lived instances create
avoidable CPU and memory pressure.

Replacing the binary with `backlog-py-mcp` alone would make each instance
lighter, but it would not guarantee a single server. A stdio MCP server is
normally launched per client/session. The fix needs to be a shared local service,
not only a different command.

## Design Review Findings

The first design pass chose B-singleton: one local `backlog-md-py` daemon used by
multiple agents. That direction still looks right, but the review found several
places where the design needed to be stricter before implementation:

- The original in-process lock was insufficient. Direct `backlog-py` CLI writes
  would bypass daemon locks, so v1 needs a shared filesystem lock used by both
  daemon-mediated MCP writes and local CLI mutation commands.
- The original transport section assumed Codex can use a URL-style local MCP
  endpoint. That must be proven with a small transport spike before the endpoint
  shape is treated as final.
- Static Codex MCP config and dynamic daemon ports can conflict. If direct URL
  configuration is used, the daemon needs a stable configured loopback address or
  an approved shim that discovers the active daemon.
- Hidden auto-start inside the request path would make failures harder to debug.
  v1 should use explicit daemon lifecycle commands plus a small `ensure` helper,
  not implicit server creation from arbitrary tool calls.
- A local loopback endpoint still needs a startup token and restrictive runtime
  file permissions to prevent accidental cross-client use on the same machine.
- Persistent caching and SQLite are not required for the first process-count
  fix. v1 should keep read caching minimal or disabled, and SQLite should remain
  a later derived index.
- The singleton transport must not use an MCP SDK. Implement the needed MCP
  JSON-RPC surface directly, using `tldw_server2`'s custom MCP implementation as
  a reference for protocol shape where useful.
- The repo has write paths outside the MCP tool registry, including drafts,
  decisions, cleanup, board export, init, and agent-instruction updates. The
  lock design must cover those paths explicitly rather than assuming MCP-only
  coverage is enough.
- Some existing write paths still use direct `Path.write_text`. The singleton
  work should convert project mutation writes to the existing atomic-write style
  where practical, not rely on daemon serialization alone.

## Goals

- Provide one lightweight `backlog-md-py` service process for local agents.
- Let multiple Codex sessions connect to the same MCP endpoint instead of
  spawning one Backlog server per session.
- Preserve the current Markdown files as the source of truth.
- Serialize mutations per Backlog project while allowing concurrent reads.
- Use the same project-scoped write lock for daemon requests and direct
  `backlog-py` CLI mutations.
- Avoid an MCP SDK dependency in the daemon and in the final recommended MCP
  entry point.
- Keep the existing CLI and stdio MCP entry point available for compatibility.
- Provide operational commands to inspect, start, stop, and troubleshoot the
  singleton.

## Non-Goals

- Replace all upstream Backlog.md browser behavior.
- Support cross-machine daemon sharing.
- Add multi-user auth or remote network exposure in the first version.
- Add a durable SQLite task index in the first version.
- Change the Backlog.md task/document/milestone file format.
- Coordinate writes performed by the official Node/native Backlog.md binary; the
  migration must stop that server before relying on the Python singleton.

## Proposed Architecture

Add a singleton local daemon to `backlog-md-py`.

The normal multi-agent path becomes:

1. `backlog-py daemon start` starts one local service if none is healthy.
2. Codex MCP configuration points to the daemon endpoint or to a tiny compatibility
   shim that forwards stdio MCP traffic to that daemon.
3. Agents call the daemon-hosted MCP tools/resources.
4. The daemon dispatches to the existing `backlog_py.mcp.tools` registry and
   repository/services layer.
5. Every mutation enters the project filesystem lock before invoking the existing
   service implementation.

Core components:

- `backlog-py daemon start`: launch the singleton.
- `backlog-py daemon ensure`: start if missing, otherwise print the healthy
  endpoint; intended for setup scripts and compatibility shims.
- `backlog-py daemon status`: print PID, endpoint, uptime, active request count,
  known projects, current lock state, and log path.
- `backlog-py daemon stop`: shut down the singleton cleanly.
- Runtime state file: store PID, endpoint, startup token id, start time, package
  version, and log path under a user cache/state directory.
- SDK-free MCP endpoint: expose existing resources and tools through a small
  local JSON-RPC protocol adapter.
- Project lock manager: coordinate writes per resolved Backlog project root using
  a cross-process filesystem lock.
- In-memory cache: optional project-scoped read cache with conservative
  invalidation after daemon-mediated writes.

The existing `backlog-py-mcp` command name remains for compatibility, but its
implementation should be replaced with an SDK-free stdio adapter or daemon shim.
The final recommended path must not require the Python `mcp` package.

Add `backlog-py daemon run --foreground` for development and tests. `daemon
start` should be the background process manager; `daemon run --foreground` should
run the same service without forking so tests can control lifecycle directly.

## SDK-Free MCP Protocol Adapter

The transport is the riskiest assumption and must be implemented without an MCP
SDK.

Use `tldw_server2/tldw_Server_API/app/core/MCP_unified/protocol.py`,
`server.py`, and `api/v1/endpoints/mcp_unified_endpoint.py` as reference
material, not as a direct dependency. The useful pieces to adapt are:

- JSON-RPC 2.0 request and response envelopes,
- method routing for `initialize`, `ping`, `tools/list`, `tools/call`,
  `resources/list`, and `resources/read`,
- batch request handling,
- session id handling for HTTP-style clients,
- consistent error objects for parse errors, invalid requests, missing methods,
  invalid params, and internal errors.

Avoid copying tldw_server's AuthNZ, RBAC, Redis idempotency, module registry,
metrics, governance, and database integrations. This project needs a small local
protocol layer over the existing `backlog_py.mcp.tools` and resource registry.

Suggested backlog-md-py modules:

- `backlog_py.mcp.protocol`: plain-Python JSON-RPC request parsing, response
  rendering, method dispatch, and batch handling.
- `backlog_py.mcp.catalog`: SDK-free resource/tool metadata built from the
  existing registry.
- `backlog_py.mcp.stdio_server`: newline-delimited stdio JSON-RPC server for
  compatibility and optional daemon shim mode.
- `backlog_py.mcp.http_server`: loopback HTTP JSON-RPC endpoint for the singleton
  daemon, using stdlib or a small existing dependency only if justified.

The first implementation stage should answer these questions with a small spike:

- Can Codex connect to a loopback SDK-free HTTP JSON-RPC endpoint?
- Can the configured endpoint be static enough for Codex configuration, or does
  it need a local discovery shim?
- Does a second Codex session connect to the same daemon without starting a new
  Backlog server process?
- Can the endpoint require a local token without breaking MCP negotiation?
- If direct HTTP does not work, can an SDK-free `backlog-py-mcp` stdio shim
  forward each request to the singleton daemon while preserving MCP behavior?

Preferred result: a loopback-only MCP endpoint on a stable configured host/port,
for example `127.0.0.1:<configured-port>`, that Codex can reference directly.
The default port should be deterministic so a static Codex config stays valid.
If the port is occupied by another healthy `backlog-md-py` daemon, `daemon start`
should reuse it. If the port is occupied by something else, startup should fail
with a clear conflict and a suggested alternate port.

Fallback result: if Codex cannot consume the direct endpoint, provide a tiny
stdio compatibility shim. The shim may be per-client, but it must only forward to
the singleton daemon and must not instantiate repositories, parse projects, or
perform writes locally. That still preserves one Backlog service process and one
write-coordination point.

Do not build the full daemon around an unproven transport assumption, and do not
use the Python MCP SDK as a shortcut.

## Request Flow

All MCP tools continue to accept a `project` argument.

For every request:

1. Resolve `project` with the existing project discovery logic.
2. Use the resolved, symlink-canonical project root as the project identity.
3. Execute read operations through `ReadOnlyRepository` or the existing document,
   milestone, decision, draft, and config read helpers.
4. Execute write operations through the existing safe mutation services:
   `MutableRepository`, `DocumentService`, `MilestoneService`, `DecisionService`,
   `DraftService`, agent instruction writers, board export writers, and config
   writers.
5. Invalidate project-local daemon caches after a successful write.

No task mutation logic should be duplicated in the daemon layer. The daemon owns
transport, lifecycle, caching, and coordination only.

The lock wrapper should sit above the mutation registry where possible. If a
mutation is only exposed through CLI code today, implementation should extract a
shared service boundary instead of reimplementing the command in the daemon.

## Mutation Lock Coverage

Before implementation, maintain a checked inventory of project write surfaces and
make every entry either lock-covered or explicitly out of scope.

Known project mutation surfaces in the current codebase:

- `init_project`: creates project/backlog directories and config. This runs
  before normal project discovery, so it needs a root-path init lock derived from
  the target directory rather than the discovered project lock.
- `MutableRepository`: task create, edit, archive, complete, and cleanup's
  complete-Done loop.
- `DraftService`: draft create, promote, demote, and archive.
- `DocumentService`: document create/update, including moves.
- `DecisionService`: decision create.
- `MilestoneService`: add, rename, remove, archive, and task-reference rewrites
  from `--update-tasks` or `--clear-tasks`.
- Config writers: `config set` and Definition of Done defaults upsert.
- Agent instruction writer: updates `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and
  GitHub Copilot instructions.
- Board export writers: generated `Backlog.md` exports and README board marker
  updates.

MCP currently exposes only a subset of those writes. That is acceptable for v1
singleton MCP behavior, but the shared lock must still wrap the CLI paths because
agents and humans can keep using `backlog-py` directly while the daemon is
running.

Any write path that currently uses direct `Path.write_text` should either move to
the existing atomic write helper or document why atomic replacement is not safe
for that target. The first implementation plan should include board export and
README board updates in this audit.

## Locking

Use one cross-process filesystem lock per resolved project root. The lock key is
derived from the canonical project root path, not from the raw `project` argument,
so symlinked paths and relative paths converge on the same lock.

Recommended shape:

- Lock files live under the `backlog-md-py` user state directory, not inside the
  Git working tree.
- The lock filename uses a stable hash of the canonical project root.
- Lock metadata records the project root, process id, command/request id,
  operation name, start time, and best-effort client label.
- The implementation uses a small cross-platform file-lock abstraction. On macOS
  and Linux this can use `fcntl`; on Windows it should use an equivalent tested
  mechanism or a minimal dependency with clear justification.
- The lock file is advisory coordination state. If the process crashes, OS-level
  lock release is authoritative; leftover metadata must be treated as diagnostic
  information, not as proof that the project is still locked.

Behavior:

- Reads are concurrent by default.
- Writes serialize for the same project root across daemon requests and direct
  Python CLI mutations.
- Writes in different projects can run concurrently.
- A write lock covers the full multi-step mutation operation, including all
  read-modify-write work and any multi-file update/rollback sequence.
- Existing atomic write helpers remain responsible for individual file
  replacement safety.
- If a write waits past a configurable timeout, return a clear lock-timeout error
  with project root, operation name, request id, owning process id if known, and
  lock age.

If stale reads become observable, add an option for reads to wait behind active
writes. That should not be the default until tests or profiling show a concrete
need.

The official Node/native Backlog.md binary will not honor this Python lock. The
local migration must stop the official MCP server before relying on singleton
write safety.

## State Directory and Runtime Files

Do not leave state-directory selection open during implementation. Use a small
stdlib helper with an override instead of adding a dependency only for platform
paths.

Resolution order:

1. `BACKLOG_PY_STATE_DIR`, if set.
2. macOS: `~/Library/Application Support/backlog-md-py`.
3. Linux and other XDG environments: `$XDG_STATE_HOME/backlog-md-py` or
   `~/.local/state/backlog-md-py`.
4. Windows: `%LOCALAPPDATA%\backlog-md-py` if available, otherwise
   `~/AppData/Local/backlog-md-py`.

Keep separate subdirectories for:

- `runtime/`: daemon PID, endpoint, token, and health files.
- `locks/`: daemon-level and per-project lock files.
- `logs/`: daemon stdout/stderr logs.

Runtime files containing token material must be created with user-only
permissions where the platform supports it. State directory paths should be
shown in `daemon status` so users can inspect and clean them without searching.

## Caching

v1 should use little to no parsed-task caching. The singleton process-count fix
is the primary goal, and a stale read cache would create more risk than benefit.

Allowed v1 caching:

- daemon runtime state,
- project discovery results with a short TTL,
- endpoint health information,
- optional per-request memoization that cannot outlive a single tool call.

Avoid a durable or long-lived task/document cache in v1. If a small daemon cache
is added later, cache keys must be project-scoped and include file metadata, and
any daemon-mediated write must invalidate all cached data for that project.

## SQLite Indexing

SQLite is a good future fit as a derived per-project index, but it should not be
part of v1.

The Markdown files remain canonical. A future SQLite index can accelerate task
list/search/board reads, store parsed metadata, and improve warm restart
behavior. That phase needs explicit handling for:

- external Markdown edits,
- file mtimes and content hashes,
- schema migrations,
- full rebuild from Markdown,
- reindex recovery,
- write-then-index failure recovery,
- stale-index detection,
- corruption handling that falls back to Markdown and rebuilds.

The SQLite index must be disposable. If it is missing, stale, corrupt, or built
by an older schema, the system should rebuild it from Markdown rather than
treating it as canonical state.

The v1 daemon boundaries should be designed so SQLite can be added later behind
the repository/read-cache layer without changing MCP tool contracts.

## Security and Local Scope

The daemon is local-only, but v1 should still avoid accidental exposure:

- Bind to `127.0.0.1` by default, not `0.0.0.0`.
- Do not enable remote network access in v1.
- Generate a startup token and store only the active token material in a runtime
  file with user-only permissions.
- Require clients to present the token if the transport supports headers or an
  equivalent local authorization mechanism.
- Never print the token in normal status output; provide an explicit config or
  setup command when the user needs to install MCP client configuration.
- Treat the token as local accident prevention, not as a multi-user security
  model.

## Failure Handling

The daemon should be easy to inspect and safe to recover:

- If no daemon is running, `daemon status` reports that clearly and exits
  non-zero.
- If the runtime file points to a missing PID or failed health check, treat it as
  stale and ignore it.
- If startup finds a healthy daemon on the configured endpoint, report its
  endpoint instead of starting a duplicate.
- If startup finds a stale runtime file, replace it atomically.
- If startup cannot bind the configured port, fail with a clear conflict unless
  the port belongs to the healthy daemon recorded in the runtime file.
- If a client reaches no daemon, return a clear "daemon unavailable" error and
  include the start or ensure command.
- If project discovery fails, surface the same project-discovery error used by
  CLI/MCP today.
- If a write fails, rely on existing atomic writes and service-specific rollback
  behavior as the recovery boundary.
- Daemon stdout/stderr should be captured in a log file shown by `daemon status`.
- `daemon stop` should attempt graceful shutdown first, wait for active writes
  unless forced, then report if the process is still alive.
- `daemon status --json` should exist for tests and future config helpers; human
  status output can stay concise.

Startup should use a daemon-level lock around runtime-file creation so two
simultaneous `daemon start` calls cannot both win.

## Local Codex Migration

The current local Codex configuration contains a global Backlog MCP command:

```toml
[mcp_servers.backlog]
command = "backlog"
args = ["mcp", "start"]
```

This should not remain active in parallel with the singleton. The migration path
is:

1. Stop current `backlog mcp start` process trees.
2. Comment or remove the global command-based Backlog MCP entry.
3. Start the `backlog-md-py` singleton daemon.
4. Add the proven Codex MCP configuration for either the direct endpoint or the
   compatibility shim.
5. Verify process count: one Python daemon process and no
   `backlog.md-darwin-arm64/backlog mcp start` children.

The package should not silently edit `~/.codex/config.toml` on install or daemon
start. It may provide an explicit command that prints a config snippet or applies
the change after a direct user request. The old config can be documented as a
rollback path, but it should not run at the same time as the singleton.

## Testing

Add tests before implementation code for each layer:

- Transport spike proving the selected MCP endpoint works with the local client
  path without an MCP SDK and does not start a second Backlog service process.
- Protocol tests for `initialize`, `ping`, `tools/list`, `tools/call`,
  `resources/list`, `resources/read`, notifications, parse errors, invalid
  params, missing methods, and batch requests.
- Stdio tests proving `backlog-py-mcp` no longer imports or requires the Python
  `mcp` package.
- Runtime-file tests for healthy daemon discovery and stale PID cleanup.
- Daemon start tests for duplicate start prevention and occupied-port failure.
- State-directory tests for env override, macOS/Linux/Windows fallback behavior,
  and restrictive runtime-file permissions where supported.
- Filesystem lock tests for same-project serialization and independent-project
  concurrency.
- Crash/stale-metadata tests proving leftover lock metadata does not block new
  writes after the OS lock is released.
- Cross-entrypoint lock tests proving daemon writes and direct `backlog-py` CLI
  writes serialize against each other.
- Mutation-inventory tests or static checks ensuring new CLI/MCP write surfaces
  are categorized as lock-covered or deliberately out of scope.
- CLI tests for `daemon status`, `daemon start`, `daemon ensure`, and
  `daemon stop` using a fake service runner where possible.
- Foreground daemon tests using `backlog-py daemon run --foreground` to avoid
  brittle background-process control in most tests.
- MCP protocol/catalog tests for the endpoint-backed server path.
- Integration smoke that starts one daemon and runs multiple simulated clients
  against the same project.
- Regression tests proving concurrent writes serialize and produce valid
  Backlog.md files.
- Regression tests proving board export/README updates are lock-covered and use
  safe write behavior.
- Security tests for loopback binding, runtime-file permissions, and token
  enforcement where the chosen transport supports it.

The final verification gate should include the existing package baseline:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src -f json -o /tmp/bandit_backlog_py_daemon.json
uv build --out-dir /tmp/backlog-md-py-daemon-dist
uv run --extra dev python -m twine check /tmp/backlog-md-py-daemon-dist/*
```

## Acceptance Criteria

- Starting the daemon twice returns the same healthy daemon instead of creating a
  duplicate service.
- Multiple clients can call MCP tools through the singleton path without the
  Python MCP SDK.
- `backlog-py-mcp` remains available as an SDK-free compatibility command.
- Multiple Codex sessions no longer spawn additional
  `backlog.md-darwin-arm64/backlog mcp start` children after migration.
- Same-project writes serialize across daemon-mediated MCP calls.
- Same-project writes serialize across daemon-mediated MCP calls and direct
  `backlog-py` CLI mutations.
- Every current project write surface is categorized as lock-covered or
  explicitly out of scope with rationale.
- Different-project writes can proceed concurrently.
- The daemon can be inspected and stopped with clear status output.
- `daemon status --json` reports endpoint, PID, log path, state directory, and
  active lock metadata without leaking token material.
- Markdown remains the only canonical project state in v1.
- SQLite, if added later, is a disposable derived index with rebuild semantics.

## Rollout Plan

1. Land this reviewed design and get user approval.
2. Write a staged implementation plan.
3. Run the SDK-free transport compatibility spike and record the chosen
   transport.
4. Add the state-directory helper, runtime-file model, and foreground daemon run
   mode.
5. Implement the shared project filesystem lock and wrap CLI mutation paths.
6. Audit project write paths and convert direct project writes to atomic helpers
   where practical.
7. Implement daemon lifecycle, runtime state, logs, and status commands.
8. Add the SDK-free endpoint-backed MCP server or stdio compatibility shim based
   on the transport spike.
9. Wrap daemon mutation dispatch with the shared lock.
10. Add local Codex migration documentation and an explicit config helper.
11. Validate with multi-client smoke and process-count checks.
12. Switch the local Codex config from the Node/native Backlog MCP command to the
    singleton path only after validation.
13. Verify one Python daemon process and no official Backlog.md binary children.

## Open Questions

- Which SDK-free MCP endpoint transport is best supported by the Codex client
  path?
- If direct URL configuration works, what default loopback port should be used?
- If direct URL configuration does not work, what is the thinnest acceptable
  SDK-free stdio compatibility shim?
- Should read calls ever block behind active writes in v1, or should that remain
  an opt-in after profiling?
