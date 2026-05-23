# README Docs Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the top-level README and add first-user documentation while preserving existing parity, cutover, daemon, and release-reference docs.

**Architecture:** Treat the README as a concise landing page and move the learning path into `docs/getting-started.md`. Add `docs/README.md` as the documentation router, keep existing specialized docs in place, and make only focused stale-command cleanups where the new flow links readers into existing docs.

**Tech Stack:** Markdown documentation, Python packaging metadata from `pyproject.toml`, existing pytest/twine/build validation.

---

## File Structure

- Modify `README.md`: concise landing page for external users, agent/MCP integrators, and docs routing.
- Create `docs/README.md`: documentation index organized by reader goal.
- Create `docs/getting-started.md`: primary user guide with safe scratch-project flow, common commands, MCP basics, and safety checklist.
- Modify `docs/integration.md`: only targeted cleanup for stale setup guidance or links exposed by the new docs flow.
- Modify `CONTRIBUTING.md`: only if needed to remove stale optional-extra guidance or point contributors at the new docs index.
- Reference design spec: `docs/superpowers/specs/2026-05-22-readme-docs-refresh-design.md`.

## Task 1: README Landing Page

**Files:**
- Modify: `README.md`
- Create: `docs/README.md` placeholder only if needed for valid links before Task 3
- Reference: `docs/superpowers/specs/2026-05-22-readme-docs-refresh-design.md`

- [x] **Step 1: Replace README with concise structure**

  Draft these sections:
  - `# backlog-md-py`
  - `## Status And Safety`
  - `## Quick Start`
  - `## Agent And MCP Use`
  - `## Documentation`
  - `## Development`

  Requirements:
  - First paragraph says this is a standalone Python compatibility implementation of Backlog.md for local-file task workflows, CLI, MCP, and agent integration without a Node/Bun runtime dependency.
  - Status says alpha/experimental, agent-critical cutover gate has passed, mutation should be validated in copied repositories first, and users should not alias `backlog` without an explicit project cutover decision.
  - Install wording must say PyPI install applies after the first tagged release is published, and GitHub install is the reliable current path until then.
  - Include only the compact command examples from the design spec.
  - Keep the daemon and browser detail as links, not long explanations.

- [x] **Step 2: Self-check README against design**

  Run:
  ```bash
  rg -n "first tagged release|git\\+https://github.com/rmusser01/backlog-md-py.git|backlog-py-mcp|docs/README.md|cutover" README.md
  ```

  Expected:
  - Matches for current-state install wording.
  - Matches for MCP stdio.
  - Matches for docs index and cutover safety.

- [x] **Step 3: Commit README landing page**

  If `README.md` links to `docs/README.md`, ensure that file exists before this
  commit. If the full docs index is not written yet, add a short placeholder:
  ```markdown
  # Documentation

  The documentation index is expanded later in this branch. Start with
  `getting-started.md` after Task 2 lands.
  ```

  Then run:
  ```bash
  git add README.md docs/README.md
  git commit -m "Refresh README landing page"
  ```

## Task 2: Getting Started Guide

**Files:**
- Create: `docs/getting-started.md`
- Reference: `docs/integration.md`, `docs/singleton-daemon.md`, `docs/cutover-validation.md`

- [x] **Step 1: Create guide skeleton**

  Add these sections:
  - `# Getting Started`
  - `## What This Project Is`
  - `## Install`
  - `## Try It In A Scratch Project`
  - `## Point At An Existing Project`
  - `## Common CLI Commands`
  - `## Browser Board`
  - `## MCP And Multi-Agent Use`
  - `## Compatibility Status`
  - `## Mutation Safety Checklist`
  - `## Next Steps`

- [x] **Step 2: Add current-state install section**

  Include:
  ```bash
  python -m pip install backlog-md-py
  python -m pip install "git+https://github.com/rmusser01/backlog-md-py.git"
  ```

  State that PyPI install is the release path after the first tagged release and GitHub install is the reliable current path until then.

- [x] **Step 3: Add safe scratch-project flow**

  Include commands like:
  ```bash
  mkdir -p /tmp/backlog-md-py-demo
  backlog-py --cwd /tmp/backlog-md-py-demo init --defaults
  backlog-py --cwd /tmp/backlog-md-py-demo task list --plain
  backlog-py --cwd /tmp/backlog-md-py-demo task create "Try backlog-md-py" --plain
  backlog-py --cwd /tmp/backlog-md-py-demo task edit task-1 --notes "Edited in a scratch project." --plain
  backlog-py --cwd /tmp/backlog-md-py-demo board
  ```

  Explain that mutation examples should be tried in this scratch project or a copied repository before live use.

- [x] **Step 4: Add common read-only commands**

  Include:
  ```bash
  backlog-py --cwd /path/to/project task list --plain
  backlog-py --cwd /path/to/project task list --status "In Progress" --plain
  backlog-py --cwd /path/to/project task <id> --plain
  backlog-py --cwd /path/to/project search "release" --plain
  backlog-py --cwd /path/to/project board
  backlog-py compat status
  backlog-py compat status --json
  ```

- [x] **Step 5: Add browser, MCP, compatibility, and safety sections**

  Include:
  ```bash
  backlog-py --cwd /path/to/project browser --port 6420 --no-open
  backlog-py-mcp
  backlog-py daemon ensure
  backlog-py daemon status --json
  ```

  Link to `integration.md`, `singleton-daemon.md`, `cutover-validation.md`, and `browser-release-validation.md`.

- [x] **Step 6: Commit getting-started guide**

  Run:
  ```bash
  git add docs/getting-started.md
  git commit -m "Add getting started guide"
  ```

## Task 3: Documentation Index

**Files:**
- Modify: `docs/README.md`
- Reference: existing `docs/*.md`

- [x] **Step 1: Create docs index**

  Replace any placeholder from Task 1 with a goal-oriented index with these routes:
  - Try it: `getting-started.md`
  - Integrate agents or MCP: `integration.md`, `singleton-daemon.md`
  - Validate migration: `cutover-validation.md`, `cutover-validation-results-2026-05-13.md`
  - Understand parity or release readiness: `agent-critical-parity.md`, `upstream-feature-parity.md`, `browser-parity.md`, `browser-release-validation.md`
  - Contribute or release: `../CONTRIBUTING.md`

  Keep each route to one short paragraph or bullet set.

- [x] **Step 2: Verify index links point to existing files**

  Run:
  ```bash
  test -f docs/getting-started.md
  test -f docs/integration.md
  test -f docs/singleton-daemon.md
  test -f docs/cutover-validation.md
  test -f docs/cutover-validation-results-2026-05-13.md
  test -f docs/agent-critical-parity.md
  test -f docs/upstream-feature-parity.md
  test -f docs/browser-parity.md
  test -f docs/browser-release-validation.md
  test -f CONTRIBUTING.md
  ```

  Expected: all commands exit 0.

- [x] **Step 3: Commit docs index**

  Run:
  ```bash
  git add docs/README.md
  git commit -m "Expand documentation index"
  ```

## Task 4: Targeted Existing Docs Cleanup

**Files:**
- Modify: `docs/integration.md`
- Modify: `CONTRIBUTING.md` only if needed

- [x] **Step 1: Search for stale optional-extra guidance**

  Run:
  ```bash
  rg -n "\\[dev,mcp\\]|--extra mcp|\\.\\[mcp\\]|mcp optional|MCP SDK" README.md docs CONTRIBUTING.md
  ```

  Expected:
  - No instructions that tell users to install nonexistent `mcp` extras.
  - Mentions that the MCP SDK is not required are acceptable.

- [x] **Step 2: Patch stale setup guidance only**

  If stale setup guidance exists, replace it with commands that match `pyproject.toml`:
  ```bash
  uv pip install -e ".[dev]"
  uv run --extra dev python -m pytest tests -v
  ```

  Do not rewrite parity, cutover, or daemon reference docs unless they contain stale commands surfaced by the new index/guide.

- [x] **Step 3: Commit targeted cleanup**

  If files changed:
  ```bash
  git add docs/integration.md CONTRIBUTING.md
  git commit -m "Clean up documentation setup commands"
  ```

  If no files changed, record that no cleanup was needed in the implementation notes.

## Task 5: Link And Package Validation

**Files:**
- Validate: `README.md`
- Validate: `docs/README.md`
- Validate: `docs/getting-started.md`
- Validate: changed existing docs

- [x] **Step 1: Check local Markdown links**

  Run:
  ```bash
  python - <<'PY'
  from pathlib import Path
  import re
  files = [Path("README.md"), Path("docs/README.md"), Path("docs/getting-started.md")]
  failures = []
  for file in files:
      text = file.read_text(encoding="utf-8")
      for target in re.findall(r"\[[^\]]+\]\(([^)#][^)]+)\)", text):
          if "://" in target or target.startswith("mailto:"):
              continue
          path = (file.parent / target.split("#", 1)[0]).resolve()
          if not path.exists():
              failures.append(f"{file}: missing {target}")
  if failures:
      raise SystemExit("\n".join(failures))
  PY
  ```

  Expected: exits 0.

- [x] **Step 2: Run whitespace check**

  Run:
  ```bash
  git diff --check
  ```

  Expected: exits 0.

- [x] **Step 3: Run package metadata tests**

  Run:
  ```bash
  uv run --extra dev python -m pytest tests/test_package_metadata.py -q
  ```

  Expected: all tests pass.

- [x] **Step 4: Build and check package long description**

  Run:
  ```bash
  uv run --extra dev python -m build --outdir /private/tmp/backlog-md-py-readme-docs-dist
  uv run --extra dev python -m twine check /private/tmp/backlog-md-py-readme-docs-dist/*
  ```

  Expected:
  - Build creates sdist and wheel.
  - `twine check` reports `PASSED` for both artifacts.

- [x] **Step 5: Check for generated byproducts**

  Run:
  ```bash
  git status --short --branch
  find . -maxdepth 3 \( -name '*.egg-info' -o -name __pycache__ -o -name .pytest_cache -o -name build \) -print
  ```

  Expected:
  - Git status contains only intended documentation changes and commits.
  - The `find` command prints nothing, or only generated paths that were
    produced by the validation commands and can be removed explicitly after
    review.

- [x] **Step 6: Remove only known generated byproducts if present**

  If the previous step shows generated directories created during this task,
  remove those explicit paths only. Examples:
  ```bash
  rm -rf build src/backlog_md_py.egg-info .pytest_cache
  find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
  ```

  Do not run broad cleanup commands that remove every ignored file in the
  worktree.

## Task 6: Final Review And PR

**Files:**
- Review all changed docs.

- [x] **Step 1: Review final diff**

  Run:
  ```bash
  git log --oneline main..HEAD
  git diff --stat main..HEAD
  git diff -- README.md docs/README.md docs/getting-started.md docs/integration.md CONTRIBUTING.md
  ```

  Expected:
  - README is shorter and easier to scan.
  - Docs index and getting-started guide exist.
  - Stale optional-extra guidance is removed if present.
  - No unrelated runtime/package/workflow changes.

- [ ] **Step 2: Push branch**

  Run:
  ```bash
  git push -u origin codex/readme-docs-refresh
  ```

- [ ] **Step 3: Create PR**

  Use title:
  ```text
  Refresh README and getting-started docs
  ```

  PR body should summarize:
  - README shortened into a landing page.
  - Added docs index and getting-started guide.
  - Cleaned stale setup guidance if applicable.
  - Verification commands and results, including package build and `twine check`.
