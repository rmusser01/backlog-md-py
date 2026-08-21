# Contributing

`backlog-md-py` is a stable 2.x Python compatibility implementation of
Backlog.md. Runtime code should stay free of Node/Bun dependencies; upstream
Backlog.md tooling is only for fixture refresh or parity-generation work.

## How The Project Is Organized

Start with [docs/architecture.md](docs/architecture.md) for the system map. The
short version:

- Markdown files under each consuming project's `backlog/` directory are the
  source of truth.
- `src/backlog_py/core/` owns task, document, decision, milestone, board export,
  project initialization, and generated agent-instruction behavior.
- `src/backlog_py/storage/` owns project discovery and config loading.
- `src/backlog_py/markdown/` owns task parsing and section-preserving
  serialization.
- `src/backlog_py/runtime/` owns local state directories, locks, and coordinated
  mutations.
- `src/backlog_py/mcp/` owns pure helper functions, SDK-free JSON-RPC protocol
  handling, MCP resources, and the tool catalog.
- `src/backlog_py/daemon/` owns singleton process lifecycle.
- `src/backlog_py/browser/` and `src/backlog_py/tui/` own human-facing board
  interfaces.
- `src/backlog_py/compat/`, `src/backlog_py/oracle/`, and related tests protect
  parity claims.

Adapters should delegate shared behavior to core services instead of
reimplementing task mutation semantics. This keeps CLI, MCP, daemon, browser,
and TUI behavior aligned.

## Local Setup

Use Python 3.11, 3.12, 3.13, or 3.14. CI tests all four. The canonical local
setup uses `uv` to create and populate a project-local virtual environment:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Development Workflow

Use a clean branch or worktree for each change. For behavior changes, start with
the focused test that should fail, implement the smallest compatible change, and
then broaden verification.

When changing public behavior:

- Update or add tests near the changed module.
- Preserve unowned Markdown sections during mutations.
- Validate paths before reading or writing files.
- Keep CLI, Python helper, MCP, daemon, browser, and TUI behavior aligned when
  they expose the same operation.
- Update compatibility inventory, oracle fixtures, parity docs, or release
  evidence docs when the supported surface changes.
- Update user-facing docs for changed commands, migration guidance, or safety
  behavior.

For documentation-only changes, keep the README short and move durable detail
into `docs/`. Prefer intent-based links over duplicating long command catalogs.

## Validation

Run the full local gate before opening a pull request:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src
uv run --extra dev python -m ruff check src tests scripts
uv run --extra dev python scripts/check_quality_baseline.py
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
```

Both static-analysis commands block CI. Regular `ruff check` covers source,
tests, and repository scripts. The quality-baseline checker runs mypy and each
ignored Ruff rule, requiring exact per-file and per-rule counts; increases and
improvements must update the reviewed baseline in the same change.

For focused agent-cutover work, also run:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
```

Useful focused checks:

```bash
uv run --extra dev python -m pytest tests/test_mcp_protocol_sdk_free.py -q
uv run --extra dev python -m pytest tests/test_compat_report.py -q
uv run --extra dev python -m pytest tests/test_package_metadata.py -q
```

For consuming-project cutovers, follow `docs/cutover-validation.md` so mutation
smoke tests run against a copied repository before live Backlog.md files are
changed.

## Release Process

Releases are tag-driven, and the tag is created automatically. When a commit on
`main` changes `src/backlog_py/__init__.py` and the CI workflow for that commit
concludes successfully, `.github/workflows/auto-release-tag.yml` pushes
`v<version>` and dispatches `.github/workflows/release.yml`, which builds the
source distribution and wheel, runs `twine check`, smoke-tests the installed
wheel and SDK-free MCP entry point, attaches `dist/*` to the GitHub Release, and
publishes the same artifacts to PyPI through trusted publishing.

A failing CI run on `main` blocks tagging and therefore blocks publishing.
Manual tagging is a fallback only; see `RELEASE.md`.

Before merging a version bump, or pushing a release tag by hand:

- Confirm `src/backlog_py/__init__.py` has the intended `__version__`.
- Confirm `pyproject.toml`, `CHANGELOG.md`, and `docs/stability-policy.md`
  describe the intended release status.
- Follow `docs/release-checklist.md` for the version, validation, tag,
  post-publish, and failure-handling gates.
- Confirm PyPI has a trusted publisher for project `backlog-md-py`, repository
  `rmusser01/backlog-md-py`, workflow `.github/workflows/release.yml`, and
  environment `pypi`.
- Run the full local validation gate above.

## Compatibility Scope

The supported 2.x contract is defined in `docs/stability-policy.md`. Breaking
changes to it need a major version bump. In short, the current cutover target
covers:

- Plain CLI task, document, milestone, board, search, and config operations.
- Pure Python MCP helper functions and workflow resources.
- Safe mutations that preserve unowned Markdown body sections and reject path
  traversal.

Browser UI, interactive TUI behavior, shell completion, hooks, auto-commit, and
remote git behavior are tracked in the compatibility inventory and parity docs.

## Change Guidelines

- Preserve exact task Markdown where a command does not own the section being
  edited.
- Reject ambiguous or unsafe paths before reading or writing files.
- Add or update tests for every behavior change.
- Keep public behavior aligned with the parity matrix and oracle manifest.
- Keep Markdown as the project source of truth; runtime records, locks, logs,
  and SQLite indexes are coordination or cache state only.
- Do not introduce a Node/Bun runtime dependency into normal package execution.
