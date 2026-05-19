# Browser Live Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight browser board refresh contract so open board pages can detect external task changes made by CLI, MCP, or another browser tab.

**Architecture:** Keep the current server-rendered browser UI and reload-based mutation model. Add a deterministic `revision` field to the `/api/board` payload, expose the initial revision in the HTML, and poll `/api/board` from the page at a conservative interval. If the revision changes and no modal dialog is open, reload the page; if a dialog is open, defer until the next poll after editing closes. Browser polling reads must not trigger fetch-only remote refreshes, because that would turn every open board into repeated `git fetch` traffic.

**Tech Stack:** Python stdlib HTTP server, existing repository readers, inline browser JavaScript, pytest service tests.

---

### Task 1: Board Revision Contract

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [ ] **Step 1: Write the failing API revision test**

Add a test that starts the browser service, reads `/api/board`, mutates the fixture task file directly, reads `/api/board` again, and asserts that `revision` changes while the updated task payload is visible.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_payload_revision_changes_after_external_task_file_update -q`

Expected: FAIL because `/api/board` does not include `revision`.

- [ ] **Step 3: Implement deterministic board revisions**

In `build_board_payload()`, build the current snapshot as before using a `ReadOnlyRepository` instance with remote refresh disabled, then compute a SHA-256 digest from the JSON-serializable snapshot using `sort_keys=True` and compact separators. Store it as `payload["revision"]`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_payload_revision_changes_after_external_task_file_update -q`

Expected: PASS.

### Task 2: Browser Polling Contract

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [ ] **Step 1: Write the failing HTML contract test**

Add a test that fetches the board HTML and asserts it exposes `data-board-revision`, `pollBoardRevision`, the `/api/board` polling URL, `setInterval`, and a dialog-open guard.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_html_exposes_live_refresh_polling_contract -q`

Expected: FAIL because the page has no polling contract.

- [ ] **Step 3: Implement polling reload behavior**

Render the initial board revision into `<main class="board" data-board-revision="...">`. Add JavaScript that stores the revision, polls `/api/board` on an interval, compares revisions, and calls `window.location.reload()` only when no dialog is open.

- [ ] **Step 4: Run browser service tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`

Expected: PASS.

### Task 3: Parity Tracking

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`
- Modify: `docs/agent-critical-parity.md`

- [ ] **Step 1: Add the inventory item**

Add `browser:live-refresh-polling` as implemented browser parity, with expected behavior describing polling-based board refresh detection.

- [ ] **Step 2: Update generated count assertions and parity docs**

Update browser and total implemented counts based on `backlog-py compat status --json`, and revise docs to say real-time behavior is implemented as conservative polling, while richer SSE/WebSocket behavior remains out of scope.

- [ ] **Step 3: Run focused compat tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py -q`

Expected: PASS.

### Task 4: Verification And Delivery

**Files:**
- No additional files expected.

- [ ] **Step 1: Run full verification**

Run:
- `uv run --extra dev python -m pytest tests -q`
- `git diff --check`
- `uv run --extra dev python -m bandit -r src`
- `uv run --extra dev backlog-py compat status --json`
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-live-refresh-dist`
- `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-live-refresh-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-live-refresh-dist/backlog_md_py-0.1.0-py3-none-any.whl`

- [ ] **Step 2: Commit, push, PR, and merge**

Commit one focused change, open a PR against `main`, inspect checks/reviews, merge when acceptable, sync `main`, and clean up the temporary worktree.
