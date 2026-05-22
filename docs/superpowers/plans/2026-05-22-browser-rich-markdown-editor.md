# Browser Rich Markdown Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe dependency-free Rich mode to existing browser Markdown editors.

**Architecture:** Keep raw textareas as the source of truth. Add one `contenteditable` pane per Markdown editor, synchronize it to the textarea through small DOM-to-Markdown and Markdown-to-DOM helpers, and reuse the existing preview endpoint for rendered HTML.

**Tech Stack:** Python stdlib loopback HTTP service, generated HTML/CSS/vanilla JavaScript, pytest browser-service contract tests, Bandit.

---

## Files

- Modify: `src/backlog_py/browser/service.py`
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/test_browser_service.py`
- Modify: `tests/test_inventory.py`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_agent_critical_matrix.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `docs/agent-critical-parity.md`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`

## Task 1: Rich Editor HTML Contract

- [x] **Step 1: Write failing tests**

Add tests in `tests/test_browser_service.py` asserting every Markdown editor
exposes a Rich mode button, a hidden rich pane, and client helpers:
`showMarkdownRich`, `syncRichEditorToTextarea`, `markdownToRichHtml`,
`richHtmlToMarkdown`.

- [x] **Step 2: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_rich_editor"`
Expected: FAIL because Rich mode controls and helpers do not exist.

Actual: FAIL with missing `data-markdown-mode="rich"` and
`syncAllRichEditors(root)`.

- [x] **Step 3: Implement minimal HTML/CSS/JS**

In `src/backlog_py/browser/service.py`, add the Rich tab, rich editor pane,
mode-specific visibility rules, and helper functions. Use DOM parsing and
escaping rules that keep textareas authoritative.

- [x] **Step 4: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_rich_editor or markdown_edit_preview or markdown_edit_toolbar"`
Expected: PASS.

Actual: PASS with the focused Rich mode tests and surrounding Markdown editor
tests.

## Task 2: Rich Mode Synchronization

- [x] **Step 1: Write failing tests**

Extend the HTML contract tests to assert submit handlers call a
`syncAllRichEditors()` helper before collecting `FormData`, and preview mode
serializes Rich edits before requesting `/api/markdown/preview`.

- [x] **Step 2: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_rich_editor or markdown_edit_preview"`
Expected: FAIL until submit/preview synchronization exists.

Actual: FAIL with missing Rich synchronization hooks before implementation.

- [x] **Step 3: Implement synchronization**

Update `submitTaskCreate`, `submitTaskEdit`, and `showMarkdownPreview` to sync
Rich panes into their paired textareas first. Ensure dialog reset clears the
rich pane and returns mode to Edit.

- [x] **Step 4: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_rich_editor or markdown_edit_preview or task_create_endpoint_creates_task or task_edit_endpoint_updates_rich_markdown_sections"`
Expected: PASS.

Actual: PASS for the focused browser Markdown editor regression set.

## Task 3: Parity Docs and Verification

- [x] **Step 1: Update docs**

Revise `docs/browser-parity.md` and `docs/upstream-feature-parity.md` to mark
dependency-free Rich mode v1 as implemented while keeping full WYSIWYG parity
and browser shell-hook exposure deferred.

- [x] **Step 2: Run verification**

Run:

- `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
- `uv run --extra dev python -m pytest tests -q`
- `uv run --extra dev python -m bandit -r src`
- `git diff --check`

Expected: all pass. If `uv` creates `uv.lock` in this worktree, remove it unless
the project intentionally starts tracking a lockfile in the same change.

Actual:

- Focused browser service suite: 77 passed after the code-span link regression
  fix identified by Cubic.
- Full test suite: 483 passed.
- Bandit touched-scope security scan: no issues identified.
- `node --check /private/tmp/backlog-rich-editor-script.js`: passed.
- `git diff --check`: passed.
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-rich-editor-dist --clear`: passed.
- `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-rich-editor-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-rich-editor-dist/backlog_md_py-0.1.0-py3-none-any.whl`: passed.
