# Changelog

## 0.2.0 - 2026-05-23 (Beta)

### Changed

- Promote `backlog-md-py` from alpha to beta package metadata.
- Document the beta supported contract and release gate in
  `docs/stability-policy.md`.
- Add a 0.2.0 beta release-readiness record.
- Route README and documentation index readers to the stability policy,
  changelog, and release-readiness evidence.
- Keep install guidance split between released PyPI packages and unreleased
  GitHub commits.
- Add an optional Textual Kanban board through `backlog-py tui` behind the
  `tui` extra.
- Add Vim-style TUI navigation aliases and direct adjacent-status task moves.

### Validation

- Release candidates must pass the beta release gate in
  `docs/stability-policy.md` before tagging.
