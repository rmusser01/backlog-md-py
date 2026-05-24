# Textual TUI Kanban Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional Textual-powered `backlog-py tui` Kanban board with navigation, task detail, move, create, archive, editor launch, filtering, and refresh without adding Textual to the base install.

**Architecture:** Keep Textual isolated under `src/backlog_py/tui/`, expose it through a lazy CLI command, and normalize local-repository and daemon-MCP data into pure dataclasses before rendering. Mutations reuse existing repository locks or SDK-free MCP tools, while Textual work runs through worker/thread boundaries so file I/O and HTTP never block the message loop.

**Tech Stack:** Python 3.11+, Click, existing `backlog_py` repository/MCP/runtime modules, optional `textual` extra, pytest, Bandit, setuptools package data.

---

## File Structure

- Create `src/backlog_py/tui/__init__.py`: optional package marker; must not import Textual.
- Create `src/backlog_py/tui/models.py`: pure dataclasses and helper functions for task conversion, filters, status choices, and refresh selection.
- Create `src/backlog_py/tui/data.py`: `BoardDataSource` protocol, local source, daemon source, daemon JSON-RPC client, and source factory.
- Create `src/backlog_py/tui/app.py`: Textual app entry point, dependency import boundary, source selection, workers, refresh timer, and editor suspend flow.
- Create `src/backlog_py/tui/screens.py`: board workspace screen and screen-level event handling.
- Create `src/backlog_py/tui/widgets.py`: board columns, task cards, inspector, filter bar, and status/footer views.
- Create `src/backlog_py/tui/dialogs.py`: create, move, archive, and editor confirmation modal screens.
- Create `src/backlog_py/tui/styles.tcss`: packaged Textual stylesheet.
- Create `src/backlog_py/tui/testing.py`: small reusable Textual test helpers only if repeated test setup justifies it.
- Create `tests/test_tui_models.py`: pure model, parser, filtering, and selection tests.
- Create `tests/test_tui_data.py`: local and daemon data-source tests.
- Create `tests/test_tui_app.py`: mounted Textual rendering and interaction tests.
- Create `tests/test_tui_interactions.py`: mounted dialog, mutation, refresh, and editor tests if `tests/test_tui_app.py` gets too large.
- Modify `pyproject.toml`: add optional `tui` extra and package `styles.tcss`.
- Modify `src/backlog_py/cli/main.py`: register lazy `tui` command and keep Textual out of base imports.
- Modify `tests/test_cli_readonly.py`: CLI command registration and lazy-runner tests.
- Modify `tests/test_package_metadata.py`: optional dependency and package-data tests.
- Modify `README.md`, `docs/getting-started.md`, `docs/integration.md`, `docs/interactive-deferrals.md`, and `CHANGELOG.md`: document the optional TUI.

## Cross-Cutting Rules

- Do not import Textual outside `src/backlog_py/tui/app.py`, `screens.py`, `widgets.py`, `dialogs.py`, `testing.py`, or mounted Textual tests.
- Do not add `textual` to `[project].dependencies`; it belongs only in `[project.optional-dependencies].tui`.
- Do not start the daemon from the TUI. Use `daemon_status()` only.
- Do not bypass existing mutation paths. Local writes must use `with_project_write_lock`; daemon writes must call MCP tools and must not retry locally after mutation failures.
- Do not scan arbitrary raw Markdown for board-local text filtering. Use only normalized fields described in the spec.
- Preserve checklist item ids and checked state for Acceptance Criteria and Definition of Done display.
- Use existing no-shell editor command behavior; never introduce shell execution for editor launch.

## Existing tldw_chatbook Kanban Reference

The closest existing Kanban code in `tldw_chatbook` is a service/domain module, not a Textual Kanban UI:

- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/local_kanban_db.py`: SQLite schema for local boards, lists, cards, labels, checklists, comments, activities, links, and optional FTS.
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/local_kanban_service.py`: local async CRUD service with transactions, version checks, activity records, import/export, search degradation metadata, bulk operations, and policy action enforcement.
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/server_kanban_service.py`: operation-spec table and active-server adapter.
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/kanban_scope_service.py`: source-aware router that dispatches `mode="local"` or `mode="server"` and normalizes responses.
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/app.py:2221`: application wiring for local, server, and scope Kanban services.
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/Tests/Kanban/`: regression tests for schema setup, transaction rollback, operation coverage, local/server routing, policy blocking, CRUD, reorder, move, archive, labels, checklists, comments, search, links, import/export, and bulk operations.

Use these as architectural references for separation and tests:

- Keep source-specific code behind a small routing/data-source boundary.
- Normalize local and remote/daemon payloads before UI code sees them.
- Test read routing, mutation routing, failure modes, and operation coverage directly.
- Keep transactional/write-safety behavior in the data layer, not widgets.

Do not copy the local SQLite Kanban model into this first slice. `backlog-md-py` already has a Markdown task source of truth, repository write locks, and MCP/daemon tools. A durable Kanban database would be a separate persistence/sync project, not part of the optional TUI board.

### Task 1: Packaging And Lazy CLI Surface

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/backlog_py/cli/main.py`
- Create: `src/backlog_py/tui/__init__.py`
- Create: `src/backlog_py/tui/app.py`
- Modify: `tests/test_package_metadata.py`
- Modify: `tests/test_cli_readonly.py`

- [ ] **Step 1: Add failing package metadata tests**

Add tests proving Textual is optional, the `tui` extra exists, and `styles.tcss` will be included as package data.

```python
def test_pyproject_declares_textual_tui_as_optional_extra_only():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]
    optional = pyproject["project"]["optional-dependencies"]

    assert not any("textual" in dependency.casefold() for dependency in dependencies)
    assert "tui" in optional
    assert any(dependency.startswith("textual>=") for dependency in optional["tui"])


def test_pyproject_packages_tui_stylesheet():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    package_data = pyproject["tool"]["setuptools"]["package-data"]["backlog_py"]

    assert "py.typed" in package_data
    assert "tui/styles.tcss" in package_data
```

- [ ] **Step 2: Add failing CLI tests**

Add command registration and lazy-runner tests without requiring Textual in the test environment.
Add `import click` at the top of `tests/test_cli_readonly.py` if it is not already present.

```python
def test_top_level_help_includes_tui_command():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "tui" in result.output


def test_tui_command_invokes_lazy_runner_with_discovered_project(monkeypatch):
    from backlog_py.cli import main as cli_main

    calls = []
    monkeypatch.setattr(cli_main, "_load_tui_runner", lambda: lambda project: calls.append(project.root))

    result = _invoke("tui")

    assert result.exit_code == 0
    assert calls == [FIXTURE_REPO]


def test_tui_command_without_extra_shows_install_hint(monkeypatch):
    from backlog_py.cli import main as cli_main

    def missing_runner():
        raise click.ClickException("Install with backlog-md-py[tui] to use the Textual TUI.")

    monkeypatch.setattr(cli_main, "_load_tui_runner", missing_runner)

    result = _invoke("tui")

    assert result.exit_code != 0
    assert "Install with backlog-md-py[tui]" in result.output
```

- [ ] **Step 3: Run targeted tests to verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_package_metadata.py::test_pyproject_declares_textual_tui_as_optional_extra_only tests/test_package_metadata.py::test_pyproject_packages_tui_stylesheet tests/test_cli_readonly.py::test_top_level_help_includes_tui_command tests/test_cli_readonly.py::test_tui_command_invokes_lazy_runner_with_discovered_project tests/test_cli_readonly.py::test_tui_command_without_extra_shows_install_hint -q
```

Expected: FAIL because the `tui` extra, package-data entry, command, and loader do not exist.

- [ ] **Step 4: Add package metadata and CLI command**

Update `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
  "bandit>=1.7.0",
  "build>=1.2.0",
  "pytest>=8.0.0",
  "pytest-asyncio>=0.23.0",
  "twine>=5.0.0",
]
tui = [
  "textual>=0.86.0",
]

[tool.setuptools.package-data]
backlog_py = ["py.typed", "tui/styles.tcss"]
```

Add `src/backlog_py/tui/__init__.py`:

```python
"""Optional Textual TUI package for backlog-md-py."""
```

Add the temporary `src/backlog_py/tui/app.py` dependency boundary:

```python
from __future__ import annotations

from typing import NoReturn

from backlog_py.core.models import BacklogProject


INSTALL_HINT = "Install with backlog-md-py[tui] to use the Textual TUI."


class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""


def run_tui_app(project: BacklogProject) -> NoReturn:
    """Launch the optional Textual TUI."""
    _ = project
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise TuiDependencyError(INSTALL_HINT) from exc
        raise
    raise RuntimeError("Textual TUI app is not implemented yet.")
```

Add `src/backlog_py/cli/main.py` helpers and command:

```python
@main.command("tui")
@click.pass_context
def tui_command(ctx: click.Context) -> None:
    """Launch the optional Textual board."""
    runner = _load_tui_runner()
    runner(_project(ctx))


def _load_tui_runner() -> Callable[[BacklogProject], None]:
    try:
        from backlog_py.tui import app as tui_app
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise click.ClickException("Install with backlog-md-py[tui] to use the Textual TUI.") from exc
        raise
    except RuntimeError as exc:
        if exc.__class__.__name__ == "TuiDependencyError":
            raise click.ClickException(str(exc)) from exc
        raise

    def runner(project: BacklogProject) -> None:
        try:
            tui_app.run_tui_app(project)
        except tui_app.TuiDependencyError as exc:
            raise click.ClickException(str(exc)) from exc

    return runner
```

Create placeholder `src/backlog_py/tui/styles.tcss` so package-data tests can pass:

```css
/* Styles are filled in by the Textual app task. */
```

- [ ] **Step 5: Run targeted tests to verify GREEN**

Run:

```bash
uv run --extra dev python -m pytest tests/test_package_metadata.py::test_pyproject_declares_textual_tui_as_optional_extra_only tests/test_package_metadata.py::test_pyproject_packages_tui_stylesheet tests/test_cli_readonly.py::test_top_level_help_includes_tui_command tests/test_cli_readonly.py::test_tui_command_invokes_lazy_runner_with_discovered_project tests/test_cli_readonly.py::test_tui_command_without_extra_shows_install_hint -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/backlog_py/cli/main.py src/backlog_py/tui/__init__.py src/backlog_py/tui/app.py src/backlog_py/tui/styles.tcss tests/test_package_metadata.py tests/test_cli_readonly.py
git commit -m "feat: add optional TUI entry point"
```

### Task 2: Pure Board Models, Conversion, Filtering, And Selection

**Files:**
- Create: `src/backlog_py/tui/models.py`
- Modify: `src/backlog_py/tui/app.py`
- Create: `tests/test_tui_models.py`

- [ ] **Step 1: Add failing pure model tests**

Create `tests/test_tui_models.py` with tests that do not import Textual.

```python
from pathlib import Path

import shutil

from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.storage.project import discover_project
from backlog_py.tui.models import (
    BoardSnapshot,
    FilterState,
    SelectionState,
    checklist_items_from_parsed,
    create_status_choices,
    filter_snapshot,
    move_status_choices,
    select_after_refresh,
    task_view_from_mcp_payload,
    task_view_from_record,
)


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_task_view_from_record_preserves_metadata_and_checklists(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).edit_task(
        "TASK-1",
        assignees=("alice",),
        labels=("python", "compat"),
        priority="high",
    )
    task = ReadOnlyRepository(project).get_task("TASK-1")

    view = task_view_from_record(project, task)

    assert view.id == "TASK-1"
    assert view.priority == "high"
    assert view.assignees == ("alice",)
    assert view.labels == ("python", "compat")
    assert [(item.item_id, item.checked, item.text) for item in view.acceptance_criteria] == [
        ("1", True, "Preserve completed acceptance criteria raw line"),
        ("2", False, "Preserve incomplete acceptance criteria raw line"),
        (None, False, "Plain checklist item without an id"),
    ]
    assert view.definition_of_done[0].checked is True


def test_checklist_items_from_parsed_preserves_item_ids_and_checked_state():
    parsed = parse_task_markdown(
        "---\nid: TASK-9\ntitle: Demo\nstatus: To Do\n---\n"
        "<!-- AC:BEGIN -->\n"
        "- [x] #done Done item\n"
        "- [ ] #todo Todo item\n"
        "<!-- AC:END -->\n"
    )

    items = checklist_items_from_parsed(parsed, "AC")

    assert [(item.item_id, item.checked, item.text) for item in items] == [
        ("done", True, "Done item"),
        ("todo", False, "Todo item"),
    ]


def test_task_view_from_mcp_payload_hydrates_missing_filter_fields_from_raw_source(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).edit_task(
        "TASK-1",
        assignees=("alice",),
        labels=("python", "compat"),
        priority="high",
    )
    task = ReadOnlyRepository(project).get_task("TASK-1")
    payload = {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "description": task.description,
        "path": task.path.relative_to(project.root).as_posix(),
        "raw_source": task.raw_source,
    }

    view = task_view_from_mcp_payload(project, payload)

    assert view.priority == "high"
    assert view.assignees == ("alice",)
    assert view.labels == ("python", "compat")
    assert view.acceptance_criteria[0].checked is True


def test_filter_snapshot_matches_normalized_fields_without_raw_markdown_body():
    hidden_raw_text = "raw-only-secret"
    task = _task_view(
        "TASK-1",
        "Parser bug",
        "In Progress",
        raw_source=hidden_raw_text,
        priority="high",
        assignees=("alice",),
        labels=("ui",),
    )
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "In Progress"),
        columns={"To Do": (), "In Progress": (task,)},
        source="local",
        revision=None,
    )

    assert filter_snapshot(snapshot, FilterState(text="parser")).columns["In Progress"] == (task,)
    assert filter_snapshot(snapshot, FilterState(status="In Progress")).columns["In Progress"] == (task,)
    assert filter_snapshot(snapshot, FilterState(priority="high")).columns["In Progress"] == (task,)
    assert filter_snapshot(snapshot, FilterState(assignee="alice")).columns["In Progress"] == (task,)
    assert filter_snapshot(snapshot, FilterState(label="ui")).columns["In Progress"] == (task,)
    assert filter_snapshot(snapshot, FilterState(text="raw-only-secret")).columns["In Progress"] == ()


def test_create_status_choices_include_default_status_for_unconfigured_board(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    assert create_status_choices(project, board_statuses=()) == (project.config.default_status,)
    assert create_status_choices(project, board_statuses=("Doing",)) == ("Doing", project.config.default_status)


def test_move_status_choices_use_configured_statuses_when_present(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    assert move_status_choices(project, board_statuses=("Ad Hoc",)) == tuple(project.config.statuses)


def test_select_after_refresh_uses_deterministic_delete_fallback():
    before = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Doing", "Done"),
        columns={
            "To Do": (_task_view("TASK-1", "One", "To Do"), _task_view("TASK-2", "Two", "To Do")),
            "Doing": (),
            "Done": (_task_view("TASK-3", "Three", "Done"),),
        },
        source="local",
        revision=None,
    )
    after = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Doing", "Done"),
        columns={
            "To Do": (_task_view("TASK-1", "One", "To Do"),),
            "Doing": (),
            "Done": (_task_view("TASK-3", "Three", "Done"),),
        },
        source="local",
        revision=None,
    )

    selected = select_after_refresh(before, after, SelectionState(task_id="TASK-2", status="To Do", row=1))

    assert selected.task_id == "TASK-1"
```

Define `_task_view()` in the test as a tiny factory that builds `TaskView`.

- [ ] **Step 2: Run pure model tests to verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_tui_models.py -q
```

Expected: FAIL because `backlog_py.tui.models` does not exist.

- [ ] **Step 3: Implement pure models and helpers**

Add `src/backlog_py/tui/models.py`:

```python
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

from backlog_py.core.models import BacklogProject, ParsedTaskMarkdown
from backlog_py.core.repository import TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown


BoardSourceName = Literal["local", "daemon"]


@dataclass(frozen=True)
class ChecklistItemView:
    item_id: str | None
    text: str
    checked: bool


@dataclass(frozen=True)
class TaskView:
    id: str
    title: str
    status: str
    description: str
    path: Path
    priority: str | None = None
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    milestone: str | None = None
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[ChecklistItemView, ...] = ()
    definition_of_done: tuple[ChecklistItemView, ...] = ()
    raw_source: str | None = None


@dataclass(frozen=True)
class BoardSnapshot:
    project_name: str
    project_root: Path
    statuses: tuple[str, ...]
    columns: Mapping[str, tuple[TaskView, ...]]
    source: BoardSourceName
    revision: str | None


@dataclass(frozen=True)
class FilterState:
    text: str = ""
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class SelectionState:
    task_id: str | None = None
    status: str | None = None
    row: int = 0


@dataclass(frozen=True)
class CreateTaskInput:
    title: str
    status: str | None = None
    priority: str | None = None
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    milestone: str | None = None
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    definition_of_done_add: tuple[str, ...] = ()
    description: str = ""
```

Implement helpers in the same file:

```python
def checklist_items_from_parsed(parsed: ParsedTaskMarkdown, name: str) -> tuple[ChecklistItemView, ...]:
    return tuple(
        ChecklistItemView(item_id=item.item_id, text=item.text, checked=item.checked)
        for item in parsed.checklists.get(name, [])
    )


def task_view_from_record(project: BacklogProject, task: TaskRecord) -> TaskView:
    parsed = task.parsed
    return TaskView(
        id=task.id,
        title=task.title,
        status=task.status,
        description=task.description,
        path=task.path,
        priority=_optional_string(parsed.frontmatter.get("priority")),
        assignees=_string_tuple(parsed.frontmatter.get("assignee")),
        labels=_string_tuple(parsed.frontmatter.get("labels")),
        milestone=_optional_string(parsed.frontmatter.get("milestone")),
        dependencies=_string_tuple(parsed.frontmatter.get("dependencies")),
        acceptance_criteria=checklist_items_from_parsed(parsed, "AC"),
        definition_of_done=checklist_items_from_parsed(parsed, "DOD"),
        raw_source=task.raw_source,
    )
```

Also implement:

- `task_view_from_mcp_payload(project, payload)` that resolves `payload["path"]` under `project.root`, parses `raw_source` when present, and overlays summary fields.
- `board_snapshot_from_local(project, board, source="local")`.
- `filter_snapshot(snapshot, filters)` with case-insensitive substring text over id, title, description, priority, milestone, assignees, labels, dependencies, AC text, and DoD text only.
- `move_status_choices(project, board_statuses)`.
- `create_status_choices(project, board_statuses)` that returns configured statuses when `config.statuses` is set, otherwise current board statuses plus `default_status` when it is not already present.
- `select_after_refresh(previous, current, old_selection)` using the fallback order in the spec.
- Private `_string_tuple()` and `_optional_string()` helpers that tolerate strings, lists, tuples, missing values, and empty strings.

- [ ] **Step 4: Run pure model tests to verify GREEN**

Run:

```bash
uv run --extra dev python -m pytest tests/test_tui_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Run base import smoke**

Run:

```bash
uv run --extra dev python - <<'PY'
import backlog_py
import backlog_py.cli.main
import backlog_py.tui.models
print("ok")
PY
```

Expected: prints `ok` without importing Textual.

- [ ] **Step 6: Commit**

```bash
git add src/backlog_py/tui/models.py tests/test_tui_models.py src/backlog_py/tui/app.py
git commit -m "feat: add pure TUI board models"
```

### Task 3: Local And Daemon Board Data Sources

**Files:**
- Create: `src/backlog_py/tui/data.py`
- Modify: `src/backlog_py/tui/models.py`
- Create: `tests/test_tui_data.py`

- [ ] **Step 0: Review the tldw_chatbook Kanban data-source reference**

Before writing data-source tests, inspect the source-router shape in:

- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/kanban_scope_service.py`
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/tldw_chatbook/Kanban_Interop/local_kanban_service.py`
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/Tests/Kanban/test_kanban_scope_service.py`
- `/Users/macbook-dev/Documents/GitHub/tldw_chatbook/Tests/Kanban/test_local_kanban_service.py`

Carry forward the separation and test style, but keep `backlog-md-py`'s implementation bound to Markdown repository records and existing MCP tools. Do not introduce a TUI-specific SQLite Kanban database.

- [ ] **Step 1: Add failing data-source tests**

Create `tests/test_tui_data.py` with local and fake-daemon coverage.

```python
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backlog_py.core.repository import ReadOnlyRepository
from backlog_py.daemon.lifecycle import DaemonNotRunningError
from backlog_py.storage.project import discover_project
from backlog_py.tui.data import (
    DaemonBoardDataSource,
    DaemonMutationError,
    LocalBoardDataSource,
    create_board_data_source,
)
from backlog_py.tui.models import CreateTaskInput


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_local_data_source_loads_board_and_mutates_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    operations = []

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.data.with_project_write_lock", fake_lock)
    source = LocalBoardDataSource(project)

    created = source.create_task(CreateTaskInput(title="New card", status="To Do", labels=("tui",)))
    moved = source.move_task(created.id, "Done")

    assert created.labels == ("tui",)
    assert moved.status == "Done"
    assert operations == [(repo, "tui_task_create"), (repo, "tui_task_move")]


def test_local_source_task_path_rejects_paths_outside_project(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    path = LocalBoardDataSource(project).task_path("TASK-1")

    assert path.is_relative_to(repo)


def test_daemon_data_source_loads_board_with_task_view_hydration():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    task = ReadOnlyRepository(project).get_task("TASK-1")
    daemon = _FakeMcpDaemon(
        {
            "task_board": {
                "In Progress": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "description": task.description,
                        "path": task.path.relative_to(project.root).as_posix(),
                    }
                ]
            },
            "task_view": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "description": task.description,
                "path": task.path.relative_to(project.root).as_posix(),
                "raw_source": task.raw_source,
            },
        }
    )
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        snapshot = source.load_board()
    finally:
        daemon.shutdown()

    assert snapshot.source == "daemon"
    assert snapshot.columns["In Progress"][0].acceptance_criteria[0].checked is True
    assert [request["tool"] for request in daemon.tool_calls] == ["task_board", "task_view"]


def test_daemon_mutation_failure_does_not_fall_back_to_local_mode():
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    daemon = _FakeMcpDaemon({"task_edit": RuntimeError("boom")})
    try:
        source = DaemonBoardDataSource(project, endpoint=daemon.endpoint, token="secret")
        with pytest.raises(DaemonMutationError):
            source.move_task("TASK-1", "Done")
    finally:
        daemon.shutdown()


def test_factory_uses_healthy_daemon_without_starting_one(monkeypatch):
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    monkeypatch.setattr("backlog_py.tui.data.daemon_status", lambda: _status("http://127.0.0.1:1/mcp", "secret"))

    source = create_board_data_source(project)

    assert isinstance(source, DaemonBoardDataSource)


def test_factory_falls_back_to_local_when_daemon_is_not_running(monkeypatch):
    project = discover_project(Path.cwd(), explicit_cwd=FIXTURE_REPO)
    monkeypatch.setattr(
        "backlog_py.tui.data.daemon_status",
        lambda: (_ for _ in ()).throw(DaemonNotRunningError("Daemon not running")),
    )

    source = create_board_data_source(project)

    assert isinstance(source, LocalBoardDataSource)
```

Use a local `_FakeMcpDaemon` modeled after `tests/test_mcp_stdio_sdk_free.py`; it should record `tools/call` names and return JSON-RPC responses for `task_board`, `task_view`, `task_create`, `task_edit`, and `task_archive`.

- [ ] **Step 2: Run data tests to verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_tui_data.py -q
```

Expected: FAIL because `backlog_py.tui.data` does not exist.

- [ ] **Step 3: Implement data-source protocol and local source**

Add `src/backlog_py/tui/data.py`:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError
from backlog_py.daemon.lifecycle import DaemonNotRunningError, daemon_status
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.security.paths import assert_path_within_base
from backlog_py.tui.models import (
    BoardSnapshot,
    CreateTaskInput,
    TaskView,
    board_snapshot_from_local,
    task_view_from_mcp_payload,
    task_view_from_record,
)
```

Define:

```python
class BoardDataSource(Protocol):
    source_name: str

    def load_board(self) -> BoardSnapshot: ...
    def create_task(self, input: CreateTaskInput) -> TaskView: ...
    def move_task(self, task_id: str, status: str) -> TaskView: ...
    def archive_task(self, task_id: str) -> TaskView: ...
    def task_path(self, task_id: str) -> Path: ...


@dataclass(frozen=True)
class LocalBoardDataSource:
    project: BacklogProject
    source_name: str = "local"

    def load_board(self) -> BoardSnapshot:
        repository = ReadOnlyRepository(self.project)
        return board_snapshot_from_local(self.project, repository.board(), source="local")

    def create_task(self, input: CreateTaskInput) -> TaskView:
        def mutate() -> TaskView:
            task = MutableRepository(self.project).create_task(
                title=input.title,
                status=input.status,
                description=input.description,
                acceptance_criteria=input.acceptance_criteria,
                definition_of_done_add=input.definition_of_done_add,
                dependencies=input.dependencies,
                assignees=input.assignees,
                labels=input.labels,
                priority=input.priority,
                milestone=input.milestone,
            )
            return task_view_from_record(self.project, task)

        return with_project_write_lock(self.project, "tui_task_create", mutate)
```

Also implement `move_task`, `archive_task`, and `task_path`. `task_path()` must resolve the repository task path and call `assert_path_within_base(self.project.root, task.path)`.

- [ ] **Step 4: Implement daemon JSON-RPC client and daemon source**

Add:

```python
class DaemonReadError(RuntimeError):
    """Raised when daemon board reads fail."""


class DaemonMutationError(RuntimeError):
    """Raised when daemon mutations fail."""


@dataclass(frozen=True)
class DaemonMcpClient:
    endpoint: str
    token: str
    timeout: float = 30.0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        request_id = 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
            result = json.loads(response.read().decode("utf-8"))
        if "error" in result:
            raise RuntimeError(result["error"].get("message", "Daemon tool call failed"))
        return _extract_tool_result(result)
```

`_extract_tool_result()` must parse the SDK-free MCP response shape returned by
`src/backlog_py/mcp/protocol.py`: `response["result"]["content"][0]["text"]`
contains either plain text or JSON text. For TUI tools, parse JSON text into the
native list/dict result and raise if `isError` is true or the content shape is
missing.

Validate daemon endpoints with the same loopback-only policy as stdio forwarding before constructing requests. If this validation is duplicated, keep it private and small; do not import underscored helpers from `mcp.stdio_server`.

Implement `DaemonBoardDataSource`:

```python
@dataclass(frozen=True)
class DaemonBoardDataSource:
    project: BacklogProject
    endpoint: str
    token: str
    source_name: str = "daemon"

    def __post_init__(self) -> None:
        _validate_loopback_http_endpoint(self.endpoint)

    def load_board(self) -> BoardSnapshot:
        client = DaemonMcpClient(self.endpoint, self.token)
        try:
            board = client.call_tool("task_board", {"project": str(self.project.root)})
            hydrated = {}
            for status, rows in board.items():
                hydrated[status] = tuple(
                    task_view_from_mcp_payload(
                        self.project,
                        client.call_tool("task_view", {"project": str(self.project.root), "task_id": row["id"]}),
                    )
                    for row in rows
                )
            return BoardSnapshot(
                project_name=self.project.config.project_name,
                project_root=self.project.root,
                statuses=tuple(board.keys()),
                columns=hydrated,
                source="daemon",
                revision=None,
            )
        except Exception as exc:
            raise DaemonReadError(str(exc)) from exc
```

For daemon mutations:

- `create_task()` calls `task_create` with `title`, `status`, `description`, `priority`, `assignees`, `labels`, `milestone`, `dependencies`, `acceptanceCriteria`, and `definitionOfDoneAdd`.
- `move_task()` calls `task_edit` with `task_id` and `status`.
- `archive_task()` calls `task_archive` with `task_id`.
- Mutations wrap all failures in `DaemonMutationError`.
- `task_path()` should use `task_view` path resolution and `assert_path_within_base()`.

Implement `create_board_data_source(project)`:

```python
def create_board_data_source(project: BacklogProject) -> BoardDataSource:
    try:
        status = daemon_status()
    except DaemonNotRunningError:
        return LocalBoardDataSource(project)
    return DaemonBoardDataSource(project, endpoint=status.record.endpoint, token=status.record.token)
```

- [ ] **Step 5: Run data tests to verify GREEN**

Run:

```bash
uv run --extra dev python -m pytest tests/test_tui_data.py tests/test_tui_models.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backlog_py/tui/data.py src/backlog_py/tui/models.py tests/test_tui_data.py
git commit -m "feat: add TUI board data sources"
```

### Task 4: Mounted Textual App Shell And Board Rendering

**Files:**
- Modify: `src/backlog_py/tui/app.py`
- Create: `src/backlog_py/tui/screens.py`
- Create: `src/backlog_py/tui/widgets.py`
- Modify: `src/backlog_py/tui/styles.tcss`
- Create: `tests/test_tui_app.py`

- [ ] **Step 1: Add failing mounted app tests**

Create `tests/test_tui_app.py`. These tests require the `tui` extra and should skip clearly if Textual is not installed.

```python
import pytest

pytest.importorskip("textual")
pytestmark = pytest.mark.asyncio

from pathlib import Path

from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.tui.data import DaemonReadError
from backlog_py.tui.app import BacklogTuiApp
from backlog_py.tui.models import BoardSnapshot


async def test_app_renders_board_columns_inspector_and_footer():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test() as pilot:
        await pilot.pause()

        assert pilot.app.query_one("#board-title").renderable.plain == "Demo"
        assert pilot.app.query_one("#column-To-Do")
        assert pilot.app.query_one("#column-In-Progress")
        assert "TASK-1" in pilot.app.query_one("#task-inspector").renderable.plain
        assert pilot.app.query_one("Footer")


async def test_keyboard_selection_moves_between_cards_and_columns():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test() as pilot:
        await pilot.press("right")
        await pilot.pause()

        assert pilot.app.selected_task_id == "TASK-2"


async def test_mouse_click_selects_task_card_where_headless_textual_supports_it():
    app = BacklogTuiApp(project=_project(), data_source=_StaticSource(_snapshot()))

    async with app.run_test() as pilot:
        await pilot.click("#task-card-TASK-2")
        await pilot.pause()

        assert pilot.app.selected_task_id == "TASK-2"


async def test_daemon_read_failure_switches_to_local_source_with_notice():
    failing = _FailingDaemonSource(DaemonReadError("daemon unavailable"))
    local = _StaticSource(_snapshot(source="local"))
    notices = []
    app = BacklogTuiApp(
        project=_project(),
        data_source=failing,
        fallback_source_factory=lambda project: local,
    )
    app.notify = lambda message, **kwargs: notices.append((message, kwargs))

    async with app.run_test() as pilot:
        await pilot.pause()

        assert pilot.app.data_source is local
        assert pilot.app.snapshot.source == "local"
        assert any("daemon unavailable" in message for message, _ in notices)
```

Add `_project()`, `_snapshot()`, `_StaticSource`, and `_FailingDaemonSource` helpers in the test file. `_StaticSource` and `_FailingDaemonSource` should implement the `BoardDataSource` protocol without touching disk.

- [ ] **Step 2: Run mounted app tests to verify RED**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_app.py -q
```

Expected: FAIL because `BacklogTuiApp`, screens, and widgets do not exist.

- [ ] **Step 3: Implement Textual app shell**

Replace the temporary `src/backlog_py/tui/app.py` with the real import boundary:

```python
from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Callable

from backlog_py.core.models import BacklogProject
from backlog_py.runtime.locks import with_project_write_lock
from backlog_py.tui.data import BoardDataSource, DaemonReadError, LocalBoardDataSource, create_board_data_source
from backlog_py.tui.models import BoardSnapshot, FilterState, SelectionState, select_after_refresh


INSTALL_HINT = "Install with backlog-md-py[tui] to use the Textual TUI."


class TuiDependencyError(RuntimeError):
    """Raised when optional Textual dependencies are unavailable."""
```

Then import Textual after the dependency boundary:

```python
try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.worker import Worker
except ModuleNotFoundError as exc:
    if exc.name == "textual":
        raise TuiDependencyError(INSTALL_HINT) from exc
    raise
```

Define:

```python
class BacklogTuiApp(App[None]):
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("/", "focus_filter", "Filter"),
        Binding("m", "move_task", "Move"),
        Binding("n", "create_task", "New"),
        Binding("a", "archive_task", "Archive"),
        Binding("e", "edit_task", "Edit"),
        Binding("enter", "focus_inspector", "Detail"),
    ]

    def __init__(
        self,
        project: BacklogProject,
        data_source: BoardDataSource | None = None,
        *,
        fallback_source_factory: Callable[[BacklogProject], BoardDataSource] = LocalBoardDataSource,
        refresh_interval: float = 5.0,
    ) -> None:
        super().__init__()
        self.project = project
        self.data_source = data_source or create_board_data_source(project)
        self.fallback_source_factory = fallback_source_factory
        self.refresh_interval = refresh_interval
        self.snapshot: BoardSnapshot | None = None
        self.filter_state = FilterState()
        self.selection = SelectionState()
        self.modal_depth = 0
        self.deferred_refresh = False
```

Use `self.run_worker(self._load_snapshot, thread=True, exclusive=True, name="load_board")` for initial and refresh loads. Worker success updates the mounted screen. If the load raises `DaemonReadError`, notify the user, replace `self.data_source` with `self.fallback_source_factory(self.project)`, and immediately load from that local source. Other worker failures show `self.notify(str(error), severity="error")` without source switching.

Expose `run_tui_app(project)`:

```python
def run_tui_app(project: BacklogProject) -> None:
    BacklogTuiApp(project).run()
```

- [ ] **Step 4: Implement screen and widgets**

In `screens.py`, define `BoardScreen` that composes header, board region, inspector, and footer.

In `widgets.py`, define:

- `BoardHeader`: project name, source, filter summary, refresh state.
- `FilterBar`: text/status/priority/assignee/label fields.
- `BoardColumn`: stable status column with deterministic id from status.
- `TaskCard`: one focusable/selectable task row with id `task-card-{task.id}`.
- `TaskInspector`: selected task metadata, description, AC, and DoD with checked state.

Use ids that tests can query:

```python
def widget_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-")
```

Board columns use `id=f"column-{widget_id(status)}"`; inspector uses `id="task-inspector"`; header title uses `id="board-title"`.

Fill `styles.tcss` with a restrained terminal layout:

```css
BacklogTuiApp {
    layout: vertical;
}

#board-root {
    layout: horizontal;
    height: 1fr;
}

.board-columns {
    width: 1fr;
    overflow-x: auto;
}

.board-column {
    width: 32;
    min-width: 24;
    height: 1fr;
    border: solid $panel;
}

.task-card {
    height: auto;
    padding: 0 1;
}

.task-card.-selected {
    background: $accent;
    color: $text;
}

#task-inspector {
    width: 42;
    border-left: solid $panel;
    padding: 1;
}
```

- [ ] **Step 5: Run mounted app tests to verify GREEN**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_app.py -q
```

Expected: PASS.

- [ ] **Step 6: Run import and base tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_cli_readonly.py::test_top_level_help_includes_tui_command tests/test_cli_readonly.py::test_tui_command_without_extra_shows_install_hint -q
```

Expected: PASS without installing the `tui` extra.

- [ ] **Step 7: Commit**

```bash
git add src/backlog_py/tui/app.py src/backlog_py/tui/screens.py src/backlog_py/tui/widgets.py src/backlog_py/tui/styles.tcss tests/test_tui_app.py
git commit -m "feat: render Textual TUI board"
```

### Task 5: TUI Interactions, Dialogs, Editor Suspend, And Refresh Deferral

**Files:**
- Modify: `src/backlog_py/tui/app.py`
- Modify: `src/backlog_py/tui/screens.py`
- Modify: `src/backlog_py/tui/widgets.py`
- Create: `src/backlog_py/tui/dialogs.py`
- Modify: `tests/test_tui_app.py`
- Create: `tests/test_tui_interactions.py`

- [ ] **Step 1: Add failing interaction tests**

Create `tests/test_tui_interactions.py` with mounted tests for mutation flows.

```python
import pytest

pytest.importorskip("textual")
pytestmark = pytest.mark.asyncio

from backlog_py.tui.app import BacklogTuiApp, default_editor_runner
from backlog_py.tui.models import CreateTaskInput


async def test_move_dialog_updates_selected_task_status():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.press("m")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert source.moves == [("TASK-1", "Done")]
        assert app.snapshot.columns["Done"][0].id == "TASK-1"


async def test_create_dialog_creates_task_with_first_slice_fields():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.press("n")
        await pilot.type("New task")
        await pilot.press("tab")
        await pilot.type("high")
        await pilot.press("tab")
        await pilot.type("alice,bob")
        await pilot.press("tab")
        await pilot.type("ui,tui")
        await pilot.press("tab")
        await pilot.type("Release 1")
        await pilot.press("tab")
        await pilot.type("TASK-1")
        await pilot.press("tab")
        await pilot.type("Works from keyboard\nKeeps metadata")
        await pilot.press("tab")
        await pilot.type("Tests pass")
        await pilot.press("tab")
        await pilot.type("Description body")
        await pilot.press("enter")
        await pilot.pause()

    assert source.creates == [
        CreateTaskInput(
            title="New task",
            status="To Do",
            priority="high",
            assignees=("alice", "bob"),
            labels=("ui", "tui"),
            milestone="Release 1",
            dependencies=("TASK-1",),
            acceptance_criteria=("Works from keyboard", "Keeps metadata"),
            definition_of_done_add=("Tests pass",),
            description="Description body",
        )
    ]


async def test_archive_confirmation_archives_selected_task():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.press("enter")
        await pilot.pause()

    assert source.archives == ["TASK-1"]


async def test_editor_confirmation_suspends_runs_editor_refreshes_and_reselects_task():
    source = _MutableSource(_snapshot())
    editor_calls = []
    project = _project()

    def fake_editor(project, path):
        editor_calls.append((project.root, path))
        source.replace_snapshot(_snapshot(title="Edited title"))

    app = BacklogTuiApp(project=project, data_source=source, editor_runner=fake_editor)

    async with app.run_test() as pilot:
        await pilot.press("e")
        await pilot.press("enter")
        await pilot.pause()

    assert editor_calls == [(project.root, source.task_path("TASK-1"))]
    assert app.selected_task_id == "TASK-1"
    assert app.snapshot.columns["To Do"][0].title == "Edited title"


def test_default_editor_runner_uses_project_write_lock(monkeypatch):
    project = _project()
    path = project.root / "backlog" / "tasks" / "task-1 - Example-task.md"
    operations = []
    editor_calls = []

    monkeypatch.setattr("backlog_py.cli.main._configured_editor_command", lambda project: ["fake-editor"])
    monkeypatch.setattr("backlog_py.cli.main._run_editor_command", lambda command, path: editor_calls.append((command, path)))

    def fake_lock(project_arg, operation, fn):
        operations.append((project_arg.root, operation))
        return fn()

    monkeypatch.setattr("backlog_py.tui.app.with_project_write_lock", fake_lock)

    default_editor_runner(project, path)

    assert operations == [(project.root, "tui_task_editor")]
    assert editor_calls == [(["fake-editor"], path)]


async def test_refresh_while_modal_open_is_deferred_until_modal_closes():
    source = _MutableSource(_snapshot())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.press("n")
        source.replace_snapshot(_snapshot(title="Externally changed"))
        app.action_refresh()
        await pilot.pause()
        assert app.snapshot.columns["To Do"][0].title != "Externally changed"
        await pilot.press("escape")
        await pilot.pause()

    assert app.snapshot.columns["To Do"][0].title == "Externally changed"
```

Add shared helpers or move common helpers into `src/backlog_py/tui/testing.py` only if duplication becomes hard to read.

- [ ] **Step 2: Run interaction tests to verify RED**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_interactions.py -q
```

Expected: FAIL because dialogs and action handlers are not implemented.

- [ ] **Step 3: Implement dialogs**

Add `src/backlog_py/tui/dialogs.py` with Textual `ModalScreen` classes:

- `MoveTaskDialog(statuses: tuple[str, ...], current_status: str)` returns the chosen status.
- `CreateTaskDialog(statuses: tuple[str, ...], default_status: str)` returns `CreateTaskInput`.
- `ArchiveTaskDialog(task_id: str, title: str)` returns `True` or `False`.
- `EditorConfirmDialog(task_id: str, path: Path)` returns `True` or `False`.

Parsing rules:

```python
def parse_multivalue(value: str) -> tuple[str, ...]:
    normalized = value.replace(",", "\n")
    return tuple(part.strip() for part in normalized.splitlines() if part.strip())
```

Validation rules:

- Title is required for create.
- Status must be one of the supplied statuses.
- Archive and editor default button should be cancel.
- Validation errors remain in the modal with visible error text.

- [ ] **Step 4: Implement actions and mutation workers**

In `BacklogTuiApp`:

- `action_move_task()` opens `MoveTaskDialog` for selected task and starts a thread worker that calls `data_source.move_task()`.
- `action_create_task()` opens `CreateTaskDialog` using `create_status_choices(project, snapshot.statuses)` and starts a thread worker that calls `data_source.create_task()`.
- `action_archive_task()` opens `ArchiveTaskDialog` and starts a thread worker that calls `data_source.archive_task()`.
- Mutation worker success refreshes the full board and preserves or reselects task id where possible.
- Mutation worker failure notifies the error and refreshes.
- Modal open increments `modal_depth`; modal close decrements it. If a refresh completed while a modal was open, apply the latest deferred snapshot immediately after close.

Worker shape:

```python
async def _run_mutation(self, operation: Callable[[], object], *, reselect_task_id: str | None = None) -> None:
    worker = self.run_worker(operation, thread=True, exclusive=False, name="mutation")
    await worker.wait()
    await self.refresh_board(reselect_task_id=reselect_task_id)
```

If the installed Textual worker API uses a different wait/result method, adapt this shape to that API while preserving the test contract: mutation work runs off the message loop, errors notify the user, and success refreshes the board.

- [ ] **Step 5: Implement editor suspend flow**

Add an injectable editor runner to `BacklogTuiApp.__init__`:

```python
EditorRunner = Callable[[BacklogProject, Path], object]
```

Default runner:

```python
def default_editor_runner(project: BacklogProject, path: Path) -> None:
    from backlog_py.cli.main import _configured_editor_command, _run_editor_command

    command = _configured_editor_command(project)

    def edit_task_file() -> None:
        _run_editor_command(command, path)

    with_project_write_lock(project, "tui_task_editor", edit_task_file)
```

`action_edit_task()`:

- Confirms via `EditorConfirmDialog`.
- Resolves `data_source.task_path(task_id)`.
- Validates containment under `project.root`.
- Uses Textual suspend/hide support around the editor subprocess:

```python
async def _run_editor_for_task(self, task_id: str) -> None:
    path = self.data_source.task_path(task_id)
    with self.suspend():
        await self.run_worker(lambda: self.editor_runner(self.project, path), thread=True, exclusive=True).wait()
    await self.refresh_board(reselect_task_id=task_id)
```

If the installed Textual API needs `await self.suspend()` or a different context manager form, use that API and keep the same behavior in tests.

- [ ] **Step 6: Implement filter input and refresh timer behavior**

Add:

- `/` focuses `FilterBar`.
- Filter widgets call `BacklogTuiApp.set_filters(...)`; that method updates `filter_state`, renders `filter_snapshot(snapshot, filter_state)`, and preserves selection where possible.
- Manual `r` calls refresh immediately.
- `on_mount()` schedules periodic refresh at `refresh_interval`.
- If `modal_depth > 0`, refresh result is stored as deferred instead of replacing visible state.

Add focused tests to `tests/test_tui_interactions.py`:

```python
async def test_text_filter_limits_visible_cards_without_raw_markdown_match():
    source = _MutableSource(_snapshot_with_two_tasks(raw_secret="hidden-raw"))
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.press("/")
        await pilot.type("second")
        await pilot.pause()

        assert pilot.app.query("#task-card-TASK-1").nodes == []
        assert pilot.app.query_one("#task-card-TASK-2")


async def test_metadata_filter_controls_limit_visible_cards():
    source = _MutableSource(_snapshot_with_two_tasks())
    app = BacklogTuiApp(project=_project(), data_source=source)

    async with app.run_test() as pilot:
        await pilot.app.set_filters(status="In Progress", priority="high", assignee="alice")
        await pilot.pause()

        assert pilot.app.query("#task-card-TASK-1").nodes == []
        assert pilot.app.query_one("#task-card-TASK-2")
```

- [ ] **Step 7: Run mounted interaction tests to verify GREEN**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_app.py tests/test_tui_interactions.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/backlog_py/tui/app.py src/backlog_py/tui/screens.py src/backlog_py/tui/widgets.py src/backlog_py/tui/dialogs.py src/backlog_py/tui/testing.py tests/test_tui_app.py tests/test_tui_interactions.py
git commit -m "feat: add TUI board interactions"
```

### Task 6: Documentation And User-Facing Guidance

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/integration.md`
- Modify: `docs/interactive-deferrals.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the short README mention**

Add one concise optional TUI bullet near CLI/browser/MCP feature mentions:

```markdown
- Optional terminal Kanban board via `pip install "backlog-md-py[tui]"` and `backlog-py tui`.
```

Keep README short; put deeper usage in docs.

- [ ] **Step 2: Document installation and launch in getting started**

Add to `docs/getting-started.md`:

````markdown
### Optional Textual TUI

The Textual board is not part of the base install:

```bash
pip install "backlog-md-py[tui]"
backlog-py tui
```

Use it for human board work when you want keyboard navigation, task detail,
filters, create/move/archive actions, and configured-editor launch. The plain
CLI and MCP tools remain the recommended automation surfaces.
````

- [ ] **Step 3: Document integration guidance**

Add to `docs/integration.md`:

```markdown
### TUI vs Automation Interfaces

`backlog-py tui` is a human-facing interface. Agents and scripts should keep
using plain CLI output, MCP tools, or the daemon HTTP/MCP path because those
surfaces are deterministic and easier to parse.
```

Mention that the TUI opportunistically uses an already-healthy daemon but never starts one.

- [ ] **Step 4: Update interactive deferrals**

Revise `docs/interactive-deferrals.md` so it distinguishes:

- Existing prompt-style `backlog-py board`.
- Optional Textual `backlog-py tui`.
- Remaining non-goals such as full metadata editing, checklist toggles, rich Markdown preview, global search, and settings.

- [ ] **Step 5: Update changelog**

Under the current unreleased or upcoming section in `CHANGELOG.md`, add:

```markdown
- Added an optional Textual Kanban board (`backlog-py tui`) behind the `tui` extra.
```

- [ ] **Step 6: Run doc-adjacent checks**

Run:

```bash
git diff --check
uv run --extra dev python -m pytest tests/test_package_metadata.py tests/test_cli_readonly.py::test_top_level_help_includes_tui_command -q
```

Expected: no whitespace errors; tests PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/getting-started.md docs/integration.md docs/interactive-deferrals.md CHANGELOG.md
git commit -m "docs: document optional Textual TUI"
```

### Task 7: Full Verification, Security Scan, And Packaging Smoke

**Files:**
- No planned source edits unless verification finds defects.

- [ ] **Step 1: Run pure and CLI test suite without the TUI extra**

Run:

```bash
uv run --extra dev python -m pytest tests/test_package_metadata.py tests/test_cli_readonly.py tests/test_tui_models.py tests/test_tui_data.py -q
```

Expected: PASS without installing the `tui` extra. No Textual import error should occur.

- [ ] **Step 2: Run mounted Textual tests with the TUI extra**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_app.py tests/test_tui_interactions.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the full test suite with all relevant extras**

Run:

```bash
uv run --extra dev --extra tui python -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 4: Run whitespace and import smoke checks**

Run:

```bash
git diff --check
uv run --extra dev python - <<'PY'
import sys
import backlog_py.cli.main
assert "textual" not in sys.modules
print("base import ok")
PY
```

Expected: no diff whitespace errors; prints `base import ok`.

- [ ] **Step 5: Run Bandit on touched source**

Run:

```bash
uv run --extra dev python -m bandit -r src/backlog_py/cli src/backlog_py/tui -f json -o /tmp/bandit_textual_tui.json
```

Expected: completes with no new high/medium findings. If Bandit flags `urllib.request.urlopen`, verify the endpoint is loopback-validated before the call and add a narrow `# nosec B310` comment only on that line.

- [ ] **Step 6: Build and inspect package**

Run:

```bash
DIST_DIR="/private/tmp/backlog-md-py-tui-dist-$(date +%Y%m%d%H%M%S)"
uv build --no-build-isolation --out-dir "$DIST_DIR"
uv run --extra dev python -m twine check "$DIST_DIR"/*
```

Expected: build succeeds; twine check PASSES.

Inspect wheel contents:

```bash
python - <<'PY'
from pathlib import Path
import zipfile

dist_dirs = sorted(Path("/private/tmp").glob("backlog-md-py-tui-dist-*"))
wheel = next(dist_dirs[-1].glob("*.whl"))
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
assert "backlog_py/tui/styles.tcss" in names
print("wheel contains TUI stylesheet")
PY
```

Expected: prints `wheel contains TUI stylesheet`.

- [ ] **Step 7: Manual CLI smoke**

Run without the TUI extra:

```bash
uv run --extra dev backlog-py --help
uv run --extra dev backlog-py --cwd tests/fixtures/repos/basic tui
```

Expected: help includes `tui`; running `tui` without the extra exits with `Install with backlog-md-py[tui] to use the Textual TUI.`

Run with the TUI extra in a non-interactive smoke that exits quickly if the app supports a test flag by this stage; otherwise rely on mounted tests:

```bash
uv run --extra dev --extra tui python -m pytest tests/test_tui_app.py::test_app_renders_board_columns_inspector_and_footer -q
```

Expected: PASS.

- [ ] **Step 8: Final commit if verification fixes were needed**

If verification required changes:

```bash
git add <changed-files>
git commit -m "fix: harden optional TUI verification"
```

If no changes were needed, do not create an empty commit.
