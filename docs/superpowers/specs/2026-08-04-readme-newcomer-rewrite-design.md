# README Newcomer Rewrite — Design

## Goal

Rewrite `README.md` so someone who has never used Backlog.md can understand
what the tool does, see its features at a glance, and complete a first-run
tutorial without leaving the README. Approved approach: in-place rewrite of
`README.md` only; `docs/` remains the deep reference set.

## Audience And Requirements

- Primary reader: newcomer who has never used Backlog.md (upstream) or this
  Python port.
- Must explain the Backlog.md concept inline (Markdown-file task workflow).
- Must include a full quick-start tutorial in the README.
- Must add badges and a scannable feature table.
- Must trim the heavy "Status And Safety" content near the top: one short
  safety note up top, fuller safety/compatibility section lower down.
- Every command and flag must already exist and be verified against the current
  README and `docs/getting-started.md`; no invented surface.

## New README Structure

1. **Header and pitch**
   - Title, badges (PyPI version, supported Python versions, license).
   - One-paragraph pitch: what the tool does and who it is for.

2. **What is Backlog.md?**
   - 3-4 sentences: tasks, documents, decisions, and milestones live as plain
     Markdown files under a project's `backlog/` directory; the files are the
     source of truth, so they diff, review, and commit with the code; both
     humans and agents can read and write them.
   - Position `backlog-md-py` as the Python implementation of that workflow,
     requiring no Node or Bun runtime, and link upstream Backlog.md.

3. **Features table**
   - Table of interfaces: Interface | Command/entry point | What it is for.
   - Rows: CLI (`backlog-py`), module entry (`python -m backlog_py`), Python
     helpers (`backlog_py.mcp`, `backlog_py.storage.project`), MCP stdio
     (`backlog-py-mcp`), singleton daemon (`backlog-py daemon ensure`),
     browser board (`backlog-py ... browser`), optional TUI
     (`backlog-md-py[tui]`, `backlog-py ... tui`).
   - The `orchestration status` row from the current README's interface list
     is deliberately omitted as too advanced for a newcomer table.
   - Note that the plain CLI and MCP tools are the recommended automation
     surfaces; browser board and TUI are human-facing.

4. **Quick Start tutorial**
   - Install from PyPI; note the `[tui]` extra.
   - Step-by-step scratch project:
     - `mkdir -p /tmp/backlog-md-py-demo`
     - `backlog-py --cwd /tmp/backlog-md-py-demo init --defaults --no-git`
     - `backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain`
     - `backlog-py --cwd /tmp/backlog-md-py-demo task list --plain`
     - `backlog-py --cwd /tmp/backlog-md-py-demo task edit task-1 --notes "..." --plain`
     - `backlog-py --cwd /tmp/backlog-md-py-demo board`
     - One line telling the reader to inspect the generated `backlog/`
       Markdown files.
   - "Point at a real project" subsection: read-only commands first
     (`task list --plain`, `task <id> --plain`, `board`), then the browser
     board (`browser --port 6420 --no-open`) and TUI.
   - "Use with agents (MCP)" subsection: `backlog-py-mcp`, optional
     `backlog-py daemon ensure` / `daemon status --json`, and
     `backlog-py --cwd /path/to/project agents --update-instructions`; link
     `docs/integration.md` and `docs/singleton-daemon.md`.

5. **Safety and compatibility (moved down, trimmed)**
   - Short bullets: Markdown files are the source of truth; smoke-test
     mutations on a copied repository and review the diff before live writes;
     do not alias `backlog-py` to `backlog` without an explicit project
     cutover decision.
   - Links to `docs/stability-policy.md` and `docs/cutover-validation.md` for
     the full contract.
   - The top of the README keeps only a single short safety sentence (try a
     scratch project first), not the current full section.

6. **Documentation and Development**
   - Docs link list with exactly these entries: `docs/README.md` (index),
     `docs/getting-started.md`, `docs/integration.md`,
     `docs/architecture.md`, `docs/stability-policy.md`,
     `docs/singleton-daemon.md`, `docs/cutover-validation.md`,
     `CHANGELOG.md`, `CONTRIBUTING.md`, `RELEASE.md`.
   - Keep the development section essentially as-is.

## Non-Goals

- No changes to `docs/getting-started.md` or other docs in this pass.
- No screenshots or embedded images.
- No changes to code, packaging, or tests.
- No alias/cutover guidance beyond what already exists in the docs.

## Verification

- Every command in the new README is copied from the current README or
  `docs/getting-started.md` (already verified sources).
- Badge targets are verified against `pyproject.toml` before use: shields.io
  PyPI badge for `backlog-md-py`, Python versions from
  `requires-python`/classifiers, license from the `LICENSE` file/metadata.
- All relative links resolve to existing files.
- Markdown renders cleanly (headings nested correctly, table well-formed).
