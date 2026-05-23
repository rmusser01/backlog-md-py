# README And Documentation Refresh Design

## Decision

Refresh the project documentation around a short external-facing README and a
deeper docs manual. The README should serve new external users first, agent and
MCP integrators second, and maintainers or release operators mostly through
links to focused docs.

This is an editorial and navigation pass only. It should not change runtime
behavior, package metadata, workflows, compatibility inventory, or release
gates.

## Goals

- Make the project understandable to a new reader in the first screen of the
  README.
- Keep the README concise, command-oriented, and honest about alpha status.
- Preserve safety guidance for live Backlog.md mutation without overwhelming
  the quick start.
- Add a clear documentation index so existing parity, cutover, daemon, and
  release-readiness docs are easy to find.
- Add a getting-started guide that teaches common usage without duplicating the
  full integration reference.

## Non-Goals

- Do not re-audit upstream Backlog.md parity.
- Do not change CLI, MCP, browser, daemon, package, or release behavior.
- Do not rename commands, imports, files, or distribution metadata.
- Do not move existing reference docs unless a link target requires it.
- Do not claim PyPI availability before the first tagged release has actually
  published.
- Do not leave stale setup commands in linked first-party docs when the refresh
  exposes them to new readers; for example, the MCP stdio entry point is now
  installed by default and docs should not tell users to install a removed
  `mcp` optional extra.

## Audience Priority

1. New external users and evaluators: what this is, why it exists, how to try
   it safely.
2. Agent and tool integrators: how to wire CLI, MCP stdio, and singleton daemon
   flows without Node or Bun.
3. Maintainers and release operators: how to validate parity, cut releases, and
   reason about cutover risk.

## README Structure

The top-level README becomes a landing page rather than the main manual.

Planned sections:

1. `# backlog-md-py`
   - One-paragraph value proposition.
   - Explain that this is a standalone Python compatibility implementation of
     Backlog.md for local-file task workflows, CLI, MCP, and agent integration
     without a Node/Bun runtime dependency.
2. `## Status And Safety`
   - State that the package is alpha/experimental.
   - State that the agent-critical cutover gate has passed.
   - Tell users to use copied-repository smoke tests before live mutation.
   - Tell users not to alias `backlog` unless the target project explicitly
     chooses that cutover.
3. `## Quick Start`
   - Show `python -m pip install backlog-md-py` as the post-tag release path.
   - Show GitHub install as the reliable current path until the first tagged
     release is published.
   - Include a small set of practical commands:
     - `backlog-py --help`
     - `backlog-py --cwd /path/to/project task list --plain`
     - `backlog-py --cwd /path/to/project board`
     - `backlog-py --cwd /path/to/project browser --port 6420 --no-open`
     - `backlog-py compat status`
4. `## Agent And MCP Use`
   - Explain that `backlog-py-mcp` is included by default and SDK-free.
   - Mention direct stdio mode.
   - Recommend the singleton daemon for multi-agent setups.
   - Link to deeper integration and daemon docs.
5. `## Documentation`
   - Link to `docs/README.md` as the docs index.
   - Link directly to getting started, integration, singleton daemon, cutover
     validation, browser release validation, and contributing.
6. `## Development`
   - Keep local setup and tests brief.
   - Link to `CONTRIBUTING.md` for the full validation and release gate.

## Documentation Structure

Add two new user-facing docs:

### `docs/README.md`

Documentation index organized by reader goal:

- "I want to try it" -> `getting-started.md`
- "I want to integrate agents or MCP" -> `integration.md`,
  `singleton-daemon.md`
- "I want to validate a migration" -> `cutover-validation.md`,
  `cutover-validation-results-2026-05-13.md`
- "I want to understand parity or release readiness" ->
  `agent-critical-parity.md`, `upstream-feature-parity.md`,
  `browser-parity.md`, `browser-release-validation.md`
- "I want to contribute or release" -> `../CONTRIBUTING.md`

### `docs/getting-started.md`

Primary user guide:

- What `backlog-md-py` is and is not.
- Installation paths, including the current PyPI caveat.
- A safe scratch-project quick start using `backlog-py --cwd <scratch-dir> init
  --defaults` before showing commands against an existing project.
- How to point commands at an existing Backlog.md project.
- Common read-only CLI commands for listing, searching, viewing, board display,
  browser board startup, and compatibility status.
- Mutation examples for creating and editing tasks, clearly framed as commands
  to run first in a scratch project or copied repository.
- MCP basics: direct `backlog-py-mcp`, when to use the daemon, where to find
  full integration details.
- Read-only compatibility status checks.
- Mutation safety checklist for first-time adoption.
- Next links for integration, daemon, cutover, browser readiness, and
  contributing.

Existing specialized docs stay in place:

- `docs/integration.md` remains the deeper CLI/Python/MCP integration
  reference.
- `docs/singleton-daemon.md` remains the multi-agent daemon manual.
- `docs/cutover-validation.md` remains the migration gate.
- `docs/upstream-feature-parity.md`, `docs/agent-critical-parity.md`,
  `docs/browser-parity.md`, and `docs/browser-release-validation.md` remain
  maintainer and release-readiness references.

## Content Rules

- Keep README concise and command-oriented.
- Keep install wording current-state accurate: PyPI is the release path after
  the first tag; direct GitHub install is the reliable current path until that
  release exists.
- Use "alpha" or "experimental" honestly, but do not bury the value proposition
  under warnings.
- Keep maintainer-only parity detail out of the README except links.
- Do not duplicate the long CLI command catalog from `docs/integration.md`.
- Use consistent terms:
  - `backlog-md-py` for the distribution and project.
  - `backlog_py` for imports.
  - `backlog-py` for the CLI.
  - `backlog-py-mcp` for MCP stdio.

## Verification

The implementation should verify:

- Local README/docs links reference existing files.
- `git diff --check` passes.
- Package metadata/docs tests pass if touched files are covered by tests.
- Because `pyproject.toml` uses `README.md` as the package readme, README
  changes require a package build and `twine check` so PyPI long-description
  rendering issues are caught before release.
- No runtime tests are required unless the implementation changes checked
  workflow, package metadata, or executable behavior.

## Acceptance Criteria

- README is shorter and easier to scan than the current version.
- README answers "what is this?", "how do I try it?", and "where do I go next?"
  without requiring readers to understand the full parity history.
- `docs/README.md` provides a clear route through existing docs.
- `docs/getting-started.md` gives a new user enough context to install, inspect
  a scratch or existing project, run basic read-only commands, start the browser
  board, and avoid unsafe live mutation before trying create/edit examples.
- Existing integration, daemon, parity, cutover, and release-validation docs
  remain discoverable from both README and the docs index.
- The refreshed docs do not reintroduce references to nonexistent optional
  extras such as `.[mcp]`.
