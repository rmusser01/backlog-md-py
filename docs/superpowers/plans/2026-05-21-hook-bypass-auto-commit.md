# Hook Bypass Auto-Commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `bypassGitHooks` parity for the opt-in `autoCommit` runtime path.

**Architecture:** Keep the existing write-lock and auto-commit flow. Reload post-mutation config once, use it to decide both `autoCommit` and `bypassGitHooks`, and append `--no-verify` only to the local `git commit` argv when hook bypass is explicitly enabled.

**Tech Stack:** Python stdlib subprocess, existing `BacklogConfig`, pytest, uv.

---

### Task 1: Runtime Hook Bypass

**Files:**
- Modify: `src/backlog_py/runtime/git.py`
- Modify: `tests/test_git_auto_commit.py`

- [x] **Step 1: Baseline focused tests**

Run: `uv run --extra dev python -m pytest tests/test_git_auto_commit.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_inventory.py tests/test_oracle_manifest.py -q`
Expected: PASS before edits.

- [x] **Step 2: Write failing hook-bypass tests**

Change the existing hook test so `bypassGitHooks: false` proves hooks still block auto-commit, and add a new test proving `bypassGitHooks: true` commits successfully with the same failing pre-commit hook.

- [x] **Step 3: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_git_auto_commit.py -q -k "hook"`
Expected: new true-path test fails because the runtime does not pass `--no-verify`.

- [x] **Step 4: Implement minimal runtime change**

Reload post-mutation config inside `maybe_auto_commit()`, reuse it for the auto-commit enabled check, and build the commit argv as `["commit", "--no-verify", "-m", message]` only when `bypass_git_hooks` is true.

- [x] **Step 5: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_git_auto_commit.py -q -k "hook"`
Expected: hook tests pass.

### Task 2: Parity Contract

**Files:**
- Modify: `src/backlog_py/compat/inventory.py`
- Modify: `tests/fixtures/oracle/manifest.yml`
- Modify: `tests/test_compat_report.py`
- Modify: `tests/test_cli_readonly.py`
- Modify: `tests/test_agent_critical_matrix.py`
- Modify: `docs/agent-critical-parity.md`
- Modify: `docs/upstream-feature-parity.md`
- Modify: `docs/interactive-deferrals.md`
- Modify: `docs/cutover-validation.md`

- [x] **Step 1: Write/update failing parity assertions**

Update tests to expect no deferred compatibility items, `git:hook-bypass` classified as implemented, and summary counts increased by one implemented item.

- [x] **Step 2: Verify RED**

Run: `uv run --extra dev python -m pytest tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_inventory.py tests/test_oracle_manifest.py -q`
Expected: FAIL until inventory/docs are updated.

- [x] **Step 3: Update inventory, oracle, and docs**

Move `git:hook-bypass` to implemented, update counts, and document the constrained safety boundary.

- [x] **Step 4: Verify GREEN**

Run: `uv run --extra dev python -m pytest tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_inventory.py tests/test_oracle_manifest.py -q`
Expected: PASS.

### Task 3: Final Verification

**Files:**
- All touched files.

- [x] **Step 1: Run focused verification**

Run: `uv run --extra dev python -m pytest tests/test_git_auto_commit.py tests/test_compat_report.py tests/test_cli_readonly.py tests/test_agent_critical_matrix.py tests/test_inventory.py tests/test_oracle_manifest.py -q`
Expected: PASS.

- [x] **Step 2: Run static/security checks**

Run: `git diff --check`
Expected: no output.

Run: `uv run --extra dev python -m bandit -r src`
Expected: no issues in touched source.

- [x] **Step 3: Run full suite and packaging checks**

Run: `uv run --extra dev python -m pytest tests -q`
Expected: PASS.

Run: `uv build --no-build-isolation --python /usr/bin/python3 --out-dir /private/tmp/backlog-md-py-hook-bypass-dist`
Expected: wheel and sdist build.

Run: `uv run --extra dev python -m twine check /private/tmp/backlog-md-py-hook-bypass-dist/backlog_md_py-0.1.0.tar.gz /private/tmp/backlog-md-py-hook-bypass-dist/backlog_md_py-0.1.0-py3-none-any.whl`
Expected: both artifacts pass.
