from pathlib import Path

import pytest

from backlog_py.storage.config import load_config
from backlog_py.storage.project import discover_project


def test_discovers_folder_local_config(tmp_path):
    (tmp_path / "backlog").mkdir()
    (tmp_path / "backlog" / "config.yml").write_text(
        "project_name: demo\nremote_operations: false\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.root == tmp_path
    assert project.backlog_dir == tmp_path / "backlog"
    assert project.config.remote_operations is False


def test_backlog_cwd_overrides_process_cwd(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    (project_root / "backlog").mkdir(parents=True)
    (project_root / "backlog" / "config.yml").write_text(
        "project_name: env-demo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BACKLOG_CWD", str(project_root))

    project = discover_project(tmp_path)

    assert project.root == project_root
    assert project.config.project_name == "env-demo"


def test_discovers_root_config_file(tmp_path):
    (tmp_path / "backlog.config.yml").write_text(
        "project_name: root-demo\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.root == tmp_path
    assert project.backlog_dir == tmp_path / "backlog"
    assert project.config_path == tmp_path / "backlog.config.yml"
    assert project.config.project_name == "root-demo"


def test_discovers_custom_backlog_directory_from_root_config(tmp_path):
    (tmp_path / "backlog.config.yml").write_text(
        "projectName: custom-demo\nbacklogDirectory: work/backlog\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.root == tmp_path
    assert project.backlog_dir == tmp_path / "work" / "backlog"
    assert project.config_path == tmp_path / "backlog.config.yml"
    assert project.config.project_name == "custom-demo"


def test_root_config_rejects_default_backlog_directory_symlinked_outside(tmp_path):
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    (project_root / "backlog.config.yml").write_text("projectName: demo\n", encoding="utf-8")
    (project_root / "backlog").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        discover_project(project_root)


def test_nested_backlog_config_rejects_backlog_directory_symlinked_outside(tmp_path):
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    (outside / "config.yml").write_text("projectName: demo\n", encoding="utf-8")
    (project_root / "backlog").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        discover_project(project_root)


def test_dot_backlog_config_rejects_dot_backlog_directory_symlinked_outside(tmp_path):
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    (outside / "config.yml").write_text("projectName: demo\n", encoding="utf-8")
    (project_root / ".backlog").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        discover_project(project_root)


def test_configured_backlog_directory_symlinked_inside_project_remains_supported(tmp_path):
    target = tmp_path / "storage"
    target.mkdir()
    (tmp_path / "linked-backlog").symlink_to(target, target_is_directory=True)
    (tmp_path / "backlog.config.yml").write_text(
        "projectName: custom-demo\nbacklogDirectory: linked-backlog\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.backlog_dir == target


def test_discovers_custom_backlog_directory_from_snake_case_root_config(tmp_path):
    (tmp_path / "backlog.config.yml").write_text(
        "project_name: custom-demo\nbacklog_directory: work/backlog\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.backlog_dir == tmp_path / "work" / "backlog"


def test_rejects_absolute_backlog_directory_in_root_config(tmp_path):
    (tmp_path / "backlog.config.yml").write_text(
        "projectName: custom-demo\nbacklogDirectory: /tmp/backlog\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="project-relative"):
        discover_project(tmp_path)


def test_rejects_empty_backlog_directory_in_root_config(tmp_path):
    (tmp_path / "backlog.config.yml").write_text(
        "projectName: custom-demo\nbacklogDirectory: ''\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-empty string"):
        discover_project(tmp_path)


def test_discovers_dot_backlog_config(tmp_path):
    (tmp_path / ".backlog").mkdir()
    (tmp_path / ".backlog" / "config.yml").write_text(
        "project_name: dot-demo\n",
        encoding="utf-8",
    )

    project = discover_project(tmp_path)

    assert project.root == tmp_path
    assert project.backlog_dir == tmp_path / ".backlog"
    assert project.config_path == tmp_path / ".backlog" / "config.yml"
    assert project.config.project_name == "dot-demo"


def test_load_config_accepts_snake_case_keys(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "project_name: snake-demo",
                "default_assignee: '@alex'",
                "default_status: In Progress",
                "date_format: dd/mm/yyyy",
                "include_datetime_in_dates: false",
                "default_editor: code --wait",
                "default_port: 8080",
                "auto_open_browser: false",
                "remote_operations: false",
                "auto_commit: true",
                "bypass_git_hooks: true",
                "zero_padded_ids: 4",
                "task_prefix: issue",
                "check_active_branches: false",
                "active_branch_days: 14",
                "definition_of_done:",
                "  - Tests pass",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.project_name == "snake-demo"
    assert config.default_assignee == "@alex"
    assert config.default_status == "In Progress"
    assert config.date_format == "dd/mm/yyyy"
    assert config.include_datetime_in_dates is False
    assert config.default_editor == "code --wait"
    assert config.default_port == 8080
    assert config.auto_open_browser is False
    assert config.remote_operations is False
    assert config.auto_commit is True
    assert config.bypass_git_hooks is True
    assert config.zero_padded_ids == 4
    assert config.task_prefix == "issue"
    assert config.check_active_branches is False
    assert config.active_branch_days == 14
    assert config.definition_of_done == ["Tests pass"]


def test_load_config_accepts_camel_case_keys(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "projectName: camel-demo",
                "defaultAssignee: '@sam'",
                "defaultStatus: Done",
                "dateFormat: mm/dd/yyyy",
                "includeDatetimeInDates: true",
                "defaultEditor: vim",
                "defaultPort: 9090",
                "autoOpenBrowser: true",
                "remoteOperations: false",
                "autoCommit: true",
                "bypassGitHooks: true",
                "zeroPaddedIds: 3",
                "prefixes:",
                "  task: JIRA",
                "checkActiveBranches: false",
                "activeBranchDays: 7",
                "definitionOfDone:",
                "  - Review complete",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.project_name == "camel-demo"
    assert config.default_assignee == "@sam"
    assert config.default_status == "Done"
    assert config.date_format == "mm/dd/yyyy"
    assert config.include_datetime_in_dates is True
    assert config.default_editor == "vim"
    assert config.default_port == 9090
    assert config.auto_open_browser is True
    assert config.remote_operations is False
    assert config.auto_commit is True
    assert config.bypass_git_hooks is True
    assert config.zero_padded_ids == 3
    assert config.task_prefix == "JIRA"
    assert config.check_active_branches is False
    assert config.active_branch_days == 7
    assert config.definition_of_done == ["Review complete"]


def test_load_config_supports_no_git_style_flags(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "project_name: no-git-demo",
                "remote_operations: false",
                "auto_commit: false",
                "check_active_branches: false",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.remote_operations is False
    assert config.auto_commit is False
    assert config.check_active_branches is False


def test_load_config_rejects_string_boolean_values(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "project_name: malformed-bool-demo",
                'remote_operations: "false"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="remote_operations"):
        load_config(config_path)


def test_explicit_cwd_takes_precedence_over_backlog_cwd(tmp_path, monkeypatch):
    env_root = tmp_path / "env"
    explicit_root = tmp_path / "explicit"
    (env_root / "backlog").mkdir(parents=True)
    (explicit_root / "backlog").mkdir(parents=True)
    (env_root / "backlog" / "config.yml").write_text(
        "project_name: env-demo\n",
        encoding="utf-8",
    )
    (explicit_root / "backlog" / "config.yml").write_text(
        "project_name: explicit-demo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BACKLOG_CWD", str(env_root))

    project = discover_project(tmp_path, explicit_cwd=explicit_root)

    assert project.root == explicit_root
    assert project.config.project_name == "explicit-demo"
