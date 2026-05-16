# Singleton Daemon

`backlog-md-py` includes a local singleton daemon for agent environments that
would otherwise start one Backlog.md server process per agent. The daemon and
stdio shim are SDK-free: they use the package's own JSON-RPC MCP adapter and do
not import the Python `mcp` package.

## Why It Exists

Multiple Codex or agent sessions can call Backlog.md tools at the same time. If
each session launches a separate MCP server, the machine pays for repeated
process startup and each process has its own view of local state. The daemon
keeps one local service alive and uses shared filesystem locks for project
mutations, so direct `backlog-py` CLI writes and daemon-mediated MCP writes
serialize on the same project root.

The official Node/native Backlog.md binary does not honor these Python locks.
Do not run the official binary and `backlog-md-py` mutation paths against the
same live project during a migration.

## Lifecycle Commands

```bash
backlog-py daemon status
backlog-py daemon status --json
backlog-py daemon ensure
backlog-py daemon start --host 127.0.0.1 --port 18765
backlog-py daemon stop
backlog-py daemon stop --force
```

`daemon ensure` reuses a healthy daemon when one is already recorded, otherwise
it starts a background `python -m backlog_py daemon run --foreground` process.
The runtime record stores PID, endpoint, token, version, and log path under the
user state directory. `daemon status --json` omits token material.

`daemon stop` removes stale runtime records. If a live daemon ignores graceful
termination, the runtime record is kept so a second daemon is not started over a
still-running process.

## MCP Integration

Use the stable stdio command for Codex-style MCP clients:

```toml
[mcp_servers.backlog]
command = "backlog-py-mcp"
```

Start the singleton before launching multiple clients:

```bash
backlog-py daemon ensure
```

When a healthy daemon runtime record exists, `backlog-py-mcp` forwards each
stdio JSON-RPC message to the daemon's loopback `POST /mcp` endpoint with the
runtime bearer token. If no healthy daemon exists, `backlog-py-mcp` falls back
to direct SDK-free stdio mode. That direct mode is the rollback path, but it
does not provide a shared singleton process.

HTTP-capable MCP clients can call the daemon endpoint directly, but most local
agent configs should prefer the stdio shim so token discovery stays local to
the package runtime record.

### No-Restart Legacy Command Shim

Some already-running Codex app-server instances keep cached MCP commands for
open sessions. If they still relaunch `backlog mcp start`, install an explicit
compatibility wrapper around the existing `backlog` command:

```bash
backlog-py integration install-legacy-mcp-shim --target "$(command -v backlog)"
```

The wrapper only intercepts `backlog mcp start` and forwards it to
`backlog-py-mcp`. All other `backlog ...` commands delegate to the backed-up
original command. The install command prints the backup path; restore by moving
that backup back over the wrapper when no open sessions need the legacy command
path.

You can pass explicit paths for managed environments:

```bash
backlog-py integration install-legacy-mcp-shim \
  --target /Users/example/.bun/bin/backlog \
  --mcp-command /Users/example/.local/bin/backlog-py-mcp \
  --backup /Users/example/.bun/bin/backlog.node-shim-backup
```

## Local Verification

Run this smoke from the same environment that will run the agent:

```bash
backlog-py daemon ensure
backlog-py daemon status --json
printf '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n' | backlog-py-mcp
ps -ef | rg "backlog|backlog-py|backlog.md-darwin-arm64"
backlog-py daemon stop
```

Expected result:

- one Python `backlog_py daemon run --foreground` process while the daemon is
  running,
- no `backlog.md-darwin-arm64/backlog mcp start` process from the new path,
- `backlog-py-mcp` responds to `initialize` without the Python MCP SDK.

## Runtime Files

By default, runtime state is stored under the platform user-state directory.
Set `BACKLOG_PY_STATE_DIR` to isolate tests or a specific integration:

```bash
BACKLOG_PY_STATE_DIR=/tmp/backlog-md-py-state backlog-py daemon ensure
```

The state layout contains:

- `runtime/daemon.json`: token-bearing daemon runtime record,
- `locks/`: daemon and project lock files,
- `logs/`: daemon stdout/stderr logs.

Treat the runtime record as private because it contains the bearer token used by
the local HTTP endpoint.
