import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py import __version__
from backlog_py.cli.main import main
from backlog_py.core.decisions import DecisionService
from backlog_py.core.documents import DocumentService
from backlog_py.core.repository import MutableRepository
from backlog_py.storage.project import discover_project


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
        modified_files=["src/components/Button.tsx"],
    )
    repository.create_task(
        title="Documentation task",
        task_id="TASK-2",
        status="To Do",
        assignees=["reviewer"],
        labels=["docs"],
        priority="low",
        milestone="Release 2",
        modified_files=["src/server/index.py"],
    )
    return repo


def test_top_level_help_includes_readonly_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--cwd" in result.output
    assert "compat" in result.output
    assert "task" in result.output
    assert "search" in result.output
    assert "board" in result.output
    assert "overview" in result.output
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


def test_search_plain_outputs_matching_documents_when_unfiltered(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    DocumentService(project).create_document(
        "guides/search.md",
        title="Search Guide",
        content="Unique document lookup body.",
    )

    result = _invoke_repo(repo, "search", "lookup body", "--plain")

    assert result.exit_code == 0
    assert "guides/search.md Search Guide" in result.output


def test_search_plain_filters_by_result_type(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    MutableRepository.from_path(repo).create_task(title="Shared needle task", task_id="TASK-2")
    DocumentService(project).create_document(
        "guides/shared.md",
        title="Shared needle document",
        content="Shared needle body.",
    )
    DecisionService(project).create_decision("Shared needle decision")

    task = _invoke_repo(repo, "search", "shared needle", "--type", "task", "--plain")
    document = _invoke_repo(repo, "search", "shared needle", "--type", "document", "--plain")
    decision = _invoke_repo(repo, "search", "shared needle", "--type", "decision", "--plain")
    combined = _invoke_repo(repo, "search", "shared needle", "--type", "document,decision", "--plain")

    assert task.exit_code == 0
    assert "TASK-2 [To Do] Shared needle task" in task.output
    assert "guides/shared.md Shared needle document" not in task.output
    assert "decision-1 [proposed] Shared needle decision" not in task.output

    assert document.exit_code == 0
    assert "guides/shared.md Shared needle document" in document.output
    assert "TASK-2 [To Do] Shared needle task" not in document.output
    assert "decision-1 [proposed] Shared needle decision" not in document.output

    assert decision.exit_code == 0
    assert "decision-1 [proposed] Shared needle decision" in decision.output
    assert "TASK-2 [To Do] Shared needle task" not in decision.output
    assert "guides/shared.md Shared needle document" not in decision.output

    assert combined.exit_code == 0
    assert "guides/shared.md Shared needle document" in combined.output
    assert "decision-1 [proposed] Shared needle decision" in combined.output
    assert "TASK-2 [To Do] Shared needle task" not in combined.output


def test_search_plain_filters_by_status_and_priority(tmp_path):
    repo = _metadata_filter_repo(tmp_path)

    result = _invoke_repo(repo, "search", "task", "--status", "To Do", "--priority", "low", "--plain")

    assert result.exit_code == 0
    assert "TASK-2" in result.output
    assert "Documentation task" in result.output
    assert "TASK-1" not in result.output


def test_search_plain_filters_by_modified_file_and_limit(tmp_path):
    repo = _metadata_filter_repo(tmp_path)

    modified_file = _invoke_repo(repo, "search", "task", "--modified-file", "server", "--plain")
    limited = _invoke_repo(repo, "search", "task", "--limit", "1", "--plain")

    assert modified_file.exit_code == 0
    assert "TASK-2" in modified_file.output
    assert "Documentation task" in modified_file.output
    assert "TASK-1" not in modified_file.output

    assert limited.exit_code == 0
    assert "TASK-1" in limited.output
    assert "TASK-2" not in limited.output


def test_board_outputs_status_grouping():
    result = _invoke("board")

    assert result.exit_code == 0
    assert "To Do" in result.output
    assert "In Progress" in result.output
    assert "TASK-1" in result.output
    assert "Done" in result.output


def test_overview_outputs_plain_project_summary():
    result = _invoke("overview")

    assert result.exit_code == 0
    assert "Project: basic-fixture" in result.output
    assert "Active tasks: 1" in result.output
    assert "Completed tasks: 0" in result.output
    assert "Total tasks: 1" in result.output
    assert "Statuses:" in result.output
    assert "  To Do: 0" in result.output
    assert "  In Progress: 1" in result.output
    assert "  Done: 0" in result.output


def test_board_export_writes_markdown_report(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    repository = MutableRepository.from_path(repo)
    repository.create_task(title="Child export task", status="In Progress", parent_task_id="TASK-1")

    result = _invoke_repo(repo, "board", "export", "status.md", "--force")

    assert result.exit_code == 0
    assert "Exported board to" in result.output
    content = (repo / "status.md").read_text(encoding="utf-8")
    assert "# Kanban Board Export (powered by Backlog.md)" in content
    assert "Project: basic-fixture" in content
    assert "Generated on: " in content
    assert "| To Do | In Progress | Done |" in content
    assert "**TASK-1** - Example task [@codex]<br>*#parser*" in content
    assert "└─ **TASK-1.1** - Child export task" in content


def test_board_export_existing_file_requires_force_confirmation(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    output = repo / "status.md"
    output.write_text("keep me\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["--cwd", str(repo), "board", "export", "status.md"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Export cancelled." in result.output
    assert output.read_text(encoding="utf-8") == "keep me\n"


def test_board_export_readme_updates_marker_section(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    (repo / "README.md").write_text("# Fixture\n\n## License\n", encoding="utf-8")

    result = _invoke_repo(repo, "board", "export", "--readme", "--export-version", "v9")

    assert result.exit_code == 0
    assert "Updated README.md with Kanban board." in result.output
    content = (repo / "README.md").read_text(encoding="utf-8")
    assert "<!-- BOARD_START -->" in content
    assert "## 📊 basic-fixture Project Status (v9)" in content
    assert "This board was automatically generated by [Backlog.md](https://backlog.md)" in content
    assert "| To Do | In Progress | Done |" in content
    assert "<!-- BOARD_END -->" in content
    assert content.index("<!-- BOARD_END -->") < content.index("## License")


def test_config_list_outputs_safe_defaults():
    result = _invoke("config", "list")

    assert result.exit_code == 0
    assert "projectName: basic-fixture" in result.output
    assert "autoCommit: false" in result.output
    assert "remoteOperations: false" in result.output


def test_compat_status_outputs_cutover_summary():
    result = _invoke("compat", "status")

    assert result.exit_code == 0
    assert "agentCutoverReady: true" in result.output
    assert "implemented: 55" in result.output
    assert "deferred: 8" in result.output
    assert "total: 63" in result.output
    assert "cli: 33 implemented, 3 deferred, 36 total" in result.output
    assert "browser: 0 implemented, 1 deferred, 1 total" in result.output
    assert "git: 0 implemented, 3 deferred, 3 total" in result.output


def test_compat_status_json_outputs_deferred_items():
    result = _invoke("compat", "status", "--json")

    assert result.exit_code == 0
    assert '"agent_cutover_ready": true' in result.output
    assert '"browser:kanban-drag-drop"' in result.output
    assert '"reason": "Browser UI parity is tracked in the browser deferral milestone."' in result.output


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
