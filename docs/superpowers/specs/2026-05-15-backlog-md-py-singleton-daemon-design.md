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

## Goals

- Provide one lightweight `backlog-md-py` service process for local agents.
- Let multiple Codex sessions connect to the same MCP endpoint instead of
  spawning one Backlog server per session.
- Preserve the current Markdown files as the source of truth.
- Serialize mutations per Backlog project while allowing concurrent reads.
- Use the same project-scoped write lock for daemon requests and direct
  `backlog-py` CLI mutations.
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
- MCP endpoint: expose existing resources and tools over a shared local transport.
- Project lock manager: coordinate writes per resolved Backlog project root using
  a cross-process filesystem lock.
- In-memory cache: optional project-scoped read cache with conservative
  invalidation after daemon-mediated writes.

The existing `backlog-py-mcp` stdio server remains for compatibility and for
clients that cannot connect to a shared endpoint. It should be documented as a
compatibility mode, not as the recommended multi-agent path.

## Transport Compatibility Gate

The transport is the riskiest assumption and should be proven before broader
implementation.

The first implementation stage should answer these questions with a small spike:

- Can Codex connect to a loopback MCP endpoint served by the installed Python MCP
  SDK using HTTP/SSE or streamable HTTP?
- Can the configured endpoint be static enough for Codex configuration, or does
  it need a local discovery shim?
- Does a second Codex session connect to the same daemon without starting a new
  Backlog server process?
- Can the endpoint require a local token without breaking MCP negotiation?

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

Do not build the full daemon around an unproven transport assumption.

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
  path and does not start a second Backlog service process.
- Runtime-file tests for healthy daemon discovery and stale PID cleanup.
- Daemon start tests for duplicate start prevention and occupied-port failure.
- Filesystem lock tests for same-project serialization and independent-project
  concurrency.
- Cross-entrypoint lock tests proving daemon writes and direct `backlog-py` CLI
  writes serialize against each other.
- CLI tests for `daemon status`, `daemon start`, `daemon ensure`, and
  `daemon stop` using a fake service runner where possible.
- MCP registration tests for the endpoint-backed server path.
- Integration smoke that starts one daemon and runs multiple simulated clients
  against the same project.
- Regression tests proving concurrent writes serialize and produce valid
  Backlog.md files.
- Security tests for loopback binding, runtime-file permissions, and token
  enforcement where the chosen transport supports it.

The final verification gate should include the existing package baseline:

```bash
uv run --extra dev --extra mcp python -m pytest tests -v
uv run --extra dev python -m bandit -r src -f json -o /tmp/bandit_backlog_py_daemon.json
uv build --out-dir /tmp/backlog-md-py-daemon-dist
uv run --extra dev python -m twine check /tmp/backlog-md-py-daemon-dist/*
```

## Acceptance Criteria

- Starting the daemon twice returns the same healthy daemon instead of creating a
  duplicate service.
- Multiple clients can call MCP tools through the singleton path.
- Multiple Codex sessions no longer spawn additional
  `backlog.md-darwin-arm64/backlog mcp start` children after migration.
- Same-project writes serialize across daemon-mediated MCP calls.
- Same-project writes serialize across daemon-mediated MCP calls and direct
  `backlog-py` CLI mutations.
- Different-project writes can proceed concurrently.
- The daemon can be inspected and stopped with clear status output.
- Markdown remains the only canonical project state in v1.
- SQLite, if added later, is a disposable derived index with rebuild semantics.

## Rollout Plan

1. Land this reviewed design and get user approval.
2. Write a staged implementation plan.
3. Run the transport compatibility spike and record the chosen transport.
4. Implement the shared project filesystem lock and wrap CLI mutation paths.
5. Implement daemon lifecycle, runtime state, logs, and status commands.
6. Add the endpoint-backed MCP server or stdio compatibility shim based on the
   transport spike.
7. Wrap daemon mutation dispatch with the shared lock.
8. Add local Codex migration documentation and an explicit config helper.
9. Validate with multi-client smoke and process-count checks.
10. Switch the local Codex config from the Node/native Backlog MCP command to the
    singleton path only after validation.
11. Verify one Python daemon process and no official Backlog.md binary children.

## Open Questions

- Which MCP endpoint transport is best supported by the currently installed MCP
  SDK version and Codex client path?
- If direct URL configuration works, what default loopback port should be used?
- If direct URL configuration does not work, what is the thinnest acceptable
  stdio compatibility shim?
- What exact state directory should be used on macOS, Linux, and Windows?
- Should read calls ever block behind active writes in v1, or should that remain
  an opt-in after profiling?
