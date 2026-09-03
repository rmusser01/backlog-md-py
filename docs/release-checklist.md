# Release Checklist

Use this checklist when preparing a tagged `backlog-md-py` release. Release
publishing is irreversible once a distribution version reaches PyPI, so the tag
push is the final maintainer-controlled gate.

## Current Candidate

- Intended next release: `v2.1.0`.
- Release commit must be on `origin/main` after the release-prep PR merges.
- Do not reuse an existing tag or package version: PyPI package versions are
  immutable after publication.
- Keep the package stable only when `pyproject.toml`,
  `docs/stability-policy.md`, and the changelog intentionally describe the 2.x
  support contract.
- Declaring the inventoried browser release scope ready requires a fresh browser
  release-evidence manifest. A package or agent-cutover release can proceed
  without that claim when `agent_cutover_ready` is true and release notes avoid
  that scoped browser-readiness declaration.

## Release-Prep PR

Before tagging, merge a release-prep PR that:

1. Bumps `src/backlog_py/__init__.py` to the intended version.
2. Moves `CHANGELOG.md` entries from `Unreleased` into a dated version section.
3. Confirms `pyproject.toml` classifiers match the intended release status.
4. Confirms `docs/stability-policy.md` still describes the supported contract.
5. Updates this checklist when the release workflow or validation gate changes.

Do not merge the release-prep PR until GitHub Actions CI is green for the exact
commit being released.

## Local Validation Gate

Run from a clean checkout of the release candidate:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev --extra tui python -m pytest tests -q
uv run --extra dev python -m bandit -r src
git diff --check
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
```

Then smoke the installed wheel in a fresh environment:

```bash
python -m venv /tmp/backlog-md-py-wheel-smoke
/tmp/backlog-md-py-wheel-smoke/bin/python -m pip install --upgrade pip
/tmp/backlog-md-py-wheel-smoke/bin/python -m pip install dist/*.whl
/tmp/backlog-md-py-wheel-smoke/bin/backlog-py --version
/tmp/backlog-md-py-wheel-smoke/bin/python -m backlog_py --version
printf '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n' \
  | BACKLOG_PY_STATE_DIR="$(mktemp -d)" \
    /tmp/backlog-md-py-wheel-smoke/bin/backlog-py-mcp
```

For multi-agent readiness, use isolated runtime state:

```bash
state_dir="$(mktemp -d)"
BACKLOG_PY_STATE_DIR="$state_dir" \
  /tmp/backlog-md-py-wheel-smoke/bin/backlog-py daemon start --port 18768 --json
BACKLOG_PY_STATE_DIR="$state_dir" \
  /tmp/backlog-md-py-wheel-smoke/bin/backlog-py daemon status --json
printf '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n' \
  | BACKLOG_PY_STATE_DIR="$state_dir" \
    /tmp/backlog-md-py-wheel-smoke/bin/backlog-py-mcp
BACKLOG_PY_STATE_DIR="$state_dir" \
  /tmp/backlog-md-py-wheel-smoke/bin/backlog-py daemon stop
```

Run the copied-repository mutation smoke in `docs/cutover-validation.md` and
review the resulting `backlog/` diff before using the release for a live
consuming-project cutover.

## Compatibility Evidence

For a package and agent-cutover release:

```bash
uv run --extra dev backlog-py compat status --json
```

Require `agent_cutover_ready: true`. If `full_browser_release_ready` is false,
release notes must avoid declaring the inventoried browser release scope ready.
The legacy `full_browser_release_ready` field covers only the explicit
compatibility inventory and its release gates, not exhaustive upstream WebUI
parity; confirm the adjacent `coverage_scope` metadata in machine-readable
reports.

For a release that declares the inventoried browser release scope ready,
generate and attach fresh browser evidence:

```bash
uv run --extra dev backlog-py compat evidence-template \
  --output release-evidence/browser-release-evidence.json \
  --rich-edit-artifact artifacts/browser-rich-edit-e2e.txt \
  --desktop-artifact artifacts/browser-desktop.png \
  --mobile-artifact artifacts/browser-mobile.png \
  --command "manual browser release validation"
uv run --extra dev backlog-py compat status --release-evidence release-evidence/browser-release-evidence.json
uv run --extra dev backlog-py compat status --json --release-evidence release-evidence/browser-release-evidence.json
```

Require `full_browser_release_ready: true` before making that scoped readiness
claim.

## Tag And Publish

After the release-prep PR is merged, `.github/workflows/auto-release-tag.yml`
tags the package version from `src/backlog_py/__init__.py` as `v<version>` when
that tag does not already exist, then dispatches `.github/workflows/release.yml`
on the tag.

Manual fallback:

```bash
git fetch origin main --tags
git switch main
git pull --ff-only origin main
git tag -a v2.1.0 -m "backlog-md-py v2.1.0"
git push origin v2.1.0
```

The `v*` tag starts `.github/workflows/release.yml`. That workflow builds the
sdist and wheel, runs `twine check`, smoke-tests the wheel and SDK-free MCP
entry point, attaches `dist/*` to the GitHub Release, and publishes the same
artifacts to PyPI through trusted publishing.

Before pushing the tag, verify the PyPI trusted publisher is configured for:

- PyPI project: `backlog-md-py`
- GitHub repository: `rmusser01/backlog-md-py`
- Workflow: `.github/workflows/release.yml`
- Environment: `pypi`

## Post-Publish Checks

After the release workflow completes:

```bash
gh release view v2.1.0 --repo rmusser01/backlog-md-py
python -m pip index versions backlog-md-py
python -m venv /tmp/backlog-md-py-pypi-smoke
/tmp/backlog-md-py-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/backlog-md-py-pypi-smoke/bin/python -m pip install "backlog-md-py==2.1.0"
/tmp/backlog-md-py-pypi-smoke/bin/backlog-py --version
```

Confirm the GitHub Release has the sdist and wheel attached, PyPI lists the new
version, and the fresh PyPI install reports the tagged version.

## Failure Handling

- If the release workflow fails before publishing to PyPI, fix the issue on a
  new PR and retag only when the existing tag can still point to the same
  release commit. Prefer a new patch version if artifact contents would change.
- If PyPI publish succeeds, never reuse that package version. Any follow-up fix
  must bump the version and publish a new tag.
- If GitHub Release creation succeeds but PyPI publish fails, inspect the
  trusted-publishing error before rerunning. Do not rebuild different artifacts
  under the same version.
- If the tag was pushed accidentally and no package was published, delete the
  remote tag only after confirming with the maintainer.
