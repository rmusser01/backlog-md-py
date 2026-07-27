from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.documents import DocumentMutationError, DocumentService
from backlog_py.mcp.tools import document_create, document_list, document_update, document_view
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _service(repo: Path) -> DocumentService:
    return DocumentService(_project(repo))


def _snapshot_docs(repo: Path) -> dict[Path, str]:
    docs_dir = repo / "backlog" / "docs"
    if not docs_dir.exists():
        return {}
    return {
        path.relative_to(docs_dir): path.read_text(encoding="utf-8")
        for path in sorted(docs_dir.rglob("*.md"))
    }


def _snapshot_markdown(repo: Path) -> dict[Path, str]:
    return {
        path.relative_to(repo): path.read_text(encoding="utf-8")
        for path in sorted(repo.rglob("*.md"))
    }


def _frontmatter(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    assert source.startswith("---\n")
    return yaml.safe_load(source.split("---\n", 2)[1])


def test_create_list_search_and_view_nested_document_by_path_and_id(tmp_path):
    repo = _copy_fixture(tmp_path)

    created = _service(repo).create_document(
        "guides/setup.md",
        title="Setup Guide",
        content="Install dependencies and run the server.",
        metadata={"id": "DOC-SETUP", "audience": "agents"},
    )

    assert created.path.as_posix().endswith("backlog/docs/guides/setup.md")
    assert created.id == "DOC-SETUP"
    assert created.title == "Setup Guide"
    assert "Install dependencies" in created.content
    assert [document.id for document in _service(repo).list_documents()] == ["DOC-SETUP"]
    assert [document.path_relative for document in _service(repo).search_documents("dependencies")] == [
        "guides/setup.md"
    ]
    assert _service(repo).view_document("guides/setup.md").id == "DOC-SETUP"
    assert _service(repo).view_document("DOC-SETUP").path_relative == "guides/setup.md"


def test_search_documents_matches_short_fuzzy_query_to_longer_word(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.create_document(
        "guides/authentication.md",
        title="Authentication Guide",
        content="Credential verification workflow.",
    )

    assert [document.path_relative for document in service.search_documents("authn")] == [
        "guides/authentication.md"
    ]


@pytest.mark.parametrize(
    ("source", "expected_title"),
    [
        ("\n# Lessons: testing evidence\n", "Lessons: testing evidence"),
        ("#  Lessons with spacing  \n", "Lessons with spacing"),
        ("# **Evidence**\n", "**Evidence**"),
        ("# Report ##\n", "Report ##"),
        ("Intro first.\n\n# Later heading\n", ""),
        ("#\tTabbed heading\n", ""),
        ("---\ntitle: Frontmatter title\n---\n\n# Different heading\n", "Frontmatter title"),
    ],
)
def test_document_title_uses_frontmatter_or_leading_h1(tmp_path, source, expected_title):
    repo = _copy_fixture(tmp_path)
    document_path = repo / "backlog" / "docs" / "title.md"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(source, encoding="utf-8")

    assert _service(repo).view_document("title.md").title == expected_title


def test_frontmatterless_leading_h1_title_is_shared_by_document_readers(tmp_path):
    repo = _copy_fixture(tmp_path)
    document_path = repo / "backlog" / "docs" / "lessons-testing-evidence.md"
    document_path.parent.mkdir(parents=True)
    source = b"# Lessons: what counts as evidence\r\n\r\nUnrelated incident body.\r\n"
    document_path.write_bytes(source)
    project = _project(repo)

    assert _service(repo).search_documents("counts as evidence")[0].title == "Lessons: what counts as evidence"

    record = _service(repo).view_document("lessons-testing-evidence.md")
    assert record.raw_source == source.decode("utf-8")

    listed = CliRunner().invoke(main, ["--cwd", str(repo), "doc", "list", "lessons"])
    assert listed.exit_code == 0
    assert "lessons-testing-evidence.md Lessons: what counts as evidence" in listed.output

    documents = document_list(project, query="lessons")
    assert documents[0]["title"] == "Lessons: what counts as evidence"
    assert document_path.read_bytes() == source


def test_create_document_allocates_ids_globally_under_docs(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)

    first = service.create_document("guides/setup.md", title="Setup Guide", content="One")
    second = service.create_document("notes/next.md", title="Next Guide", content="Two")

    assert first.id == "DOC-1"
    assert second.id == "DOC-2"
    assert service.view_document("DOC-2").path_relative == "notes/next.md"


def test_create_document_from_title_uses_upstream_style_path_and_metadata(tmp_path):
    repo = _copy_fixture(tmp_path)

    created = _service(repo).create_document_from_title(
        "Setup Guide",
        directory="guides",
        content="Body.",
        metadata={"type": "guide", "tags": ["setup", "runbook"]},
    )

    assert created.id == "DOC-1"
    assert created.path_relative == "guides/doc-1 - Setup-Guide.md"
    document_path = repo / "backlog" / "docs" / "guides" / "doc-1 - Setup-Guide.md"
    frontmatter = _frontmatter(document_path)
    assert frontmatter["type"] == "guide"
    assert frontmatter["tags"] == ["setup", "runbook"]


def test_update_document_preserves_omitted_metadata_and_content(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.create_document(
        "guides/setup.md",
        title="Setup Guide",
        content="Original body.",
        metadata={"id": "DOC-SETUP", "audience": "agents"},
    )

    updated = service.update_document("DOC-SETUP", title="Updated Setup Guide")

    assert updated.title == "Updated Setup Guide"
    assert updated.content == "Original body."
    document_path = repo / "backlog" / "docs" / "guides" / "setup.md"
    frontmatter = _frontmatter(document_path)
    assert frontmatter["id"] == "DOC-SETUP"
    assert frontmatter["audience"] == "agents"
    assert frontmatter["title"] == "Updated Setup Guide"


def test_update_document_preserves_omitted_body_source(tmp_path):
    repo = _copy_fixture(tmp_path)
    document_path = repo / "backlog" / "docs" / "guides" / "setup.md"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(
        "---\nid: DOC-SETUP\ntitle: Setup Guide\n---\n\n\nOriginal body.\n\n  Indented line.  \n\n",
        encoding="utf-8",
    )
    before_body = document_path.read_text(encoding="utf-8").split("---\n", 2)[2]

    _service(repo).update_document("DOC-SETUP", title="Updated Setup Guide")

    after_body = document_path.read_text(encoding="utf-8").split("---\n", 2)[2]
    assert after_body == before_body


def test_update_document_can_move_and_update_metadata(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.create_document(
        "notes/setup.md",
        title="Setup Guide",
        content="Original body.",
        metadata={"id": "DOC-SETUP", "type": "note", "tags": ["old"]},
    )

    updated = service.update_document(
        "doc-setup",
        title="Setup Handbook",
        content="Updated body.",
        directory="guides",
        metadata={"type": "guide", "tags": ["setup", "runbook"]},
    )

    assert updated.path_relative == "guides/setup.md"
    assert updated.title == "Setup Handbook"
    assert updated.content == "Updated body."
    assert not (repo / "backlog" / "docs" / "notes" / "setup.md").exists()
    document_path = repo / "backlog" / "docs" / "guides" / "setup.md"
    frontmatter = _frontmatter(document_path)
    assert frontmatter["id"] == "DOC-SETUP"
    assert frontmatter["title"] == "Setup Handbook"
    assert frontmatter["type"] == "guide"
    assert frontmatter["tags"] == ["setup", "runbook"]


def test_document_path_traversal_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_docs(repo)
    before_repo_markdown = _snapshot_markdown(repo)

    with pytest.raises(DocumentMutationError, match="Invalid document path"):
        _service(repo).create_document("../escape.md", title="Escape", content="Nope")

    with pytest.raises(DocumentMutationError, match="Invalid document path"):
        _service(repo).create_document(str((repo / "outside.md").absolute()), title="Escape", content="Nope")

    assert _snapshot_docs(repo) == before
    assert _snapshot_markdown(repo) == before_repo_markdown
    assert not (repo / "outside.md").exists()


def test_document_list_rejects_symlinked_file_escape_before_read(tmp_path):
    repo = _copy_fixture(tmp_path)
    docs_dir = repo / "backlog" / "docs"
    docs_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("---\nid: DOC-OUTSIDE\ntitle: Outside\n---\n\nSecret\n", encoding="utf-8")
    escaped = docs_dir / "escaped.md"
    try:
        escaped.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(DocumentMutationError, match="outside allowed base"):
        _service(repo).list_documents()


def test_update_document_path_traversal_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.create_document("guides/setup.md", title="Setup Guide", content="Original body.")
    before = _snapshot_docs(repo)
    before_repo_markdown = _snapshot_markdown(repo)

    with pytest.raises(DocumentMutationError, match="Invalid document path"):
        service.update_document("../escape.md", title="Escape")

    with pytest.raises(DocumentMutationError, match="Invalid document path"):
        service.update_document(str((repo / "outside.md").absolute()), title="Escape")

    assert _snapshot_docs(repo) == before
    assert _snapshot_markdown(repo) == before_repo_markdown


def test_cli_document_commands_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "doc",
            "create",
            "guides/setup.md",
            "--title",
            "Setup Guide",
            "--content",
            "Created from CLI.",
        ],
    )
    assert create.exit_code == 0
    assert "guides/setup.md Setup Guide" in create.output

    view = runner.invoke(main, ["--cwd", str(repo), "doc", "view", "guides/setup.md"])
    assert view.exit_code == 0
    assert "Created from CLI." in view.output

    update = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "doc",
            "update",
            "guides/setup.md",
            "--title",
            "Updated Setup",
        ],
    )
    assert update.exit_code == 0
    assert "guides/setup.md Updated Setup" in update.output

    listed = runner.invoke(main, ["--cwd", str(repo), "doc", "list", "updated"])
    assert listed.exit_code == 0
    assert "guides/setup.md Updated Setup" in listed.output


def test_cli_document_commands_support_upstream_title_path_type_and_tags(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "doc",
            "create",
            "Setup Guide",
            "--path",
            "guides",
            "--content",
            "Created from modern CLI.",
            "--type",
            "guide",
            "--tags",
            "setup,runbook",
        ],
    )

    assert create.exit_code == 0
    assert "guides/doc-1 - Setup-Guide.md Setup Guide" in create.output

    update = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "doc",
            "update",
            "doc-1",
            "--title",
            "Setup Handbook",
            "--content",
            "Updated from modern CLI.",
            "--path",
            "runbooks",
            "--type",
            "runbook",
            "--tags",
            "setup,verified",
        ],
    )

    assert update.exit_code == 0
    assert "runbooks/doc-1 - Setup-Guide.md Setup Handbook" in update.output
    document_path = repo / "backlog" / "docs" / "runbooks" / "doc-1 - Setup-Guide.md"
    frontmatter = _frontmatter(document_path)
    assert frontmatter["type"] == "runbook"
    assert frontmatter["tags"] == ["setup", "verified"]
    assert "Updated from modern CLI." in document_path.read_text(encoding="utf-8")


def test_mcp_document_tools_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    created = document_create(
        project,
        path="guides/setup.md",
        title="Setup Guide",
        content="Created from MCP.",
        metadata={"id": "DOC-SETUP"},
    )
    assert created["id"] == "DOC-SETUP"

    listed = document_list(project, query="MCP")
    assert [document["path"] for document in listed] == ["guides/setup.md"]

    viewed = document_view(project, "DOC-SETUP")
    assert viewed["content"] == "Created from MCP."

    updated = document_update(project, "DOC-SETUP", content="Updated from MCP.")
    assert updated["content"] == "Updated from MCP."
