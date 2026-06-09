# Documentation Onboarding Refresh Design

## Purpose

New users and contributors should be able to understand `backlog-md-py` in a
few minutes: what it is, why it exists, how to try it safely, how the main
runtime pieces fit together, and where to contribute without reading the whole
codebase.

The current documentation has the right facts, but the first path through it is
weighted toward release gates and parity evidence. This refresh keeps the README
short, moves deeper explanation into docs, and adds a contributor-oriented
architecture map.

## Audience

- New users evaluating whether to install `backlog-md-py`.
- Agent integrators deciding whether to use CLI, MCP stdio, or the singleton
  daemon.
- Contributors trying to find the right source modules, tests, and validation
  commands.
- Maintainers checking parity, release readiness, and safety invariants.

## Scope

Update:

- `README.md`
- `docs/README.md`
- `docs/getting-started.md`
- `CONTRIBUTING.md`

Add:

- `docs/architecture.md`

Do not rewrite the parity, release-readiness, browser-validation, or singleton
daemon guides except for links needed from the refreshed entry points.

## Information Architecture

The README remains the project landing page. It should answer:

- What is `backlog-md-py`?
- When should someone use it?
- What are the primary interfaces?
- What is the safest first command path?
- Where should different readers go next?

`docs/README.md` becomes an intent-based index with four paths:

- New user.
- Agent or MCP integrator.
- Contributor.
- Maintainer or parity reviewer.

`docs/getting-started.md` focuses on the first successful experience: install,
initialize a scratch project, run read-only commands, try one safe mutation, and
then move to copied-repository validation before touching a live project.

`CONTRIBUTING.md` stays practical and points contributors to the new architecture
guide instead of embedding a large architecture handbook.

`docs/architecture.md` explains the system in terms of stable responsibilities:

- Markdown files are the source of truth.
- `storage` and `core` own project discovery and mutations.
- CLI, Python helpers, MCP, daemon, browser, and TUI are adapters over shared
  behavior.
- Runtime locks and the daemon coordinate local agent processes.
- The SQLite index is disposable read acceleration, not durable storage.
- Compatibility inventory and oracle tests define parity expectations.

## Safety Model

The docs should repeat the critical migration rule in plain language:

- Start in a scratch project.
- Use read-only commands first on existing projects.
- Run mutation smoke tests in a copy before live writes.
- Do not alias `backlog-py` to `backlog` without an explicit cutover decision.
- Do not run upstream Backlog.md mutation paths and `backlog-md-py` mutation
  paths against the same live project during migration.

## Verification

Run the documentation-relevant local checks:

- `uv run --extra dev python -m pytest tests/test_package_metadata.py tests/test_compat_report.py -q`
- `git diff --check`
- a local Markdown link check for repo-relative links in the changed Markdown
  files

Because this task changes Markdown only, Bandit is not required. If any Python
code changes are introduced, run `uv run --extra dev python -m bandit -r src`.
