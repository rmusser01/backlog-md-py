# Contributing

`backlog-md-py` is an experimental Python compatibility implementation of
Backlog.md. Runtime code should stay free of Node/Bun dependencies; upstream
Backlog.md tooling is only for fixture refresh or parity-generation work.

## Local Setup

Use Python 3.11, 3.12, or 3.13. The canonical local setup uses `uv` to create
and populate a project-local virtual environment:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev,mcp]"
```

## Validation

Run the full local gate before opening a pull request:

```bash
uv run --extra dev --extra mcp python -m pytest tests -v
uv run --extra dev python -m bandit -r src
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
```

For focused agent-cutover work, also run:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
```

For consuming-project cutovers, follow `docs/cutover-validation.md` so mutation
smoke tests run against a copied repository before live Backlog.md files are
changed.

## Release Process

Releases are tag-driven. Pushing a `v*` tag runs `.github/workflows/release.yml`,
which builds the source distribution and wheel, runs `twine check`, smoke-tests
the installed wheel and SDK-free MCP entry point, attaches `dist/*` to the
GitHub Release, and publishes the same artifacts to PyPI through trusted
publishing.

Before pushing a release tag:

- Confirm `src/backlog_py/__init__.py` has the intended `__version__`.
- Confirm PyPI has a trusted publisher for project `backlog-md-py`, repository
  `rmusser01/backlog-md-py`, workflow `.github/workflows/release.yml`, and
  environment `pypi`.
- Run the full local validation gate above.

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
