# Browser Responsive Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser board explicitly responsive for narrow viewports and track that full-clone browser parity surface as implemented.

**Architecture:** Keep the current dependency-free, server-rendered browser board. Add CSS-only responsive rules for header actions, board columns, cards, dialogs, and form actions; lock the behavior with HTML/CSS contract tests so the browser surface remains self-contained and does not require a JavaScript build step.

**Tech Stack:** Python standard-library browser service, static HTML/CSS, pytest browser-service contract tests, compatibility inventory/docs.

---

### Task 1: Responsive Browser Contract

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py`

- [x] **Step 1: Baseline focused tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS before changes.

- [x] **Step 2: Write failing responsive contract test**

Add a test proving the rendered browser board includes explicit narrow-viewport CSS for the board, header, action buttons, dialogs, and form actions.

- [x] **Step 3: Run focused tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: FAIL because the explicit mobile layout contract is not present yet.

- [x] **Step 4: Implement responsive CSS**

Add dependency-free CSS media queries to stack the header, make action buttons full-width, reduce board padding, constrain dialog height, and keep form actions usable on narrow screens.

- [x] **Step 5: Run focused tests to verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS.

### Task 2: Parity Inventory and Documentation

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/test_agent_critical_matrix.py`
- Modify: `docs/agent-critical-parity.md`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`

- [x] **Step 1: Add `browser:responsive-layout` to inventory and manifest**

Expected: compatibility totals increase by one implemented browser item.

- [x] **Step 2: Update docs**

Expected: browser parity docs no longer list responsive/mobile layout as intentionally deferred, while keeping rich Markdown editing, executable Mermaid rendering, and git/shell settings deferred.

- [x] **Step 3: Run focused parity tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py -q`
Expected: PASS.

### Task 3: Verification and Delivery

**Files:**
- All touched files above.

- [x] **Step 1: Full test suite**

Run: `uv run --extra dev python -m pytest tests -q`
Expected: PASS.

- [x] **Step 2: Static/security checks**

Run: `git diff --check`
Run: `uv run --extra dev python -m bandit -r src`
Expected: PASS / no issues.

- [x] **Step 3: Compatibility and packaging**

Run: `uv run --extra dev backlog-py compat status --json`
Run: `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-responsive-layout-dist`
Run: `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-responsive-layout-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-responsive-layout-dist/backlog_md_py-0.1.0-py3-none-any.whl`
Expected: PASS.
