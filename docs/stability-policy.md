# Stability Policy

`backlog-md-py` is in beta starting with the 0.2.0 release line. Beta means the
documented local-file CLI, MCP, daemon, browser-board, and Python helper
workflows are ready for real consuming-project integration after validation.
It does not mean the project has a 1.0 API freeze.

## Supported Contract

The beta support contract covers:

- Python 3.11, 3.12, and 3.13 package installation from released wheels and
  source distributions.
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

The audited upstream baseline is `backlog.md@1.45.1`. The compatibility
inventory and oracle manifest define the implemented local behavior for that
baseline. Future upstream audits should update the inventory, oracle manifest,
and parity docs before changing the supported contract.

## Change Policy

Before 1.0, minor releases may refine CLI, MCP, browser, or Python helper
behavior when the parity inventory or safety model requires it. Patch releases
should preserve the beta supported contract and focus on bug fixes,
documentation, compatibility evidence, and release automation.

Known behavior outside the beta contract must stay explicitly documented in the
parity docs instead of being implied by the README.

## Beta Release Gate

A beta release candidate should pass:

- `uv run --extra dev python -m pytest tests -v`
- `uv run --extra dev python -m bandit -r src`
- `git diff --check`
- `uv run --extra dev python -m build`
- `uv run --extra dev python -m twine check dist/*`
- `backlog-py compat status --release-evidence <manifest.json>`
- copied-repository mutation smoke with diff review
- direct `backlog-py-mcp` stdio smoke
- singleton daemon smoke for multi-agent use

Release notes should link the validation record or summarize the evidence used
for the tag.
