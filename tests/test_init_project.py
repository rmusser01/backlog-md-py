from __future__ import annotations

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.repository import MutableRepository
from backlog_py.storage.project import discover_project


def test_cli_init_defaults_creates_backlog_project(tmp_path):
    result = CliRunner().invoke(main, ["--cwd", str(tmp_path), "init", "Demo Project", "--defaults"])

    assert result.exit_code == 0
    assert "Initialized Backlog.md project at" in result.output
    assert (tmp_path / "backlog" / "config.yml").is_file()
    for relative_path in [
        "tasks",
        "completed",
        "drafts",
        "docs",
        "decisions",
        "milestones",
        "archive/tasks",
        "archive/drafts",
        "archive/milestones",
    ]:
        assert (tmp_path / "backlog" / relative_path).is_dir()

    project = discover_project(tmp_path)
    assert project.config.project_name == "Demo Project"
    assert project.config.statuses == ["To Do", "In Progress", "Done"]
    assert project.config.remote_operations is True
    assert project.config.check_active_branches is True
    assert project.config.active_branch_days == 30
    assert project.config.auto_commit is False
    assert project.config.bypass_git_hooks is False


def test_cli_init_no_git_defaults_disable_git_dependent_settings(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init", "Filesystem Project", "--defaults", "--no-git"],
    )

    assert result.exit_code == 0

    project = discover_project(tmp_path)
    assert project.config.remote_operations is False
    assert project.config.check_active_branches is False
    assert project.config.auto_commit is False


def test_cli_init_requires_defaults_for_non_interactive_setup(tmp_path):
    result = CliRunner().invoke(main, ["--cwd", str(tmp_path), "init", "Interactive Project"])

    assert result.exit_code != 0
    assert "Pass --defaults" in result.output
    assert not (tmp_path / "backlog" / "config.yml").exists()


def test_cli_init_custom_backlog_dir_uses_discoverable_root_config(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "--cwd",
            str(tmp_path),
            "init",
            "Custom Project",
            "--defaults",
            "--backlog-dir",
            "work/backlog",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "backlog.config.yml").is_file()
    assert (tmp_path / "work" / "backlog" / "tasks").is_dir()

    project = discover_project(tmp_path)
    assert project.backlog_dir == tmp_path / "work" / "backlog"
    assert project.config.project_name == "Custom Project"


def test_cli_init_task_prefix_sets_permanent_task_prefix(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init", "Prefix Project", "--defaults", "--task-prefix", "JIRA"],
    )

    assert result.exit_code == 0

    project = discover_project(tmp_path)
    assert project.config.task_prefix == "JIRA"

    created = MutableRepository(project).create_task(title="Prefixed init task")

    assert created.id == "JIRA-1"
    assert (tmp_path / "backlog" / "tasks" / "jira-1 - Prefixed-init-task.md").exists()


def test_cli_init_preserves_existing_config(tmp_path):
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    config_path = backlog_dir / "config.yml"
    config_path.write_text("projectName: Existing\ncustomKey: keep-me\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["--cwd", str(tmp_path), "init", "Replacement", "--defaults"])

    assert result.exit_code == 0
    assert "Preserved existing config" in result.output
    assert config_path.read_text(encoding="utf-8") == "projectName: Existing\ncustomKey: keep-me\n"
    assert (backlog_dir / "tasks").is_dir()


def test_cli_init_can_create_agent_instructions(tmp_path):
    result = CliRunner().invoke(
        main,
        ["--cwd", str(tmp_path), "init", "Agent Demo", "--defaults", "--agent-instructions"],
    )

    assert result.exit_code == 0
    assert "Updated AGENTS.md" in result.output
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Agent Demo" in content
    assert "Search before creating tasks" in content
    assert "`backlog://docs/task-workflow`" in content
    assert "Do not manually edit files under `backlog/`" in content
