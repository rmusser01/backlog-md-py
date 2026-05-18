# backlog-md-py

Standalone Python compatibility implementation of Backlog.md.

This repository was split out from the `tools/backlog-py` implementation
incubated in `tldw_server` so it can be used by other projects without taking a
dependency on that monorepo.

This package is experimental, but the first agent-critical local-file cutover
gate has passed for the documented CLI, Python helper, and MCP workflows. Keep
live-repository mutation behind copied-repository smoke tests and review, and
do not alias this command to `backlog` unless that is an explicit project-level
cutover decision.

The Python import package remains `backlog_py`, and the experimental CLI command
is still `backlog-py`.

## Installing In Another Project

Until package publishing is configured, install directly from GitHub:

```bash
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
```

The `backlog-py-mcp` stdio entry point is included by default and does not
require the Python MCP SDK.

Use `backlog-py` or `python -m backlog_py` for the experimental CLI:

```bash
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project task list --priority high -a codex -l implementation --milestone "Release 1" --plain
python -m backlog_py --cwd /path/to/project board
```

Start the optional loopback browser board service:

```bash
backlog-py --cwd /path/to/project browser --port 6420 --no-open
```

Check the built-in cutover inventory without mutating a project:

```bash
backlog-py compat status
backlog-py compat status --json
```

Install shell completion for the `backlog-py` command:

```bash
backlog-py completion install --shell zsh
backlog-py completion install --shell bash
backlog-py completion install --shell fish
backlog-py completion install --shell pwsh
```

See `docs/integration.md` for CLI, Python helper, and MCP integration notes.

## Singleton Daemon For Agents

For multi-agent environments, start one local daemon and let `backlog-py-mcp`
forward stdio MCP traffic to it:

```bash
backlog-py daemon ensure
backlog-py daemon status --json
backlog-py-mcp
backlog-py daemon stop
```

The daemon exposes a loopback `/mcp` JSON-RPC endpoint protected by a runtime
token. The `backlog-py-mcp` command discovers a healthy daemon automatically; if
none is running, it falls back to direct SDK-free stdio mode. See
`docs/singleton-daemon.md` for Codex configuration notes, process-count checks,
and rollback guidance.

## Optional Orchestration Metadata

`backlog-md-py` can parse optional `orchestration` frontmatter for agent or
workflow coordinators. The current supported slice is read-only: parse metadata,
validate it against the default workflow policy, and report eligible tasks,
active claims, stale leases, and status summaries. The library does not launch
agents or mutate orchestration state in this slice.

## Oracle Fixtures

Compatibility fixtures are pinned to explicit upstream Backlog.md metadata. The
oracle manifest records `backlog.md@1.45.1`, source kind, source reference,
package metadata hash, generation date, and the agent-critical
commands/resources/tools that future golden fixtures must cover.

Upstream Backlog.md and its Node/Bun toolchain are only allowed in fixture
generation or refresh jobs. Normal `backlog-py` runtime, regular tests, and
future repository cutover paths must remain Node/Bun-free.

## Development

Use Python 3.11, 3.12, or 3.13. Create a local virtual environment with `uv`
and install editable development dependencies:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Then run the focused or full test suite:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
uv run --extra dev python -m pytest tests -v
```

See `CONTRIBUTING.md` for the full local validation gate.
See `docs/cutover-validation.md` for the reusable validation checklist and
`docs/cutover-validation-results-2026-05-13.md` for the first completed
agent-critical cutover validation record.

## Agent Cutover Gate

Agent-critical parity is tracked in `docs/agent-critical-parity.md`. The matrix
enumerates every CLI command, MCP resource, and pure MCP helper that blocks the
first local-file agent cutover candidate, plus the browser, interactive, hook,
and git behaviors that are explicitly deferred.

The gate is enforced by `tests/test_agent_critical_matrix.py`: every
`golden-required` inventory item must have a matching oracle manifest fixture,
and the matrix document must mention every implemented or deferred item. Run it
with:

```bash
python -m pytest tests/test_agent_critical_matrix.py -v
```

Before enabling this in another project, also run the full local validation and
copied-repo mutation smoke documented in `docs/cutover-validation.md`. Mutation
smoke commands must use a temporary copy, not the live repository backlog.

Browser editor/settings gaps and the remaining hook-bypass decision are
tracked separately from the first agent cutover candidate:

- `docs/browser-parity.md` records browser requirements such as rich Markdown
  editing and mobile behavior. The custom-port browser service, drag/drop
  status movement, basic task creation/editing, task archive confirmation, and
  read-only task detail dialog with checklist state controls are implemented,
  but rich browser editing remains deferred.
- `docs/interactive-deferrals.md` records the implemented task detail/editor,
  search filter, board, and overview flows, remaining CLI/TUI deferrals, hook
  bypass, fetch-only remote operations, and the opt-in auto-commit runtime policy.
- `docs/upstream-feature-parity.md` records the current upstream feature-set
  audit and the work that remains before claiming full clone parity.
