# backlog-md-py

Standalone Python compatibility implementation of Backlog.md.

This repository was split out from the `tools/backlog-py` implementation
incubated in `tldw_server` so it can be used by other projects without taking a
dependency on that monorepo.

This package is experimental. Do not put it on PATH as `backlog` and do not use
it to mutate live repository data until the cutover gates in the design spec
pass.

The Python import package remains `backlog_py`, and the experimental CLI command
is still `backlog-py`.

## Oracle Fixtures

Compatibility fixtures are pinned to explicit upstream Backlog.md release
metadata. The initial oracle manifest records `backlog.md@1.44.0`, source kind,
source reference, package metadata hash, generation date, and the agent-critical
commands/resources/tools that future golden fixtures must cover.

Upstream Backlog.md and its Node/Bun toolchain are only allowed in fixture
generation or refresh jobs. Normal `backlog-py` runtime, regular tests, and
future repository cutover paths must remain Node/Bun-free.

## Development

Install the package in editable mode with test dependencies available:

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

Then run the focused or full test suite:

```bash
python -m pytest tests/test_agent_critical_matrix.py -v
python -m pytest tests -v
```

See `CONTRIBUTING.md` for the full local validation gate.

## Agent Cutover Gate

Agent-critical parity is tracked in `docs/agent-critical-parity.md`. The matrix
enumerates every CLI command, MCP resource, and pure MCP helper that blocks the
first local-file agent cutover candidate, plus the browser, interactive,
completion, hook, and git behaviors that are explicitly deferred.

The gate is enforced by `tests/test_agent_critical_matrix.py`: every
`golden-required` inventory item must have a matching oracle manifest fixture,
and the matrix document must mention every implemented or deferred item. Run it
with:

```bash
python -m pytest tests/test_agent_critical_matrix.py -v
```

Before cutover, also run the full local validation and copied-repo mutation
smoke documented in the implementation plan. Mutation smoke commands must use a
temporary copy, not the live repository backlog.

Browser and interactive behavior is tracked separately from the first agent
cutover candidate:

- `docs/browser-parity.md` records browser requirements such as drag-and-drop,
  service mode, rich Markdown editing, and mobile behavior.
- `docs/interactive-deferrals.md` records CLI/TUI, `onStatusChange`,
  auto-commit, hook bypass, and remote-operation deferrals.
