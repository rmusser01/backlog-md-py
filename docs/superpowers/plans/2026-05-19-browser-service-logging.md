# Browser Service Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded in-memory request logging to the loopback browser service and expose it through a read-only JSON endpoint plus the Service dialog.

**Architecture:** The browser server owns a small in-memory ring buffer of request log entries. Every response written by `_send_text()` records method, path, status, content type, and a timestamp, without storing request bodies, headers, query strings, or client identity. `/api/service/requests` returns the current log snapshot and the Service dialog renders a compact recent-request list.

**Tech Stack:** Python standard library `deque`, `datetime`, `http.server`, static HTML/JavaScript, existing pytest browser-service tests, compatibility inventory/docs.

---

### Task 1: Request Log API

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Baseline focused tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS before changes.

- [x] **Step 2: Write failing endpoint tests**

Add tests for `/api/service/requests` proving request entries are recorded without query strings and that the bounded log keeps only the most recent entries.

- [x] **Step 3: Run tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: FAIL with missing `/api/service/requests` behavior.

- [x] **Step 4: Implement request logging**

Initialize a `deque(maxlen=50)` on `BrowserThreadingHTTPServer`, record entries in `_send_text()`, and return `{"requests": [...]}` from `/api/service/requests`.

- [x] **Step 5: Run focused tests to verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS.

### Task 2: Service Dialog Request Log

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write failing HTML contract test**

Assert the rendered board HTML exposes request-log elements and fetches `/api/service/requests` from the Service dialog.

- [x] **Step 2: Run tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: FAIL with missing HTML/JS contract.

- [x] **Step 3: Implement minimal UI**

Add a `service-request-log` list to the Service dialog, refresh it with service status, and keep rendering text-only DOM nodes.

- [x] **Step 4: Run focused tests to verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS.

### Task 3: Parity Inventory and Documentation

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/test_agent_critical_matrix.py`
- Modify: `docs/agent-critical-parity.md`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`

- [x] **Step 1: Add `browser:service-request-log` to inventory and manifest**

Expected: compatibility totals increase by one implemented browser item.

- [x] **Step 2: Update docs**

Expected: browser-service remaining-work row no longer says richer request logging remains deferred.

- [x] **Step 3: Run focused parity tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py -q`
Expected: PASS.

### Task 4: Verification and Delivery

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
Run: `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-service-logging-dist`
Run: `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-service-logging-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-service-logging-dist/backlog_md_py-0.1.0-py3-none-any.whl`
Expected: PASS.
