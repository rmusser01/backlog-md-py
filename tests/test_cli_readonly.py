import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py import __version__
from backlog_py.cli.main import main
from backlog_py.core.repository import MutableRepository


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _invoke(*args: str):
    return CliRunner().invoke(main, ["--cwd", str(FIXTURE_REPO), *args])


def _invoke_repo(repo: Path, *args: str):
    return CliRunner().invoke(main, ["--cwd", str(repo), *args])


def _metadata_filter_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    repository = MutableRepository.from_path(repo)
    repository.edit_task(
        "TASK-1",
        assignees=["Codex"],
        labels=["Parser", "UI"],
        priority="high",
        milestone="Release 1",
    )
    repository.create_task(
        title="Documentation task",
        task_id="TASK-2",
        status="To Do",
        assignees=["reviewer"],
        labels=["docs"],
        priority="low",
        milestone="Release 2",
    )
    return repo


def test_top_level_help_includes_readonly_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--cwd" in result.output
    assert "task" in result.output
    assert "search" in result.output
    assert "board" in result.output
    assert "config" in result.output


def test_top_level_version_reports_package_version():
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_task_list_plain_outputs_task_id():
    result = _invoke("task", "list", "--plain")

    assert result.exit_code == 0
    assert "TASK-1" in result.output
    assert "Example task" in result.output


def test_task_view_plain_outputs_task_body():
    result = _invoke("task", "TASK-1", "--plain")

    assert result.exit_code == 0
    assert "TASK-1" in result.output
    assert "Implement a fixture" in result.output


def test_search_plain_outputs_matching_task():
    result = _invoke("search", "parser preservation", "--plain")

    assert result.exit_code == 0
    assert "TASK-1" in result.output
    assert "Example task" in result.output


def test_search_plain_filters_by_status_and_priority(tmp_path):
    repo = _metadata_filter_repo(tmp_path)

    result = _invoke_repo(repo, "search", "task", "--status", "To Do", "--priority", "low", "--plain")

    assert result.exit_code == 0
    assert "TASK-2" in result.output
    assert "Documentation task" in result.output
    assert "TASK-1" not in result.output


def test_board_outputs_status_grouping():
    result = _invoke("board")

    assert result.exit_code == 0
    assert "To Do" in result.output
    assert "In Progress" in result.output
    assert "TASK-1" in result.output
    assert "Done" in result.output


def test_config_list_outputs_safe_defaults():
    result = _invoke("config", "list")

    assert result.exit_code == 0
    assert "projectName: basic-fixture" in result.output
    assert "autoCommit: false" in result.output
    assert "remoteOperations: false" in result.output


def test_task_list_plain_filters_by_metadata(tmp_path):
    repo = _metadata_filter_repo(tmp_path)

    priority = _invoke_repo(repo, "task", "list", "--plain", "--priority", "HIGH")
    milestone = _invoke_repo(repo, "task", "list", "--plain", "-m", "release 1", "--status", "in progress")
    assignee = _invoke_repo(repo, "task", "list", "--plain", "-a", "codex")
    label = _invoke_repo(repo, "task", "list", "--plain", "-l", "parser", "-l", "ui")

    for result in (priority, milestone, assignee, label):
        assert result.exit_code == 0
        assert "TASK-1" in result.output
        assert "TASK-2" not in result.output


def test_task_list_plain_filters_by_parent(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    repository = MutableRepository.from_path(repo)
    repository.create_task(title="Child task", parent_task_id="TASK-1")
    repository.create_task(title="Sibling task", task_id="TASK-2")

    result = _invoke_repo(repo, "task", "list", "--plain", "-p", "1")

    assert result.exit_code == 0
    assert "TASK-1.1" in result.output
    assert "Child task" in result.output
    assert "TASK-1 [In Progress] Example task" not in result.output
    assert "TASK-2" not in result.output


def test_task_list_rejects_invalid_priority_filter(tmp_path):
    repo = _metadata_filter_repo(tmp_path)

    result = _invoke_repo(repo, "task", "list", "--plain", "--priority", "urgent")

    assert result.exit_code == 1
    assert "Invalid priority: urgent" in result.output
    assert "Valid values are: high, medium, low" in result.output
