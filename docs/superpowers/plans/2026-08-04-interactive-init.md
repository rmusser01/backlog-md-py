# Interactive `backlog-py init` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let human operators run bare `backlog-py init` in a terminal and answer prompts for the core init settings, while `--defaults` and the non-TTY error path stay exactly as they are.

**Architecture:** All prompting lives in the CLI layer (`src/backlog_py/cli/main.py`). A new `_prompt_init_options` helper collects answers with `click.prompt`/`click.confirm` *before* the init lock is acquired, then the answers flow through the existing `_locked_init` → `_initialize_project` → `core/init.py:init_project` path unchanged. Core is not modified.

**Tech Stack:** Python 3.11+, click (already a dependency), pytest + click.testing.CliRunner, ruff lint (line-length 120).

**Spec:** `docs/superpowers/specs/2026-08-04-interactive-init-design.md`

**File map:**
- Modify: `src/backlog_py/cli/main.py` — `init_command` (lines 182-232) gains the interactive branch; new `_prompt_init_options` helper placed near it.
- Modify: `tests/test_init_project.py` — new interactive tests appended.
- Modify: `docs/interactive-deferrals.md`, `docs/agent-critical-parity.md`, `docs/getting-started.md` — doc updates.

**Tooling commands (run from repo root):**
- Tests: `uv run --extra dev pytest tests/test_init_project.py -v`
- Full suite: `uv run --extra dev pytest`
- Lint: `uv run --extra dev ruff check src tests`

---

### Task 1: Interactive prompt helper and `init_command` wiring (happy path)

**Files:**
- Modify: `src/backlog_py/cli/main.py` (`init_command` at lines 182-232; add `_prompt_init_options` right after it)
- Test: `tests/test_init_project.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_init_project.py`:

```python
def _force_interactive(monkeypatch):
    """CliRunner stdin is never a TTY; pretend it is for interactive-init tests."""
    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)


def test_cli_init_interactive_prompts_for_custom_values(tmp_path, monkeypatch):
    _force_interactive(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init"],
        input="Wizard Project\n\nJIRA\nroot\ny\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "Initialized Backlog.md project at" in result.output
    assert "Updated AGENTS.md" in result.output

    project = discover_project(tmp_path)
    assert project.config.project_name == "Wizard Project"
    assert project.config.task_prefix == "JIRA"
    assert project.backlog_dir == tmp_path / "backlog"
    # config location "root" with the default backlog dir writes backlog.config.yml
    assert (tmp_path / "backlog.config.yml").is_file()
    # answered "y" to disabling git integration
    assert project.config.remote_operations is False
    assert project.config.check_active_branches is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev pytest tests/test_init_project.py::test_cli_init_interactive_prompts_for_custom_values -v`
Expected: FAIL — the command exits non-zero with the current "Interactive init is not available" message.

- [ ] **Step 3: Implement the interactive branch**

In `src/backlog_py/cli/main.py`, replace the body of `init_command` (currently lines 206-232). The `--defaults` path and all output lines stay identical; only the guard changes and an interactive branch is added:

```python
def init_command(
    ctx: click.Context,
    project_name: str | None,
    defaults: bool,
    no_git: bool,
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    agent_instructions: bool,
) -> None:
    """Initialize a Backlog.md project, interactively when a terminal is available."""
    if not defaults:
        if not _stdin_is_interactive():
            raise click.ClickException(
                "Interactive init requires a terminal. Pass --defaults to use non-interactive defaults."
            )
        project_name, backlog_dir, task_prefix, config_location, no_git, agent_instructions = (
            _prompt_init_options(
                ctx,
                project_name=project_name,
                backlog_dir=backlog_dir,
                task_prefix=task_prefix,
                config_location=config_location,
                no_git=no_git,
                agent_instructions=agent_instructions,
            )
        )
    try:
        result, instruction_updates = _locked_init(
            ctx,
            "init_project",
            lambda: _initialize_project(
                ctx,
                project_name=project_name,
                backlog_dir=backlog_dir,
                task_prefix=task_prefix,
                config_location=config_location,
                no_git=no_git,
                agent_instructions=agent_instructions,
            ),
        )
    except InitProjectError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Initialized Backlog.md project at {result.project.backlog_dir}")
    if not result.config_created:
        click.echo(f"Preserved existing config at {result.project.config_path}")
    for update in instruction_updates:
        click.echo(f"Updated {update.path_relative}")
```

Note: the non-TTY error message changes from "Interactive init is not available in backlog-py yet. ..." to the new wording above. The existing test `test_cli_init_requires_defaults_for_non_interactive_setup` asserts the substring `"Pass --defaults"`, which the new message still contains, so it keeps passing. The new wording is required because the old message claims a feature that now exists does not.

Add the new helper immediately after `init_command`:

```python
def _prompt_init_options(
    ctx: click.Context,
    *,
    project_name: str | None,
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    no_git: bool,
    agent_instructions: bool,
) -> tuple[str, str, str, str, bool, bool]:
    """Collect init settings interactively; each parsed flag seeds its prompt default.

    Runs before the init lock is acquired so user think-time never holds the lock.
    """
    click.echo("Interactive Backlog.md project setup")
    name = click.prompt("Project name", default=project_name or _cwd(ctx).resolve().name).strip()
    directory = click.prompt("Backlog directory", default=backlog_dir).strip()
    while True:
        prefix = click.prompt("Task ID prefix", default=task_prefix).strip()
        if prefix.isalpha():
            break
        click.echo("Task ID prefix must be non-empty and contain only letters.")
    location = click.prompt(
        "Config location",
        default=config_location,
        type=click.Choice(["local", "root"], case_sensitive=False),
    )
    disable_git = click.confirm("Disable git integration?", default=no_git)
    instructions = click.confirm("Create agent instruction files?", default=agent_instructions)
    return name, directory, prefix, location, disable_git, instructions
```

(`_cwd(ctx)` is the existing helper at `main.py:2166`, also used by `_locked_init`/`_initialize_project` at `main.py:2202-2217`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --extra dev pytest tests/test_init_project.py::test_cli_init_interactive_prompts_for_custom_values -v`
Expected: PASS

- [ ] **Step 5: Run the whole init test file plus lint**

Run: `uv run --extra dev pytest tests/test_init_project.py -v && uv run --extra dev ruff check src/backlog_py/cli/main.py tests/test_init_project.py`
Expected: all tests PASS (including the pre-existing `--defaults` and non-TTY tests), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/backlog_py/cli/main.py tests/test_init_project.py
git commit -m "feat(cli): add interactive init prompts for core settings"
```

---

### Task 2: Pre-fill, accept-defaults, and invalid-prefix retry tests

These tests pin the brainstorming decisions: flags seed prompt defaults, Enter accepts everything, and a bad task prefix re-prompts instead of aborting.

**Files:**
- Test: `tests/test_init_project.py`
- Modify (only if a test exposes a defect): `src/backlog_py/cli/main.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_init_project.py`:

```python
def test_cli_init_interactive_enter_accepts_defaults(tmp_path, monkeypatch):
    _force_interactive(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init", "Demo Project"],
        input="\n" * 6,
    )

    assert result.exit_code == 0, result.output
    project = discover_project(tmp_path)
    assert project.config.project_name == "Demo Project"
    assert project.config.task_prefix == "task"
    assert (tmp_path / "backlog" / "config.yml").is_file()
    assert project.config.remote_operations is True
    assert project.config.check_active_branches is True


def test_cli_init_interactive_prefills_flag_values(tmp_path, monkeypatch):
    _force_interactive(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init", "Myproj", "--task-prefix", "feat", "--no-git"],
        input="\n" * 6,
    )

    assert result.exit_code == 0, result.output
    project = discover_project(tmp_path)
    assert project.config.project_name == "Myproj"
    assert project.config.task_prefix == "feat"
    assert project.config.remote_operations is False


def test_cli_init_interactive_reprompts_on_invalid_task_prefix(tmp_path, monkeypatch):
    _force_interactive(monkeypatch)

    # Answers: name (default), backlog dir (default), invalid prefix, retry prefix,
    # config location (default), git confirm (default), instructions confirm (default).
    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init"],
        input="\n\nfeat1\nfeat\n\n\n\n",
    )

    assert result.exit_code == 0, result.output
    assert "only letters" in result.output
    project = discover_project(tmp_path)
    assert project.config.task_prefix == "feat"
```

- [ ] **Step 2: Run the tests**

Run: `uv run --extra dev pytest tests/test_init_project.py -v`
Expected: PASS for all three. If the Task 1 implementation is exactly as written, no code changes are needed — these tests characterize behavior that already works. If one fails, fix `_prompt_init_options` minimally (do not change the tests to match buggy behavior).

- [ ] **Step 3: Lint**

Run: `uv run --extra dev ruff check src tests`
Expected: clean (no new violations).

- [ ] **Step 4: Commit**

```bash
git add tests/test_init_project.py src/backlog_py/cli/main.py
git commit -m "test(cli): pin interactive init pre-fill, default, and retry behavior"
```

---

### Task 3: Full-suite regression check

**Files:** none modified (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `uv run --extra dev pytest`
Expected: all tests PASS. Pay particular attention to `tests/test_init_project.py::test_cli_init_requires_defaults_for_non_interactive_setup` (non-TTY guard unchanged) and `tests/test_cli_locking.py` (init lock behavior unchanged).

- [ ] **Step 2: Commit only if something changed**

If a fix was needed, commit it with a message describing the regression fixed. Otherwise move on — no empty commits.

---

### Task 4: Documentation updates

**Files:**
- Modify: `docs/agent-critical-parity.md:14` (the `cli:init` row)
- Modify: `docs/interactive-deferrals.md`
- Modify: `docs/getting-started.md:52-69` (the init section)

- [ ] **Step 1: Update the parity matrix row**

In `docs/agent-critical-parity.md`, change the `cli:init` row (line 14) from:

```markdown
| cli:init | implemented | backlog init [project-name] --defaults [--no-git] --backlog-dir <path> --task-prefix <prefix> --config-location <location> --agent-instructions | cli:init |
```

to:

```markdown
| cli:init | implemented | backlog init [project-name] (interactive prompts on a TTY; parsed flags seed prompt defaults) or backlog init [project-name] --defaults [--no-git] --backlog-dir <path> --task-prefix <prefix> --config-location <location> --agent-instructions | cli:init |
```

- [ ] **Step 2: Record the init wizard in interactive-deferrals.md**

In `docs/interactive-deferrals.md`, add a row to the Deferral Matrix table (after the "Prompt-style board controls" row, keeping the existing column structure):

```markdown
| Interactive init wizard | Interactive CLI | Implemented | Interactive terminals are prompted for project name, backlog directory, task prefix, config location, git integration, and agent instruction files; non-interactive init remains deterministic via `--defaults`. |
```

and add a bullet to the "Current Runtime Policy" list (alongside the other "implemented for human operators" bullets):

```markdown
- Interactive init prompts are implemented for human operators on a TTY; bare
  `init` without `--defaults` still errors on non-interactive stdin so agents
  keep the deterministic `--defaults` contract.
```

- [ ] **Step 3: Mention interactive init in getting-started.md**

In `docs/getting-started.md`, after the `--no-git` example block (line 69), add:

```markdown
On an interactive terminal you can also omit `--defaults` to be prompted for
the project name, backlog directory, task prefix, config location, git
integration, and agent instruction files; any flags you pass become the prompt
defaults. Scripts and agents should keep using `--defaults`.
```

- [ ] **Step 4: Verify docs render and nothing else references the old error**

Run: `grep -rn "Interactive init is not available" src tests docs README.md --exclude-dir=superpowers`
Expected: no matches outside `docs/superpowers/` (the spec and plan quote the old message as historical background; that is intentional).

- [ ] **Step 5: Commit**

```bash
git add docs/agent-critical-parity.md docs/interactive-deferrals.md docs/getting-started.md
git commit -m "docs: record interactive init wizard in parity and deferral docs"
```

---

## Self-review notes

- Core (`src/backlog_py/core/init.py`), locking (`_locked_init`), and the `--defaults` path are untouched per the spec.
- Prompting happens entirely before the lock is acquired (`_prompt_init_options` returns before `_locked_init` is called).
- Six prompts in fixed order: project name → backlog directory → task prefix → config location → disable git → agent instructions. Tests that script `input=` must supply exactly six lines (seven for the invalid-prefix retry test).
- Out of scope per spec: full config keys, MCP/browser surfaces, core changes.
