from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from click.testing import CliRunner

from backlog_py.core.decisions import DecisionService
from backlog_py.core.documents import DocumentService
from backlog_py.core.drafts import DraftService
from backlog_py.cli.main import main
from backlog_py.core.repository import MutableRepository
from backlog_py.mcp.tools import definition_of_done_defaults_get, definition_of_done_defaults_upsert, task_create
from backlog_py.storage.config import get_definition_of_done_defaults, load_config, replace_definition_of_done_defaults
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

    updated = replace_definition_of_done_defaults(project, [" Tests pass ", "", "Docs updated"])

    assert updated.definition_of_done == ["Tests pass", "Docs updated"]
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass", "Docs updated"]
    config_source = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")
    assert "definitionOfDone:" in config_source
    assert "- Tests pass" in config_source


def test_config_definition_of_done_defaults_reject_non_string_items_without_mutation(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    replace_definition_of_done_defaults(project, ["Tests pass"])
    before = project.config_path.read_text(encoding="utf-8")

    try:
        replace_definition_of_done_defaults(project, ["Docs updated", 7])  # type: ignore[list-item]
    except ValueError as exc:
        assert "Definition of Done defaults must be strings" in str(exc)
    else:
        raise AssertionError("Expected non-string Definition of Done defaults to be rejected")

    assert project.config_path.read_text(encoding="utf-8") == before
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass"]


def test_config_definition_of_done_defaults_reject_non_string_items_from_disk(tmp_path):
    repo = _copy_fixture(tmp_path)
    config_path = repo / "backlog" / "config.yml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_config["definitionOfDone"] = ["Tests pass", 7]
    config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "Definition of Done defaults must be strings" in str(exc)
    else:
        raise AssertionError("Expected non-string Definition of Done defaults from disk to be rejected")


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


def test_cli_config_set_definition_of_done_normalizes_and_rejects_invalid_items(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    updated = runner.invoke(
        main,
        ["--cwd", str(repo), "config", "set", "definitionOfDone", " Tests pass , , Docs updated "],
    )

    assert updated.exit_code == 0
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass", "Docs updated"]

    before = (repo / "backlog" / "config.yml").read_text(encoding="utf-8")
    invalid = runner.invoke(
        main,
        ["--cwd", str(repo), "config", "set", "definitionOfDone", "[Docs updated, 7]"],
    )

    assert invalid.exit_code != 0
    assert "Definition of Done defaults must be strings" in invalid.output
    assert (repo / "backlog" / "config.yml").read_text(encoding="utf-8") == before
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass", "Docs updated"]


def test_cli_config_get_outputs_effective_values(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    default_status = runner.invoke(main, ["--cwd", str(repo), "config", "get", "defaultStatus"])
    date_format = runner.invoke(main, ["--cwd", str(repo), "config", "get", "dateFormat"])
    default_port = runner.invoke(main, ["--cwd", str(repo), "config", "get", "defaultPort"])
    auto_open_browser = runner.invoke(main, ["--cwd", str(repo), "config", "get", "autoOpenBrowser"])
    zero_padded_ids = runner.invoke(main, ["--cwd", str(repo), "config", "get", "zeroPaddedIds"])
    remote_operations = runner.invoke(main, ["--cwd", str(repo), "config", "get", "remoteOperations"])
    on_status_change = runner.invoke(main, ["--cwd", str(repo), "config", "get", "onStatusChange"])

    assert default_status.exit_code == 0
    assert default_status.output == "To Do\n"
    assert date_format.exit_code == 0
    assert date_format.output == "yyyy-mm-dd\n"
    assert default_port.exit_code == 0
    assert default_port.output == "6420\n"
    assert auto_open_browser.exit_code == 0
    assert auto_open_browser.output == "true\n"
    assert zero_padded_ids.exit_code == 0
    assert zero_padded_ids.output == "(disabled)\n"
    assert remote_operations.exit_code == 0
    assert remote_operations.output == "false\n"
    assert on_status_change.exit_code == 0
    assert on_status_change.output == "(disabled)\n"


def test_on_status_change_false_config_is_disabled(tmp_path):
    repo = _copy_fixture(tmp_path)
    config_path = repo / "backlog" / "config.yml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_config["onStatusChange"] = False
    config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config", "get", "onStatusChange"])

    assert result.exit_code == 0
    assert result.output == "(disabled)\n"
    assert load_config(config_path).on_status_change is None


def test_cli_config_without_subcommand_runs_interactive_wizard(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    user_input = "\n".join(
        [
            "Wizard Project",
            "@sam",
            "Review",
            "dd/mm/yyyy",
            "n",
            "vim",
            "7777",
            "n",
            "y",
            "y",
            "n",
            "echo status",
            "4",
            "n",
            "14",
            "Review,Done",
            "Tests pass,Docs updated",
        ]
    )

    result = runner.invoke(main, ["--cwd", str(repo), "config"], input=f"{user_input}\n")

    assert result.exit_code == 0
    assert "Interactive Backlog.md configuration" in result.output
    assert "Updated config" in result.output
    config = load_config(repo / "backlog" / "config.yml")
    assert config.project_name == "Wizard Project"
    assert config.default_assignee == "@sam"
    assert config.default_status == "Review"
    assert config.date_format == "dd/mm/yyyy"
    assert config.include_datetime_in_dates is False
    assert config.default_editor == "vim"
    assert config.default_port == 7777
    assert config.auto_open_browser is False
    assert config.remote_operations is True
    assert config.auto_commit is True
    assert config.bypass_git_hooks is False
    assert config.on_status_change == "echo status"
    assert config.zero_padded_ids == 4
    assert config.check_active_branches is False
    assert config.active_branch_days == 14
    assert config.statuses == ["Review", "Done"]
    assert get_definition_of_done_defaults(_project(repo)) == ["Tests pass", "Docs updated"]


def test_cli_config_wizard_keeps_disabled_status_hook_on_blank_default(tmp_path):
    repo = _copy_fixture(tmp_path)
    user_input = "\n".join([""] * 17)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config"], input=f"{user_input}\n")

    assert result.exit_code == 0
    assert load_config(repo / "backlog" / "config.yml").on_status_change is None
    raw_config = yaml.safe_load((repo / "backlog" / "config.yml").read_text(encoding="utf-8"))
    assert "onStatusChange" not in raw_config
    assert "on_status_change" not in raw_config


def test_cli_config_wizard_reports_invalid_values(tmp_path):
    repo = _copy_fixture(tmp_path)
    user_input = "\n".join(
        [
            "Wizard Project",
            "@sam",
            "Review",
            "dd/mm/yyyy",
            "n",
            "vim",
            "70000",
        ]
    )

    result = CliRunner().invoke(main, ["--cwd", str(repo), "config"], input=f"{user_input}\n")

    assert result.exit_code == 1
    assert "valid port number" in result.output


def test_cli_config_set_updates_typed_values_and_extension_keys(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    default_assignee = runner.invoke(main, ["--cwd", str(repo), "config", "set", "defaultAssignee", "@alex"])
    date_format = runner.invoke(main, ["--cwd", str(repo), "config", "set", "dateFormat", "dd/mm/yyyy"])
    include_datetime = runner.invoke(main, ["--cwd", str(repo), "config", "set", "includeDatetimeInDates", "false"])
    default_port = runner.invoke(main, ["--cwd", str(repo), "config", "set", "defaultPort", "8080"])
    auto_open_browser = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoOpenBrowser", "false"])
    zero_padded_ids = runner.invoke(main, ["--cwd", str(repo), "config", "set", "zeroPaddedIds", "3"])
    auto_commit = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoCommit", "true"])
    active_days = runner.invoke(main, ["--cwd", str(repo), "config", "set", "activeBranchDays", "45"])
    on_status_change = runner.invoke(main, ["--cwd", str(repo), "config", "set", "onStatusChange", "echo changed"])
    default_editor = runner.invoke(
        main,
        ["--cwd", str(repo), "config", "set", "defaultEditor", "code --wait"],
    )
    get_zero_padded_ids = runner.invoke(main, ["--cwd", str(repo), "config", "get", "zeroPaddedIds"])
    get_default_editor = runner.invoke(main, ["--cwd", str(repo), "config", "get", "defaultEditor"])

    assert default_assignee.exit_code == 0
    assert default_assignee.output == "defaultAssignee: @alex\n"
    assert date_format.exit_code == 0
    assert date_format.output == "dateFormat: dd/mm/yyyy\n"
    assert include_datetime.exit_code == 0
    assert include_datetime.output == "includeDatetimeInDates: false\n"
    assert default_port.exit_code == 0
    assert default_port.output == "defaultPort: 8080\n"
    assert auto_open_browser.exit_code == 0
    assert auto_open_browser.output == "autoOpenBrowser: false\n"
    assert zero_padded_ids.exit_code == 0
    assert zero_padded_ids.output == "zeroPaddedIds: 3\n"
    assert auto_commit.exit_code == 0
    assert auto_commit.output == "autoCommit: true\n"
    assert active_days.exit_code == 0
    assert active_days.output == "activeBranchDays: 45\n"
    assert on_status_change.exit_code == 0
    assert on_status_change.output == "onStatusChange: echo changed\n"
    assert default_editor.exit_code == 0
    assert default_editor.output == "defaultEditor: code --wait\n"
    assert get_zero_padded_ids.exit_code == 0
    assert get_zero_padded_ids.output == "3\n"
    assert get_default_editor.exit_code == 0
    assert get_default_editor.output == "code --wait\n"

    config = _project(repo).config
    assert config.default_assignee == "@alex"
    assert config.date_format == "dd/mm/yyyy"
    assert config.include_datetime_in_dates is False
    assert config.default_port == 8080
    assert config.auto_open_browser is False
    assert config.zero_padded_ids == 3
    assert config.auto_commit is True
    assert config.active_branch_days == 45
    assert config.on_status_change == "echo changed"
    raw_config = yaml.safe_load((repo / "backlog" / "config.yml").read_text(encoding="utf-8"))
    assert raw_config["defaultAssignee"] == "@alex"
    assert raw_config["dateFormat"] == "dd/mm/yyyy"
    assert raw_config["includeDatetimeInDates"] is False
    assert raw_config["defaultPort"] == 8080
    assert raw_config["autoOpenBrowser"] is False
    assert raw_config["zeroPaddedIds"] == 3
    assert raw_config["autoCommit"] is True
    assert raw_config["activeBranchDays"] == 45
    assert raw_config["onStatusChange"] == "echo changed"
    assert raw_config["defaultEditor"] == "code --wait"


def test_cli_config_set_zero_padded_ids_zero_disables_padding(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    set_padding = runner.invoke(main, ["--cwd", str(repo), "config", "set", "zeroPaddedIds", "3"])
    disable_padding = runner.invoke(main, ["--cwd", str(repo), "config", "set", "zeroPaddedIds", "0"])
    get_padding = runner.invoke(main, ["--cwd", str(repo), "config", "get", "zeroPaddedIds"])

    assert set_padding.exit_code == 0
    assert disable_padding.exit_code == 0
    assert disable_padding.output == "zeroPaddedIds: (disabled)\n"
    assert get_padding.exit_code == 0
    assert get_padding.output == "(disabled)\n"
    raw_config = yaml.safe_load((repo / "backlog" / "config.yml").read_text(encoding="utf-8"))
    assert "zeroPaddedIds" not in raw_config


def test_cli_config_set_accepts_upstream_boolean_aliases(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    auto_open_browser = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoOpenBrowser", "no"])
    include_datetime = runner.invoke(main, ["--cwd", str(repo), "config", "set", "includeDatetimeInDates", "0"])
    auto_commit = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoCommit", "yes"])
    remote_operations = runner.invoke(main, ["--cwd", str(repo), "config", "set", "remoteOperations", "1"])

    assert auto_open_browser.exit_code == 0
    assert auto_open_browser.output == "autoOpenBrowser: false\n"
    assert include_datetime.exit_code == 0
    assert include_datetime.output == "includeDatetimeInDates: false\n"
    assert auto_commit.exit_code == 0
    assert auto_commit.output == "autoCommit: true\n"
    assert remote_operations.exit_code == 0
    assert remote_operations.output == "remoteOperations: true\n"

    config = _project(repo).config
    assert config.auto_open_browser is False
    assert config.include_datetime_in_dates is False
    assert config.auto_commit is True
    assert config.remote_operations is True


def test_task_prefix_config_is_read_only_and_drives_generated_task_ids(tmp_path):
    repo = _copy_fixture(tmp_path)
    config_path = repo / "backlog" / "config.yml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw_config["prefixes"] = {"task": "JIRA"}
    config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
    runner = CliRunner()

    config_list = runner.invoke(main, ["--cwd", str(repo), "config", "list"])
    set_task_prefix = runner.invoke(main, ["--cwd", str(repo), "config", "set", "taskPrefix", "BUG"])
    set_prefixes = runner.invoke(main, ["--cwd", str(repo), "config", "set", "prefixes", "BUG"])

    assert config_list.exit_code == 0
    assert "taskPrefix: JIRA (read-only)" in config_list.output
    assert set_task_prefix.exit_code != 0
    assert "Task prefix cannot be changed after initialization" in set_task_prefix.output
    assert set_prefixes.exit_code != 0
    assert "Task prefix cannot be changed after initialization" in set_prefixes.output

    created = MutableRepository(_project(repo)).create_task(title="Prefixed task")
    child = MutableRepository(_project(repo)).create_task(title="Prefixed child", parent_task_id="1")
    dependent = MutableRepository(_project(repo)).create_task(title="Prefixed dependency", dependencies=["1"])

    assert created.id == "JIRA-1"
    assert child.id == "JIRA-1.1"
    assert child.parsed.frontmatter["parent_task_id"] == "JIRA-1"
    assert dependent.id == "JIRA-2"
    assert dependent.parsed.frontmatter["dependencies"] == ["JIRA-1"]
    assert (repo / "backlog" / "tasks" / "jira-1 - Prefixed-task.md").exists()
    assert MutableRepository(_project(repo)).get_task("1").id == "JIRA-1"
    assert _project(repo).config.task_prefix == "JIRA"


def test_zero_padded_ids_apply_to_generated_item_ids(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    set_padding = runner.invoke(main, ["--cwd", str(repo), "config", "set", "zeroPaddedIds", "3"])
    assert set_padding.exit_code == 0

    task = MutableRepository(_project(repo)).create_task(title="Padded task")
    child = MutableRepository(_project(repo)).create_task(title="Padded child", parent_task_id=task.id)
    draft = DraftService(_project(repo)).create_draft(title="Padded draft")
    document = DocumentService(_project(repo)).create_document_from_title("Padded document")
    decision = DecisionService(_project(repo)).create_decision("Padded decision")

    assert task.id == "TASK-002"
    assert child.id == "TASK-002.01"
    assert draft.id == "draft-001"
    assert document.id == "DOC-001"
    assert decision.id == "decision-001"


def test_cli_config_set_rejects_invalid_typed_values_without_writing(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    config_path = repo / "backlog" / "config.yml"
    before = config_path.read_text(encoding="utf-8")

    result = runner.invoke(main, ["--cwd", str(repo), "config", "set", "autoCommit", "sometimes"])
    bad_port = runner.invoke(main, ["--cwd", str(repo), "config", "set", "defaultPort", "70000"])
    bad_padding = runner.invoke(main, ["--cwd", str(repo), "config", "set", "zeroPaddedIds", "--", "-1"])

    assert result.exit_code != 0
    assert "boolean" in result.output
    assert bad_port.exit_code != 0
    assert "valid port" in bad_port.output
    assert bad_padding.exit_code != 0
    assert "non-negative" in bad_padding.output
    assert config_path.read_text(encoding="utf-8") == before


def test_mcp_definition_of_done_defaults_and_task_create_use_safe_core(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    assert definition_of_done_defaults_get(project) == {"items": []}
    assert definition_of_done_defaults_upsert(project, [" Tests pass ", ""]) == {"items": ["Tests pass"]}

    created = task_create(project, title="MCP DoD", definitionOfDoneAdd=["MCP specific"])

    source = _task_file(repo, created["id"]).read_text(encoding="utf-8")
    assert "- [ ] #1 Tests pass" in source
    assert "- [ ] #2 MCP specific" in source
    assert definition_of_done_defaults_get(_project(repo)) == {"items": ["Tests pass"]}


def test_mcp_definition_of_done_defaults_reject_non_string_items_without_mutation(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    definition_of_done_defaults_upsert(project, ["Tests pass"])
    before = project.config_path.read_text(encoding="utf-8")

    try:
        definition_of_done_defaults_upsert(project, ["Docs updated", object()])  # type: ignore[list-item]
    except ValueError as exc:
        assert "Definition of Done defaults must be strings" in str(exc)
    else:
        raise AssertionError("Expected MCP Definition of Done defaults to reject non-string items")

    assert project.config_path.read_text(encoding="utf-8") == before
    assert definition_of_done_defaults_get(_project(repo)) == {"items": ["Tests pass"]}
