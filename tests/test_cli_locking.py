import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.board_export import export_board_to_file, update_readme_with_board
from backlog_py.core.documents import DocumentService
from backlog_py.core.milestones import MilestoneService
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, target)
    return target


@pytest.mark.parametrize(
    ("args", "operation"),
    [
        (("task", "create", "Locked task"), "task_create"),
        (("task", "create", "Draft task", "--draft"), "draft_create"),
        (("task", "edit", "TASK-1", "--title", "Updated"), "task_edit"),
        (("task", "archive", "TASK-1"), "task_archive"),
        (("task", "demote", "TASK-1"), "draft_demote"),
        (("draft", "create", "Draft"), "draft_create"),
        (("decision", "create", "Decision"), "decision_create"),
        (("config", "set", "defaultStatus", "To Do"), "config_set"),
        (("config", "dod-defaults-upsert", "Tests pass"), "definition_of_done_defaults_upsert"),
        (("agents", "--update-instructions"), "agents_update_instructions"),
        (("board", "export", "status.md", "--force"), "board_export_file"),
        (("board", "export", "--readme"), "board_export_readme"),
    ],
)
def test_cli_write_commands_acquire_project_lock(repo, monkeypatch, args, operation):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)

    result = CliRunner().invoke(main, ["--cwd", str(repo), *args])

    assert result.exit_code == 0, result.output
    assert operation in seen


@pytest.mark.parametrize(
    ("prepare", "args", "operation"),
    [
        (lambda repo: _create_draft(repo), ("draft", "promote", "draft-1"), "draft_promote"),
        (lambda repo: _create_draft(repo), ("draft", "archive", "draft-1"), "draft_archive"),
        (lambda repo: None, ("doc", "create", "notes/locked.md", "--title", "Locked"), "document_create"),
        (
            lambda repo: DocumentService(_project(repo)).create_document(
                "notes/locked.md",
                title="Locked",
                content="",
            ),
            ("doc", "update", "notes/locked.md", "--title", "Updated"),
            "document_update",
        ),
        (lambda repo: None, ("milestone", "add", "Alpha"), "milestone_add"),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            ("milestone", "rename", "Alpha", "Beta"),
            "milestone_rename",
        ),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            ("milestone", "remove", "Alpha"),
            "milestone_remove",
        ),
        (
            lambda repo: MilestoneService(_project(repo)).add_milestone("Alpha"),
            ("milestone", "archive", "Alpha"),
            "milestone_archive",
        ),
    ],
)
def test_additional_cli_write_commands_acquire_project_lock(repo, monkeypatch, prepare, args, operation):
    prepare(repo)
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)

    result = CliRunner().invoke(main, ["--cwd", str(repo), *args])

    assert result.exit_code == 0, result.output
    assert operation in seen


def test_cleanup_acquires_project_lock(repo, monkeypatch):
    MutableRepository.from_path(repo).edit_task("TASK-1", status="Done")
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "cleanup"])

    assert result.exit_code == 0, result.output
    assert "cleanup_complete_done" in seen


def test_init_command_acquires_init_lock(tmp_path, monkeypatch):
    target = tmp_path / "new-project"
    seen = []

    def fake_with_init_lock(root, op, fn):
        seen.append((root, op))
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_init_lock", fake_with_init_lock)

    result = CliRunner().invoke(main, ["--cwd", str(target), "init", "Locked Project", "--defaults"])

    assert result.exit_code == 0, result.output
    assert seen == [(target, "init_project")]


def test_board_export_uses_atomic_write_helper(repo, monkeypatch):
    writes = []
    project = _project(repo)
    tasks = ReadOnlyRepository(project).list_tasks()

    def fake_atomic_write(path: Path, content: str) -> None:
        writes.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr("backlog_py.core.board_export._atomic_write_text", fake_atomic_write)

    export_board_to_file(project, tasks, "status.md")
    update_readme_with_board(project, tasks)

    assert writes == [repo / "status.md", repo / "README.md"]


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _create_draft(repo: Path) -> None:
    project = _project(repo)
    from backlog_py.core.drafts import DraftService

    DraftService(project).create_draft(title="Draft")


def test_config_wizard_writes_once_under_a_single_lock(repo, monkeypatch):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)
    answers = "\n".join(["Renamed Project", *[""] * 16])

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config"], input=f"{answers}\n")

    assert result.exit_code == 0, result.output
    assert len(seen) == 1, seen
    config_text = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")
    assert "Renamed Project" in config_text


def test_config_wizard_skips_writes_when_nothing_changed(repo, monkeypatch):
    seen = []

    def fake_with_project_lock(project, op, fn):
        seen.append(op)
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)
    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config"], input="\n" * 17)

    assert result.exit_code == 0, result.output
    assert seen == []
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before


def test_config_wizard_discovers_the_project_once(repo, monkeypatch):
    from backlog_py.cli import main as cli_main

    calls = {"count": 0}
    real_discover_project = cli_main.discover_project

    def counting_discover_project(*args, **kwargs):
        calls["count"] += 1
        return real_discover_project(*args, **kwargs)

    monkeypatch.setattr(cli_main, "discover_project", counting_discover_project)
    answers = "\n".join(["Renamed Project", *[""] * 16])

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config"], input=f"{answers}\n")

    assert result.exit_code == 0, result.output
    assert calls["count"] == 1


def test_cleanup_lists_the_tasks_it_moved_under_the_lock(repo, monkeypatch):
    MutableRepository.from_path(repo).edit_task("TASK-1", status="Done")

    def fake_with_project_lock(project, op, fn):
        # A concurrent writer completes another task before the lock is granted.
        MutableRepository.from_path(repo).create_task(title="Late done task", task_id="TASK-9", status="Done")
        return fn()

    monkeypatch.setattr("backlog_py.cli.main.with_project_write_lock", fake_with_project_lock)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "cleanup"])

    assert result.exit_code == 0, result.output
    assert "Moved 2 completed tasks to backlog/completed." in result.output
    assert "TASK-9" in result.output


def test_cleanup_confirms_through_the_shared_interactive_helper(repo, monkeypatch):
    MutableRepository.from_path(repo).edit_task("TASK-1", status="Done")
    monkeypatch.setattr("backlog_py.cli.main._stdin_is_interactive", lambda: True)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "cleanup"], input="n\n")

    assert result.exit_code != 0
    assert "Move these tasks?" in result.output
    assert (repo / "backlog" / "tasks" / "task-1 - Example-task.md").is_file()
