# Architecture

`backlog-md-py` is built around one constraint: a Backlog.md project is still a
directory of Markdown files. The Python package adds safer local adapters,
shared mutation services, and agent-friendly process coordination around that
file format.

## Source Of Truth

The authoritative project state lives under each consuming project's `backlog/`
directory:

- `backlog/config.yml`: project configuration, statuses, labels, settings, and
  integration flags.
- `backlog/tasks/`: active task Markdown files.
- `backlog/completed/`: completed task storage.
- `backlog/archive/`: archived task storage.
- `backlog/docs/` and related folders: project documents and decisions when
  present.

The package may create runtime files under the user state directory for daemon
records, locks, logs, and disposable read indexes. Those files are coordination
or cache state, not the task database.

## Source Layout

The main package lives under `src/backlog_py/`:

- `cli/`: Click command tree for `backlog-py` and `python -m backlog_py`.
- `core/`: task, document, decision, milestone, board export, agent instruction,
  and project initialization behavior.
- `storage/`: project discovery and config loading.
- `markdown/`: Markdown parsing and section-preserving task serialization.
- `runtime/`: filesystem locks, state directories, and mutation coordination.
- `mcp/`: pure MCP helper functions, SDK-free JSON-RPC protocol handling,
  stdio server, HTTP server, resources, and tool catalog.
- `daemon/`: singleton daemon lifecycle and local service wrapper.
- `browser/`: loopback browser board service, templates, and static assets.
- `tui/`: optional Textual terminal board.
- `compat/`: audited compatibility inventory and release-readiness report
  generation.
- `oracle/`: generated manifest fixtures used to compare expected behavior.
- `indexing/`: optional disposable SQLite read index.
- `integration/`: compatibility shims for integration and migration paths.
- `security/`: path-containment and safety helpers.

Tests under `tests/` mirror these responsibilities. When behavior changes, add
or update the focused test closest to the changed module and run the relevant
parity tests before broader validation.

## Interface Flow

Most public interfaces follow the same path:

1. Resolve the project with `storage.project.discover_project`.
2. Load config and Markdown-backed records through storage/core helpers.
3. Execute read or mutation behavior through shared core services.
4. Serialize output for the caller: plain CLI text, JSON-compatible Python
   objects, MCP JSON-RPC responses, browser responses, or TUI models.

The important adapter rule is that CLI, MCP, daemon, browser, and TUI surfaces
should not each invent their own task mutation semantics. They should delegate
to the same core operations so Markdown preservation, validation, status
handling, and locking stay consistent.

## CLI And Python Helpers

`backlog-py` is the stable installed CLI name. It intentionally does not replace
an upstream `backlog` command unless a consuming project makes an explicit
cutover decision.

Python integrations can call helper functions from `backlog_py.mcp` directly
after discovering a project. These helpers are the same pure operations exposed
through the MCP server and are useful for embedding in Python processes without
spawning subprocesses.

## MCP And Daemon

`backlog-py-mcp` implements MCP stdio without importing the Python MCP SDK. In
direct mode it handles JSON-RPC messages in the current process. When a healthy
singleton daemon runtime record exists, it forwards requests to the daemon's
loopback endpoint instead.

The daemon exists for local multi-agent environments. It keeps one process alive
for repeated MCP traffic and exposes token-protected loopback endpoints. It does
not own a separate task store. Mutations still go through the same Markdown
write paths and project locks used by direct CLI writes.

Use `backlog-py daemon status --json` to inspect token-safe runtime state,
known projects, and lock metadata. See [singleton-daemon.md](singleton-daemon.md)
for lifecycle and rollback details.

## Browser Board And TUI

The browser board and optional Textual TUI are human-facing interfaces. They are
useful for inspecting a board, navigating task details, editing supported
fields, and checking project settings.

Automation should prefer plain CLI output, Python helpers, MCP tools, or the
daemon path. Those surfaces are deterministic and easier for agents and scripts
to parse.

## Runtime State And SQLite Index

Runtime state is stored under the platform user-state directory, or under
`BACKLOG_PY_STATE_DIR` when set. It can contain:

- daemon runtime records,
- logs,
- lock files,
- disposable SQLite read indexes.

The SQLite index is an optional acceleration cache for read-heavy operations.
It can be rebuilt or deleted at any time. Markdown files remain authoritative,
and mutation paths continue to write the normal Backlog.md files.

## Compatibility Inventory

The compatibility inventory records the audited upstream Backlog.md baseline
and the implemented local behavior. `backlog-py compat status` is read-only and
summarizes CLI, MCP, browser, config, core, and git coverage.

Use the inventory and parity docs when changing public behavior:

- [Agent-critical parity](agent-critical-parity.md)
- [Upstream feature parity](upstream-feature-parity.md)
- [Browser parity](browser-parity.md)
- [Browser release validation](browser-release-validation.md)

If a feature claim changes, update the inventory, parity docs, and tests in the
same change.

## Safety Invariants

Keep these invariants intact:

- Markdown files are the source of truth.
- Mutations must preserve Markdown sections they do not own.
- Paths must be validated before file reads and writes.
- Project mutations must use shared lock paths.
- Direct CLI writes and daemon-mediated writes must serialize on the same
  project root.
- The official upstream Backlog.md binary does not honor these Python locks, so
  do not run both mutation paths against the same live project during migration.
- The daemon runtime token and runtime record are private local state.
- Browser and TUI features should not expose shell execution settings that are
  unsafe for local web or terminal interaction.

## Validation Map

Useful focused checks:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
uv run --extra dev python -m pytest tests/test_mcp_protocol_sdk_free.py -q
uv run --extra dev python -m pytest tests/test_compat_report.py -q
uv run --extra dev python -m pytest tests/test_package_metadata.py -q
```

Before a PR that changes behavior, run the full suite:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src
git diff --check
```

For Markdown-only documentation changes, the focused package/docs tests,
repo-relative link checks, and `git diff --check` are usually sufficient.
