# backlog-md-py

`backlog-md-py` is a standalone Python implementation of the local-file
[Backlog.md](https://github.com/MrLesk/Backlog.md) task workflow. It keeps
Backlog.md projects as Markdown files on disk, while providing a Python CLI,
Python helper functions, SDK-free MCP server, singleton daemon for multi-agent
setups, browser board, and optional Textual TUI.

Use it when you want Backlog.md-compatible project tracking from Python tooling
or local agents without requiring a Node or Bun runtime.

## Status And Safety

This project is stable starting with the 1.0 release line. The audited
compatibility inventory reports the documented local-file CLI, Python helper,
MCP, daemon, browser-board, and TUI surfaces as implemented for the current
baseline. The supported 1.x contract is the behavior represented in the
compatibility inventory, stability policy, and parity docs.

The source of truth remains the `backlog/` Markdown directory in each project.
Before live mutation in a consuming project, run copied-repository smoke tests
and review the resulting diff. See the [stability policy](docs/stability-policy.md)
for the supported contract and release gate.

Do not alias `backlog-py` to `backlog` unless the target project has made an
explicit project cutover decision.

## Quick Start

For released versions, install from PyPI:

```bash
python -m pip install backlog-md-py
```

For unreleased commits, install from GitHub:

```bash
python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
```

Run the CLI against a Backlog.md project:

```bash
backlog-py --help
backlog-py --cwd /path/to/project task list --plain
backlog-py --cwd /path/to/project board
backlog-py --cwd /path/to/project browser --port 6420 --no-open
backlog-py compat status
```

For a safe first run, start with a scratch project:

```bash
mkdir -p /tmp/backlog-md-py-demo
backlog-py --cwd /tmp/backlog-md-py-demo init --defaults --no-git
backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain
backlog-py --cwd /tmp/backlog-md-py-demo board
```

## Interfaces At A Glance

- CLI: `backlog-py --cwd /path/to/project ...`
- Python module entry point: `python -m backlog_py ...`
- Python helpers: import from `backlog_py.mcp` and `backlog_py.storage.project`
- MCP stdio: `backlog-py-mcp`
- Multi-agent singleton daemon: `backlog-py daemon ensure`
- Browser board: `backlog-py --cwd /path/to/project browser --port 6420`
- Optional TUI: install `backlog-md-py[tui]`, then run
  `backlog-py --cwd /path/to/project tui`

The plain CLI and MCP tools are the recommended automation surfaces. The
browser board and TUI are human-facing project navigation surfaces.

## Agent And MCP Use

`backlog-py-mcp` is included by default and provides SDK-free MCP stdio mode:

```bash
backlog-py-mcp
```

For multi-agent environments, prefer one local singleton daemon and let MCP
clients connect through it:

```bash
backlog-py daemon ensure
backlog-py daemon status --json
```

See [integration.md](docs/integration.md) for CLI, Python helper, and MCP
configuration, and [singleton-daemon.md](docs/singleton-daemon.md) for daemon
setup and rollback guidance.

## How It Fits Together

All interfaces resolve a Backlog.md project, parse Markdown-backed records, and
delegate mutations to shared core services that preserve unowned Markdown
sections and serialize writes with local filesystem locks. The daemon is a
process-reuse and coordination layer, not a separate database. The optional
SQLite index is disposable read acceleration; Markdown remains authoritative.

See [architecture.md](docs/architecture.md) for a contributor-oriented system
map.

## Documentation

Start with the [documentation index](docs/README.md). The most commonly used
references are:

- [Getting started](docs/getting-started.md)
- [Integration guide](docs/integration.md)
- [Architecture guide](docs/architecture.md)
- [Stability policy](docs/stability-policy.md)
- [Singleton daemon guide](docs/singleton-daemon.md)
- [Cutover validation checklist](docs/cutover-validation.md)
- [Browser release validation](docs/browser-release-validation.md)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [Release process](RELEASE.md)
- [Release checklist](docs/release-checklist.md)

## Development

Use Python 3.11, 3.12, or 3.13. Create a local virtual environment with `uv`
and install editable development dependencies:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Run the focused agent-critical gate or the full test suite:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
uv run --extra dev python -m pytest tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow and
[architecture.md](docs/architecture.md) for the source layout.
