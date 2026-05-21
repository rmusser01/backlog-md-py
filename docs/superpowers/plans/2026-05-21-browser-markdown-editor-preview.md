# Browser Markdown Editor Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe browser Markdown preview mode for task create/edit Markdown fields without changing raw Markdown storage.

**Architecture:** Reuse the existing dependency-free browser service and `_markdown_to_html()` renderer. Add one same-origin POST endpoint for Markdown preview and wrap existing Markdown textareas with Edit/Preview controls that request server-rendered HTML.

**Tech Stack:** Python stdlib HTTP server, existing safe Markdown renderer, vanilla browser JavaScript, pytest.

---

### Task 1: Markdown Preview Endpoint

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Modify: `tests/test_browser_service.py`

- [x] **Step 1: Write failing endpoint tests**

Add tests for `POST /api/markdown/preview` proving:
- valid `{ "markdown": "..." }` returns safe rendered HTML;
- unsafe HTML is escaped;
- cross-origin requests are rejected;
- non-string markdown payloads return `400`.

- [x] **Step 2: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_preview"`
Expected: FAIL because the endpoint does not exist.

- [x] **Step 3: Implement endpoint**

Add a `do_POST` branch for `/api/markdown/preview` before task mutation routes. Require `_origin_allowed()`, parse JSON, require a string `markdown` value, render with `_markdown_to_html()`, and return `{ "html": rendered }`.

- [x] **Step 4: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_preview"`
Expected: PASS.

### Task 2: Browser Edit/Preview Controls

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Modify: `tests/test_browser_service.py`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`

- [x] **Step 1: Write failing HTML contract tests**

Extend the Markdown toolbar test, or add a focused test, proving the board HTML exposes:
- `data-markdown-editor="true"` containers for all Markdown fields;
- Edit and Preview buttons with `data-markdown-mode`;
- preview panels using `data-markdown-preview-for`;
- client functions `showMarkdownPreview`, `showMarkdownEdit`, and `renderMarkdownPreview`;
- fetch to `/api/markdown/preview`.

- [x] **Step 2: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_edit"`
Expected: FAIL until the HTML/JS contract is added.

- [x] **Step 3: Implement minimal editor wrapper**

Replace repeated toolbar/textarea markup with a helper that renders the label,
Edit/Preview buttons, existing toolbar, textarea, and preview panel. Preserve
the existing textarea `name`, `id`, and `data-markdown-field` attributes.

- [x] **Step 4: Implement preview JavaScript**

Add functions to toggle preview/edit mode, post the textarea value to
`/api/markdown/preview`, show returned HTML, render Mermaid diagrams inside the
preview panel, and return to the raw textarea without altering its value.

- [x] **Step 5: Update parity docs**

Update browser parity docs to describe safe edit/preview support while still
leaving full WYSIWYG editing deferred.

- [x] **Step 6: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q -k "markdown_edit or markdown_preview or task_create_endpoint_creates_task or task_edit_endpoint_updates_rich_markdown_sections"`
Expected: PASS.

### Task 3: Final Verification

**Files:**
- All touched files.

- [x] **Step 1: Run focused browser tests**

Run: `uv run --extra dev python -m pytest tests/test_browser_service.py -q`
Expected: PASS.

- [x] **Step 2: Run static/security checks**

Run: `git diff --check`
Expected: no output.

Run: `uv run --extra dev python -m bandit -r src`
Expected: no issues introduced in touched source.

- [x] **Step 3: Run full suite and packaging checks**

Run: `uv run --extra dev python -m pytest tests -q`
Expected: PASS.

Run: `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-markdown-editor-dist --clear`
Expected: wheel and sdist build.

Run: `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-markdown-editor-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-browser-markdown-editor-dist/backlog_md_py-0.1.0-py3-none-any.whl`
Expected: both artifacts pass.
