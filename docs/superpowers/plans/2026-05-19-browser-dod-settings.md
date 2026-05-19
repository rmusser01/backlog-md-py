# Browser Definition of Done Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add browser-service support for viewing and updating project-level Definition of Done defaults through safe config helpers.

**Architecture:** Reuse the existing loopback browser service and `replace_definition_of_done_defaults()` config helper. Add one read-only settings endpoint, one same-origin protected write endpoint guarded by the project write lock, and a small settings dialog in the static board HTML.

**Tech Stack:** Python `http.server`, existing `backlog_py.browser.service`, existing YAML config helpers, pytest, stdlib HTTP test helpers.

---

### Task 1: Browser DoD Settings Endpoints

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [ ] **Step 1: Write failing endpoint tests**

Add tests that:
- `GET /api/settings/dod-defaults` returns the configured defaults.
- `POST /api/settings/dod-defaults` writes defaults through `replace_definition_of_done_defaults()` under operation `browser_dod_defaults_update`.
- Cross-origin POST returns 403 and leaves `backlog/config.yml` unchanged.
- Invalid payload returns 400 and leaves `backlog/config.yml` unchanged.

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py -q
```

Expected: new tests fail with 404 for missing endpoints.

- [ ] **Step 3: Implement minimal endpoint behavior**

In `service.py`:
- Import `get_definition_of_done_defaults` and `replace_definition_of_done_defaults`.
- Route `GET /api/settings/dod-defaults` to JSON `{"items": [...]}`.
- Route `POST /api/settings/dod-defaults` through `_origin_allowed()`, parse `{"items": [...]}`, reject non-list payloads, normalize strings by trimming and dropping blanks, write with the project lock, and return the refreshed items.

- [ ] **Step 4: Run browser service tests to verify green**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py -q
```

Expected: all browser service tests pass.

### Task 2: Browser Settings Dialog

**Files:**
- Modify: `src/backlog_py/browser/service.py`
- Test: `tests/test_browser_service.py`

- [ ] **Step 1: Write failing HTML exposure test**

Add a test asserting the board HTML includes:
- A settings button.
- A `dod-defaults-dialog`.
- A `dod-defaults-form`.
- JavaScript functions that call `/api/settings/dod-defaults`.

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py::test_browser_board_html_exposes_dod_defaults_settings_dialog -q
```

Expected: failure because the settings dialog does not exist.

- [ ] **Step 3: Implement minimal UI**

In `render_board_html()`:
- Add a Settings button in the header.
- Add a dialog with one textarea for DoD defaults, one item per line.
- Add `openDodDefaultsSettings()` and `submitDodDefaultsSettings()` JavaScript helpers.
- Reload the page after successful save, matching existing create/edit/archive behavior.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_browser_service.py -q
```

Expected: all browser service tests pass.

### Task 3: Parity Tracking and Verification

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `docs/browser-parity.md`
- Modify: `docs/upstream-feature-parity.md`
- Test: `tests/test_compat_report.py` if inventory count assertions require updates.

- [ ] **Step 1: Update parity inventory and docs**

Add `browser:dod-defaults-settings` as implemented and update docs so Definition of Done browser settings are no longer described as missing.

- [ ] **Step 2: Run verification**

Run:

```bash
uv run --extra dev python -m pytest tests -q
uv run --extra dev python -m bandit -r src
uv run --extra dev backlog-py compat status --json
git diff --check
uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-browser-dod-settings-dist
uv run --extra dev python -m twine check /private/tmp/backlog-md-py-browser-dod-settings-dist/*
```

Expected: tests, Bandit, diff check, build, and Twine checks pass. Compat status remains agent-cutover ready and increases the browser implemented count by one while keeping only `git:hook-bypass` deferred.
