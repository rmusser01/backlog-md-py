# Documentation Onboarding Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the `backlog-md-py` README and docs so new users and contributors can quickly understand what the project is, how to run it safely, and how the main pieces fit together.

**Architecture:** Keep the README short and route deeper explanations into focused docs. Add one contributor-oriented architecture guide that maps source modules, runtime surfaces, compatibility evidence, and safety invariants without duplicating the parity and release-readiness guides.

**Tech Stack:** Markdown documentation, Python package metadata, pytest docs/package tests, simple repo-relative Markdown link validation.

---

### Task 1: README And Docs Index

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`

- [x] **Step 1: Update README structure**

Rewrite the README into short sections:

- What it is.
- When to use it.
- Quick start.
- Interfaces at a glance.
- Safety and status.
- Where to go next.

- [x] **Step 2: Update docs index**

Reorganize `docs/README.md` by reader intent:

- New users.
- Agent and MCP integrators.
- Contributors.
- Maintainers and parity reviewers.

- [x] **Step 3: Review for duplicate detail**

Make sure detailed command catalogs remain in `docs/integration.md` and release
gates remain in parity/release docs.

### Task 2: Getting Started And Architecture Guide

**Files:**
- Modify: `docs/getting-started.md`
- Create: `docs/architecture.md`

- [x] **Step 1: Tighten the first-run guide**

Keep the scratch-project setup first. Make existing-project use explicitly
read-only until copied-repository validation is complete.

- [x] **Step 2: Add architecture guide**

Create `docs/architecture.md` with sections for:

- Source of truth and project layout.
- Source module map.
- Interface flow from CLI/MCP/daemon/browser/TUI into shared core services.
- Runtime state, locks, and disposable SQLite index.
- Compatibility inventory and tests.
- Mutation safety invariants.

- [x] **Step 3: Link from entry points**

Add `docs/architecture.md` links from `docs/README.md` and `CONTRIBUTING.md`.

### Task 3: Contributor Guide

**Files:**
- Modify: `CONTRIBUTING.md`

- [x] **Step 1: Add contributor on-ramp**

Add a short "How The Project Is Organized" section that links to
`docs/architecture.md` and summarizes the main module families.

- [x] **Step 2: Clarify workflow**

Document a conservative contributor workflow:

- Start from a clean branch.
- Add tests for behavior changes.
- Keep docs and compatibility inventory aligned.
- Run focused tests first, then full checks before PR.

- [x] **Step 3: Keep release guidance intact**

Retain the existing release process and validation gate content.

### Task 4: Verification And Closeout

**Files:**
- Inspect all changed Markdown files.
- No Python source files should change.

- [x] **Step 1: Run docs/package tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_package_metadata.py tests/test_compat_report.py -q
```

Expected: pass.

- [x] **Step 2: Run Markdown link check**

Run a small repo-relative link checker against changed Markdown files.

Expected: all local links resolve.

- [x] **Step 3: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [x] **Step 4: Confirm Bandit scope**

If no Python files changed, record that Bandit is not applicable for this
Markdown-only change. If Python files changed, run:

```bash
uv run --extra dev python -m bandit -r src
```

- [ ] **Step 5: Commit and PR**

Commit the documentation refresh and open a PR against `rmusser01/backlog-md-py`
with a change summary that explains what changed and why.
