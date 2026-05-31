import json
import shutil
from datetime import date
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from backlog_py import __version__
from backlog_py.cli.main import main
from backlog_py.core.decisions import DecisionService
from backlog_py.core.documents import DocumentService
from backlog_py.core.repository import MutableRepository
from backlog_py.storage.config import set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _invoke(*args: str):
    return CliRunner().invoke(main, ["--cwd", str(FIXTURE_REPO), *args])


def _invoke_color(*args: str, input: str | None = None):
    return CliRunner().invoke(main, ["--cwd", str(FIXTURE_REPO), *args], color=True, input=input)


def _invoke_repo(repo: Path, *args: str, input: str | None = None):
    return CliRunner().invoke(main, ["--cwd", str(repo), *args], input=input)


def _release_evidence_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "upstream_baseline": {
            "package": "backlog.md",
            "version": "1.45.2",
            "audit_date": "2026-05-31",
        },
        "command": {
            "argv": ["backlog-py", "compat", "evidence-template"],
            "cwd": ".",
        },
        "freshness": {
            "max_age_days": 14,
        },
        "release_gates": {
            "browser:rich-edit-e2e-release-check": {
                "status": "passed",
                "artifacts": ["artifacts/browser-rich-edit-e2e.txt"],
            },
            "browser:desktop-mobile-screenshot-release-check": {
                "status": "passed",
                "artifacts": [
                    "artifacts/browser-desktop.png",
                    "artifacts/browser-mobile.png",
                ],
            },
        },
    }


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


def _task_file(repo: Path, task_id: str = "task-1") -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob(f"{task_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def test_top_level_help_includes_readonly_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--cwd" in result.output
    assert "init" in result.output
    assert "browser" in result.output
    assert "compat" in result.output
    assert "task" in result.output
    assert "search" in result.output
    assert "board" in result.output
    assert "overview" in result.output
    assert "config" in result.output


def test_top_level_help_includes_tui_command():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "tui" in result.output


def test_top_level_version_reports_package_version():
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_tui_command_invokes_lazy_runner_with_discovered_project(monkeypatch):
    from backlog_py.cli import main as cli_main

    calls = []
    monkeypatch.setattr(cli_main, "_load_tui_runner", lambda: lambda project: calls.append(project.root))

    result = _invoke("tui")

    assert result.exit_code == 0
    assert calls == [FIXTURE_REPO]


def test_tui_command_without_extra_shows_install_hint(monkeypatch):
    from backlog_py.cli import main as cli_main

    def missing_runner():
        raise click.ClickException("Install with backlog-md-py[tui] to use the Textual TUI.")

    monkeypatch.setattr(cli_main, "_load_tui_runner", missing_runner)

    result = _invoke("tui")

    assert result.exit_code != 0
    assert "Install with backlog-md-py[tui]" in result.output


def test_tui_command_without_project_shows_cli_error(tmp_path, monkeypatch):
    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_load_tui_runner", lambda: lambda project: None)

    result = CliRunner().invoke(main, ["--cwd", str(tmp_path), "tui"])

    assert result.exit_code != 0
    assert "No Backlog.md config found" in result.output
    assert not isinstance(result.exception, FileNotFoundError)


def test_tui_command_invokes_implemented_tui_app(monkeypatch):
    pytest.importorskip("textual")

    from backlog_py.cli import main as cli_main
    from backlog_py.tui import app as tui_app

    calls = []
    monkeypatch.setattr(tui_app.BacklogTuiApp, "run", lambda self: calls.append(self.project.root))

    result = _invoke("tui")

    assert result.exit_code == 0
    assert calls == [FIXTURE_REPO]


def test_task_list_plain_outputs_task_id():
    result = _invoke("task", "list", "--plain")

    assert result.exit_code == 0
    assert "TASK-1" in result.output
    assert "Example task" in result.output


def test_task_list_default_uses_ansi_color_without_changing_plain_contract():
    result = _invoke_color("task", "list")
    plain = _invoke_color("task", "list", "--plain")

    assert result.exit_code == 0
    assert "\x1b[" in result.output
    assert "TASK-1" in result.output
    assert "Example task" in result.output
    assert "In Progress" in result.output

    assert plain.exit_code == 0
    assert "\x1b[" not in plain.output
    assert "TASK-1 [In Progress] Example task" in plain.output


def test_task_view_plain_outputs_task_body():
    result = _invoke("task", "TASK-1", "--plain")

    assert result.exit_code == 0
    assert result.output.startswith("File: backlog/tasks/task-1 - Example-task.md\n\n")
    assert "Task TASK-1 - Example task" in result.output
    assert "Status: ◒ In Progress" in result.output
    assert "Created: 2026-05-10 10:00" in result.output
    assert "Description:\nImplement a fixture" in result.output
    assert "Acceptance Criteria:" in result.output
    assert "- [x] #1 Preserve completed acceptance criteria raw line" in result.output
    assert "Implementation Notes:" in result.output
    assert "Final Summary:" in result.output
    assert "Definition of Done:" in result.output
    assert "---" not in result.output


def test_task_view_plain_falls_back_to_legacy_body_without_description_section(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    task_path = _task_file(repo)
    task_path.write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Legacy task\n"
        "status: To Do\n"
        "---\n"
        "Legacy body only.\n",
        encoding="utf-8",
    )

    result = _invoke_repo(repo, "task", "TASK-1", "--plain")

    assert result.exit_code == 0
    assert "Task TASK-1 - Legacy task" in result.output
    assert "Description:\nLegacy body only." in result.output


def test_task_view_default_renders_interactive_task_detail():
    result = _invoke_color("task", "TASK-1")

    assert result.exit_code == 0
    assert "\x1b[" in result.output
    assert "Task TASK-1" in result.output
    assert "Status: In Progress" in result.output
    assert "File: backlog/tasks/task-1 - Example-task.md" in result.output
    assert "Created: 2026-05-10 10:00" in result.output
    assert "Actions: [E]dit in editor  [Q]uit" in result.output
    assert "---" not in result.output


def test_task_view_default_honors_date_display_config(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    task_path = _task_file(repo)
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "created_date: '2026-05-10 10:00'\n",
            "created_date: '2026-05-10 10:00'\nupdated_date: '2026-05-11 12:34'\n",
        ),
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "dateFormat", "dd/mm/yyyy")
    set_config_value(project, "includeDatetimeInDates", "false")

    result = _invoke_repo(repo, "task", "TASK-1")

    assert result.exit_code == 0
    assert "Created: 10/05/2026" in result.output
    assert "Updated: 11/05/2026" in result.output
    assert "2026-05-10 10:00" not in result.output
    assert "2026-05-11 12:34" not in result.output
    assert "12:34" not in result.output


def test_task_view_editor_key_launches_default_editor_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "defaultEditor", "fake-editor --wait")
    lock_operations = []
    editor_invocations = []

    from backlog_py.cli import main as cli_main

    original_lock = cli_main.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    def fake_editor(command, path):
        editor_invocations.append((command, path))
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited by test.\n", encoding="utf-8")

    monkeypatch.setattr(cli_main, "with_project_write_lock", tracking_lock)
    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli_main, "_run_editor_command", fake_editor)

    result = _invoke_repo(repo, "task", "TASK-1", input="e")

    assert result.exit_code == 0
    assert lock_operations == [(repo, "task_editor")]
    assert editor_invocations == [(["fake-editor", "--wait"], _task_file(repo))]
    assert "Edited TASK-1" in result.output
    assert "Edited by test." in _task_file(repo).read_text(encoding="utf-8")


def test_search_plain_outputs_matching_task():
    result = _invoke("search", "parser preservation", "--plain")

    assert result.exit_code == 0
    assert "TASK-1" in result.output
    assert "Example task" in result.output
    assert "Actions:" not in result.output


def test_search_default_renders_interactive_filter_panel():
    result = _invoke_color("search", "parser preservation")

    assert result.exit_code == 0
    assert "\x1b[" in result.output
    assert "Search results for: parser preservation" in result.output
    assert "TASK-1" in result.output
    assert "Actions: [S]tatus  [P]riority  [T]ype  [M]odified file  [Q]uit" in result.output


def test_search_interactive_status_filter_refines_results(tmp_path, monkeypatch):
    repo = _metadata_filter_repo(tmp_path)

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "search", "task", input="sTo Do\n")

    assert result.exit_code == 0
    assert "Filter status: To Do" in result.output
    filtered_output = result.output.split("Filtered results", maxsplit=1)[1]
    assert "TASK-2" in filtered_output
    assert "Documentation task" in filtered_output
    assert "TASK-1" not in filtered_output


def test_search_interactive_priority_filter_refines_results(tmp_path, monkeypatch):
    repo = _metadata_filter_repo(tmp_path)

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "search", "task", input="plow\n")

    assert result.exit_code == 0
    assert "Filter priority: low" in result.output
    filtered_output = result.output.split("Filtered results", maxsplit=1)[1]
    assert "TASK-2" in filtered_output
    assert "Documentation task" in filtered_output
    assert "TASK-1" not in filtered_output


def test_search_interactive_type_filter_refines_results(tmp_path, monkeypatch):
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

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "search", "shared needle", input="tdocument\n")

    assert result.exit_code == 0
    assert "Filter type: document" in result.output
    filtered_output = result.output.split("Filtered results", maxsplit=1)[1]
    assert "guides/shared.md Shared needle document" in filtered_output
    assert "TASK-2" not in filtered_output
    assert "decision-1" not in filtered_output


def test_search_interactive_modified_file_filter_refines_results(tmp_path, monkeypatch):
    repo = _metadata_filter_repo(tmp_path)

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "search", "task", input="msrc/server\n")

    assert result.exit_code == 0
    assert "Filter modified file: src/server" in result.output
    filtered_output = result.output.split("Filtered results", maxsplit=1)[1]
    assert "TASK-2" in filtered_output
    assert "Documentation task" in filtered_output
    assert "TASK-1" not in filtered_output


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


def test_search_plain_limit_applies_after_fuzzy_ranking(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    repository = MutableRepository.from_path(repo)
    repository.create_task(title="Authentication rollout", task_id="TASK-2", status="To Do")
    repository.create_task(title="Auth", task_id="TASK-3", status="To Do")

    result = _invoke_repo(repo, "search", "auth", "--status", "To Do", "--limit", "1", "--plain")

    assert result.exit_code == 0
    assert "TASK-3 [To Do] Auth" in result.output
    assert "TASK-2 [To Do] Authentication rollout" not in result.output


def test_board_outputs_status_grouping():
    result = _invoke("board")

    assert result.exit_code == 0
    assert "To Do" in result.output
    assert "In Progress" in result.output
    assert "TASK-1" in result.output
    assert "Done" in result.output
    assert "Actions:" not in result.output


def test_board_interactive_renders_action_panel(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "board", input="q")

    assert result.exit_code == 0
    assert "Actions: [V]iew task  [E]dit task  [M]ove task  [Q]uit" in result.output


def test_board_interactive_move_updates_task_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    lock_operations = []

    from backlog_py.cli import main as cli_main

    original_lock = cli_main.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    monkeypatch.setattr(cli_main, "with_project_write_lock", tracking_lock)
    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "board", input="mTASK-1\nDone\n")

    assert result.exit_code == 0
    assert lock_operations == [(repo, "board_move_task")]
    assert "Moved TASK-1 to Done" in result.output
    assert MutableRepository.from_path(repo).get_task("TASK-1").status == "Done"


def test_board_interactive_edit_launches_default_editor_under_project_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "defaultEditor", "fake-editor --wait")
    lock_operations = []
    editor_invocations = []

    from backlog_py.cli import main as cli_main

    original_lock = cli_main.with_project_write_lock

    def tracking_lock(project, operation, fn):
        lock_operations.append((project.root, operation))
        return original_lock(project, operation, fn)

    def fake_editor(command, path):
        editor_invocations.append((command, path))
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited from board.\n", encoding="utf-8")

    monkeypatch.setattr(cli_main, "with_project_write_lock", tracking_lock)
    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(cli_main, "_run_editor_command", fake_editor)

    result = _invoke_repo(repo, "board", input="eTASK-1\n")

    assert result.exit_code == 0
    assert lock_operations == [(repo, "task_editor")]
    assert editor_invocations == [(["fake-editor", "--wait"], _task_file(repo))]
    assert "Edited TASK-1" in result.output
    assert "Edited from board." in _task_file(repo).read_text(encoding="utf-8")


def test_board_interactive_view_renders_task_detail(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "board", input="vTASK-1\n")

    assert result.exit_code == 0
    assert "Task id: TASK-1" in result.output
    assert "Task TASK-1" in result.output
    assert "Status: In Progress" in result.output
    assert "Actions: [E]dit in editor  [Q]uit" in result.output


def test_board_and_search_default_use_ansi_color_for_human_output():
    board = _invoke_color("board")
    search = _invoke_color("search", "parser preservation")

    assert board.exit_code == 0
    assert "\x1b[" in board.output
    assert "In Progress" in board.output

    assert search.exit_code == 0
    assert "\x1b[" in search.output
    assert "TASK-1" in search.output
    assert "Example task" in search.output
    assert "In Progress" in search.output


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
    assert "Project Overview" not in result.output
    assert "Actions:" not in result.output


def test_overview_interactive_renders_dashboard_sections(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    repository = MutableRepository.from_path(repo)
    repository.edit_task("TASK-1", priority="high")
    repository.create_task(
        title="Blocked overview task",
        task_id="TASK-2",
        status="To Do",
        priority="low",
        dependencies=["TASK-1"],
    )

    from backlog_py.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: True)

    result = _invoke_repo(repo, "overview", input="q")

    assert result.exit_code == 0
    assert "basic-fixture - Project Overview" in result.output
    assert "Status Overview" in result.output
    assert "To Do: 1 tasks" in result.output
    assert "In Progress: 1 tasks" in result.output
    assert "Done: 0 tasks" in result.output
    assert "Total Tasks: 2" in result.output
    assert "Completion: 0%" in result.output
    assert "Priority Breakdown" in result.output
    assert "High: 1 tasks" in result.output
    assert "Low: 1 tasks" in result.output
    assert "Project Health" in result.output
    assert "Blocked Tasks: 1" in result.output
    assert "Actions: [Q]uit" in result.output


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
    assert "defaultAssignee: (not set)" in result.output
    assert "dateFormat: yyyy-mm-dd" in result.output
    assert "includeDatetimeInDates: true" in result.output
    assert "defaultEditor: (not set)" in result.output
    assert "defaultPort: 6420" in result.output
    assert "autoOpenBrowser: true" in result.output
    assert "onStatusChange: (disabled)" in result.output
    assert "zeroPaddedIds: (disabled)" in result.output
    assert "taskPrefix: task (read-only)" in result.output
    assert "autoCommit: false" in result.output
    assert "remoteOperations: false" in result.output


def test_compat_status_outputs_cutover_summary():
    result = _invoke("compat", "status")

    assert result.exit_code == 0
    assert "agentCutoverReady: true" in result.output
    assert "fullBrowserReleaseReady: false" in result.output
    assert "upstreamBaseline: backlog.md 1.45.2 audited 2026-05-31" in result.output
    assert "implemented: 100" in result.output
    assert "deferred: 0" in result.output
    assert "total: 100" in result.output
    assert "cli: 45 implemented, 0 deferred, 45 total" in result.output
    assert "browser: 24 implemented, 0 deferred, 24 total" in result.output
    assert "config: 2 implemented, 0 deferred, 2 total" in result.output
    assert "core: 3 implemented, 0 deferred, 3 total" in result.output
    assert "git: 4 implemented, 0 deferred, 4 total" in result.output
    assert "releaseGates:" in result.output
    assert "browser:rich-edit-e2e-release-check: required" in result.output
    assert "browser:desktop-mobile-screenshot-release-check: required" in result.output


def test_compat_status_json_outputs_deferred_items():
    result = _invoke("compat", "status", "--json")

    assert result.exit_code == 0
    assert '"agent_cutover_ready": true' in result.output
    assert '"full_browser_release_ready": false' in result.output
    assert '"upstream_baseline": {' in result.output
    assert '"package": "backlog.md"' in result.output
    assert '"version": "1.45.2"' in result.output
    assert '"audit_date": "2026-05-31"' in result.output
    assert '"browser:rich-edit-e2e-release-check"' in result.output
    assert '"browser:desktop-mobile-screenshot-release-check"' in result.output
    assert '"cli:interactive-task-view-editor"' in result.output
    assert '"cli:interactive-overview"' in result.output
    assert '"cli:task-plain-detail"' in result.output
    assert '"cli:interactive-date-display"' in result.output
    assert '"browser:responsive-layout"' in result.output
    assert '"browser:service-lifecycle"' in result.output
    assert '"browser:service-request-log"' in result.output
    assert '"browser:service-shutdown-state"' in result.output
    assert '"browser:task-detail-view"' in result.output
    assert '"browser:markdown-detail-rendering"' in result.output
    assert '"browser:mermaid-rendering"' in result.output
    assert '"browser:rich-section-editing"' in result.output
    assert '"browser:markdown-edit-toolbar"' in result.output
    assert '"browser:rich-markdown-editor"' in result.output
    assert '"browser:metadata-editing"' in result.output
    assert '"browser:task-create-form"' in result.output
    assert '"browser:task-edit-form"' in result.output
    assert '"browser:task-archive-confirmation"' in result.output
    assert '"browser:checklist-state-controls"' in result.output
    assert '"browser:document-decision-readonly"' in result.output
    assert '"browser:general-settings"' in result.output
    assert '"browser:safe-git-settings"' in result.output
    assert '"browser:live-refresh-polling"' in result.output
    assert '"browser:sse-live-refresh"' in result.output
    assert '"browser:service-transport-shutdown"' in result.output
    assert '"core:task-timestamps"' in result.output
    assert '"core:date-only-timestamps"' in result.output
    assert '"git:hook-bypass"' in result.output
    assert '"status": "implemented"' in result.output


def test_compat_status_accepts_release_evidence_manifest(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(_release_evidence_manifest()),
        encoding="utf-8",
    )

    result = _invoke("compat", "status", "--release-evidence", str(evidence_path))

    assert result.exit_code == 0
    assert "fullBrowserReleaseReady: true" in result.output
    assert "browser:rich-edit-e2e-release-check: passed" in result.output
    assert "browser:desktop-mobile-screenshot-release-check: passed" in result.output


def test_compat_status_json_includes_release_evidence_artifacts(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(_release_evidence_manifest()),
        encoding="utf-8",
    )

    result = _invoke("compat", "status", "--json", "--release-evidence", str(evidence_path))

    assert result.exit_code == 0
    assert '"full_browser_release_ready": true' in result.output
    assert '"artifacts": [' in result.output
    assert '"artifacts/browser-desktop.png"' in result.output
    assert '"artifacts/browser-mobile.png"' in result.output


def test_compat_evidence_template_writes_portable_manifest(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"

    result = _invoke(
        "compat",
        "evidence-template",
        "--output",
        str(evidence_path),
        "--rich-edit-artifact",
        "artifacts/browser-rich-edit-e2e.txt",
        "--desktop-artifact",
        "artifacts/browser-desktop.png",
        "--mobile-artifact",
        "artifacts/browser-mobile.png",
        "--command",
        "manual browser validation",
    )

    assert result.exit_code == 0
    manifest = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["upstream_baseline"] == {
        "package": "backlog.md",
        "version": "1.45.2",
        "audit_date": "2026-05-31",
    }
    assert manifest["freshness"]["max_age_days"] == 14
    assert manifest["command"]["argv"] == ["manual browser validation"]
    assert manifest["release_gates"]["browser:rich-edit-e2e-release-check"]["artifacts"] == [
        "artifacts/browser-rich-edit-e2e.txt"
    ]
    assert manifest["release_gates"]["browser:desktop-mobile-screenshot-release-check"]["artifacts"] == [
        "artifacts/browser-desktop.png",
        "artifacts/browser-mobile.png",
    ]

    status = _invoke("compat", "status", "--release-evidence", str(evidence_path))

    assert status.exit_code == 0
    assert "releaseEvidence: fresh" in status.output
    assert "fullBrowserReleaseReady: true" in status.output


def test_compat_status_plain_outputs_gate_evidence_errors(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence = _release_evidence_manifest()
    evidence["release_gates"]["browser:desktop-mobile-screenshot-release-check"]["artifacts"] = [
        "artifacts/browser-desktop.png"
    ]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = _invoke("compat", "status", "--release-evidence", str(evidence_path))

    assert result.exit_code == 0
    assert "releaseEvidence: fresh" in result.output
    assert (
        "browser:desktop-mobile-screenshot-release-check: required "
        "(full-browser-release) - evidenceError: Screenshot release evidence "
        "requires desktop and mobile artifacts."
    ) in result.output
    assert (
        "browser:rich-edit-e2e-release-check: passed "
        "(full-browser-release) - evidenceError:"
    ) not in result.output


def test_compat_evidence_template_rejects_absolute_artifact_paths(tmp_path):
    result = _invoke(
        "compat",
        "evidence-template",
        "--output",
        str(tmp_path / "browser-release-evidence.json"),
        "--rich-edit-artifact",
        "/private/tmp/browser-rich-edit-e2e.txt",
    )

    assert result.exit_code != 0
    assert "relative artifact paths" in result.output


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
