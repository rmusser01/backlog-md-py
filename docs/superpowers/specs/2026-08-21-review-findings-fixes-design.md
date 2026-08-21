# Review Findings Fixes Design

## Goal

Fix the concrete security, correctness, and measured performance defects found in the August 2026 repository review without expanding the work into a broad architectural rewrite.

## Scope

The repair pass covers these confirmed behaviors:

1. Markdown links containing ASCII control whitespace can bypass the scheme filter and execute `javascript:` URLs in the browser board.
2. A symlinked default `backlog` or `.backlog` directory can redirect managed reads and writes outside the repository.
3. Auto-commit can include unrelated files that were already staged elsewhere in the containing Git worktree.
4. Daemon shutdown signals a recorded PID when endpoint ownership is inconclusive.
5. Atomic replacement silently changes existing task and config files from their prior mode to `0600`.
6. The documented `::1` listener fails to bind and emits malformed unbracketed URLs.
7. Active-branch task discovery starts one or more Git subprocesses per task, and browser task details perform unnecessary whole-project queue and remote-refresh work.
8. Mypy and selected Ruff debt are described as ratchets but CI does not reject regressions.

## Design

### Trust-boundary fixes

Markdown URL validation will use one strict policy in both the Python renderer and browser rich editor: reject any URL containing a C0 or DEL control character, allow relative URLs, and allow only `http`, `https`, and `mailto` absolute schemes. Regression tests will cover tab, carriage-return, newline, and NUL variants rather than only the contiguous `javascript:` spelling.

Project discovery will validate the default backlog directory itself as a trusted subpath before constructing `BacklogProject`. A symlink in any managed anchor component will be rejected before config loading or mutation. Explicit project-relative `backlogDirectory` values will retain their current containment behavior.

### Data and process safety

Auto-commit will commit only the backlog pathspecs it stages. Existing index entries outside those pathspecs must remain staged but absent from the generated commit. The implementation will use Git's path-limited commit behavior rather than manipulating a temporary index.

Daemon shutdown will signal only when authenticated endpoint ownership is exactly `True`. A `False` result remains a stale-record cleanup; an inconclusive `None` result will fail closed, retain the record, and return a clear error.

Atomic file replacement will preserve the existing file's permission bits when overwriting. New files will retain secure temporary-file defaults. The duplicated task/config implementation will share the smallest suitable helper so both paths cannot drift again.

### Network correctness

The browser and MCP servers will select an IPv6-capable `ThreadingHTTPServer` when the requested bind address is IPv6. All generated HTTP URLs will share bracket-aware host formatting. IPv4 behavior and loopback/remote authorization rules remain unchanged. Tests that bind `::1` will skip only when the host OS genuinely lacks IPv6 loopback support.

### Performance and CI guardrails

Active-branch discovery will use at most two Git subprocesses per ref: one batched history read for per-path timestamps and one batched content read for task Markdown. Subprocess count must therefore remain constant as task count grows.

Browser task-detail reads will reuse one `ReadOnlyRepository(refresh_remote_refs=False)` for lookup and orchestration data. They will categorize only the requested task using the shared orchestration categorizer and the completed-task ID set required for dependency status; they must not construct a full `OrchestrationQueueReport`, refresh remotes, or create a second repository.

CI will run one stdlib-only baseline checker. Mypy baselines will be exact per-file counts totalling 59: browser/service.py=24, tui/data.py=11, runtime/locks.py=4, core/repository.py=4, tui/models.py=3, cli/main.py=3, tui/widgets.py=2, orchestration/reports.py=2, mcp/tools.py=2, and one each in tui/screens.py, tui/app.py, orchestration/service.py, and mcp/protocol.py. The original review measured 60 errors in 14 files, but Task 6's IPv6 URL fix intentionally removed the single `mcp/http_server.py` `[str-bytes-safe]` diagnostic by normalizing the server address with `str(host)` and `int(port)`. Globally ignored Ruff rule baselines will also be exact: `E501=45`, `I001=59`, `UP017=32`, `UP035=21`, `UP037=13`, `B904=4`, and `B009=2`. Any increase or decrease fails with an instruction to update the checked baseline deliberately in the same reviewed change. The existing blocking Ruff command remains in place. This pass will not reformat the entire tree or resolve all existing ignored violations.

## Implementation Workstreams

The implementation plan will keep four independently testable workstreams:

1. Security boundaries: Markdown URL validation and trusted project anchors.
2. Git/process safety: path-limited auto-commit and fail-closed daemon shutdown.
3. Filesystem/network correctness: mode-preserving atomic replacement and IPv6 binding/URL formatting.
4. Performance/CI: bounded active-branch Git processes, single-task browser categorization, and exact quality baselines.

## Error Handling

- Unsafe Markdown URLs degrade to `#` and never reach a clickable dangerous scheme.
- Untrusted managed-directory anchors raise the project's existing configuration/containment error family.
- Inconclusive daemon ownership raises a specific stop error and leaves both process and runtime record untouched.
- IPv6 errors retain the same user-facing startup failure path as IPv4 bind errors.

## Testing

Every production change follows red-green-refactor:

- focused regression tests demonstrate each defect before implementation;
- affected module suites run after each fix;
- the final gate runs the full test suite, Ruff, mypy ratchet, Bandit, package build/check, and `git diff --check`;
- security reproductions verify the Markdown and symlink paths are closed.

## Non-goals

- Refactoring every high-complexity CLI/browser/repository function.
- Eliminating all 59 remaining mypy errors in this pass.
- Replacing Loguru or removing the mutation-surface inventory.
- Changing the documented trust model for explicitly configured shell callbacks.
- Adding dependencies or building a new abstraction layer for isolated fixes.
