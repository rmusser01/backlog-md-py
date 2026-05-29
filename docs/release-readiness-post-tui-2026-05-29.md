# Post-TUI Beta Validation - 2026-05-29

This record validates the current `main` branch after the optional Textual TUI
settings work merged. It is a post-beta evidence refresh, not a new version tag.

## Target

- Commit: `85cbbb6` (`feat: add TUI DoD defaults settings (#74)`)
- Package version: `0.2.0`
- Upstream compatibility baseline: `backlog.md@1.45.1`
- Scope: package, CLI/MCP/daemon, optional TUI, compatibility inventory, and
  copied-repository mutation smoke.

The browser-specific release validation from 2026-05-22 remains the latest
browser visual/rich-edit evidence because PRs #73 and #74 did not change browser
runtime code. This run still exercised the machine-readable compatibility
release-evidence gate with the documented manifest format.

## Validation Result

- `uv run --isolated --extra dev python -m pytest tests -q`: 530 passed, 3
  skipped.
- `uv run --extra dev --extra tui python -m pytest tests -q`: 570 passed.
- `uv run --extra dev python -m bandit -r src -f json -o
  /private/tmp/bandit_backlog_py_post_tui_2026_05_29.json`: 0 errors and 0
  findings.
- `git diff --check`: passed.
- `uv build --out-dir
  /private/tmp/backlog-md-py-post-tui-release-validation-dist-85cbbb6`: built
  `backlog_md_py-0.2.0.tar.gz` and
  `backlog_md_py-0.2.0-py3-none-any.whl`.
- `uv run --extra dev python -m twine check
  /private/tmp/backlog-md-py-post-tui-release-validation-dist-85cbbb6/*`:
  sdist and wheel passed.
- `uv run --extra dev backlog-py compat status --release-evidence <historical
  local evidence manifest>`: `agentCutoverReady: true`,
  `fullBrowserReleaseReady: true`, 100 implemented, 0 deferred. This was a
  workstation-local 2026-05-29 manifest before the portable evidence metadata
  contract; current release validation should regenerate the manifest with
  `backlog-py compat evidence-template` and publish repo-relative artifact
  paths.
- Direct `backlog-py-mcp` stdio initialize with isolated runtime state returned
  server version `0.2.0`.
- Singleton daemon smoke with isolated runtime state on loopback port `18768`:
  daemon status reported version `0.2.0`, MCP stdio forwarding returned server
  version `0.2.0`, and the daemon stopped cleanly.
- Copied-repository mutation smoke against `tests/fixtures/repos/basic` passed
  read-only task/board commands, board export, task create, task edit, search,
  archive, doc list, milestone list, and config list.
- Copied-repository diff review: changes were limited to generated `Backlog.md`
  and archived smoke task files under `backlog/archive/tasks/`.

## Operational Note

An initial direct MCP smoke without an isolated `BACKLOG_PY_STATE_DIR` forwarded
to the existing user daemon on port `18765`, which still reported version
`0.1.0`. This is expected daemon-forwarding behavior, not a package metadata
bug. Release and cutover smokes must use a fresh `BACKLOG_PY_STATE_DIR`, or stop
and restart the intended daemon, when verifying server version and daemon
forwarding behavior.
