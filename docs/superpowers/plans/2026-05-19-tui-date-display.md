# TUI Date Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make human-facing task detail output honor `dateFormat` and `includeDatetimeInDates` while preserving raw `--plain` output.

**Architecture:** Keep task file serialization unchanged. Add a small CLI display formatter that converts known Backlog date format tokens for non-plain task detail views, then track the behavior as an implemented interactive/config parity surface.

**Tech Stack:** Python stdlib `datetime`, existing Click CLI rendering, existing compatibility inventory and oracle manifest.

---

### Task 1: Human Task Detail Date Formatting

**Files:**
- Modify: `tests/test_cli_readonly.py`
- Modify: `src/backlog_py/cli/main.py`

- [x] **Step 1: Write failing CLI tests**

Add tests proving default non-plain task detail includes Created/Updated metadata, `dd/mm/yyyy` reorders dates, and `includeDatetimeInDates: false` hides time. Keep `--plain` assertions unchanged.

- [x] **Step 2: Run targeted tests to verify RED**

Run: `uv run --extra dev python -m pytest tests/test_cli_readonly.py::test_task_view_default_renders_interactive_task_detail tests/test_cli_readonly.py::test_task_view_default_honors_date_display_config -q`

Expected: FAIL because non-plain task detail currently omits Created/Updated metadata.

- [x] **Step 3: Implement minimal formatter**

Add helper functions to parse frontmatter date strings, map known config tokens (`yyyy`, `mm`, `dd`) to display output, append time only when configured and present, and use them only in `_format_interactive_task_detail`.

- [x] **Step 4: Run targeted CLI tests**

Run: `uv run --extra dev python -m pytest tests/test_cli_readonly.py::test_task_view_plain_outputs_task_body tests/test_cli_readonly.py::test_task_view_default_renders_interactive_task_detail tests/test_cli_readonly.py::test_task_view_default_honors_date_display_config -q`

Expected: PASS.

### Task 2: Compatibility Tracking And Docs

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/test_agent_critical_matrix.py`
- Modify: `tests/test_inventory.py`
- Modify: `docs/agent-critical-parity.md`
- Modify: `docs/interactive-deferrals.md`
- Modify: `docs/upstream-feature-parity.md`

- [x] **Step 1: Add failing parity expectations**

Update tests to expect a new implemented item named `cli:interactive-date-display` and updated totals.

- [x] **Step 2: Update inventory and docs**

Track the item as implemented, update the oracle manifest/matrix, and revise deferral docs so date display preferences are no longer a remaining TUI gap.

- [x] **Step 3: Run full verification**

Run:
- `uv run --extra dev python -m pytest tests -q`
- `git diff --check`
- `uv run --extra dev python -m bandit -r src`
- `uv run --extra dev backlog-py compat status --json`
- `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/tui-date-display-dist`
- `uv run --extra dev python -m twine check /private/tmp/tui-date-display-dist/*`

Expected: all commands pass; compatibility status shows only `git:hook-bypass` deferred.
