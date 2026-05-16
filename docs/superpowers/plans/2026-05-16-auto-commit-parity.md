# Auto-Commit Parity Implementation Plan

**Goal:** Implement upstream `autoCommit` behavior without weakening local
review boundaries or hook policy.

## Stage 1: Runtime Boundary

**Goal:** Attach auto-commit to the shared project write-lock path so CLI and
MCP mutations get consistent behavior.
**Success Criteria:** Project writes capture pre-mutation git state and only
attempt auto-commit after the mutation succeeds.
**Tests:** Focused runtime tests around `with_project_write_lock`.
**Status:** Complete.

## Stage 2: Git Safety Policy

**Goal:** Keep auto-commit local-only and opt-in.
**Success Criteria:** Auto-commit runs only when post-mutation config enables
`autoCommit`, skips pre-dirty project roots, never pushes or pulls, and never
passes `--no-verify`.
**Tests:** Clean-repo commit, dirty-repo skip, and failing pre-commit hook
coverage.
**Status:** Complete.

## Stage 3: Compatibility Tracking

**Goal:** Move `git:auto-commit` from deferred to implemented while leaving
remote operations and hook bypass deferred.
**Success Criteria:** Inventory, oracle fixture, matrix docs, and summary
counts agree.
**Tests:** Compatibility report, agent-critical matrix, oracle manifest, and
CLI status tests.
**Status:** Complete.
