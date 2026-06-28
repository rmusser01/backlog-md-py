# Release Process

This document describes how to publish `backlog-md-py` to TestPyPI and PyPI.
Publishing a version to PyPI is irreversible: package versions cannot be
replaced after upload.

## What Triggers A Release

PyPI publishing is tag-gated. A normal push to `main` does not publish a
package.

The release workflow runs for:

- `v*` tag pushes, such as `v1.0.0`.
- Manual workflow dispatches, but the release job still only runs when the ref
  is a `v*` tag.

The release workflow does not publish for:

- commits pushed to `main`;
- pull request merges;
- manual workflow dispatches against `main`.

The workflow is `.github/workflows/release.yml`. Its release job is guarded by:

```yaml
if: startsWith(github.ref, 'refs/tags/v')
```

## One-Time PyPI Setup

Production PyPI uses Trusted Publishing. Configure the pending publisher in the
PyPI account that should own the package:

- Publisher: GitHub Actions
- PyPI project name: `backlog-md-py`
- Owner: `rmusser01`
- Repository name: `backlog-md-py`
- Workflow name: `release.yml`
- Environment name: `pypi`

The GitHub workflow already grants `id-token: write` and uses the `pypi`
environment. Do not store a production PyPI API token in the repository.

## Release-Prep PR

Prepare release changes on a branch and merge them to `main` before tagging.

Required updates:

1. Bump `src/backlog_py/__init__.py`.
2. Move relevant `CHANGELOG.md` entries into a dated version section.
3. Confirm `pyproject.toml` classifiers match the intended release status.
4. Update release docs if workflow behavior, validation gates, or supported
   contract changed.
5. Ensure CI is green on the exact `main` commit that will be tagged.

See `docs/release-checklist.md` for the detailed validation gate.

## Local Validation

Run from a clean release-candidate checkout:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev --extra tui python -m pytest tests -q
uv run --extra dev python -m bandit -r src
git diff --check
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
```

Smoke the built wheel in a fresh environment:

```bash
python -m venv /tmp/backlog-md-py-wheel-smoke
/tmp/backlog-md-py-wheel-smoke/bin/python -m pip install --upgrade pip
/tmp/backlog-md-py-wheel-smoke/bin/python -m pip install dist/*.whl
/tmp/backlog-md-py-wheel-smoke/bin/backlog-py --version
/tmp/backlog-md-py-wheel-smoke/bin/python -m backlog_py --version
```

## Optional TestPyPI Smoke

Use TestPyPI to prove the package metadata and upload path before publishing a
production version. Build fresh artifacts into a temporary directory so stale
local `dist/` files are not uploaded accidentally.

```bash
tmp_dist="$(mktemp -d /tmp/backlog-md-py-testpypi-dist.XXXXXX)"
uv run --extra dev python -m build --outdir "$tmp_dist"
uv run --extra dev python -m twine check "$tmp_dist"/*
```

Upload to TestPyPI with a TestPyPI-scoped token. Keep the token outside the
repository.

```bash
TWINE_USERNAME=__token__ \
TWINE_PASSWORD="$TEST_PYPI_TOKEN" \
  uv run --extra dev python -m twine upload \
    --repository-url https://test.pypi.org/legacy/ \
    "$tmp_dist"/*
```

Verify with a fresh install. Use PyPI as an extra index for normal runtime
dependencies:

```bash
python -m venv /tmp/backlog-md-py-testpypi-smoke
/tmp/backlog-md-py-testpypi-smoke/bin/python -m pip install --upgrade pip
/tmp/backlog-md-py-testpypi-smoke/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  "backlog-md-py==X.Y.Z"
/tmp/backlog-md-py-testpypi-smoke/bin/backlog-py --version
```

## Production Publish

After the release-prep PR is merged and `origin/main` is green:

```bash
git fetch origin main --tags
git switch main
git pull --ff-only origin main
git tag -a vX.Y.Z -m "backlog-md-py vX.Y.Z"
git push origin vX.Y.Z
```

The tag starts the Release workflow. The workflow builds the sdist and wheel,
runs `twine check`, smoke-tests the wheel and SDK-free MCP entry point, attaches
the artifacts to the GitHub Release, and publishes the same artifacts to PyPI
through Trusted Publishing.

Manual dispatch is only useful when rerunning an existing release tag:

```bash
gh workflow run Release --repo rmusser01/backlog-md-py --ref vX.Y.Z
```

## Post-Publish Verification

After the Release workflow completes:

```bash
gh run list --repo rmusser01/backlog-md-py --workflow Release --limit 5
gh release view vX.Y.Z --repo rmusser01/backlog-md-py
python -m venv /tmp/backlog-md-py-pypi-smoke
/tmp/backlog-md-py-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/backlog-md-py-pypi-smoke/bin/python -m pip install "backlog-md-py==X.Y.Z"
/tmp/backlog-md-py-pypi-smoke/bin/backlog-py --version
```

Also confirm the PyPI project page lists the new version:

```text
https://pypi.org/project/backlog-md-py/
```

## Failure Handling

- If the workflow fails before PyPI upload, fix the issue on a new PR before
  rerunning the tag workflow.
- If GitHub Release creation succeeds but PyPI publish fails, inspect the
  Trusted Publishing error first. Do not rebuild different artifacts under the
  same version.
- If PyPI publish succeeds, never reuse that version. Any follow-up fix needs a
  new version and a new tag.
- If the release job remains queued, check other active GitHub Actions runs in
  the account before rerunning. Large queues in unrelated repositories can delay
  hosted runner assignment.
- If a queued run cannot be canceled normally, use GitHub's force-cancel
  endpoint. Delete only orphaned queued runs that have no jobs and cannot be
  canceled.
