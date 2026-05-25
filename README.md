# backlog-md-py

`backlog-md-py` is a standalone Python compatibility implementation of
Backlog.md for local-file task workflows, CLI, MCP, and agent integration
without a Node/Bun runtime dependency.

## Status And Safety

This project is in beta. The agent-critical cutover gate and audited parity
inventory have passed for the documented local-file CLI, Python helper, MCP,
daemon, and browser-board workflows. Beta means the supported contract is ready
for real project integration after validation, but it is not yet a 1.0 API
freeze.

Before live mutation in a consuming project, run copied-repository smoke tests
and review the resulting Backlog.md diff. See the
[stability policy](docs/stability-policy.md) for the supported contract and
release gate.

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

- Optional terminal Kanban board: `python -m pip install "backlog-md-py[tui]"`
  and run `backlog-py --cwd /path/to/project tui` for keyboard navigation,
  task detail, dependency visibility, filters, checklist toggles, and
  create/edit/move/archive workflows.

The browser board is optional; see the
[browser release validation guide](docs/browser-release-validation.md) for the
release-readiness evidence model.

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

## Documentation

Start with the [documentation index](docs/README.md). The most commonly used
references are:

- [Integration guide](docs/integration.md)
- [Stability policy](docs/stability-policy.md)
- [Singleton daemon guide](docs/singleton-daemon.md)
- [Cutover validation checklist](docs/cutover-validation.md)
- [Browser release validation](docs/browser-release-validation.md)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full local validation and release
gate.
