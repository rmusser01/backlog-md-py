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
