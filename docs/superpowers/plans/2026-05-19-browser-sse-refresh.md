# Browser SSE Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dependency-free Server-Sent Events support for browser board revision updates while preserving the existing polling fallback.

**Architecture:** The loopback browser service will expose `/api/board/events` as a short-lived SSE endpoint that emits the current deterministic board revision and closes. Browsers with `EventSource` will subscribe to that endpoint and rely on automatic reconnects; browsers without it will keep using the existing `/api/board` polling path.

**Tech Stack:** Python stdlib `http.server`, existing browser service HTML/JS, existing compatibility inventory and docs.

---

### Task 1: SSE Endpoint Contract

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py`

- [x] **Step 1: Write the failing endpoint test**

Add a test that starts the browser service, requests `/api/board/events`, asserts the response uses `text/event-stream`, includes a retry cadence aligned to the existing five-second polling interval, `event: revision`, and JSON data with the current board revision, and proves no remote refs are refreshed.

- [x] **Step 2: Run the targeted test to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_sse_endpoint_returns_revision_event_without_remote_refresh -q`

Expected: FAIL because `/api/board/events` currently returns JSON 404.

- [x] **Step 3: Implement minimal SSE output**

Add a GET branch before `/api/board` that builds the existing board payload, formats one SSE revision event, sends no-store headers, records the request, and returns.

- [x] **Step 4: Run targeted browser service tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`

Expected: PASS.

### Task 2: Browser EventSource Wiring

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py`

- [x] **Step 1: Write the failing HTML contract test**

Update the browser live refresh HTML test to require `EventSource`, `/api/board/events`, `connectBoardRevisionEvents`, `handleBoardRevision`, and polling fallback.

- [x] **Step 2: Run the targeted test to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_html_exposes_live_refresh_sse_contract -q`

Expected: FAIL because the HTML only exposes polling.

- [x] **Step 3: Implement minimal EventSource wiring**

Extract revision handling into `handleBoardRevision`, add `startBoardRevisionPolling`, add `connectBoardRevisionEvents`, and start polling only when `EventSource` is unavailable.

- [x] **Step 4: Run targeted browser service tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`

Expected: PASS.

### Task 3: Parity Tracking And Verification

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`

- [x] **Step 1: Write/update failing parity assertions**

Run the compatibility status after adding the inventory item and update docs assertions if needed.

- [x] **Step 2: Update docs and inventory**

Track `browser:sse-live-refresh`, move browser live updates from polling-only to SSE-with-polling-fallback, and remove SSE from the remaining browser transport gap.

- [x] **Step 3: Run full verification**

Run:
- `uv run --extra dev python -m pytest tests -q`
- `git diff --check`
- `uv run --extra dev python -m bandit -r src`
- `uv run --extra dev backlog-py compat status --json`
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/browser-sse-refresh-dist`
- `uv run --extra dev python -m twine check /private/tmp/browser-sse-refresh-dist/*`

Expected: all commands pass; compatibility status shows one deferred item remains.
