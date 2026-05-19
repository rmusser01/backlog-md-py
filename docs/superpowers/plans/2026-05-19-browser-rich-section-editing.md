# Browser Rich Section Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the browser task edit flow update the already-rendered rich Markdown task sections: Implementation Notes and Final Summary.

**Architecture:** Reuse the existing locked `/api/tasks/<id>/edit` endpoint and `MutableRepository.edit_task()` support for `notes` and `final_summary`. The browser form remains plain textarea based, stores raw Markdown, and the detail payload continues to render sanitized HTML through the existing Markdown renderer.

**Tech Stack:** Python standard-library browser service, static HTML/JavaScript, existing repository mutation helpers, pytest browser-service contract tests, compatibility inventory/docs.

---

### Task 1: Rich Section Edit API

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Baseline focused tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS before changes.

- [x] **Step 2: Write failing endpoint tests**

Add tests proving `/api/tasks/<id>/edit` accepts `implementationNotes` and `finalSummary`, writes the corresponding Markdown sections under the project write lock, and rejects invalid non-string payloads without mutation.

- [x] **Step 3: Run tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: FAIL because browser edit payload normalization does not expose the rich section fields yet.

- [x] **Step 4: Implement endpoint normalization**

Map `implementationNotes` to repository `notes` and `finalSummary` to repository `final_summary`, preserving existing title/status/description/Acceptance Criteria behavior and validation style.

- [x] **Step 5: Run focused tests to verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS.

### Task 2: Rich Section Edit Dialog

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write failing HTML contract test**

Assert the edit form exposes `implementationNotes` and `finalSummary` textareas, pre-fills them from the task detail payload, and submits both fields.

- [x] **Step 2: Run tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: FAIL with missing HTML/JS contract.

- [x] **Step 3: Implement minimal UI**

Add two textareas to the edit dialog and include both raw Markdown section values in the existing edit submission payload.

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

- [x] **Step 1: Add `browser:rich-section-editing` to inventory and manifest**

Expected: compatibility totals increase by one implemented browser item.

- [x] **Step 2: Update docs**

Expected: browser remaining-work text recognizes rich section editing as implemented while keeping executable Mermaid rendering and broader rich editing deferred.

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
Run: `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-rich-section-editing-dist`
Run: `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-rich-section-editing-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-rich-section-editing-dist/backlog_md_py-0.1.0-py3-none-any.whl`
Expected: PASS.
