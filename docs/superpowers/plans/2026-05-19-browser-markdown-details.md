# Browser Markdown Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render task detail Markdown safely in the browser detail dialog without adding editing behavior or external JavaScript dependencies.

**Architecture:** Extend the loopback browser service's task detail payload with server-generated safe HTML for description, implementation notes, and final summary. The renderer is intentionally small and dependency-free: it escapes all source text first, supports common read-only Markdown shapes, and keeps fenced Mermaid blocks visible as code rather than executing diagrams. The browser dialog consumes the safe HTML fields and continues to use existing checklist controls for Acceptance Criteria and Definition of Done.

**Tech Stack:** Python stdlib HTTP server, `html.escape`, inline browser JavaScript, pytest service tests, existing compatibility inventory.

---

### Task 1: Safe Markdown HTML Payload

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write failing task-detail payload tests**

Add tests proving:
- `/api/tasks/TASK-1` returns `descriptionHtml`, `implementationNotesHtml`, and `finalSummaryHtml`.
- Markdown bullets render as escaped `<ul><li>...</li></ul>` HTML.
- Unsafe inline HTML and script-like content are escaped in generated HTML.
- Mermaid fenced blocks remain visible as escaped code and are not executed.

- [x] **Step 2: Run focused payload tests to verify they fail**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_task_detail_endpoint_returns_markdown_html_sections tests/test_browser_service.py::test_browser_task_detail_markdown_html_escapes_unsafe_content -q`

Expected: FAIL because the HTML fields and renderer do not exist yet.

- [x] **Step 3: Implement dependency-free safe Markdown renderer**

Add helper functions in `src/backlog_py/browser/service.py`:
- `_markdown_to_html(text: str) -> str`
- small block parsing for paragraphs, headings, unordered lists, and fenced code blocks
- inline escaping for code spans and simple emphasis after source text is escaped

Do not add a package dependency, do not execute Mermaid, and do not use raw `innerHTML` with untrusted strings.

- [x] **Step 4: Run focused payload tests to verify they pass**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_task_detail_endpoint_returns_markdown_html_sections tests/test_browser_service.py::test_browser_task_detail_markdown_html_escapes_unsafe_content -q`

Expected: PASS.

### Task 2: Browser Detail Dialog Wiring

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [x] **Step 1: Write failing HTML contract test**

Add a test proving the browser page exposes:
- `id="task-dialog-description-html"`
- `id="task-dialog-implementation-notes"`
- `id="task-dialog-final-summary"`
- `setHtml`
- `descriptionHtml`
- `implementationNotesHtml`
- `finalSummaryHtml`

- [x] **Step 2: Run focused HTML contract test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_html_exposes_markdown_detail_sections -q`

Expected: FAIL because the dialog still renders description as plain text only.

- [x] **Step 3: Wire task detail HTML fields into the dialog**

Replace the plain description paragraph with a Markdown body container and add Implementation Notes and Final Summary sections. Add `setHtml()` that assigns only server-produced safe HTML fields, falling back to escaped text if a field is missing.

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

- [x] **Step 1: Add inventory item**

Add `browser:markdown-detail-rendering` as an implemented browser item. Keep rich Markdown editing and executable Mermaid rendering deferred.

- [x] **Step 2: Update counts and docs**

Use `uv run --extra dev backlog-py compat status --json` to confirm counts, then update tests and docs from the compatibility output.

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
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-markdown-details-dist`
- `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-markdown-details-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-markdown-details-dist/backlog_md_py-0.1.0-py3-none-any.whl`

- [ ] **Step 2: Commit, push, PR, merge, and cleanup**

Commit one focused change, open a PR against `main`, inspect reviews/checks, merge when acceptable, sync `main`, and remove the temporary worktree.
