from __future__ import annotations

import re
import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.drafts import DraftService
from backlog_py.storage.config import replace_definition_of_done_defaults, set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _draft_file(repo: Path, draft_id: str = "draft-1") -> Path:
    matches = sorted((repo / "backlog" / "drafts").glob(f"{draft_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def _task_file(repo: Path, task_id: str = "task-1") -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob(f"{task_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def _archived_draft_file(repo: Path, draft_id: str = "draft-1") -> Path:
    matches = sorted((repo / "backlog" / "archive" / "drafts").glob(f"{draft_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def test_create_list_and_view_draft(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = DraftService(_project(repo))

    draft = service.create_draft(
        title="Spike GraphQL",
        description="Explore schema options.",
        assignees=["codex"],
        labels=["research"],
        priority="high",
    )

    assert draft.id == "draft-1"
    assert draft.status == "Draft"
    assert draft.title == "Spike GraphQL"
    assert draft.description == "Explore schema options."
    assert draft.parsed.frontmatter["assignee"] == ["codex"]
    assert draft.parsed.frontmatter["labels"] == ["research"]
    assert draft.parsed.frontmatter["priority"] == "high"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", str(draft.parsed.frontmatter["created_date"]))
    assert _draft_file(repo).name == "draft-1 - Spike-GraphQL.md"
    assert "id: draft-1" in _draft_file(repo).read_text(encoding="utf-8")

    assert [record.id for record in service.list_drafts()] == ["draft-1"]
    assert service.view_draft("1").id == "draft-1"
    assert service.view_draft("draft-1").title == "Spike GraphQL"


def test_create_draft_honors_date_only_timestamp_config(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    set_config_value(project, "includeDatetimeInDates", "false")
    service = DraftService(project)

    draft = service.create_draft(title="Date only draft")

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(draft.parsed.frontmatter["created_date"]))


def test_create_draft_inherits_definition_of_done_defaults(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    replace_definition_of_done_defaults(project, ["Default review"])
    service = DraftService(project)

    inherited = service.create_draft(title="Defaulted draft")
    disabled = service.create_draft(title="No default draft", disable_definition_of_done_defaults=True)
    explicit = service.create_draft(
        title="Explicit draft",
        definition_of_done=["Explicit review"],
        disable_definition_of_done_defaults=True,
    )

    assert "- [ ] #1 Default review" in inherited.raw_source
    assert "Default review" not in disabled.raw_source
    assert "- [ ] #1 Explicit review" in explicit.raw_source
    assert "Default review" not in explicit.raw_source


def test_promote_draft_moves_to_tasks_with_new_id_and_default_status(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = DraftService(_project(repo))
    draft = service.create_draft(title="Promotable draft", description="Promote me.")

    promoted = service.promote_draft(draft.id)

    assert promoted.id == "TASK-2"
    assert promoted.status == "To Do"
    assert promoted.title == "Promotable draft"
    assert promoted.description == "Promote me."
    assert not draft.path.exists()
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "id: TASK-2" in written
    assert "status: To Do" in written
    assert "Promote me." in written


def test_demote_task_moves_to_drafts_with_new_draft_id_and_status(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = DraftService(_project(repo))
    task_path = _task_file(repo)

    draft = service.demote_task("TASK-1")

    assert draft.id == "draft-1"
    assert draft.status == "Draft"
    assert draft.title == "Example task"
    assert not task_path.exists()
    written = _draft_file(repo).read_text(encoding="utf-8")
    assert "id: draft-1" in written
    assert "status: Draft" in written
    assert "Unowned body content before acceptance criteria must be preserved." in written


def test_archive_draft_moves_to_archive_drafts(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = DraftService(_project(repo))
    draft = service.create_draft(title="Archived draft")

    archived = service.archive_draft(draft.id)

    assert archived.id == "draft-1"
    assert archived.status == "Draft"
    assert not draft.path.exists()
    assert _archived_draft_file(repo).read_text(encoding="utf-8") == archived.raw_source


def test_cli_task_create_draft_writes_to_drafts(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI draft task",
            "--draft",
            "-d",
            "Drafted through task create.",
            "--parent",
            "1",
            "--dep",
            "TASK-1",
            "--plan",
            "Draft plan.",
            "--final-summary",
            "Draft summary.",
            "--ordinal",
            "1000",
            "--milestone",
            "Release 1",
            "--ref",
            "src/draft.py",
            "--doc",
            "docs/draft.md",
            "--modified-file",
            "src/draft.py",
            "--ac",
            "Draft can be promoted.",
            "--dod",
            "Draft is reviewed.",
            "--plain",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "draft-1 [Draft] CLI draft task" in result.output
    written = _draft_file(repo).read_text(encoding="utf-8")
    assert "Drafted through task create." in written
    assert "dependencies:" in written
    assert "- TASK-1" in written
    assert "parent_task_id: TASK-1" in written
    assert "ordinal: 1000" in written
    assert "milestone: Release 1" in written
    assert "references:" in written
    assert "documentation:" in written
    assert "modified_files:" in written
    assert "- [ ] #1 Draft can be promoted." in written
    assert "- [ ] #1 Draft is reviewed." in written
    assert "Draft plan." in written
    assert "Draft summary." in written
    assert not list((repo / "backlog" / "tasks").glob("draft-1 -*.md"))


def test_cli_draft_create_list_and_view_plain(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "draft",
            "create",
            "Spike GraphQL",
            "-d",
            "Explore schema.",
            "-l",
            "research",
        ],
    )

    assert create.exit_code == 0, create.output
    assert "Created draft draft-1" in create.output
    assert "File:" in create.output

    listing = runner.invoke(main, ["--cwd", str(repo), "draft", "list", "--plain"])
    assert listing.exit_code == 0, listing.output
    assert "Drafts:" in listing.output
    assert "draft-1 - Spike GraphQL" in listing.output

    view = runner.invoke(main, ["--cwd", str(repo), "draft", "view", "draft-1", "--plain"])
    assert view.exit_code == 0, view.output
    assert view.output.startswith("File: backlog/drafts/draft-1 - Spike-GraphQL.md\n\n")
    assert "Task draft-1 - Spike GraphQL" in view.output
    assert "Status: ○ Draft" in view.output
    assert re.search(r"Created: \d{4}-\d{2}-\d{2} \d{2}:\d{2}", view.output)
    assert "Description:\nExplore schema." in view.output
    assert "Definition of Done:" in view.output
    assert "---" not in view.output


def test_cli_draft_lifecycle_commands_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    service = DraftService(_project(repo))
    service.create_draft(title="Lifecycle draft", description="Move me.")

    promote = runner.invoke(main, ["--cwd", str(repo), "draft", "promote", "draft-1"])
    assert promote.exit_code == 0, promote.output
    assert "Promoted draft draft-1 to TASK-2" in promote.output
    assert _task_file(repo, "task-2").is_file()
    assert not list((repo / "backlog" / "drafts").glob("draft-1 -*.md"))

    demote = runner.invoke(main, ["--cwd", str(repo), "task", "demote", "TASK-1"])
    assert demote.exit_code == 0, demote.output
    assert "Demoted task TASK-1 to draft-1" in demote.output
    assert _draft_file(repo).is_file()
    assert not list((repo / "backlog" / "tasks").glob("task-1 -*.md"))

    archive = runner.invoke(main, ["--cwd", str(repo), "draft", "archive", "draft-1"])
    assert archive.exit_code == 0, archive.output
    assert "Archived draft draft-1" in archive.output
    assert _archived_draft_file(repo).is_file()
    assert not list((repo / "backlog" / "drafts").glob("draft-1 -*.md"))
