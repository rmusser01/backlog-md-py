# Interactive `backlog-py init` Design

Date: 2026-08-04
Status: Approved by user (design phase)

## Background

`backlog-py init` already initializes a project non-interactively via
`core/init.py:init_project()` and the `--defaults` CLI flag. Today, running
`init` without `--defaults` fails with:

> Interactive init is not available in backlog-py yet. Pass --defaults to use non-interactive defaults.

This change adds the interactive init wizard so a human operator can run bare
`backlog-py init` in a terminal and provide custom values at prompts. The
non-interactive `--defaults` path is unchanged, preserving the deterministic
agent contract.

## Decisions (from brainstorming)

- **Scope:** prompt for core settings only — the knobs `init_project` already
  accepts: project name, backlog directory, task prefix, config location,
  git integration (`--no-git`), and agent instruction files
  (`--agent-instructions`). The existing `backlog config` wizard remains the
  place for full config editing.
- **Flag mixing:** when `init` runs interactively, any flags already parsed
  (positional project name, `--backlog-dir`, `--task-prefix`,
  `--config-location`, `--no-git`, `--agent-instructions`) become the default
  answers of the corresponding prompts; pressing Enter accepts them.
- **Non-TTY:** bare `init` without `--defaults` on a non-interactive stdin
  keeps the current error. Agents and scripts must pass `--defaults`.

## Approach

Prompt in the CLI layer (`src/backlog_py/cli/main.py`), then reuse the existing
init path unchanged. Core (`src/backlog_py/core/init.py`) is not modified.

Alternatives considered and rejected:

- *Prompter callback injected into core* — pushes I/O into core and risks
  prompting while the init lock is held.
- *Reuse config-wizard helpers* — they assume an existing project and write
  through `set_config_value`; wrong lifecycle for init.

## Components

### CLI: `init_command` (src/backlog_py/cli/main.py)

Replace the unconditional "interactive not available" error with:

1. If `--defaults` was passed: behave exactly as today.
2. Else if `_stdin_is_interactive()` is false: raise a
   `click.ClickException` telling the caller to pass `--defaults`. The wording
   is updated from "Interactive init is not available in backlog-py yet" to
   "Interactive init requires a terminal. Pass --defaults to use
   non-interactive defaults." — the old message claims a now-existing feature
   does not exist. The pinned test contract (substring `"Pass --defaults"`)
   is preserved.
3. Else: run the interactive prompt sequence, then call the existing
   `_locked_init(ctx, "init_project", lambda: _initialize_project(...))` with
   the collected answers. Post-init output ("Initialized Backlog.md project
   at ...", "Preserved existing config ...", agent instruction updates) is
   unchanged.

All prompting happens **before** the init lock is acquired, so user think-time
never holds the lock.

### Prompt sequence (new helper, e.g. `_prompt_init_options`)

In order, each defaulting to the parsed flag value:

1. Project name — `click.prompt`, default: positional `project_name` arg if
   given, else the resolved working directory name.
2. Backlog directory — `click.prompt`, default: `--backlog-dir` value
   (`"backlog"` unless overridden).
3. Task prefix — `click.prompt`, default: `--task-prefix` value; validated at
   the prompt to be non-empty letters-only (same rule as
   `core/init.py:_normalize_task_prefix`) with `click.prompt` retry on bad
   input instead of failing after all questions.
4. Config location — `click.prompt` with `type=click.Choice(["local",
   "root"], case_sensitive=False)`, default: `--config-location` value.
5. Git integration — `click.confirm("Disable git integration?", ...)`
   defaulting to the `--no-git` flag value; the answer maps back to `no_git`.
6. Agent instruction files — `click.confirm("Create agent instruction
   files?", ...)` defaulting to the `--agent-instructions` flag value.

### Error handling

- Validation that only core can perform (backlog-dir path containment,
  config-location edge cases) still surfaces from `init_project` as
  `InitProjectError`, converted to the existing clean `ClickException` by
  `init_command`'s current `try/except`.
- Aborting a prompt (Ctrl-C / EOF) raises click's standard `Abort`, matching
  other interactive commands in this CLI.

## Data flow

```
argv parse (click)
  └─ init_command
       ├─ --defaults          → _locked_init(_initialize_project(flags))
       ├─ non-TTY, no flag    → ClickException("... Pass --defaults ...")
       └─ interactive         → _prompt_init_options(flags)  [no lock held]
                                  → _locked_init(_initialize_project(answers))
```

`_initialize_project` and `init_project` receive the same parameters in both
paths; only their source differs (flags vs prompt answers).

## Testing

New tests go in `tests/test_init_project.py` alongside the existing init
tests, using `click.testing.CliRunner` with `input=` to script prompt answers.
`CliRunner` stdin is never a TTY, so interactive tests monkeypatch
`_stdin_is_interactive` to return `True`, following the existing pattern in
`tests/test_cli_readonly.py` and `tests/test_cli_locking.py`:

- Interactive happy path: bare `init` with scripted answers produces the same
  directory skeleton and config values as the equivalent `--defaults` run.
- Enter-accepts-all-defaults: bare `init` with all answers left at their
  defaults (input of one newline per prompt, e.g. `"\n" * 6` — a literally
  empty `input=""` hits EOF and aborts the first prompt) equals
  `init --defaults` output on disk.
- Flag pre-fill: `init Myproj --task-prefix feat` (no `--defaults`) with
  newline-only answers writes `prefixes.task: feat` and project name `Myproj`.
- Non-TTY without `--defaults` still errors with the existing message
  (covered today by the test at line 54; keep it passing).
- Invalid task prefix at the prompt (e.g. `feat1`) re-prompts and a valid
  retry succeeds.
- All existing `--defaults` tests pass unchanged.

## Documentation updates

- `docs/interactive-deferrals.md`: add a row/notes recording that the init
  wizard is implemented for human operators and that non-interactive init
  remains deterministic via `--defaults`.
- `docs/agent-critical-parity.md`: update the `cli:init` row to include
  interactive usage.
- `docs/getting-started.md`: mention interactive init in the init section.

## Out of scope

- Prompting for full config keys (statuses, ports, hooks, etc.) — that remains
  the `backlog config` wizard's job.
- Changes to `core/init.py`, locking, or the `--defaults` behavior.
- MCP/browser surfaces; this is CLI-only.
