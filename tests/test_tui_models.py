import shutil
from pathlib import Path

import pytest
import yaml

from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.storage.project import discover_project
from backlog_py.tui import models as tui_models
from backlog_py.tui.models import (
    BoardSnapshot,
    FilterState,
    SelectionState,
    TaskView,
    checklist_items_from_parsed,
    create_status_choices,
    filter_snapshot,
    move_status_choices,
    select_after_refresh,
    task_view_from_mcp_payload,
    task_view_from_record,
)


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_task_view_from_record_preserves_metadata_checklists_and_relative_path(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).edit_task(
        "TASK-1",
        assignees=("alice",),
        labels=("python", "compat"),
        priority="high",
        milestone="Release 1",
        dependencies=(),
    )
    task = ReadOnlyRepository(project).get_task("TASK-1")

    view = task_view_from_record(project, task)

    assert view.id == "TASK-1"
    assert view.title == "Example task"
    assert view.status == "In Progress"
    assert view.path == Path("backlog/tasks/task-1 - Example-task.md")
    assert view.queue_category == "in_workflow"
    assert view.priority == "high"
    assert view.assignees == ("alice",)
    assert view.labels == ("python", "compat")
    assert view.milestone == "Release 1"
    assert [(item.item_id, item.checked, item.text) for item in view.acceptance_criteria] == [
        ("1", True, "Preserve completed acceptance criteria raw line"),
        ("2", False, "Preserve incomplete acceptance criteria raw line"),
        (None, False, "Plain checklist item without an id"),
    ]
    assert [(item.item_id, item.checked, item.text) for item in view.definition_of_done] == [
        ("1", True, "Tests written"),
        ("2", False, "Verification recorded"),
    ]
    assert view.raw_source == task.raw_source


def test_task_view_from_record_hydrates_run_history_events(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    task_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8")
        + "\n## Run History\n"
        + "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "```yaml\n"
        + "event_id: run-1\n"
        + "type: record_run\n"
        + "actor: codex\n"
        + "timestamp: 2026-06-26T18:04:00Z\n"
        + "result: succeeded\n"
        + "task_id: TASK-1\n"
        + "```\n"
        + "Done.\n"
        + "<!-- RUN_HISTORY_ENTRY:END -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n",
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    task = ReadOnlyRepository(project).get_task("TASK-1")

    view = task_view_from_record(project, task)

    assert view.run_history_issues == ()
    assert [(event.event_id, event.type, event.actor, event.result, event.summary) for event in view.run_history_events] == [
        ("run-1", "record_run", "codex", "succeeded", "Done.")
    ]


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


def test_task_view_from_mcp_payload_hydrates_missing_fields_and_overlays_summary(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository(project).edit_task(
        "TASK-1",
        assignees=("alice",),
        labels=("python", "compat"),
        priority="high",
        milestone="Release 1",
    )
    task = ReadOnlyRepository(project).get_task("TASK-1")
    payload = {
        "id": "TASK-1",
        "title": "Summary title",
        "status": "Done",
        "description": "Summary description",
        "path": task.path.relative_to(project.root).as_posix(),
        "raw_source": task.raw_source,
        "queueCategory": "claimed",
        "effectiveStatus": "inprogress",
    }

    view = task_view_from_mcp_payload(project, payload)

    assert view.id == "TASK-1"
    assert view.title == "Summary title"
    assert view.status == "Done"
    assert view.description == "Summary description"
    assert view.path == Path("backlog/tasks/task-1 - Example-task.md")
    assert view.queue_category == "claimed"
    assert view.effective_status == "inprogress"
    assert view.priority == "high"
    assert view.assignees == ("alice",)
    assert view.labels == ("python", "compat")
    assert view.milestone == "Release 1"
    assert view.acceptance_criteria[0].checked is True


def test_task_view_from_mcp_payload_hydrates_run_history_and_issues(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    task = ReadOnlyRepository(project).get_task("TASK-1")

    view = task_view_from_mcp_payload(
        project,
        {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "path": task.path.relative_to(project.root).as_posix(),
            "runHistoryEvents": [
                {
                    "eventId": "run-1",
                    "type": "claim_task",
                    "actor": "codex",
                    "timestamp": "2026-06-26T18:04:00Z",
                    "result": "succeeded",
                    "summary": "Claimed.",
                }
            ],
            "runHistoryIssues": [
                {"code": "run_history_entry_unterminated", "message": "Bad entry", "path": "RUN_HISTORY_ENTRY"}
            ],
        },
    )

    assert view.run_history_events[0].event_id == "run-1"
    assert view.run_history_events[0].type == "claim_task"
    assert view.run_history_issues[0] == "run_history_entry_unterminated: Bad entry"


def test_task_view_from_mcp_payload_rejects_paths_outside_project(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    with pytest.raises(ValueError):
        task_view_from_mcp_payload(project, {"id": "TASK-1", "path": "../outside.md"})


def test_filter_snapshot_matches_normalized_fields_without_raw_markdown_body():
    hidden_raw_text = "raw-only-secret"
    task = _task_view(
        "TASK-1",
        "Parser bug",
        "In Progress",
        description="Fix the board parser",
        raw_source=hidden_raw_text,
        priority="high",
        assignees=("alice",),
        labels=("ui",),
        milestone="Release 1",
        dependencies=("TASK-0",),
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
    assert filter_snapshot(snapshot, FilterState(text="release 1")).columns["In Progress"] == ()
    assert filter_snapshot(snapshot, FilterState(text="TASK-0")).columns["In Progress"] == ()
    assert filter_snapshot(snapshot, FilterState(text="raw-only-secret")).columns["In Progress"] == ()


def test_filter_snapshot_matches_queue_category():
    eligible = _task_view("TASK-1", "Eligible", "To Do", queue_category="eligible")
    claimed = _task_view("TASK-2", "Claimed", "In Progress", queue_category="claimed")
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "In Progress"),
        columns={"To Do": (eligible,), "In Progress": (claimed,)},
        source="local",
        revision=None,
    )

    filtered = filter_snapshot(snapshot, FilterState(queue_category="claimed"))

    assert filtered.columns["To Do"] == ()
    assert filtered.columns["In Progress"] == (claimed,)


def test_dependency_state_marks_complete_open_and_missing_dependencies():
    done = _task_view("TASK-1", "Done dependency", "Done")
    open_task = _task_view("TASK-2", "Open dependency", "In Progress")
    dependent = _task_view(
        "TASK-3",
        "Dependent task",
        "To Do",
        dependencies=("TASK-1", "task-2", "TASK-99"),
    )
    snapshot = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "In Progress", "Done"),
        columns={"To Do": (dependent,), "In Progress": (open_task,), "Done": (done,)},
        source="local",
        revision=None,
    )

    state = tui_models.dependency_state_for_task(snapshot, dependent)

    assert state.total == 3
    assert state.complete == ("TASK-1",)
    assert state.open == ("task-2",)
    assert state.missing == ("TASK-99",)
    assert state.is_blocked is True


def test_create_status_choices_include_default_status_for_unconfigured_board(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    config_path = repo / "backlog" / "config.yml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_config.pop("statuses")
    config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    assert create_status_choices(project, board_statuses=()) == (project.config.default_status,)
    assert create_status_choices(project, board_statuses=("Doing",)) == ("Doing", project.config.default_status)


def test_move_status_choices_use_configured_statuses_when_present(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    assert move_status_choices(project, board_statuses=("Ad Hoc",)) == tuple(project.config.statuses)


def test_select_after_refresh_preserves_selected_task_id_when_still_present():
    before = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Done"),
        columns={
            "To Do": (_task_view("TASK-1", "One", "To Do"),),
            "Done": (),
        },
        source="local",
        revision=None,
    )
    after = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Done"),
        columns={
            "To Do": (),
            "Done": (_task_view("TASK-1", "One", "Done"),),
        },
        source="local",
        revision=None,
    )

    selected = select_after_refresh(before, after, SelectionState(task_id="TASK-1", status="To Do", row=0))

    assert selected == SelectionState(task_id="TASK-1", status="Done", row=0)


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

    assert selected == SelectionState(task_id="TASK-1", status="To Do", row=0)


def test_select_after_refresh_uses_nearest_non_empty_column_then_empty_selection():
    before = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Doing", "Done"),
        columns={
            "To Do": (),
            "Doing": (_task_view("TASK-2", "Two", "Doing"),),
            "Done": (),
        },
        source="local",
        revision=None,
    )
    after = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Doing", "Done"),
        columns={
            "To Do": (),
            "Doing": (),
            "Done": (_task_view("TASK-3", "Three", "Done"),),
        },
        source="local",
        revision=None,
    )
    empty = BoardSnapshot(
        project_name="Demo",
        project_root=Path("/tmp/demo"),
        statuses=("To Do", "Doing", "Done"),
        columns={"To Do": (), "Doing": (), "Done": ()},
        source="local",
        revision=None,
    )

    selected = select_after_refresh(before, after, SelectionState(task_id="TASK-2", status="Doing", row=0))
    cleared = select_after_refresh(before, empty, SelectionState(task_id="TASK-2", status="Doing", row=0))

    assert selected == SelectionState(task_id="TASK-3", status="Done", row=0)
    assert cleared == SelectionState()


def _task_view(
    task_id: str,
    title: str,
    status: str,
    *,
    description: str = "",
    path: Path | None = None,
    priority: str | None = None,
    assignees: tuple[str, ...] = (),
    labels: tuple[str, ...] = (),
    milestone: str | None = None,
    dependencies: tuple[str, ...] = (),
    acceptance_criteria_text: tuple[str, ...] = (),
    definition_of_done_text: tuple[str, ...] = (),
    raw_source: str | None = None,
    queue_category: str | None = None,
) -> TaskView:
    from backlog_py.tui.models import ChecklistItemView

    return TaskView(
        id=task_id,
        title=title,
        status=status,
        description=description,
        path=path or Path(f"backlog/tasks/{task_id.lower()}.md"),
        priority=priority,
        assignees=assignees,
        labels=labels,
        milestone=milestone,
        dependencies=dependencies,
        acceptance_criteria=tuple(
            ChecklistItemView(item_id=None, text=text, checked=False) for text in acceptance_criteria_text
        ),
        definition_of_done=tuple(
            ChecklistItemView(item_id=None, text=text, checked=False) for text in definition_of_done_text
        ),
        raw_source=raw_source,
        queue_category=queue_category,
    )
