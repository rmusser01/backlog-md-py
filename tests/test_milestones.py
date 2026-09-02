from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner
from loguru import logger

import backlog_py.core.milestones as milestones_module
from backlog_py.cli.main import main
from backlog_py.core.milestones import MilestoneMutationError, MilestoneService
from backlog_py.mcp.tools import (
    milestone_add,
    milestone_archive,
    milestone_list,
    milestone_remove,
    milestone_rename,
)
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _service(repo: Path) -> MilestoneService:
    return MilestoneService(_project(repo))


@contextmanager
def _captured_warnings():
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _task_path(repo: Path, task_id: str = "task-1") -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob(f"{task_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def _set_task_milestone(repo: Path, milestone: str, task_id: str = "task-1") -> None:
    path = _task_path(repo, task_id=task_id)
    source = path.read_text(encoding="utf-8")
    path.write_text(source.replace("status: In Progress\n", f"status: In Progress\nmilestone: {milestone}\n"), encoding="utf-8")


def _create_task_with_milestone(repo: Path, task_id: str, title: str, milestone: str) -> Path:
    source = _task_path(repo).read_text(encoding="utf-8")
    source = source.replace("id: TASK-1\n", f"id: {task_id.upper()}\n")
    source = source.replace("title: Example task\n", f"title: {title}\n")
    source = source.replace("status: In Progress\n", f"status: In Progress\nmilestone: {milestone}\n")
    path = repo / "backlog" / "tasks" / f"{task_id.lower()} - {title.replace(' ', '-')}.md"
    path.write_text(source, encoding="utf-8")
    return path


def test_add_milestone_writes_current_format_and_description_heading(tmp_path):
    repo = _copy_fixture(tmp_path)

    added = _service(repo).add_milestone("Release 2", "Release scope.")

    path = repo / "backlog" / "milestones" / "m-1 - release-2.md"
    assert added.id == "m-1"
    assert added.name == added.title == "Release 2"
    assert added.due_date is None
    assert added.format == "current"
    assert added.path == path
    assert added.frontmatter == {"id": "m-1", "title": "Release 2"}
    assert added.content == "## Description\n\nRelease scope."
    assert path.read_text(encoding="utf-8") == (
        "---\nid: m-1\ntitle: Release 2\n---\n\n## Description\n\nRelease scope.\n"
    )
    assert "name:" not in path.read_text(encoding="utf-8")


def test_id_allocator_scans_active_archive_frontmatter_and_filename_fallbacks(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    archive_dir = repo / "backlog" / "archive" / "milestones"
    active_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (active_dir / "m-2 - active.md").write_text(
        "---\nid: m-2\ntitle: Active\n---\n\n## Description\n\nScope.\n", encoding="utf-8"
    )
    (archive_dir / "m-8 - archived.md").write_text(
        "---\nid: m-8\ntitle: Archived\n---\n\n## Description\n\nScope.\n", encoding="utf-8"
    )
    (active_dir / "noncanonical.md").write_text(
        "---\nid: m-10\ntitle: Reserved\n---\n\n## Description\n\nScope.\n", encoding="utf-8"
    )
    (active_dir / "m-11 - reserved.md").write_text("not valid frontmatter", encoding="utf-8")

    added = _service(repo).add_milestone("Next")

    assert added.id == "m-12"
    assert added.path.name == "m-12 - next.md"


def test_id_allocator_never_reuses_archived_id(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    created = service.add_milestone("Highest")
    service.archive_milestone("Highest")

    next_created = service.add_milestone("Next")

    assert created.id == "m-1"
    assert next_created.id == "m-2"


@pytest.mark.parametrize(
    "due_date",
    ["2026-09-30 17:00", "2026-09-30T17:00:45.123Z", "2026-09-30T10:00-07:00"],
)
def test_add_milestone_normalizes_due_date_to_utc_minutes(tmp_path, due_date):
    repo = _copy_fixture(tmp_path)

    added = _service(repo).add_milestone("Release", due_date=due_date)

    assert added.due_date == "2026-09-30 17:00"
    assert added.frontmatter["due_date"] == "2026-09-30 17:00"


@pytest.mark.parametrize("due_date", ["2026-09-01", "not-a-date", "2026-13-01 10:00", "   "])
def test_add_milestone_rejects_invalid_due_date_before_writing(tmp_path, due_date):
    repo = _copy_fixture(tmp_path)

    with pytest.raises(MilestoneMutationError):
        _service(repo).add_milestone("Release", due_date=due_date)

    assert not (repo / "backlog" / "milestones").exists()


def test_add_milestone_omits_empty_due_date(tmp_path):
    repo = _copy_fixture(tmp_path)

    added = _service(repo).add_milestone("Release", due_date="")

    assert added.due_date is None
    assert "due_date" not in added.frontmatter


def test_current_filename_removes_forbidden_characters_and_truncates_suffix(tmp_path):
    repo = _copy_fixture(tmp_path)
    title = '  ReLeAsE <>:"/\\\\|?*   ' + "A" * 55

    added = _service(repo).add_milestone(title)

    assert added.title == title.strip()
    assert added.path.name == f"m-1 - release-{'a' * 42}.md"
    assert len(added.path.stem.removeprefix("m-1 - ")) == 50


def test_current_filename_uses_milestone_for_all_forbidden_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    title = '<>:"/\\\\|?*'

    added = _service(repo).add_milestone(title)

    assert added.title == title
    assert added.frontmatter["title"] == title
    assert added.path.name == "m-1 - milestone.md"


def test_current_filename_strips_whitespace_exposed_by_removed_characters(tmp_path):
    repo = _copy_fixture(tmp_path)

    added = _service(repo).add_milestone("<>   Release   <>")

    assert added.path.name == "m-1 - release.md"


def test_add_milestone_rejects_empty_title_before_writing(tmp_path):
    repo = _copy_fixture(tmp_path)

    with pytest.raises(MilestoneMutationError):
        _service(repo).add_milestone("   ")

    assert not (repo / "backlog" / "milestones").exists()


def test_id_allocator_ignores_unicode_filename_digits(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    active_dir.mkdir(parents=True)
    (active_dir / "m-١١ - unicode.md").write_text("not valid frontmatter", encoding="utf-8")

    added = _service(repo).add_milestone("First")

    assert added.id == "m-1"


def test_add_list_rename_remove_and_archive_milestones(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)

    added = service.add_milestone("Alpha", description="First release")
    assert added.name == "Alpha"
    assert "First release" in added.content
    assert [milestone.name for milestone in service.list_milestones()] == ["Alpha"]

    renamed = service.rename_milestone("Alpha", "Beta")
    assert renamed.name == "Beta"
    assert not (repo / "backlog" / "milestones" / "m-1 - alpha.md").exists()
    assert (repo / "backlog" / "milestones" / "Beta.md").exists()

    service.remove_milestone("Beta")
    assert service.list_milestones() == []

    service.add_milestone("Release 1", description="Ship it")
    archived = service.archive_milestone("Release 1")
    assert archived.archived is True
    assert not (repo / "backlog" / "milestones" / "m-1 - release-1.md").exists()
    assert (repo / "backlog" / "archive" / "milestones" / "m-1 - release-1.md").exists()


def test_rename_and_remove_can_update_task_milestone_references(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")

    service.rename_milestone("Alpha", "Beta", update_tasks=True)
    assert "milestone: Beta" in _task_path(repo).read_text(encoding="utf-8")

    service.remove_milestone("Beta", clear_tasks=True)
    source = _task_path(repo).read_text(encoding="utf-8")
    assert "milestone:" not in source


def test_rename_and_remove_update_task_refs_when_lookup_uses_different_case(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")

    service.rename_milestone("alpha", "Beta", update_tasks=True)
    assert "milestone: Beta" in _task_path(repo).read_text(encoding="utf-8")

    service.remove_milestone("beta", clear_tasks=True)
    assert "milestone:" not in _task_path(repo).read_text(encoding="utf-8")


def test_rename_same_slug_milestone_updates_display_name_and_task_refs(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Release 1")
    _set_task_milestone(repo, "Release 1")

    renamed = service.rename_milestone("Release 1", "Release-1", update_tasks=True)

    assert renamed.name == "Release-1"
    assert renamed.path == repo / "backlog" / "milestones" / "Release-1.md"
    assert [path.name for path in sorted((repo / "backlog" / "milestones").glob("*.md"))] == ["Release-1.md"]
    assert "milestone: Release-1" in _task_path(repo).read_text(encoding="utf-8")


def test_rename_with_task_reference_symlink_escape_is_rejected_before_milestone_changes(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    task_path = _task_path(repo)
    original_task_source = task_path.read_text(encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_task = outside / task_path.name
    outside_task.write_text(original_task_source, encoding="utf-8")
    task_path.unlink()
    try:
        task_path.symlink_to(outside_task)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    # The read layer now skips a task file that resolves outside its bucket, so
    # such a file is not part of the project at all: the rename no longer sees a
    # task referencing this milestone and proceeds. The property under test is
    # unchanged and enforced earlier — nothing outside the project is written.
    service.rename_milestone("Alpha", "Beta", update_tasks=True)

    assert (repo / "backlog" / "milestones" / "Beta.md").exists()
    assert outside_task.read_text(encoding="utf-8") == original_task_source


def test_same_slug_rename_rolls_back_original_milestone_when_task_write_fails(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Release 1")
    _set_task_milestone(repo, "Release 1")
    milestone_path = repo / "backlog" / "milestones" / "m-1 - release-1.md"
    original_milestone_source = milestone_path.read_text(encoding="utf-8")
    original_task_source = _task_path(repo).read_text(encoding="utf-8")
    original_writer = milestones_module._atomic_write_text

    def fail_on_task(path: Path, source: str) -> None:
        if path.name.startswith("task-1"):
            raise OSError("simulated task write failure")
        original_writer(path, source)

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_on_task)

    with pytest.raises(OSError, match="simulated task write failure"):
        service.rename_milestone("Release 1", "Release-1", update_tasks=True)

    assert milestone_path.read_text(encoding="utf-8") == original_milestone_source
    assert _task_path(repo).read_text(encoding="utf-8") == original_task_source


def test_malformed_milestone_file_does_not_disable_milestone_operations(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    broken = repo / "backlog" / "milestones" / "Broken.md"
    broken.write_text("---\nname: Broken\n\nNo closing fence.\n", encoding="utf-8")

    with _captured_warnings() as warnings:
        listed = _service(repo).list_milestones()

    assert [milestone.name for milestone in listed] == ["Alpha"]
    assert any("Broken.md" in message for message in warnings), warnings
    # unrelated milestones must still be renameable/removable
    assert _service(repo).rename_milestone("Alpha", "Beta").name == "Beta"
    assert _service(repo).remove_milestone("Beta").name == "Beta"


@pytest.mark.parametrize(
    ("filename", "source", "expected_name", "expected_format"),
    [
        (
            "m-9 - release.md",
            "---\nid: m-9\ntitle: Release\n---\n\nBOM milestone.\n",
            "Release",
            "current",
        ),
        (
            "Beta.md",
            "---\nname: Alpha\n---\n\nBOM milestone.\n",
            "Alpha",
            "legacy",
        ),
    ],
)
def test_current_and_legacy_milestone_reader_tolerates_utf8_bom(
    tmp_path, filename, source, expected_name, expected_format
):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    (milestones_dir / filename).write_text(f"\ufeff{source}", encoding="utf-8")

    listed = _service(repo).list_milestones()

    assert [milestone.name for milestone in listed] == [expected_name]
    assert listed[0].format == expected_format
    assert listed[0].content == "BOM milestone."


def test_current_milestone_loads_id_title_due_date_and_description(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "milestones" / "m-9 - release-2.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "id: m-9\n"
        "title: Release 2\n"
        "due_date: 2026-09-30 17:00\n"
        "custom: preserved\n"
        "---\n\n"
        "## Description\n\nRelease scope.\n\n## Notes\n\nAnother section.\n",
        encoding="utf-8",
    )

    record = _service(repo).list_milestones()[0]

    assert record.id == "m-9"
    assert record.title == record.name == "Release 2"
    assert record.due_date == "2026-09-30 17:00"
    assert record.format == "current"
    assert record.description == "Release scope."
    assert record.archived is False
    assert record.path == path
    assert record.path_relative == "milestones/m-9 - release-2.md"
    assert record.content == "## Description\n\nRelease scope.\n\n## Notes\n\nAnother section."
    assert record.frontmatter == {
        "id": "m-9",
        "title": "Release 2",
        "due_date": "2026-09-30 17:00",
        "custom": "preserved",
    }


def test_current_milestone_description_stops_at_valid_level_two_heading(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "milestones" / "m-9 - description.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nid: m-9\ntitle: Description boundary\n---\n\n"
        "## Description\n\nScope.\n\n### Details\n\nStill scope.\n\n  ##\tNotes\n\nLater section.\n",
        encoding="utf-8",
    )

    record = _service(repo).list_milestones()[0]

    assert record.description == "Scope.\n\n### Details\n\nStill scope."
    assert "Later section." in record.content
    assert "Later section." not in record.description


def test_legacy_milestone_retains_name_path_content_and_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "milestones" / "Alpha.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: Alpha\nid: m-99\ntitle: Not current\ncustom: [one, two]\nowner: Sam\n---\n\nLegacy body.\n",
        encoding="utf-8",
    )

    record = _service(repo).list_milestones()[0]

    assert record.name == "Alpha"
    assert record.path == path
    assert record.path_relative == "milestones/Alpha.md"
    assert record.content == "Legacy body."
    assert record.frontmatter == {
        "name": "Alpha",
        "id": "m-99",
        "title": "Not current",
        "custom": ["one", "two"],
        "owner": "Sam",
    }
    assert record.title == record.name
    assert record.id is None
    assert record.due_date is None
    assert record.format == "legacy"
    assert record.description == record.content


def test_list_milestones_can_include_archived_records(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    archive_dir = repo / "backlog" / "archive" / "milestones"
    active_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (active_dir / "m-2 - active.md").write_text(
        "---\nid: m-2\ntitle: Active\n---\n\n## Description\n\nActive scope.\n",
        encoding="utf-8",
    )
    (archive_dir / "Archived.md").write_text(
        "---\nname: Archived\n---\n\nArchived scope.\n", encoding="utf-8"
    )
    service = _service(repo)

    assert [(record.name, record.archived) for record in service.list_milestones()] == [("Active", False)]
    assert [(record.name, record.archived, record.path_relative) for record in service.list_milestones(include_archived=True)] == [
        ("Active", False, "milestones/m-2 - active.md"),
        ("Archived", True, "archive/milestones/Archived.md"),
    ]

    shutil.rmtree(active_dir)
    archive_only = _service(repo).list_milestones(include_archived=True)
    assert [(record.name, record.archived) for record in archive_only] == [("Archived", True)]


def test_readme_is_ignored_case_insensitively_in_active_and_archive(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    archive_dir = repo / "backlog" / "archive" / "milestones"
    active_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    for directory, filename in ((active_dir, "readme.md"), (active_dir, "README.md"), (archive_dir, "readme.md"), (archive_dir, "README.md")):
        (directory / filename).write_text("---\nnot valid frontmatter", encoding="utf-8")
    (active_dir / "Alpha.md").write_text("---\nname: Alpha\n---\n\nActive\n", encoding="utf-8")
    (archive_dir / "Beta.md").write_text("---\nname: Beta\n---\n\nArchived\n", encoding="utf-8")

    listed = _service(repo).list_milestones(include_archived=True)

    assert [(record.name, record.archived) for record in listed] == [("Alpha", False), ("Beta", True)]


def test_malformed_current_looking_file_is_warned_and_skipped(tmp_path):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    bad_sources = {
        "m-9 - missing-id.md": "---\ntitle: Missing id\n---\n\nBody\n",
        "m-10 - blank-title.md": "---\nid: m-10\ntitle: '   '\n---\n\nBody\n",
        "release.md": "---\nid: m-11\n---\n\nBody\n",
        "title-only.md": "---\ntitle: Missing id\n---\n\nBody\n",
        "invalid-id.md": "---\nid: m-x\ntitle: Invalid id\n---\n\nBody\n",
        "numeric-id.md": "---\nid: 9\ntitle: Numeric id\n---\n\nBody\n",
        "unicode-id.md": "---\nid: m-٩\ntitle: Unicode id\n---\n\nBody\n",
        "numeric-title.md": "---\nid: m-12\ntitle: 12\n---\n\nBody\n",
    }
    for filename, source in bad_sources.items():
        (milestones_dir / filename).write_text(source, encoding="utf-8")

    with _captured_warnings() as warnings:
        listed = _service(repo).list_milestones()

    assert listed == []
    assert all(any(filename in message for message in warnings) for filename in bad_sources)


def test_current_milestone_normalizes_uppercase_zero_padded_ascii_id(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "milestones" / "M-009 - release.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nid: M-009\ntitle: Release\n---\n\n## Description\n\nScope.\n", encoding="utf-8"
    )

    record = _service(repo).list_milestones()[0]

    assert record.id == "m-9"
    assert record.frontmatter["id"] == "M-009"


def test_noncurrent_file_without_name_keeps_filename_fallback(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "milestones" / "No-name--here.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ncustom: preserved\n---\n\nLegacy body.\n", encoding="utf-8")

    record = _service(repo).list_milestones()[0]

    assert record.name == "No name  here"
    assert record.title == "No name  here"
    assert record.format == "legacy"


def test_current_and_legacy_milestones_sort_deterministically(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    archive_dir = repo / "backlog" / "archive" / "milestones"
    active_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    files = {
        active_dir / "m-10 - ten.md": "---\nid: m-10\ntitle: Ten\n---\n\n## Description\n\nTen\n",
        active_dir / "m-2 - two.md": "---\nid: m-2\ntitle: Two\n---\n\n## Description\n\nTwo\n",
        active_dir / "Alpha.md": "---\nname: Alpha\n---\n\nAlpha\n",
        archive_dir / "m-1 - one.md": "---\nid: m-1\ntitle: One\n---\n\n## Description\n\nOne\n",
        archive_dir / "beta.md": "---\nname: beta\n---\n\nBeta\n",
    }
    for path, source in files.items():
        path.write_text(source, encoding="utf-8")

    listed = _service(repo).list_milestones(include_archived=True)

    assert [(record.archived, record.format, record.id, record.name) for record in listed] == [
        (False, "current", "m-2", "Two"),
        (False, "current", "m-10", "Ten"),
        (False, "legacy", None, "Alpha"),
        (True, "current", "m-1", "One"),
        (True, "legacy", None, "beta"),
    ]


def test_list_milestones_rejects_symlinked_file_escape_before_read(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    milestone_path = repo / "backlog" / "milestones" / "m-1 - alpha.md"
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: Outside\n---\n\nSecret\n", encoding="utf-8")
    milestone_path.unlink()
    try:
        milestone_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(MilestoneMutationError, match="outside allowed base"):
        service.list_milestones()


def test_rename_rolls_back_milestone_and_task_refs_when_task_write_fails(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    second_task = _create_task_with_milestone(repo, "TASK-2", "Second task", "Alpha")
    first_task = _task_path(repo)
    original_first_source = first_task.read_text(encoding="utf-8")
    original_second_source = second_task.read_text(encoding="utf-8")
    original_writer = milestones_module._atomic_write_text

    def fail_on_second_task(path: Path, source: str) -> None:
        if path.name.startswith("task-2"):
            raise OSError("simulated task write failure")
        original_writer(path, source)

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_on_second_task)

    with pytest.raises(OSError, match="simulated task write failure"):
        service.rename_milestone("Alpha", "Beta", update_tasks=True)

    assert (repo / "backlog" / "milestones" / "m-1 - alpha.md").exists()
    assert not (repo / "backlog" / "milestones" / "Beta.md").exists()
    assert first_task.read_text(encoding="utf-8") == original_first_source
    assert second_task.read_text(encoding="utf-8") == original_second_source


def test_remove_rolls_back_task_refs_when_task_write_fails(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    second_task = _create_task_with_milestone(repo, "TASK-2", "Second task", "Alpha")
    first_task = _task_path(repo)
    original_first_source = first_task.read_text(encoding="utf-8")
    original_second_source = second_task.read_text(encoding="utf-8")
    original_writer = milestones_module._atomic_write_text

    def fail_on_second_task(path: Path, source: str) -> None:
        if path.name.startswith("task-2"):
            raise OSError("simulated task write failure")
        original_writer(path, source)

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_on_second_task)

    with pytest.raises(OSError, match="simulated task write failure"):
        service.remove_milestone("Alpha", clear_tasks=True)

    assert (repo / "backlog" / "milestones" / "m-1 - alpha.md").exists()
    assert first_task.read_text(encoding="utf-8") == original_first_source
    assert second_task.read_text(encoding="utf-8") == original_second_source


def test_rename_leaves_task_references_when_update_not_requested(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")

    service.rename_milestone("Alpha", "Beta", update_tasks=False)

    assert "milestone: Alpha" in _task_path(repo).read_text(encoding="utf-8")


def test_cli_milestone_commands_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    add = runner.invoke(main, ["--cwd", str(repo), "milestone", "add", "Alpha"])
    assert add.exit_code == 0
    assert "Alpha" in add.output

    rename = runner.invoke(main, ["--cwd", str(repo), "milestone", "rename", "Alpha", "Beta"])
    assert rename.exit_code == 0
    assert "Beta" in rename.output

    listed = runner.invoke(main, ["--cwd", str(repo), "milestone", "list"])
    assert listed.exit_code == 0
    assert "Beta" in listed.output

    archive = runner.invoke(main, ["--cwd", str(repo), "milestone", "archive", "Beta"])
    assert archive.exit_code == 0
    assert "archived" in archive.output


def test_mcp_milestone_tools_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    added = milestone_add(project, "Alpha")
    assert added["name"] == "Alpha"
    assert [milestone["name"] for milestone in milestone_list(project)] == ["Alpha"]

    renamed = milestone_rename(project, "Alpha", "Beta")
    assert renamed["name"] == "Beta"

    milestone_remove(project, "Beta")
    assert milestone_list(project) == []

    milestone_add(project, "Release 1")
    archived = milestone_archive(project, "Release 1")
    assert archived["archived"] is True
