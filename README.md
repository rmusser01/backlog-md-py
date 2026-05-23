# backlog-md-py

`backlog-md-py` is a standalone Python compatibility implementation of
Backlog.md for local-file task workflows, CLI, MCP, and agent integration
without a Node/Bun runtime dependency.

## Status And Safety

This project is alpha/experimental. The agent-critical cutover gate has passed
for the documented local-file CLI, Python helper, and MCP workflows, but live
mutation should still be validated in copied repositories before use.

Do not alias `backlog-py` to `backlog` unless the target project has made an
explicit project cutover decision.

## Quick Start

After the first tagged release is published, install from PyPI:

```bash
python -m pip install backlog-md-py
```

Until then, the reliable current path is installing from GitHub:

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
- [Singleton daemon guide](docs/singleton-daemon.md)
- [Cutover validation checklist](docs/cutover-validation.md)
- [Browser release validation](docs/browser-release-validation.md)
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
