# Stability Policy

`backlog-md-py` is stable starting with the 1.0.0 release line. Stable means
the documented local-file CLI, MCP, daemon, browser-board, TUI, and Python
helper workflows are covered by the current major compatibility contract after
release validation. The contract in force is the 2.x line; see the 2.0.0
CHANGELOG entry for the breaking changes that closed the 1.x line and the
migration step for each.

## Supported Contract

The stable support contract covers:

- Python 3.11, 3.12, 3.13, and 3.14 package installation from released wheels
  and source distributions.
- The `backlog-py` CLI and `python -m backlog_py` module entry point for the
  operations represented in the compatibility inventory.
- The SDK-free `backlog-py-mcp` stdio entry point, pure MCP helper functions,
  workflow resources, and singleton daemon forwarding path.
- Local Backlog.md project discovery, task/document/decision/milestone/config
  parsing, and safe mutations that preserve unowned Markdown sections.
- The opt-in disposable SQLite read index as a rebuildable cache only; its file
  format and contents are not stable API and must never replace Markdown as the
  task source of truth.
- The browser board scope described by the browser parity and browser release
  validation docs.

Projects should still run copied-repository mutation smoke before live writes,
and should not alias `backlog-py` to `backlog` without an explicit cutover
decision.

## Compatibility Baseline

The audited upstream baseline is `backlog.md@1.45.2`. The compatibility
inventory and oracle manifest define the implemented local behavior for that
baseline. The `v1.45.2` upstream delta adds Windows ARM prebuilt binary
packaging support and release workflow updates without changing the audited
CLI, MCP, browser, or configuration surfaces. Future upstream audits should
update the inventory, oracle manifest, and parity docs before changing the
supported contract.

## Change Policy

Within a major line, minor releases may add backward-compatible CLI, MCP,
browser, TUI, daemon, or Python helper behavior when the parity inventory or
safety model requires it. Patch releases should preserve the stable supported
contract and focus on bug fixes, documentation, compatibility evidence, and
release automation. Breaking changes require a new major version or an explicit
deprecation path.

Known behavior outside the stable contract must stay explicitly documented in the
parity docs instead of being implied by the README.

## Trust Model

The project files are treated as data, with one deliberate exception:
`onStatusChange` runs a shell command. From 2.0.0 a command carried in a task
file's own frontmatter requires `taskFrontmatterStatusCallbacks: true`, because
task markdown routinely arrives from branches and pull requests.

That gate is defence in depth, not a trust boundary. `backlog/config.yml` comes
through the same channel, so a hostile repository can set `onStatusChange` at
config level or enable the key itself. Opening a project means trusting its
config file. Cloning a repository you do not trust and running any mutating
command against it is outside the supported safety model.

## Stable Release Gate

A stable release candidate should pass:

- `uv run --extra dev python -m pytest tests -v`
- `uv run --extra dev --extra tui python -m pytest tests -q`
- `uv run --extra dev python -m bandit -r src`
- `git diff --check`
- `uv run --extra dev python -m build`
- `uv run --extra dev python -m twine check dist/*`
- `backlog-py compat status --json`
- `backlog-py compat status --release-evidence <manifest.json>` when release
  notes advertise full browser parity
- copied-repository mutation smoke with diff review
- direct `backlog-py-mcp` stdio smoke
- singleton daemon smoke for multi-agent use

Release notes should link the validation record or summarize the evidence used
for the tag.
