# Backlog.md Python Singleton Daemon Design

Date: 2026-05-15
Scope: `backlog-md-py`

## Problem

Multiple Codex agents can currently launch the official Backlog.md MCP server as
separate `backlog mcp start` process trees. On this machine the global Codex MCP
configuration starts `backlog`, which spawns a Node wrapper and the native
`backlog.md-darwin-arm64/backlog` binary. Multiple long-lived instances create
avoidable CPU and memory pressure.

Replacing the binary with `backlog-py-mcp` alone would make each instance
lighter, but it would not guarantee a single process. A stdio MCP server is
normally launched per client/session. The fix needs to be a shared local service,
not only a different command.

## Goals

- Provide one lightweight `backlog-md-py` service process for local agents.
- Let multiple Codex sessions connect to the same MCP endpoint instead of
  spawning one Backlog server per session.
- Preserve the current Markdown files as the source of truth.
- Serialize mutations per Backlog project while allowing concurrent reads.
- Keep the existing CLI and stdio MCP entry point available for compatibility.
- Provide operational commands to inspect, start, stop, and troubleshoot the
  singleton.

## Non-Goals

- Replace all upstream Backlog.md browser behavior.
- Support cross-machine daemon sharing.
- Add multi-user auth or remote network exposure in the first version.
- Add a durable SQLite task index in the first version.
- Change the Backlog.md task/document/milestone file format.

## Proposed Architecture

Add a singleton local daemon to `backlog-md-py`.

The normal multi-agent path becomes:

1. `backlog-py daemon start` starts one local service if none is healthy.
2. Codex MCP configuration points to the daemon endpoint.
3. Agents call the daemon-hosted MCP tools/resources.
4. The daemon dispatches to the existing `backlog_py.mcp.tools` registry and
   repository/services layer.

Core components:

- `backlog-py daemon start`: launch the singleton.
- `backlog-py daemon status`: print PID, endpoint, uptime, active request count,
  known projects, and current lock state.
- `backlog-py daemon stop`: shut down the singleton cleanly.
- Runtime state file: store PID, endpoint, startup token, and start time under a
  user cache/state directory.
- MCP endpoint: expose existing resources and tools over a shared local
  transport.
- Project lock manager: coordinate writes per resolved Backlog project root.
- In-memory cache: optional project-scoped read cache with conservative
  invalidation after writes.

The existing `backlog-py-mcp` stdio server remains for compatibility and for
clients that cannot connect to a shared endpoint.

## Transport

Prefer a loopback-only endpoint that Codex can reference in URL-style MCP
configuration, similar to other configured HTTP MCP servers. If the installed MCP
SDK supports serving the required MCP protocol over HTTP/SSE or streamable HTTP,
use that directly.

If the SDK path is not clean enough, add a small local proxy layer that exposes
the current tool registry through a loopback server, then adapt MCP transport
once the SDK support is confirmed.

The daemon must bind only to localhost by default. It should not expose a remote
network interface in v1.

## Request Flow

All MCP tools continue to accept a `project` argument.

For every request:

1. Resolve `project` with the existing project discovery logic.
2. Use the resolved project root as the canonical project identity.
3. Execute read operations through `ReadOnlyRepository` or the existing document,
   milestone, and config read helpers.
4. Execute write operations through the existing safe mutation services:
   `MutableRepository`, `DocumentService`, `MilestoneService`, and config
   writers.
5. Invalidate project-local caches after a successful write.

No task mutation logic should be duplicated in the daemon layer. The daemon owns
transport, lifecycle, caching, and coordination only.

## Locking

Use one in-process lock per resolved project root.

- Reads are concurrent by default.
- Writes serialize for the same project root.
- Writes in different projects can run concurrently.
- A write lock covers the full multi-step mutation operation, not only the final
  file write.
- Existing atomic write helpers remain responsible for individual file
  replacement safety.
- If a write waits past a configurable timeout, return a clear lock-timeout error
  with project root and lock/request metadata.

If stale reads become observable, add an option for reads to wait behind active
writes. That should not be the default until profiling or tests show a concrete
need.

## Caching

v1 should use conservative in-memory caching only.

Cache keys are project-scoped and should include enough file metadata to avoid
serving stale data after external edits. The initial cache may be minimal or
disabled for write-heavy paths; the singleton alone should remove the largest
process-count cost.

After any daemon-mediated write, invalidate all cached data for that project.

## SQLite Indexing

SQLite is a good future fit as a derived per-project index, but it should not be
part of v1.

The Markdown files remain canonical. A future SQLite index can accelerate task
list/search/board reads, store parsed metadata, and improve warm restart
behavior. That phase needs explicit handling for:

- external Markdown edits,
- file mtimes and hashes,
- schema migrations,
- reindex recovery,
- write-then-index failure recovery,
- stale-index detection.

The v1 daemon boundaries should be designed so SQLite can be added later behind
the repository/read-cache layer without changing MCP tool contracts.

## Failure Handling

The daemon should be easy to inspect and safe to recover:

- If no daemon is running, `daemon status` reports that clearly and exits
  non-zero.
- If the runtime file points to a missing PID or failed health check, treat it as
  stale and ignore it.
- If startup finds a healthy daemon, report its endpoint instead of starting a
  duplicate.
- If startup finds a stale runtime file, replace it.
- If a client reaches no daemon, return a clear "daemon unavailable" error and
  include the start command.
- If project discovery fails, surface the same project-discovery error used by
  CLI/MCP today.
- If a write fails, rely on existing atomic writes and service-specific rollback
  behavior as the recovery boundary.

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
4. Add a URL-style MCP configuration pointing to the singleton endpoint.
5. Verify process count: one daemon process and no
   `backlog.md-darwin-arm64/backlog mcp start` children.

The old config can be documented as a rollback path, but should not run at the
same time as the singleton.

## Testing

Add tests before implementation code for each layer:

- Runtime-file tests for healthy daemon discovery and stale PID cleanup.
- Lock-manager tests for same-project serialization and independent-project
  concurrency.
- CLI tests for `daemon status`, `daemon start`, and `daemon stop` using a fake
  service runner where possible.
- MCP registration tests for the endpoint-backed server path.
- Integration smoke that starts one daemon and runs multiple simulated clients
  against the same project.
- Regression tests proving concurrent writes serialize and produce valid
  Backlog.md files.

The final verification gate should include the existing package baseline:

```bash
uv run --extra dev --extra mcp python -m pytest tests -v
uv run --extra dev python -m bandit -r src -f json -o /tmp/bandit_backlog_py_daemon.json
uv build --out-dir /tmp/backlog-md-py-daemon-dist
uv run --extra dev python -m twine check /tmp/backlog-md-py-daemon-dist/*
```

## Rollout Plan

1. Land the design and implementation plan.
2. Implement daemon lifecycle and status commands.
3. Add project-scoped lock manager around MCP mutation tools.
4. Add shared endpoint transport.
5. Add local Codex migration documentation.
6. Validate with multi-client smoke.
7. Switch the local Codex config from the Node/native Backlog MCP command to the
   singleton endpoint.
8. Verify one daemon process and no official Backlog.md binary children.

## Open Questions

- Which MCP endpoint transport is best supported by the currently installed MCP
  SDK version?
- Should the daemon be started manually by `backlog-py daemon start`, or should a
  wrapper command auto-start it before printing endpoint config?
- What exact state directory should be used on macOS and Linux for the runtime
  file?
- Should read calls ever block behind active writes in v1, or should that remain
  an opt-in after profiling?
