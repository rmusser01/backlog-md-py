from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp.tools import definition_of_done_defaults_get, definition_of_done_defaults_upsert, task_create
from backlog_py.storage.config import get_definition_of_done_defaults, replace_definition_of_done_defaults
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _repository(repo: Path) -> MutableRepository:
    return MutableRepository(_project(repo))


def _task_file(repo: Path, task_id: str) -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob(f"{task_id.lower()} -*.md"))
    assert len(matches) == 1
    return matches[0]


def test_config_definition_of_done_defaults_can_be_read_and_replaced(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    assert get_definition_of_done_defaults(project) == []

    updated = replace_definition_of_done_defaults(project, ["Tests pass", "Docs updated"])

    assert updated.definition_of_done == ["Tests pass", "Docs updated"]
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass", "Docs updated"]
    config_source = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")
    assert "definitionOfDone:" in config_source
    assert "- Tests pass" in config_source


def test_task_creation_inherits_project_defaults_unless_disabled(tmp_path):
    repo = _copy_fixture(tmp_path)
    replace_definition_of_done_defaults(_project(repo), ["Tests pass", "Docs updated"])

    inherited = _repository(repo).create_task(title="Inherited DoD", task_id="TASK-2")
    disabled = _repository(repo).create_task(
        title="Disabled DoD",
        task_id="TASK-3",
        disable_definition_of_done_defaults=True,
    )

    inherited_source = _task_file(repo, inherited.id).read_text(encoding="utf-8")
    disabled_source = _task_file(repo, disabled.id).read_text(encoding="utf-8")
    assert "- [ ] #1 Tests pass" in inherited_source
    assert "- [ ] #2 Docs updated" in inherited_source
    assert "Tests pass" not in disabled_source


def test_task_creation_reloads_definition_of_done_defaults_for_long_lived_repository(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    repository = MutableRepository(project)
    replace_definition_of_done_defaults(project, ["Tests pass"])

    created = repository.create_task(title="Fresh DoD", task_id="TASK-2")

    source = _task_file(repo, created.id).read_text(encoding="utf-8")
    assert "- [ ] #1 Tests pass" in source


def test_task_specific_definition_of_done_additions_do_not_mutate_project_defaults(tmp_path):
    repo = _copy_fixture(tmp_path)
    replace_definition_of_done_defaults(_project(repo), ["Tests pass"])

    created = _repository(repo).create_task(
        title="Specific DoD",
        task_id="TASK-2",
        definition_of_done_add=["Screenshots attached"],
    )

    source = _task_file(repo, created.id).read_text(encoding="utf-8")
    assert "- [ ] #1 Tests pass" in source
    assert "- [ ] #2 Screenshots attached" in source
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass"]


def test_explicit_definition_of_done_replaces_project_defaults(tmp_path):
    repo = _copy_fixture(tmp_path)
    replace_definition_of_done_defaults(_project(repo), ["Project default"])

    created = _repository(repo).create_task(
        title="Explicit DoD",
        task_id="TASK-2",
        definition_of_done=["Explicit only"],
    )

    source = _task_file(repo, created.id).read_text(encoding="utf-8")
    assert "Project default" not in source
    assert "- [ ] #1 Explicit only" in source


def test_cli_definition_of_done_default_commands_use_config_writer(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    upsert = runner.invoke(
        main,
        ["--cwd", str(repo), "config", "dod-defaults-upsert", "Tests pass", "Docs updated"],
    )
    assert upsert.exit_code == 0
    assert "Tests pass" in upsert.output

    get = runner.invoke(main, ["--cwd", str(repo), "config", "dod-defaults-get"])
    assert get.exit_code == 0
    assert "Tests pass" in get.output
    assert "Docs updated" in get.output

    clear = runner.invoke(main, ["--cwd", str(repo), "config", "dod-defaults-upsert"])
    assert clear.exit_code == 0
    assert clear.output == ""
    assert get_definition_of_done_defaults(_project(repo)) == []


def test_cli_config_get_outputs_effective_values(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    default_status = runner.invoke(main, ["--cwd", str(repo), "config", "get", "defaultStatus"])
    remote_operations = runner.invoke(main, ["--cwd", str(repo), "config", "get", "remoteOperations"])

    assert default_status.exit_code == 0
    assert default_status.output == "To Do\n"
    assert remote_operations.exit_code == 0
    assert remote_operations.output == "false\n"


def test_cli_config_set_updates_typed_values_and_extension_keys(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    auto_commit = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoCommit", "true"])
    active_days = runner.invoke(main, ["--cwd", str(repo), "config", "set", "activeBranchDays", "45"])
    default_editor = runner.invoke(
        main,
        ["--cwd", str(repo), "config", "set", "defaultEditor", "code --wait"],
    )
    get_default_editor = runner.invoke(main, ["--cwd", str(repo), "config", "get", "defaultEditor"])

    assert auto_commit.exit_code == 0
    assert auto_commit.output == "autoCommit: true\n"
    assert active_days.exit_code == 0
    assert active_days.output == "activeBranchDays: 45\n"
    assert default_editor.exit_code == 0
    assert default_editor.output == "defaultEditor: code --wait\n"
    assert get_default_editor.exit_code == 0
    assert get_default_editor.output == "code --wait\n"

    config = _project(repo).config
    assert config.auto_commit is True
    assert config.active_branch_days == 45
    raw_config = yaml.safe_load((repo / "backlog" / "config.yml").read_text(encoding="utf-8"))
    assert raw_config["autoCommit"] is True
    assert raw_config["activeBranchDays"] == 45
    assert raw_config["defaultEditor"] == "code --wait"


def test_cli_config_set_rejects_invalid_typed_values_without_writing(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    config_path = repo / "backlog" / "config.yml"
    before = config_path.read_text(encoding="utf-8")

    result = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoCommit", "sometimes"])

    assert result.exit_code != 0
    assert "boolean" in result.output
    assert config_path.read_text(encoding="utf-8") == before


def test_mcp_definition_of_done_defaults_and_task_create_use_safe_core(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    assert definition_of_done_defaults_get(project) == {"items": []}
    assert definition_of_done_defaults_upsert(project, ["Tests pass"]) == {"items": ["Tests pass"]}

    created = task_create(project, title="MCP DoD", definitionOfDoneAdd=["MCP specific"])

    source = _task_file(repo, created["id"]).read_text(encoding="utf-8")
    assert "- [ ] #1 Tests pass" in source
    assert "- [ ] #2 MCP specific" in source
    assert definition_of_done_defaults_get(_project(repo)) == {"items": ["Tests pass"]}
