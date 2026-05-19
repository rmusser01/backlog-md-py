# Browser General Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe browser settings dialog for common non-shell, non-git project configuration values.

**Architecture:** Extend the existing loopback browser service with a `/api/settings/config` endpoint. The endpoint exposes and updates a fixed allowlist of safe config keys: `projectName`, `defaultAssignee`, `defaultStatus`, `dateFormat`, `includeDatetimeInDates`, `defaultPort`, `autoOpenBrowser`, `zeroPaddedIds`, and `statuses`. It must reject unknown keys, git automation keys, hook commands, and invalid values before mutating the config file, and it must refresh `server.project` after successful writes so subsequent board and HTML renders use the updated config without a service restart.

**Tech Stack:** Python stdlib HTTP server, existing config storage helpers, project write lock, inline browser JavaScript, pytest service tests.

---

### Task 1: Settings Config API

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write the failing GET/POST endpoint tests**

Add tests for:
- `GET /api/settings/config` returns the safe settings payload from disk.
- `POST /api/settings/config` persists safe values under the project write lock.
- successful settings updates refresh `server.project`, so subsequent `/api/board` uses updated statuses.

- [x] **Step 2: Run focused tests to verify they fail**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_config_settings_endpoint_returns_safe_values tests/test_browser_service.py::test_browser_config_settings_update_endpoint_writes_safe_values_under_project_lock tests/test_browser_service.py::test_browser_config_settings_update_refreshes_server_project -q`

Expected: FAIL with 404 or missing endpoint behavior.

- [x] **Step 3: Implement safe config helpers and routes**

Add:
- `GET /api/settings/config`
- `POST /api/settings/config`
- payload normalization helpers for string, boolean, port, zero padding, and statuses list values
- a refreshed `BacklogProject` assignment after successful writes

Use `with_project_write_lock` and existing `set_config_value()` for persistence. Do not expose `remoteOperations`, `autoCommit`, `bypassGitHooks`, `onStatusChange`, `checkActiveBranches`, or `activeBranchDays` through this browser endpoint.

- [x] **Step 4: Run focused endpoint tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_config_settings_endpoint_returns_safe_values tests/test_browser_service.py::test_browser_config_settings_update_endpoint_writes_safe_values_under_project_lock tests/test_browser_service.py::test_browser_config_settings_update_refreshes_server_project -q`

Expected: PASS.

### Task 2: Validation And Browser Dialog

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write failing validation and HTML contract tests**

Add tests for:
- invalid payloads reject without mutating config
- cross-origin settings update rejects without mutation
- board HTML exposes a general settings dialog and JavaScript submit function

- [x] **Step 2: Run focused tests to verify they fail**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_config_settings_update_rejects_invalid_payload_without_mutation tests/test_browser_config_settings_update_rejects_cross_origin_without_mutation tests/test_browser_board_html_exposes_general_settings_dialog -q`

Expected: FAIL until validation and HTML exist.

- [x] **Step 3: Implement dialog UI**

Replace the ambiguous `Settings` button with two settings actions:
- `Project settings` for safe general config
- `Definition of Done` for the existing DoD defaults dialog

Add a project settings dialog with fields for project name, default assignee, default status, date format, include datetime, default port, auto-open browser, zero-padded IDs, and statuses. Submit to `/api/settings/config` and reload on success.

- [x] **Step 4: Run browser service tests**

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

- [x] **Step 1: Add the inventory item**

Add `browser:general-settings` as an implemented browser item with expected behavior describing safe general settings editing.

- [x] **Step 2: Update count assertions and docs**

Use `uv run --extra dev backlog-py compat status --json` to confirm counts, then update tests/docs. Keep git automation and shell-hook settings explicitly out of the browser settings scope.

- [x] **Step 3: Run focused parity tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_oracle_manifest.py -q`

Expected: PASS.

### Task 4: Verification And Delivery

**Files:**
- No additional files expected.

- [x] **Step 1: Run full verification**

Run:
- `uv run --extra dev python -m pytest tests -q`
- `git diff --check`
- `uv run --extra dev python -m bandit -r src`
- `uv run --extra dev backlog-py compat status --json`
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-general-settings-dist`
- `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-general-settings-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-general-settings-dist/backlog_md_py-0.1.0-py3-none-any.whl`

- [ ] **Step 2: Commit, push, PR, merge, and cleanup**

Commit one focused change, open a PR against `main`, inspect reviews/checks, merge when acceptable, sync `main`, and remove the temporary worktree.
