# Review Findings Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the confirmed security, data-safety, IPv6, performance, and CI-regression defects from the August 2026 repository review.

**Architecture:** Keep each fix at its shared root control: one URL policy per renderer, trusted project anchors at discovery, path-limited Git commits, fail-closed daemon ownership, one atomic replacement primitive, bracket-aware HTTP utilities, batched Git reads, single-task orchestration categorization, and one exact static-analysis baseline checker. Preserve existing public behavior except where it is unsafe or demonstrably incorrect.

**Tech Stack:** Python 3.11+, Click, stdlib HTTP/Git subprocesses, vanilla JavaScript, pytest, Ruff, mypy, Bandit.

**Spec:** `docs/superpowers/specs/2026-08-21-review-findings-fixes-design.md`

---

## Workstream 1: Security boundaries

### Task 1: Reject control-obfuscated Markdown schemes

**Files:**
- Modify: `src/backlog_py/browser/service.py:1393-1404`
- Modify: `src/backlog_py/browser/assets/board.js:444-450`
- Test: `tests/test_browser_service.py:1038-1058`

- [ ] **Step 1: Write failing regression tests**

Parameterize Python preview tests with `java\tscript:`, `java\nscript:`, `java\rscript:`, and `java\x00script:` links, plus otherwise-safe URLs carrying leading or trailing controls such as `https://example.test\n`. Assert every generated `href` is `#` and no dangerous spelling survives. Extend the static board asset assertion to require the equivalent JavaScript C0/DEL guard.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_browser_service.py -k 'markdown_preview_endpoint_renders_safe_links or browser_board_asset' -q`

Expected: the control-obfuscated cases fail because `_safe_markdown_href` returns them unchanged and `safeRichHref` lacks a control-character check.

- [ ] **Step 3: Implement the minimal shared policy in each runtime**

Python:

```python
_URL_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

def _safe_markdown_href(href: str) -> str:
    if _URL_CONTROL_CHARACTERS.search(href):
        return "#"
    value = href.strip()
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", value)
    if scheme and scheme.group(1).lower() not in {"http", "https", "mailto"}:
        return "#"
    return value
```

JavaScript mirrors the same allowlist and checks the untrimmed string for `/[\u0000-\u001f\u007f]/` before trimming or assigning `link.href`.

- [ ] **Step 4: Verify GREEN and the focused browser suite**

Run: `.venv/bin/python -m pytest tests/test_browser_service.py tests/test_browser_hardening.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/browser/service.py src/backlog_py/browser/assets/board.js tests/test_browser_service.py
git commit -m "fix: reject obfuscated markdown schemes"
```

### Task 2: Reject symlinked managed-directory anchors

**Files:**
- Modify: `src/backlog_py/storage/project.py:9-78`
- Test: `tests/test_project_discovery.py`

- [ ] **Step 1: Write failing discovery tests**

Create root-config, `backlog/config.yml`, and `.backlog/config.yml` project variants whose managed directory is a symlink to a writable outside directory. Assert `discover_project` raises `ValueError` containing `symlink` before a `MutableRepository` can be constructed.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_project_discovery.py -k symlink -q`

Expected: discovery currently accepts each symlinked anchor.

- [ ] **Step 3: Validate every discovered anchor**

Import `assert_trusted_subpath`. Route only the default root-config `backlog`, nested `backlog`, and `.backlog` anchors through a small `_trusted_backlog_dir(root, candidate)` wrapper that translates `PathContainmentError` to `ValueError`. Do this before `discover_project` calls `load_config`. Keep explicitly configured project-relative `backlogDirectory` values on their current `assert_path_within_base` path so contained symlink configurations remain compatible while outside-root targets remain rejected; add a test pinning that distinction.

- [ ] **Step 4: Verify GREEN and mutation containment coverage**

Run: `.venv/bin/python -m pytest tests/test_project_discovery.py tests/test_core_review_fixes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/storage/project.py tests/test_project_discovery.py
git commit -m "fix: reject redirected backlog anchors"
```

## Workstream 2: Git and process safety

### Task 3: Keep unrelated staged files out of auto-commits

**Files:**
- Modify: `src/backlog_py/runtime/git.py:58-99`
- Test: `tests/test_git_auto_commit.py`

- [ ] **Step 1: Write a failing nested-worktree regression test**

Create a Git repository with the Backlog project in `nested/`, stage `unrelated.txt` at repository root, mutate `nested/backlog/config.yml`, and run `maybe_auto_commit`. Assert the new commit contains only the Backlog path, while `unrelated.txt` remains staged.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_git_auto_commit.py -k unrelated_staged -q`

Expected: `unrelated.txt` appears in the generated commit.

- [ ] **Step 3: Limit the commit itself to Backlog pathspecs**

After `-m`, append `--only`, `--`, and the existing `pathspecs` to `commit_args`. Keep the current scoped `git add`, hook behavior, and failure cleanup.

```python
commit_args.extend(("-m", f"backlog: {operation}", "--only", "--", *pathspecs))
```

- [ ] **Step 4: Verify GREEN and all Git behavior**

Run: `.venv/bin/python -m pytest tests/test_git_auto_commit.py tests/test_git_hardening.py tests/test_runtime_git.py -q`

Expected: PASS; unrelated staged content is still present in `git diff --cached`.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/runtime/git.py tests/test_git_auto_commit.py
git commit -m "fix: scope automatic git commits"
```

### Task 4: Fail closed when daemon ownership is uncertain

**Files:**
- Modify: `src/backlog_py/daemon/lifecycle.py:35-43,208-238`
- Modify: `src/backlog_py/daemon/__init__.py`
- Modify: `src/backlog_py/cli/main.py:1545-1556`
- Test: `tests/test_daemon_lifecycle.py:220-273`
- Test: `tests/test_cli_readonly.py`

- [ ] **Step 1: Write the failing ownership test**

With a live recorded PID, monkeypatch `_daemon_endpoint_owned` to return `None` and capture `os.kill`. Assert `daemon_stop` raises a `DaemonOwnershipError`, sends no signal, and retains the runtime record. Add a CLI test asserting `daemon stop` exits nonzero with a clean `unable to verify daemon ownership` message and no traceback.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_daemon_lifecycle.py tests/test_cli_readonly.py -k 'uncertain or daemon_stop' -q`

Expected: SIGTERM is recorded instead of an error.

- [ ] **Step 3: Add the fail-closed branch**

Add a `DaemonOwnershipError(RuntimeError)` exported from `backlog_py.daemon`. Store the ownership result once. Preserve the current `False` stale-record cleanup, signal only on `True`, and raise the new error on `None`. Catch `DaemonOwnershipError` alongside `TimeoutError` in `daemon_stop_command` and raise `click.ClickException` so the CLI contract is explicit.

- [ ] **Step 4: Verify GREEN and daemon integration**

Run: `.venv/bin/python -m pytest tests/test_daemon_lifecycle.py tests/test_daemon_http_server.py tests/test_cli_readonly.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/daemon/lifecycle.py src/backlog_py/daemon/__init__.py src/backlog_py/cli/main.py tests/test_daemon_lifecycle.py tests/test_cli_readonly.py
git commit -m "fix: verify daemon ownership before signaling"
```

## Workstream 3: Filesystem and network correctness

### Task 5: Preserve file modes across atomic replacement

**Files:**
- Create: `src/backlog_py/storage/atomic.py`
- Modify: `src/backlog_py/core/repository.py:1073-1101`
- Modify: `src/backlog_py/storage/config.py:189-216`
- Test: `tests/test_data_loss_fixes.py`
- Test: `tests/test_definition_of_done.py`

- [ ] **Step 1: Write failing POSIX mode tests**

On non-Windows platforms, create a task and config with mode `0644`, edit both through their public mutation paths, and assert the resulting files remain `0644`. Also assert a newly created file remains user-only (`0600`) under a controlled umask.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_data_loss_fixes.py tests/test_definition_of_done.py -k 'mode or permission' -q`

Expected: overwrite cases become `0600`.

- [ ] **Step 3: Extract only the shared replacement primitive**

Create `atomic_replace_text(path, content)` in `storage/atomic.py`. Before opening the temporary file, capture `stat.S_IMODE(path.stat().st_mode)` when the destination exists. After the temp file is flushed/fsynced and before `os.replace`, apply that mode with `os.chmod(temp_name, existing_mode)`. Leave containment and domain-specific exception translation in the two callers.

- [ ] **Step 4: Verify GREEN and atomic-write coverage**

Run: `.venv/bin/python -m pytest tests/test_data_loss_fixes.py tests/test_definition_of_done.py tests/test_core_review_fixes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/storage/atomic.py src/backlog_py/core/repository.py src/backlog_py/storage/config.py tests/test_data_loss_fixes.py tests/test_definition_of_done.py
git commit -m "fix: preserve modes during atomic writes"
```

### Task 6: Make IPv6 loopback binding and URLs valid

**Files:**
- Modify: `src/backlog_py/security/http.py`
- Modify: `src/backlog_py/mcp/http_server.py:64-124`
- Modify: `src/backlog_py/browser/service.py:112-165,1520`
- Modify: `src/backlog_py/daemon/lifecycle.py:150-154,290-297`
- Test: `tests/test_daemon_http_server.py`
- Test: `tests/test_browser_service.py`
- Test: `tests/test_daemon_lifecycle.py`

- [ ] **Step 1: Write failing IPv6 tests**

Add an OS-capability helper that attempts an `AF_INET6` bind and skips only if unavailable. Start MCP and browser services on `::1`, assert their server address family is `AF_INET6`, and assert endpoints/root URLs use `http://[::1]:PORT/...`. Assert daemon runtime endpoint construction is bracketed too.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_daemon_http_server.py tests/test_browser_service.py tests/test_daemon_lifecycle.py -k ipv6 -q`

Expected: bind raises `socket.gaierror` or URL assertions fail.

- [ ] **Step 3: Select the correct server family and centralize URL formatting**

Add `bracketed_host(host)` and `http_url(host, port, path="")` to `security/http.py`. Add IPv6 subclasses of the existing MCP and browser server classes with `address_family = socket.AF_INET6`; select them when `":" in host`. Replace local/manual URL string construction with `http_url`.

- [ ] **Step 4: Verify GREEN and HTTP hardening**

Run: `.venv/bin/python -m pytest tests/test_daemon_http_server.py tests/test_browser_service.py tests/test_browser_hardening.py tests/test_daemon_lifecycle.py -q`

Expected: PASS on IPv4 and supported IPv6 hosts.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/security/http.py src/backlog_py/mcp/http_server.py src/backlog_py/browser/service.py src/backlog_py/daemon/lifecycle.py tests/test_daemon_http_server.py tests/test_browser_service.py tests/test_daemon_lifecycle.py
git commit -m "fix: support IPv6 loopback servers"
```

## Workstream 4: Performance and CI guardrails

### Task 7: Batch active-branch Git reads per ref

**Files:**
- Modify: `src/backlog_py/runtime/git.py:239-266,416-453`
- Test: `tests/test_git_auto_commit.py:166-226`
- Test: `tests/test_runtime_git.py`

- [ ] **Step 1: Write a failing subprocess-count test**

Create one active branch containing at least three task files. Wrap the Git runner(s), call `list_active_branch_task_snapshots`, and assert exactly one batched history command and one batched content command for that ref, independent of task count. Assert neither `_ref_commit_timestamp` nor any per-path Git helper is called.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_runtime_git.py tests/test_git_auto_commit.py -k batch -q`

Expected: current code launches per-path `git log` and `git show` commands.

- [ ] **Step 3: Implement two bounded reads**

Change `_recent_branch_refs` to retain each ref's already-enumerated committer timestamp and carry it into snapshot loading as the fallback; do not call `_ref_commit_timestamp` inside the per-ref or per-task path. Use one binary, NUL-framed history command per ref (for example `git log -z --format=%x00%ct --name-only <ref> -- <task dirs>`) and parse commit boundaries from the double-NUL header sentinel, never from newline or record-separator characters that a filename may contain. Use one binary `git archive --format=tar <ref> -- <task dirs>` pass and `tarfile.open(fileobj=BytesIO(...))` to read contents without extracting paths. Accept only regular `.md` members whose POSIX names are strictly beneath `<backlog>/tasks/` or `<backlog>/completed/`; ignore every other member. Reuse the current timeout/noninteractive environment in a `_run_git_bytes` sibling. Preserve deterministic ordering.

- [ ] **Step 4: Verify GREEN and branch accuracy**

Run: `.venv/bin/python -m pytest tests/test_runtime_git.py tests/test_git_auto_commit.py tests/test_sqlite_index.py -q`

Expected: PASS; command count stays constant as tasks grow.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/runtime/git.py tests/test_runtime_git.py tests/test_git_auto_commit.py
git commit -m "perf: batch active branch task reads"
```

### Task 8: Build browser task details with one repository and one task category

**Files:**
- Modify: `src/backlog_py/orchestration/reports.py:73-149,168-173`
- Modify: `src/backlog_py/browser/service.py:454-461,1218-1226,1431-1436`
- Test: `tests/test_orchestration.py`
- Test: `tests/test_browser_service.py:531-710`

- [ ] **Step 1: Write failing instrumentation tests**

Instrument `ReadOnlyRepository` construction, `maybe_fetch_remote_refs`, and `OrchestrationService.queue`. Fetch one task detail and assert one repository instance, zero remote refreshes, and zero full queue calls while the returned category still matches `queue_report` for eligible, completed-dependency, and blocked-dependency fixtures.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_browser_service.py -k 'task_detail and repository' -q`

Expected: the endpoint creates multiple repositories and calls the full queue path.

- [ ] **Step 3: Add a focused report helper and reuse the repository**

Expose `queue_item_for_task(repository, task, policy=None, now=None)` in `orchestration/reports.py`. It computes the completed-task ID set once and calls existing `categorize_task` only for `task`. In the endpoint, create `ReadOnlyRepository(project, refresh_remote_refs=False)`, fetch the task, compute its queue item through the helper, and pass that item into `_task_detail_payload`/`_task_payload`. Remove `_queue_item_for_task` and the `OrchestrationService.queue` dependency from detail rendering.

- [ ] **Step 4: Verify GREEN and browser/orchestration suites**

Run: `.venv/bin/python -m pytest tests/test_browser_service.py tests/test_orchestration.py tests/test_orchestration_service.py -q`

Expected: PASS with unchanged task-detail JSON semantics.

- [ ] **Step 5: Commit**

```bash
git add src/backlog_py/orchestration/reports.py src/backlog_py/browser/service.py tests/test_orchestration.py tests/test_browser_service.py
git commit -m "perf: focus browser task detail reads"
```

### Task 9: Enforce exact mypy and Ruff baselines

**Files:**
- Create: `scripts/check_quality_baseline.py`
- Create: `tests/test_quality_baseline.py`
- Modify: `.github/workflows/ci.yml:70-81`
- Modify: `tests/test_package_metadata.py:119-145`
- Modify: `pyproject.toml:88-146`

- [ ] **Step 1: Write failing parser and CI contract tests**

Load the script with `runpy.run_path`. Test parsing mypy `path:line: error:` output into exact per-file counts and Ruff `--statistics` output into exact per-rule counts. Update package metadata tests to require a blocking `Run quality baselines` step with no `continue-on-error`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_quality_baseline.py tests/test_package_metadata.py -q`

Expected: the script and blocking CI step do not exist.

- [ ] **Step 3: Implement the stdlib-only checker**

Resolve the repository root as `Path(__file__).resolve().parents[1]` and run every tool with that `cwd`. Store the exact spec baselines as dictionaries. Run mypy exactly as `[sys.executable, "-m", "mypy"]`; `pyproject.toml` already supplies `files = ["src"]`, and the checker tests must assert that configured target remains present. Accept mypy's normal diagnostic exit codes 0/1, parse only lines containing `: error:`, and normalize every reported path to a repository-relative POSIX path before comparing the complete per-file counter. Run Ruff once per ignored rule as `[sys.executable, "-m", "ruff", "check", "--select", rule, "--config", "lint.ignore=[]", "--statistics", "src", "tests"]`; accept diagnostic exits 0/1 and parse the exact rule count. Treat missing tools, timeouts, undecodable output, and exit codes above 1 as checker failures rather than baseline mismatches. Print added/removed count deltas and return nonzero on any mismatch, including improvements, so baseline reductions must be reviewed in the same change.

The original review measured 60 mypy errors in 14 files. Task 6's IPv6 URL fix intentionally removes the one `mcp/http_server.py` `[str-bytes-safe]` diagnostic by passing `str(host)` and `int(port)` to the shared URL builder, so the implemented exact baseline is 59 errors in 13 files. Pin that reviewed reduction in checker tests rather than silently omitting the file.

- [ ] **Step 4: Wire the blocking CI step and verify GREEN**

Replace the advisory mypy step with `python scripts/check_quality_baseline.py`; keep the existing blocking Ruff step. Update comments in `pyproject.toml` to identify the script as the source of truth.

Run:

```bash
.venv/bin/python -m pytest tests/test_quality_baseline.py tests/test_package_metadata.py -q
.venv/bin/python scripts/check_quality_baseline.py
```

Expected: both commands PASS with the intentionally reduced 59 mypy errors and the seven exact Ruff counts.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_quality_baseline.py tests/test_quality_baseline.py .github/workflows/ci.yml tests/test_package_metadata.py pyproject.toml
git commit -m "ci: enforce static analysis baselines"
```

## Final verification

Tasks that share production files execute sequentially in numeric order. In particular, Tasks 1/6/8 overlap in `browser/service.py`, Tasks 3/7 overlap in `runtime/git.py`, and Tasks 4/6 overlap in `daemon/lifecycle.py`; do not assign those overlapping tasks concurrently.

### Task 10: Run the complete release and security regression gate

**Files:** No production edits expected.

- [ ] **Step 1: Run all automated checks**

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python scripts/check_quality_baseline.py
.venv/bin/python -m bandit -q -r src
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
git diff --check main...HEAD
```

Expected: 1,188 baseline tests plus new regressions pass, one existing test remains skipped, Ruff/Bandit/build/Twine/diff checks pass, and quality diagnostics exactly match their reviewed baselines (or the same change deliberately lowers them).

- [ ] **Step 2: Re-run the two security proofs**

Confirm control-obfuscated links render as `href="#"`. Confirm discovery rejects a root config plus `backlog -> outside` before any task file is created outside the repository.

- [ ] **Step 3: Review the complete diff**

Run: `git diff --stat main...HEAD && git diff --check main...HEAD`

Confirm no generated distributions, temporary proof files, or unrelated formatting are tracked.

- [ ] **Step 4: Request final code review**

Use `superpowers:requesting-code-review` for the complete branch. Resolve all important findings, rerun affected checks, then use `superpowers:verification-before-completion` before claiming completion.
