# Hook Bypass Auto-Commit Design

## Decision

Implement upstream `bypassGitHooks` parity only for the existing opt-in
`autoCommit` path. When `autoCommit` is enabled and `bypassGitHooks` is true in
the post-mutation config, the internal local `git commit` call may pass
`--no-verify`. The default remains safe: hooks run unless the project config
explicitly opts into bypassing them.

## Boundaries

- Keep dirty-worktree protection unchanged. If the project had pre-existing
  changes before a mutation, auto-commit still skips.
- Keep remote behavior unchanged. Auto-commit never pushes, pulls, or fetches.
- Keep browser settings rejecting `bypassGitHooks`. The browser surface remains
  limited to safe git automation settings.
- Keep the fixed-argv, no-shell subprocess contract for all git calls.

## Runtime Flow

1. The project write lock captures pre-mutation git state.
2. The mutation runs.
3. `maybe_auto_commit()` reloads the post-mutation config.
4. If `autoCommit` is enabled, the repository was clean before the mutation,
   and changes now exist, the runtime stages the project and commits.
5. The commit argv includes `--no-verify` only when the reloaded config has
   `bypassGitHooks: true`.

## Verification

Tests must prove that a failing pre-commit hook blocks auto-commit when
`bypassGitHooks` is false, and that the same hook is bypassed when
`bypassGitHooks` is true. Compatibility inventory, oracle manifest, and parity
docs must move `git:hook-bypass` from deferred to implemented while retaining
the security warning in prose.
