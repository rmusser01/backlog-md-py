from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.drafts import DraftService
from backlog_py.storage.config import replace_definition_of_done_defaults
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
    assert _draft_file(repo).name == "draft-1 - Spike-GraphQL.md"
    assert "id: draft-1" in _draft_file(repo).read_text(encoding="utf-8")

    assert [record.id for record in service.list_drafts()] == ["draft-1"]
    assert service.view_draft("1").id == "draft-1"
    assert service.view_draft("draft-1").title == "Spike GraphQL"


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
    assert "draft-1 [Draft] Spike GraphQL" in view.output
    assert "Explore schema." in view.output
