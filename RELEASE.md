# Release Process

This document describes how to publish `backlog-md-py` to TestPyPI and PyPI.
Publishing a version to PyPI is irreversible: package versions cannot be
replaced after upload.

## What Triggers A Release

PyPI publishing is tag-gated, and the release tag is created automatically once
CI passes. A version bump merged to `main` therefore does publish a package.

The normal flow is:

1. A release-prep PR that bumps `src/backlog_py/__init__.py` merges to `main`.
2. `.github/workflows/ci.yml` runs for that commit.
3. Only after that CI run concludes successfully,
   `.github/workflows/auto-release-tag.yml` runs, creates the annotated tag
   `v<__version__>` on the exact commit CI validated, and dispatches
   `.github/workflows/release.yml` against that tag.
4. `.github/workflows/release.yml` builds the sdist and wheel, runs
   `twine check`, smoke-tests the wheel and the SDK-free MCP entry point,
   attaches `dist/*` to the GitHub Release, and publishes to PyPI through
   Trusted Publishing.

Auto-tagging is gated. `auto-release-tag.yml` triggers on CI completion, not on
the push itself, and only tags when all of the following hold:

- the CI run for that commit concluded `success` (the full test matrix, Bandit,
  Ruff, and the package build and smoke jobs);
- the CI run was for a `push` event on `main` in this repository, so pull
  request runs and fork runs never tag;
- the commit is contained in `origin/main`;
- the commit changed `src/backlog_py/__init__.py`;
- the `v<version>` tag does not already exist on `origin`.

The job guard is:

```yaml
if: >-
  github.event_name == 'workflow_dispatch' ||
  (github.event.workflow_run.conclusion == 'success' &&
  github.event.workflow_run.event == 'push' &&
  github.event.workflow_run.head_branch == 'main' &&
  github.event.workflow_run.head_repository.full_name == github.repository)
```

Nothing is published when CI fails on the release commit, when the merge does
not change `__version__`, or when the tag already exists.

**A `workflow_dispatch` run bypasses the CI-success gate.** The first clause of
the guard (`github.event_name == 'workflow_dispatch'`) short-circuits the whole
`workflow_run` condition, so a manual dispatch tags whatever `main` currently
points at without ever looking at the CI conclusion for that commit. It also
bypasses the "commit changed `__version__`" guard. Only these two guards still
apply to a manual dispatch:

- the commit is contained in `origin/main`;
- the `v<version>` tag does not already exist on `origin`.

Tagging starts `release.yml`, and a PyPI publish cannot be undone, so use manual
dispatch only after confirming by hand that CI concluded `success` on the exact
commit being tagged:

```bash
gh run list --repo rmusser01/backlog-md-py --workflow CI --branch main --limit 5
```

The release workflow itself still runs only for `v*` tags. Its release job is
guarded by:

```yaml
if: startsWith(github.ref, 'refs/tags/v')
```

so a manual dispatch of `release.yml` against `main` does not publish.

Because `workflow_run` executes the default-branch copy of the workflow with
`github.ref` pointing at the default branch, `auto-release-tag.yml` resolves the
commit and the version from `github.event.workflow_run.head_sha` rather than
from the ambient ref.

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
5. Ensure CI is green on the exact `main` commit that will be tagged. Merging a
   version bump to `main` is the publish decision: CI success on that commit is
   what releases the package.

See `docs/release-checklist.md` for the detailed validation gate.

## Local Validation

Run from a clean release-candidate checkout:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev --extra tui python -m pytest tests -q
uv run --extra dev python -m bandit -r src
uv run --extra dev python -m ruff check src tests
git diff --check
uv run --extra dev python -m build
uv run --extra dev python -m twine check dist/*
```

`ruff check` is a blocking CI gate, so it must pass before the release commit
merges. `uv run --extra dev python -m mypy` is advisory: it currently reports a
known baseline of pre-existing errors and does not block CI or the release.

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

### Primary path: automatic

Merging the release-prep PR is the publish action. Once CI passes on that `main`
commit, `Auto Release Tag` pushes `vX.Y.Z` and dispatches `Release`. Watch it:

```bash
gh run list --repo rmusser01/backlog-md-py --workflow CI --branch main --limit 3
gh run list --repo rmusser01/backlog-md-py --workflow "Auto Release Tag" --limit 3
gh run list --repo rmusser01/backlog-md-py --workflow Release --limit 3
```

If CI fails on the release commit, nothing is tagged and nothing is published.
Fix forward on a new PR; the next successful CI run on `main` tags the version
that is then in `src/backlog_py/__init__.py`.

### Fallback: manual tagging

Use this only when auto-tagging did not run or could not complete, for example
when the version bump landed in a commit that did not touch
`src/backlog_py/__init__.py`, or when the `Auto Release Tag` run itself failed
after CI was green.

Prefer rerunning the automation first:

```bash
gh workflow run "Auto Release Tag" --repo rmusser01/backlog-md-py --ref main
```

A manual dispatch skips both the CI-success gate and the "commit changed
`__version__`" guard; it still refuses to tag a commit that is not contained in
`origin/main`, and still skips an existing tag. Confirm CI is green on the
commit `main` points at before dispatching. If that is not usable, tag by hand:

```bash
git fetch origin main --tags
git switch main
git pull --ff-only origin main
git tag -a vX.Y.Z -m "backlog-md-py vX.Y.Z"
git push origin vX.Y.Z
```

A hand-pushed tag starts the Release workflow directly. Confirm CI is green on
that exact commit before pushing the tag: hand tagging bypasses the CI gate.

To rerun an existing release tag:

```bash
gh workflow run Release --repo rmusser01/backlog-md-py --ref vX.Y.Z
```

In every path the Release workflow builds the sdist and wheel, runs
`twine check`, smoke-tests the wheel and SDK-free MCP entry point, attaches the
artifacts to the GitHub Release, and publishes the same artifacts to PyPI
through Trusted Publishing.

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
