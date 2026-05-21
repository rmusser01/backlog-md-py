## Stage 1: Active Branch Read Model
**Goal**: Include recently active local and remote branch task snapshots in read-only task listings while preserving current checkout writes.
**Success Criteria**: `ReadOnlyRepository` surfaces the latest task state from active branches, respects `activeBranchDays`, and does not require shell execution or working-tree checkout.
**Tests**: Git fixture tests for active branch override, stale branch exclusion, and mutable repository write-safety.
**Status**: Complete

## Stage 2: Parity Inventory And Docs
**Goal**: Record the active branch accuracy behavior as implemented and keep remaining git deferrals explicit.
**Success Criteria**: Compatibility inventory and parity docs distinguish read-only active branch snapshots from deferred hook bypass behavior.
**Tests**: Compatibility status/oracle tests and documentation assertions where existing coverage expects inventory counts.
**Status**: Complete

## Stage 3: Verification And PR
**Goal**: Run the full test/security/package gates and publish a clean PR.
**Success Criteria**: Tests, diff check, Bandit, compatibility status, package build, and twine checks all pass.
**Tests**: Full verification commands from the project closeout checklist.
**Status**: Complete
