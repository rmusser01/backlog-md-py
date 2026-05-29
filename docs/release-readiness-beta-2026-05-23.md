# Beta Release Readiness - 2026-05-23

This record tracks the 0.2.0 beta promotion out of alpha status.

## Target

- Package version: `0.2.0`
- Package classifier: `Development Status :: 4 - Beta`
- Supported contract: `docs/stability-policy.md`
- Upstream compatibility baseline: `backlog.md@1.45.1`

## Promotion Rationale

- Agent-critical CLI, MCP, and local-file workflows have passed the first
  cutover validation gate.
- The compatibility inventory reports no explicit deferred blockers.
- Browser release readiness is tracked separately with release evidence rather
  than implied by feature count alone.
- Release automation builds, checks, smokes, publishes GitHub Release assets,
  and publishes to PyPI through trusted publishing.

## Required Validation

Run these checks from a clean release candidate checkout before tagging:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src
git diff --check
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
backlog-py compat evidence-template \
  --output release-evidence/browser-release-evidence.json \
  --rich-edit-artifact artifacts/browser-rich-edit-e2e.txt \
  --desktop-artifact artifacts/browser-desktop.png \
  --mobile-artifact artifacts/browser-mobile.png \
  --command "manual browser release validation"
backlog-py compat status --release-evidence release-evidence/browser-release-evidence.json
printf '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n' | backlog-py-mcp
backlog-py daemon ensure
backlog-py daemon status --json
backlog-py daemon stop
```

Also run copied-repository mutation smoke from `docs/cutover-validation.md` and
review the resulting Backlog.md diff before using the release in a live
consuming project.

## Release Notes Checklist

- `README.md` no longer describes the package as alpha.
- `pyproject.toml` uses the beta classifier.
- `src/backlog_py/__init__.py` is bumped to `0.2.0`.
- `CHANGELOG.md` includes a `0.2.0` entry.
- `docs/stability-policy.md` documents the beta supported contract and release
  gate.

## PR Validation Result

Validated in the `codex/beta-exit` release-prep branch on 2026-05-23:

- `uv run --extra dev python -m pytest tests/test_package_metadata.py -q`:
  8 passed.
- Local Markdown link check for README, docs index, getting started, stability
  policy, beta readiness record, and changelog: passed.
- `uv run --extra dev backlog-py --version`: reported `0.2.0`.
- `uv run --extra dev python -m pytest tests -v`: 490 passed.
- `uv run --extra dev python -m bandit -r src`: no issues identified.
- `git diff --check`: passed.
- `uv run --extra dev backlog-py compat status --release-evidence <historical
  local evidence manifest>`: `agentCutoverReady: true`,
  `fullBrowserReleaseReady: true`, 100 implemented, 0 deferred. This was a
  workstation-local 2026-05-23 manifest before the portable evidence metadata
  contract; current release validation should regenerate the manifest with
  `backlog-py compat evidence-template` and publish repo-relative artifact
  paths.
- `uv run --extra dev python -m build --outdir
  /private/tmp/backlog-md-py-beta-exit-dist`: built
  `backlog_md_py-0.2.0.tar.gz` and `backlog_md_py-0.2.0-py3-none-any.whl`.
- `uv run --extra dev python -m twine check
  /private/tmp/backlog-md-py-beta-exit-dist/*`: sdist and wheel passed.
- Direct `backlog-py-mcp` stdio initialize with isolated runtime state:
  returned server version `0.2.0`.
- Singleton daemon smoke on alternate loopback port `18766` with isolated
  runtime state: daemon status reported version `0.2.0`, MCP stdio forwarding
  returned server version `0.2.0`, and the daemon stopped cleanly.
- Copied-repository mutation smoke against `tests/fixtures/repos/basic`:
  read-only task/board commands, board export, task create, task edit, search,
  archive, doc list, milestone list, and config list all exited 0.
- Copied-repository diff review: changes were limited to generated `Backlog.md`
  and archived smoke task files under `backlog/archive/tasks/`.
