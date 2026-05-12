# Contributing

`backlog-md-py` is an experimental Python compatibility implementation of
Backlog.md. Runtime code should stay free of Node/Bun dependencies; upstream
Backlog.md tooling is only for fixture refresh or parity-generation work.

## Local Setup

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Validation

Run the full local gate before opening a pull request:

```bash
python -m pytest tests -v
python -m bandit -r src
python -m build
python -m twine check dist/*
```

For focused agent-cutover work, also run:

```bash
python -m pytest tests/test_agent_critical_matrix.py -v
```

For consuming-project cutovers, follow `docs/cutover-validation.md` so mutation
smoke tests run against a copied repository before live Backlog.md files are
changed.

## Compatibility Scope

The current cutover target is non-interactive local-file agent workflows:

- Plain CLI task, document, milestone, board, search, and config operations.
- Pure Python MCP helper functions and workflow resources.
- Safe mutations that preserve unowned Markdown body sections and reject path
  traversal.

Browser UI, interactive TUI behavior, shell completion, hooks, auto-commit, and
remote git behavior are tracked as explicit deferrals in `docs/`.

## Change Guidelines

- Preserve exact task Markdown where a command does not own the section being
  edited.
- Reject ambiguous or unsafe paths before reading or writing files.
- Add or update tests for every behavior change.
- Keep public behavior aligned with the parity matrix and oracle manifest.
