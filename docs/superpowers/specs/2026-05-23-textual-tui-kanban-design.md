# Optional Textual TUI Kanban Design

## Goal

Add an optional Textual-based terminal Kanban app to `backlog-md-py` for human
operators who want a richer board workflow without installing Node/Bun or
opening the browser board.

The TUI must remain outside the base dependency set. Existing CLI, MCP, daemon,
and browser workflows must keep working when Textual is not installed.

## User Decisions

- Launch surface: `backlog-py tui`.
- Packaging: install with the optional `backlog-md-py[tui]` extra.
- Missing dependency behavior: always register `tui`, lazy-import Textual, and
  show a focused install hint when the extra is missing.
- First functional scope: board navigation, task detail, move status, create
  task, archive task, open configured editor, and board-local filtering.
- Daemon behavior: use a healthy singleton daemon only if it is already
  running; never start the daemon from the TUI.
- Input model: keyboard-first with mouse/click support where Textual handles it
  reliably.
- Refresh model: manual refresh plus lightweight periodic refresh.
- Layout: board columns with a right-side task inspector.
- Status moves: configured statuses when `config.statuses` is set, otherwise
  current board statuses. Task creation uses the same rule, but also includes
  `defaultStatus` when `config.statuses` is unset so empty boards can create the
  first task without inventing a custom status.
- Create form scope: title, status, priority, assignees, labels, milestone,
  dependencies, acceptance criteria, Definition of Done additions, and
  description.

## Non-Goals

- Do not change `backlog-py board` behavior.
- Do not auto-upgrade existing board output to Textual.
- Do not start or manage the singleton daemon from the TUI.
- Do not attempt browser-board parity in the first slice.
- Do not add settings, full metadata editing, checklist toggles, Markdown
  preview, rich editing, or global search in the first slice.
- Do not make Textual a base dependency.

## Architecture

Add a new optional package area:

```text
src/backlog_py/tui/
  __init__.py
  app.py
  data.py
  models.py
  screens.py
  widgets.py
  dialogs.py
  styles.tcss
  testing.py
```

This structure follows the useful `tldw_chatbook` Textual patterns: keep the app
entry small, isolate feature behavior in data/service seams, use feature
widgets and dialogs, package TCSS with the wheel, and test mounted Textual flows
through pilot APIs. It should avoid the older monolithic-app pattern from the
large sibling project.

The base CLI registers `backlog-py tui` but does not import Textual at module
import time. The command resolves the project, then lazy-imports
`backlog_py.tui.app`. If Textual is unavailable, it raises a Click error such as:

```text
Install with backlog-md-py[tui] to use the Textual TUI.
```

## Components

`app.py`
: Owns the Textual `App`, global bindings, app startup, refresh timer,
notifications, and source selection.

`screens.py`
: Contains the single board workspace screen. The first slice is not a multi-tab
shell.

`models.py`
: Defines plain dataclasses for `BoardSnapshot`, `TaskView`,
`ChecklistItemView`, `CreateTaskInput`, `FilterState`, and selection state.
These models should not depend on Textual. The field contract is:

```text
BoardSnapshot
  project_name: str
  project_root: Path
  statuses: tuple[str, ...]
  columns: dict[str, tuple[TaskView, ...]]
  source: "local" | "daemon"
  revision: str | None

TaskView
  id: str
  title: str
  status: str
  description: str
  path: Path
  priority: str | None
  assignees: tuple[str, ...]
  labels: tuple[str, ...]
  milestone: str | None
  dependencies: tuple[str, ...]
  acceptance_criteria: tuple[ChecklistItemView, ...]
  definition_of_done: tuple[ChecklistItemView, ...]
  raw_source: str | None

ChecklistItemView
  item_id: str | None
  text: str
  checked: bool
```

Local and daemon sources must both normalize into this model before widgets or
filters consume the snapshot. Create-task input still accepts plain strings for
new acceptance criteria and Definition of Done additions, but task display must
preserve checklist checked state and item ids from the parser.

`data.py`
: Defines a `BoardDataSource` protocol and two implementations:
  `LocalBoardDataSource` and `DaemonBoardDataSource`.

`widgets.py`
: Contains `BoardColumn`, `TaskCard`, `TaskInspector`, and `FilterBar`.

`dialogs.py`
: Contains `CreateTaskDialog`, `MoveTaskDialog`, `ArchiveTaskDialog`, and
`EditorConfirmDialog`.

`styles.tcss`
: Provides packaged Textual styles for the optional UI. The wheel must include
this file.

`testing.py`
: May contain tiny Textual test helpers only if they remove repeated test setup.
It should not become a second framework.

## Layout And Interaction

The TUI is a board workspace:

- Header: project name, data source (`daemon` or `local`), active filter
  summary, and refresh state.
- Main area: horizontally scrollable status columns on the left and a right-side
  inspector for the selected task.
- Footer: key bindings and the current action hint.
- Dependency display is informational: task cards and the inspector may show
  done/open/missing dependency counts from the current snapshot, but dependency
  state does not block movement in this slice. The dependency shortcut selects
  the first listed dependency that is visible in the current filtered board and
  then cycles through remaining visible dependencies for the same source task.
  The dependent shortcut selects the first visible task that depends on the
  selected task and then cycles through the remaining visible dependents for the
  same source task. Dependency navigation pushes the source task onto a local
  history stack so users can return without re-finding it. Manual task
  selection resets dependency/dependent cycle source state so subsequent jumps
  start from the newly selected task.

Primary bindings:

- Arrow keys move between cards and columns. `h/j/k/l` provide Vim-style
  navigation aliases.
- `shift+h` and `shift+l` move the selected task to the adjacent status through
  the same mutation path used by the move dialog.
- `d` jumps to the selected task's first visible dependency, then cycles through
  additional visible dependencies.
- `shift+d` jumps to the first visible task that depends on the selection, then
  cycles through additional visible dependents.
- `backspace` returns through dependency navigation history.
- Mouse clicks select cards and activate visible controls where practical.
- Enter focuses/expands the right-side inspector for the selected task. The
  first slice should not add a separate detail screen or modal unless later
  implementation discovers a Textual accessibility issue that makes inspector
  focus unusable.
- `m` opens a move-status dialog.
- `n` opens a create-task dialog.
- `a` opens archive confirmation.
- `e` opens editor confirmation, then launches the configured editor.
- `r` refreshes immediately.
- `/` focuses the filter input.
- `q` or `ctrl+q` exits.

The move dialog lists only statuses that repository mutation accepts:
configured statuses when `config.statuses` is set, otherwise current board
statuses. This prevents accidental new columns from typos and avoids offering a
status that `MutableRepository.edit_task()` would reject.

The create dialog includes title, status, priority, assignees, labels,
milestone, dependencies, acceptance criteria, Definition of Done additions, and
description. Multi-value fields accept comma-separated or newline-separated
input and then reuse existing repository normalization.

Create-task status uses the same known-status rule as move, with one extra
empty-board fallback: configured statuses when `config.statuses` is set,
otherwise current board statuses plus `defaultStatus`. The default selected
value is the project `defaultStatus` when available. Creating arbitrary new
statuses remains a config/settings concern outside the TUI first slice.

Filters are board-local and affect only the visible snapshot. First-slice
filters are text, status, priority, assignee, and label. The text filter is a
case-insensitive substring match over normalized task id, title, description,
priority, milestone, assignees, labels, dependencies, acceptance-criteria text,
and Definition of Done text. It does not scan arbitrary raw Markdown body text;
global search across tasks, documents, and decisions remains the existing
`search` CLI surface.

## Data Flow

Startup:

1. Resolve `--cwd` through the existing project discovery path.
2. Probe `daemon_status()`.
3. If the daemon is healthy, use `DaemonBoardDataSource`; otherwise use
   `LocalBoardDataSource`.
4. Load a board snapshot and render the board screen.

The data source protocol should expose:

```python
load_board() -> BoardSnapshot
create_task(input: CreateTaskInput) -> TaskView
move_task(task_id: str, status: str) -> TaskView
archive_task(task_id: str) -> TaskView
task_path(task_id: str) -> Path
```

This is the logical contract, not a requirement to run blocking work on the
Textual message loop. Repository reads, daemon HTTP calls, and mutations should
be invoked through Textual worker/thread boundaries or another equivalent
non-freezing adapter so refreshes, notifications, and cancellation remain
responsive.

Local mode uses `ReadOnlyRepository`, `MutableRepository`, and existing project
write locks.

Daemon mode uses the existing SDK-free MCP HTTP surface and runtime token for
`task_board`, `task_view`, `task_create`, `task_edit`, and `task_archive`.
`task_board` is the primary board load call, but the current board summary does
not expose every first-slice filter field. The TUI should fill missing
`TaskView` fields by hydrating visible tasks with `task_view` and parsing the
returned raw task Markdown through existing parser helpers. The first slice
should avoid expanding the MCP `task_board` contract unless profiling shows
per-visible-task hydration is too slow for real projects.

The existing MCP task payload includes a project-relative `path`, so editor
launch can still resolve the file locally against `project.root`.

Editor launch stays local and should reuse the existing no-shell configured
editor behavior used by the current CLI task/board editor flow. Because
terminal editors such as `vim` or `nano` need control of the terminal, the TUI
should explicitly suspend/hide the Textual app while the editor subprocess runs,
then refresh and reselect the edited task after the editor exits. The first
slice does not need detached GUI-editor-specific behavior.

## Refresh And Concurrency

Manual `r` always reloads the board snapshot.

A conservative periodic refresh, such as every five seconds, keeps the board
aware of CLI, MCP, browser, or other TUI changes. If a modal is open or the user
has unsaved form text, refresh application is deferred until the modal closes.

When an external refresh changes the selected task:

- If the selected task still exists, preserve the selection by task id.
- If it moved to another status, update the column and show a visible notice.
- If it disappeared, select deterministically: first try the next task in the
  previous column at the old row index, then the previous task in that column,
  then the first task in the nearest non-empty column to the right, then the
  nearest non-empty column to the left. If no tasks remain, clear selection and
  show the empty-board state.

All writes continue through existing project write locks. Daemon write failures
are surfaced and followed by a refresh; the same mutation must not silently
retry through local mode because the daemon may have failed after partially
handling the request. Daemon read failures may switch the app to local mode with
a visible notice.

## Error Handling

Missing Textual dependency:

- Fail before app launch with install guidance.
- Normal CLI, MCP, daemon, and browser imports must not import Textual.

Project discovery/config errors:

- Surface the existing error before launch.
- Do not initialize a Backlog project from the TUI.

Validation errors:

- Invalid create/move/archive input stays in the modal with a clear validation
  message.
- Archive always requires confirmation.
- Status moves are limited to known statuses.

Editor errors:

- Validate task path containment under the project root.
- Split configured editor argv without a shell.
- Suspend/hide Textual while the editor owns the terminal, then refresh the
  selected task after exit.
- Surface non-zero editor failures in the app.

Daemon errors:

- Read failure: fall back to local mode and notify the user.
- Mutation failure: show the error, refresh, and keep the current source choice
  unless the user restarts the TUI.

## Packaging

`pyproject.toml` should add a `tui` optional dependency containing Textual. The
base dependencies remain unchanged.

The package data configuration must include `backlog_py/tui/styles.tcss`.

The `dev` extra may include test support needed for Textual mounted tests, but
runtime Textual remains under `tui`.

## Tests

Packaging and import tests:

- Base dependency metadata does not include Textual.
- `backlog-py tui` without the extra exits with the install hint.
- `pyproject.toml` exposes the `tui` extra.
- Packaged wheel includes `backlog_py/tui/styles.tcss`.
- CI or local validation for mounted Textual tests installs both `dev` and
  `tui` extras, for example `uv run --extra dev --extra tui ...`.

Pure tests:

- Board model conversion from local repository records.
- Board model conversion from daemon/MCP payloads.
- Checklist model conversion preserves text, checked state, and item ids for
  Acceptance Criteria and Definition of Done.
- Filtering by text, status, priority, assignee, and label.
- Selection preservation across refresh when tasks move or disappear.
- Refresh-after-disappear uses the deterministic fallback selection order.
- Existing-status-only move validation.
- Local writes use existing write-lock operations.
- Daemon source calls existing MCP tools.
- Daemon source falls back only for read failures, not mutation failures.

Mounted Textual tests:

- App renders board columns, inspector, and footer.
- Keyboard selection works.
- Mouse selection works where practical in headless Textual tests.
- Move dialog updates a task.
- Create dialog creates a task with the approved first-slice fields.
- Archive confirmation archives a task.
- Editor confirmation invokes the configured editor adapter without shell
  execution.
- Editor exit refreshes and reselects the edited task when it still exists.
- Refresh while a modal is open defers visible replacement until the modal
  closes.

## Documentation

- README: add one short optional TUI mention.
- `docs/getting-started.md`: document `pip install "backlog-md-py[tui]"` and
  `backlog-py tui`.
- `docs/integration.md`: explain that TUI is human-facing and optional; agent
  workflows should continue to use plain CLI or MCP.
- `docs/interactive-deferrals.md`: distinguish current prompt-style interactive
  board from optional Textual board.
- `CHANGELOG.md`: record the optional TUI addition when implemented.

## Acceptance Criteria

- Installing `backlog-md-py` without extras does not install or import Textual.
- `backlog-py tui` gives a clear install hint without the extra.
- Installing `backlog-md-py[tui]` enables the Textual board workspace.
- Existing `backlog-py board`, `task`, `search`, MCP, daemon, and browser
  workflows are unchanged.
- The first TUI slice supports board navigation, task detail, status movement,
  task creation, task archival, configured-editor launch, board-local filters,
  manual refresh, and periodic external-change refresh.
- The TUI never starts the singleton daemon.
- All mutations use existing project write-lock protection.
- Textual mounted tests and pure data-source tests cover the supported behavior.
