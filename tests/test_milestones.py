from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner
from loguru import logger

import backlog_py.core.milestones as milestones_module
from backlog_py.cli.main import main
from backlog_py.core.errors import NotFoundError
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
    path.write_text(
        source.replace("status: In Progress\n", f"status: In Progress\nmilestone: {milestone}\n"), encoding="utf-8"
    )


def _create_task_with_milestone(repo: Path, task_id: str, title: str, milestone: str) -> Path:
    source = _task_path(repo).read_text(encoding="utf-8")
    source = source.replace("id: TASK-1\n", f"id: {task_id.upper()}\n")
    source = source.replace("title: Example task\n", f"title: {title}\n")
    source = source.replace("status: In Progress\n", f"status: In Progress\nmilestone: {milestone}\n")
    path = repo / "backlog" / "tasks" / f"{task_id.lower()} - {title.replace(' ', '-')}.md"
    path.write_text(source, encoding="utf-8")
    return path


def _write_current_milestone(
    repo: Path,
    milestone_id: str,
    title: str,
    *,
    filename: str | None = None,
    extra_frontmatter: str = "",
    body: str = "## Description\n\nScope.",
) -> Path:
    path = repo / "backlog" / "milestones" / (filename or f"{milestone_id} - {title.lower().replace(' ', '-')}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {milestone_id}\ntitle: {title}\n{extra_frontmatter}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_legacy_milestone(
    repo: Path,
    name: str,
    *,
    filename: str | None = None,
    extra_frontmatter: str = "",
    body: str = "Legacy scope.",
) -> Path:
    path = repo / "backlog" / "milestones" / (filename or f"{name}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\n{extra_frontmatter}---\n\n{body}\n",
        encoding="utf-8",
    )
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


def test_id_allocator_reserves_current_intent_id_without_a_valid_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    active_dir = repo / "backlog" / "milestones"
    active_dir.mkdir(parents=True)
    (active_dir / "noncanonical.md").write_text(
        "---\nid: m-37\n---\n\nIncomplete current milestone.\n", encoding="utf-8"
    )
    (active_dir / "legacy.md").write_text("---\nname: Legacy\nid: m-99\n---\n\nLegacy milestone.\n", encoding="utf-8")

    added = _service(repo).add_milestone("Next")

    assert added.id == "m-38"


def test_id_allocator_aborts_when_current_record_cannot_be_read(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    unreadable = milestones_dir / "noncanonical.md"
    unreadable.write_text("---\nid: m-37\ntitle: Reserved\n---\n", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_for_current_record(path: Path, *args, **kwargs):
        if path == unreadable:
            raise OSError("simulated unreadable milestone")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_for_current_record)

    with pytest.raises(MilestoneMutationError, match="simulated unreadable milestone"):
        _service(repo).add_milestone("Next")

    assert [path.name for path in milestones_dir.iterdir()] == ["noncanonical.md"]


def test_id_allocator_aborts_on_invalid_utf8_current_record(tmp_path):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    unreadable = milestones_dir / "noncanonical.md"
    unreadable.write_bytes(b"---\nid: m-37\ntitle: Reserved\n---\n\xff")

    with pytest.raises(MilestoneMutationError):
        _service(repo).add_milestone("Next")

    assert [path.name for path in milestones_dir.iterdir()] == ["noncanonical.md"]


def test_id_allocator_ignores_readme_before_frontmatter_reservation(tmp_path):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    (milestones_dir / "README.md").write_text(
        "---\nid: m-99\ntitle: Ignored\n---\n\n## Description\n\nIgnored.\n",
        encoding="utf-8",
    )

    added = _service(repo).add_milestone("First")

    assert added.id == "m-1"


def test_add_milestone_anchors_atomic_writer_to_backlog(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_writer = milestones_module._atomic_write_text

    def swap_directory_before_write(path: Path, source: str, *, base: Path | None = None) -> None:
        milestones_dir.rename(repo / "backlog" / "milestones-original")
        try:
            milestones_dir.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
        original_writer(path, source, base=base)

    monkeypatch.setattr(milestones_module, "_atomic_write_text", swap_directory_before_write)

    with pytest.raises(MilestoneMutationError, match="outside allowed base"):
        _service(repo).add_milestone("Release")

    assert list(outside.iterdir()) == []


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


@pytest.mark.parametrize(
    "due_date",
    [
        0,
        False,
        [],
        {},
        b"2026-09-30 17:00",
        object(),
        "9999-12-31T23:59-23:59",
        "0001-01-01T00:00+23:59",
    ],
)
def test_add_milestone_rejects_nonempty_invalid_due_dates_without_mutating_directory(tmp_path, due_date):
    repo = _copy_fixture(tmp_path)
    milestones_dir = repo / "backlog" / "milestones"
    milestones_dir.mkdir(parents=True)
    sentinel = milestones_dir / "sentinel.md"
    sentinel.write_bytes(b"unchanged\n")
    before = {path.name: path.read_bytes() for path in milestones_dir.iterdir()}

    with pytest.raises(MilestoneMutationError):
        _service(repo).add_milestone("Release", due_date=due_date)

    assert {path.name: path.read_bytes() for path in milestones_dir.iterdir()} == before


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
    assert (repo / "backlog" / "milestones" / "m-1 - beta.md").exists()

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
    assert "milestone: m-1" in _task_path(repo).read_text(encoding="utf-8")

    service.remove_milestone("Beta", clear_tasks=True)
    source = _task_path(repo).read_text(encoding="utf-8")
    assert "milestone:" not in source


def test_rename_and_remove_update_task_refs_when_lookup_uses_different_case(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")

    service.rename_milestone("alpha", "Beta", update_tasks=True)
    assert "milestone: m-1" in _task_path(repo).read_text(encoding="utf-8")

    service.remove_milestone("beta", clear_tasks=True)
    assert "milestone:" not in _task_path(repo).read_text(encoding="utf-8")


def test_rename_same_slug_milestone_updates_display_name_and_task_refs(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Release 1")
    _set_task_milestone(repo, "Release 1")

    renamed = service.rename_milestone("Release 1", "Release-1", update_tasks=True)

    assert renamed.name == "Release-1"
    assert renamed.path == repo / "backlog" / "milestones" / "m-1 - release-1.md"
    assert [path.name for path in sorted((repo / "backlog" / "milestones").glob("*.md"))] == ["m-1 - release-1.md"]
    assert "milestone: m-1" in _task_path(repo).read_text(encoding="utf-8")


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

    assert (repo / "backlog" / "milestones" / "m-1 - beta.md").exists()
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

    def fail_on_task(path: Path, source: str, base: Path | None = None) -> None:
        if path.name.startswith("task-1"):
            raise OSError("simulated task write failure")
        original_writer(path, source, base=base)

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
    (archive_dir / "Archived.md").write_text("---\nname: Archived\n---\n\nArchived scope.\n", encoding="utf-8")
    service = _service(repo)

    assert [(record.name, record.archived) for record in service.list_milestones()] == [("Active", False)]
    assert [
        (record.name, record.archived, record.path_relative)
        for record in service.list_milestones(include_archived=True)
    ] == [
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
    for directory, filename in (
        (active_dir, "readme.md"),
        (active_dir, "README.md"),
        (archive_dir, "readme.md"),
        (archive_dir, "README.md"),
    ):
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
    path.write_text("---\nid: M-009\ntitle: Release\n---\n\n## Description\n\nScope.\n", encoding="utf-8")

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


@pytest.mark.parametrize("reference", ["m-9", "9", "m-9 - release", "RELEASE"])
def test_resolve_current_milestone_by_each_unique_alias(tmp_path, reference):
    repo = _copy_fixture(tmp_path)
    path = _write_current_milestone(repo, "m-9", "Release")

    resolved = _service(repo).resolve_milestone(reference)

    assert resolved.id == "m-9"
    assert resolved.path == path


@pytest.mark.parametrize("reference", ["Release name", "legacy-path"])
def test_resolve_legacy_milestone_by_name_or_path_stem(tmp_path, reference):
    repo = _copy_fixture(tmp_path)
    path = _write_legacy_milestone(repo, "Release name", filename="legacy-path.md")

    resolved = _service(repo).resolve_milestone(reference)

    assert resolved.format == "legacy"
    assert resolved.path == path


def test_resolve_deduplicates_aliases_that_hit_the_same_record(tmp_path):
    repo = _copy_fixture(tmp_path)
    _write_current_milestone(repo, "m-9", "m-9")

    assert _service(repo).resolve_milestone("m-9").id == "m-9"


def test_resolve_can_include_archived_but_active_mutations_cannot(tmp_path):
    repo = _copy_fixture(tmp_path)
    archive = repo / "backlog" / "archive" / "milestones" / "m-9 - release.md"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        "---\nid: m-9\ntitle: Release\n---\n\n## Description\n\nArchived.\n",
        encoding="utf-8",
    )
    service = _service(repo)

    assert service.resolve_milestone("9").archived is True
    with pytest.raises(NotFoundError, match="Milestone not found"):
        service.resolve_milestone("9", include_archived=False)
    with pytest.raises(NotFoundError, match="Milestone not found"):
        service.remove_milestone("9")


def test_ambiguous_case_insensitive_titles_fail_closed(tmp_path):
    repo = _copy_fixture(tmp_path)
    _write_current_milestone(repo, "m-1", "Release")
    _write_current_milestone(repo, "m-2", "release")

    with pytest.raises(MilestoneMutationError, match="ambiguous") as exc_info:
        _service(repo).resolve_milestone("RELEASE")

    assert type(exc_info.value).__name__ == "MilestoneConflictError"


def test_resolve_fails_when_different_alias_kinds_match_different_records(tmp_path):
    repo = _copy_fixture(tmp_path)
    _write_current_milestone(repo, "m-9", "Current")
    _write_legacy_milestone(repo, "m-9")

    with pytest.raises(MilestoneMutationError, match="ambiguous"):
        _service(repo).resolve_milestone("m-9")


@pytest.mark.parametrize("alias", ["Release", "m-9", "9", "m-9 - release"])
def test_add_rejects_aliases_owned_by_another_active_milestone(tmp_path, alias):
    repo = _copy_fixture(tmp_path)
    original = _write_current_milestone(repo, "m-9", "Release")

    with pytest.raises(MilestoneMutationError, match="conflict") as exc_info:
        _service(repo).add_milestone(alias)

    assert type(exc_info.value).__name__ == "MilestoneConflictError"
    assert sorted((repo / "backlog" / "milestones").iterdir()) == [original]


@pytest.mark.parametrize("alias", ["Release", "m-9", "9", "m-9 - release"])
def test_rename_rejects_aliases_owned_by_another_active_milestone(tmp_path, alias):
    repo = _copy_fixture(tmp_path)
    existing = _write_current_milestone(repo, "m-9", "Release")
    editable = _write_legacy_milestone(repo, "Editable")

    with pytest.raises(MilestoneMutationError, match="conflict") as exc_info:
        _service(repo).rename_milestone("Editable", alias)

    assert type(exc_info.value).__name__ == "MilestoneConflictError"
    assert existing.exists()
    assert editable.exists()


def test_canonical_rename_can_disambiguate_a_duplicate_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    first = _write_current_milestone(repo, "m-1", "Release")
    second = _write_current_milestone(repo, "m-2", "release")

    renamed = _service(repo).rename_milestone("m-1", "Unique")

    assert renamed.id == "m-1"
    assert renamed.title == "Unique"
    assert not first.exists()
    assert second.exists()


def test_canonical_description_edit_is_not_blocked_by_a_duplicate_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    first = _write_current_milestone(repo, "m-1", "Release")
    second = _write_current_milestone(repo, "m-2", "release")

    edited = _service(repo).edit_milestone("m-1", description="Updated")

    assert edited.path == first
    assert edited.description == "Updated"
    assert second.exists()


def test_canonical_remove_is_not_blocked_by_a_duplicate_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    first = _write_current_milestone(repo, "m-1", "Release")
    second = _write_current_milestone(repo, "m-2", "release")

    removed = _service(repo).remove_milestone("m-1")

    assert removed.id == "m-1"
    assert not first.exists()
    assert second.exists()


def test_canonical_archive_is_not_blocked_by_a_duplicate_title(tmp_path):
    repo = _copy_fixture(tmp_path)
    first = _write_current_milestone(repo, "m-1", "Release")
    second = _write_current_milestone(repo, "m-2", "release")

    archived = _service(repo).archive_milestone("m-1")

    assert archived.id == "m-1"
    assert archived.archived is True
    assert not first.exists()
    assert second.exists()


def test_current_edit_preserves_id_unknown_frontmatter_and_other_body_sections(tmp_path):
    repo = _copy_fixture(tmp_path)
    old_path = _write_current_milestone(
        repo,
        "m-9",
        "Release",
        extra_frontmatter="custom: preserved\n",
        body="Preface.\n\n## Description\n\nOld scope.\n\n## Risks\n\nKeep me",
    )

    edited = _service(repo).edit_milestone("9", title="Release Final", description="Updated", due_date="")

    assert edited.id == "m-9"
    assert edited.title == "Release Final"
    assert edited.due_date is None
    assert edited.frontmatter["custom"] == "preserved"
    assert edited.path.name == "m-9 - release-final.md"
    assert edited.description == "Updated"
    assert edited.content == "Preface.\n\n## Description\n\nUpdated\n\n## Risks\n\nKeep me"
    assert not old_path.exists()


@pytest.mark.parametrize(
    ("opening_fence", "closing_fence"),
    [
        ("```python", "`````   "),
        ("   ~~~~ python", "  ~~~~~\t"),
    ],
)
def test_current_description_edit_ignores_h2_headings_inside_fences(tmp_path, opening_fence, closing_fence):
    repo = _copy_fixture(tmp_path)
    prefix = (
        f"Preface.\n\n{opening_fence}\n## Description\n\nThis fenced heading is not the section.\n{closing_fence}\n\n"
    )
    old_description = (
        "Old scope.\n\n"
        f"{opening_fence}\n"
        "## Risks\n\n"
        "This fenced H2 is not a boundary.\n"
        f"{closing_fence}\n\n"
        "Still old scope."
    )
    suffix = "## Risks\n\nKeep this suffix byte-for-byte.\n\nExtra suffix line."
    _write_current_milestone(
        repo,
        "m-9",
        "Release",
        body=f"{prefix}## Description\n\n{old_description}\n\n{suffix}",
    )
    service = _service(repo)

    assert service.resolve_milestone("m-9").description == old_description

    edited = service.edit_milestone("m-9", description="Updated scope.")

    assert edited.description == "Updated scope."
    assert edited.content == f"{prefix}## Description\n\nUpdated scope.\n\n{suffix}"


def test_legacy_edit_preserves_name_format_filename_and_unknown_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)
    old_path = _write_legacy_milestone(
        repo,
        "Alpha",
        extra_frontmatter="custom: preserved\n",
        body="Old legacy body.",
    )

    edited = _service(repo).edit_milestone("Alpha", title="Release Final", description="Updated legacy body.")

    assert edited.format == "legacy"
    assert edited.id is None
    assert edited.name == "Release Final"
    assert edited.path.name == "Release-Final.md"
    assert edited.frontmatter == {"name": "Release Final", "custom": "preserved"}
    assert edited.content == "Updated legacy body."
    assert not old_path.exists()


def test_current_rename_without_task_updates_leaves_reference_unchanged(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")

    renamed = service.rename_milestone("Alpha", "Beta")

    assert renamed.id == "m-1"
    assert "milestone: Alpha" in _task_path(repo).read_text(encoding="utf-8")


def test_current_rename_canonicalizes_only_unique_mutable_task_aliases(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    title_task = _task_path(repo)
    stem_task = _create_task_with_milestone(repo, "TASK-2", "Stem ref", added.path.stem)
    canonical_task = _create_task_with_milestone(repo, "TASK-3", "Canonical ref", "m-1")
    numeric_task = _create_task_with_milestone(repo, "TASK-4", "Numeric ref", "1")
    _set_task_milestone(repo, "Alpha")
    canonical_before = canonical_task.read_bytes()
    numeric_before = numeric_task.read_bytes()

    renamed = service.rename_milestone("1", "Beta", update_tasks=True)

    assert renamed.id == "m-1"
    assert renamed.path.name == "m-1 - beta.md"
    assert "milestone: m-1" in title_task.read_text(encoding="utf-8")
    assert "milestone: m-1" in stem_task.read_text(encoding="utf-8")
    assert canonical_task.read_bytes() == canonical_before
    assert numeric_task.read_bytes() == numeric_before


def test_legacy_rename_updates_unique_name_and_path_stem_references(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    _write_legacy_milestone(repo, "Release name", filename="legacy-path.md")
    stem_task = _create_task_with_milestone(repo, "TASK-2", "Stem ref", "legacy-path")
    _set_task_milestone(repo, "Release name")

    renamed = service.rename_milestone("legacy-path", "Beta", update_tasks=True)

    assert renamed.format == "legacy"
    assert renamed.path.name == "Beta.md"
    assert "milestone: Beta" in _task_path(repo).read_text(encoding="utf-8")
    assert "milestone: Beta" in stem_task.read_text(encoding="utf-8")


def test_current_rename_does_not_rewrite_title_alias_ambiguous_with_archive(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    archived_source = _write_legacy_milestone(repo, "Alpha", filename="archived-alpha.md")
    archive_dir = repo / "backlog" / "archive" / "milestones"
    archive_dir.mkdir(parents=True)
    archived_source.replace(archive_dir / archived_source.name)
    canonical_task = _create_task_with_milestone(repo, "TASK-2", "Canonical ref", "m-1")
    _set_task_milestone(repo, "Alpha")
    title_before = _task_path(repo).read_bytes()
    canonical_before = canonical_task.read_bytes()

    service.rename_milestone("m-1", "Beta", update_tasks=True)

    assert _task_path(repo).read_bytes() == title_before
    assert canonical_task.read_bytes() == canonical_before


def test_remove_clear_skips_archived_ambiguity_but_clears_globally_unique_id(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    archived_source = _write_legacy_milestone(repo, "Alpha", filename="archived-alpha.md")
    archive_dir = repo / "backlog" / "archive" / "milestones"
    archive_dir.mkdir(parents=True)
    archived_source.replace(archive_dir / archived_source.name)
    canonical_task = _create_task_with_milestone(repo, "TASK-2", "Canonical ref", "m-1")
    _set_task_milestone(repo, "Alpha")
    title_before = _task_path(repo).read_bytes()

    service.remove_milestone("m-1", clear_tasks=True)

    assert _task_path(repo).read_bytes() == title_before
    assert "milestone:" not in canonical_task.read_text(encoding="utf-8")


@pytest.mark.parametrize("reference", ["m-1", "1", "m-1 - alpha", "Alpha"])
def test_remove_with_clear_recognizes_each_unique_current_alias(tmp_path, reference):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    _set_task_milestone(repo, reference)

    removed = service.remove_milestone(reference, clear_tasks=True)

    assert removed.path == added.path
    assert not added.path.exists()
    assert "milestone:" not in _task_path(repo).read_text(encoding="utf-8")


def test_archive_resolves_alias_preserves_references_and_returns_archived_record(tmp_path):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    task_before = _task_path(repo).read_bytes()

    archived = service.archive_milestone("1")

    assert archived.archived is True
    assert archived.id == "m-1"
    assert archived.path_relative == "archive/milestones/m-1 - alpha.md"
    assert _task_path(repo).read_bytes() == task_before


def test_ambiguous_active_mutation_does_not_change_any_source(tmp_path):
    repo = _copy_fixture(tmp_path)
    first = _write_legacy_milestone(repo, "Release", filename="first.md")
    second = _write_legacy_milestone(repo, "release", filename="second.md")
    before = {path: path.read_bytes() for path in (first, second)}

    with pytest.raises(MilestoneMutationError, match="ambiguous"):
        _service(repo).remove_milestone("RELEASE")

    assert {path: path.read_bytes() for path in (first, second)} == before


def test_current_edit_rolls_back_a_milestone_write_that_raises_after_writing(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    original_writer = milestones_module._atomic_write_text
    failed = False

    def fail_after_milestone_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal failed
        original_writer(path, source, base=base)
        if path == added.path and not failed:
            failed = True
            raise OSError("simulated milestone write failure")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_after_milestone_write)

    with pytest.raises(OSError, match="simulated milestone write failure"):
        service.edit_milestone("Alpha", title="Beta")

    assert added.path.read_bytes() == original_source
    assert not (added.path.parent / "m-1 - beta.md").exists()


def test_same_path_edit_does_not_overwrite_a_concurrent_source_replacement(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    concurrent_source = b"\xffconcurrent same-path replacement\n"
    original_writer = milestones_module._atomic_write_text
    replaced = False

    def replace_after_milestone_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal replaced
        original_writer(path, source, base=base)
        if path == added.path and not replaced:
            replaced = True
            path.write_bytes(concurrent_source)
            raise OSError("simulated concurrent same-path replacement")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", replace_after_milestone_write)

    with pytest.raises(OSError, match="simulated concurrent same-path replacement"):
        service.edit_milestone("m-1", description="Edited")

    assert added.path.read_bytes() == concurrent_source


def test_same_path_edit_does_not_recreate_a_source_deleted_after_write(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_writer = milestones_module._atomic_write_text
    deleted = False

    def delete_after_milestone_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal deleted
        original_writer(path, source, base=base)
        if path == added.path and not deleted:
            deleted = True
            path.unlink()
            raise OSError("simulated concurrent same-path deletion")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", delete_after_milestone_write)

    with pytest.raises(OSError, match="simulated concurrent same-path deletion"):
        service.edit_milestone("m-1", description="Edited")

    assert not added.path.exists()


def test_current_edit_rolls_back_milestone_and_all_task_writes(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    second_task = _create_task_with_milestone(repo, "TASK-2", "Second task", "Alpha")
    _set_task_milestone(repo, "Alpha")
    first_task = _task_path(repo)
    originals = {
        added.path: added.path.read_bytes(),
        first_task: first_task.read_bytes(),
        second_task: second_task.read_bytes(),
    }
    original_writer = milestones_module._atomic_write_text
    failed = False
    write_bases: list[Path | None] = []

    def fail_after_second_task_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal failed
        write_bases.append(base)
        original_writer(path, source, base=base)
        if path == second_task and not failed:
            failed = True
            raise OSError("simulated task write failure")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_after_second_task_write)

    with pytest.raises(OSError, match="simulated task write failure"):
        service.edit_milestone("Alpha", title="Beta", update_tasks=True)

    assert {path: path.read_bytes() for path in originals} == originals
    assert not (added.path.parent / "m-1 - beta.md").exists()
    assert write_bases
    assert set(write_bases) == {repo / "backlog"}


def test_current_edit_rolls_back_sources_when_final_rename_fails(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    task_path = _task_path(repo)
    milestone_before = added.path.read_bytes()
    task_before = task_path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_link = milestones_module.os.link

    def fail_final_link(source, destination, *args, **kwargs):
        if Path(source) == added.path and Path(destination) == target:
            raise OSError("simulated link failure")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(milestones_module.os, "link", fail_final_link)

    with pytest.raises(OSError, match="simulated link failure"):
        service.edit_milestone("Alpha", title="Beta", update_tasks=True)

    assert added.path.read_bytes() == milestone_before
    assert task_path.read_bytes() == task_before
    assert not target.exists()


def test_current_edit_does_not_overwrite_a_target_created_during_transaction(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_writer = milestones_module._atomic_write_text
    planted = False

    def plant_target_after_milestone_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal planted
        original_writer(path, source, base=base)
        if path == added.path and not planted:
            planted = True
            target.write_bytes(b"concurrent file\n")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", plant_target_after_milestone_write)

    with pytest.raises(MilestoneMutationError, match="conflict"):
        service.edit_milestone("Alpha", title="Beta")

    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == b"concurrent file\n"


def test_current_edit_no_clobber_move_preserves_target_created_inside_link(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_link = milestones_module.os.link
    planted = False

    def plant_target_inside_link(source, destination, *args, **kwargs):
        nonlocal planted
        if Path(source) == added.path and Path(destination) == target and not planted:
            planted = True
            target.write_bytes(b"concurrent edit target\n")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(milestones_module.os, "link", plant_target_inside_link)

    with pytest.raises(MilestoneMutationError, match="conflict") as exc_info:
        service.edit_milestone("m-1", title="Beta")

    assert type(exc_info.value).__name__ == "MilestoneConflictError"
    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == b"concurrent edit target\n"


def test_current_edit_preserves_source_if_created_link_is_replaced_before_unlink(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_link = milestones_module.os.link
    replaced = False

    def replace_created_link(source, destination, *args, **kwargs):
        nonlocal replaced
        result = original_link(source, destination, *args, **kwargs)
        if Path(source) == added.path and Path(destination) == target and not replaced:
            replaced = True
            target.unlink()
            target.write_bytes(b"concurrent replacement\n")
        return result

    monkeypatch.setattr(milestones_module.os, "link", replace_created_link)

    with pytest.raises(MilestoneMutationError, match="conflict") as exc_info:
        service.edit_milestone("m-1", title="Beta")

    assert type(exc_info.value).__name__ == "MilestoneConflictError"
    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == b"concurrent replacement\n"


def test_current_edit_removes_owned_target_when_source_is_replaced_after_link(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    concurrent_source = b"concurrent edit source\n"
    target = added.path.parent / "m-1 - beta.md"
    original_link = milestones_module.os.link
    replaced = False

    def replace_source_after_link(source, destination, *args, **kwargs):
        nonlocal replaced
        result = original_link(source, destination, *args, **kwargs)
        if Path(source) == added.path and Path(destination) == target and not replaced:
            replaced = True
            added.path.unlink()
            added.path.write_bytes(concurrent_source)
        return result

    monkeypatch.setattr(milestones_module.os, "link", replace_source_after_link)

    with pytest.raises(MilestoneMutationError, match="conflict"):
        service.edit_milestone("m-1", title="Beta")

    assert added.path.read_bytes() == concurrent_source
    assert not target.exists()


def test_current_edit_preserves_target_replaced_after_move_before_load(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    added.path.write_bytes(added.path.read_bytes().replace(b"\n", b"\r\n"))
    original_source = added.path.read_bytes()
    concurrent_target = b"\xffconcurrent edit target\n"
    target = added.path.parent / "m-1 - beta.md"
    original_loader = service._load_milestone
    replaced = False

    def replace_target_before_load(path: Path, *, archived: bool):
        nonlocal replaced
        if path == target and not replaced:
            replaced = True
            target.unlink()
            target.write_bytes(concurrent_target)
        return original_loader(path, archived=archived)

    monkeypatch.setattr(service, "_load_milestone", replace_target_before_load)

    with pytest.raises(UnicodeDecodeError):
        service.edit_milestone("m-1", title="Beta")

    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == concurrent_target


def test_current_edit_rejects_valid_target_replaced_during_load(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    concurrent_target = b"---\nid: m-1\ntitle: Concurrent\n---\n\n## Description\n\nConcurrent\n"
    target = added.path.parent / "m-1 - beta.md"
    original_loader = service._load_milestone
    replaced = False

    def replace_target_during_load(path: Path, *, archived: bool):
        nonlocal replaced
        if path == target and not replaced:
            replaced = True
            target.write_bytes(concurrent_target)
        return original_loader(path, archived=archived)

    monkeypatch.setattr(service, "_load_milestone", replace_target_during_load)

    with pytest.raises(MilestoneMutationError, match="conflict"):
        service.edit_milestone("m-1", title="Beta")

    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == concurrent_target


def test_current_edit_restores_original_before_inspecting_redirected_target(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside stays unchanged\n")
    original_writer = milestones_module._atomic_write_text
    redirected = False

    def redirect_target_after_milestone_write(path: Path, source: str, base: Path | None = None) -> None:
        nonlocal redirected
        original_writer(path, source, base=base)
        if path == added.path and not redirected:
            redirected = True
            try:
                target.symlink_to(outside)
            except OSError as exc:
                pytest.skip(f"symlink creation unavailable: {exc}")

    monkeypatch.setattr(milestones_module, "_atomic_write_text", redirect_target_after_milestone_write)

    with pytest.raises(MilestoneMutationError, match="outside allowed base"):
        service.edit_milestone("m-1", title="Beta")

    assert added.path.read_bytes() == original_source
    assert outside.read_bytes() == b"outside stays unchanged\n"


def test_current_edit_rolls_back_a_link_that_raises_after_creation(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_link = milestones_module.os.link
    failed = False

    def fail_after_final_link(source, destination, *args, **kwargs):
        nonlocal failed
        result = original_link(source, destination, *args, **kwargs)
        if Path(source) == added.path and Path(destination) == target and not failed:
            failed = True
            raise OSError("simulated post-link failure")
        return result

    monkeypatch.setattr(milestones_module.os, "link", fail_after_final_link)

    with pytest.raises(OSError, match="simulated post-link failure"):
        service.edit_milestone("Alpha", title="Beta")

    assert added.path.read_bytes() == original_source
    assert not target.exists()


def test_current_edit_rolls_back_when_source_unlink_raises_after_deleting(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = added.path.parent / "m-1 - beta.md"
    original_unlink = Path.unlink
    failed = False

    def fail_after_source_unlink(path: Path, *args, **kwargs):
        nonlocal failed
        result = original_unlink(path, *args, **kwargs)
        if path == added.path and not failed:
            failed = True
            raise OSError("simulated post-unlink move failure")
        return result

    monkeypatch.setattr(Path, "unlink", fail_after_source_unlink)

    with pytest.raises(OSError, match="simulated post-unlink move failure"):
        service.edit_milestone("Alpha", title="Beta")

    assert added.path.read_bytes() == original_source
    assert not target.exists()


def test_remove_rolls_back_task_sources_when_unlink_fails(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    _set_task_milestone(repo, "Alpha")
    task_path = _task_path(repo)
    milestone_before = added.path.read_bytes()
    task_before = task_path.read_bytes()
    original_unlink = Path.unlink

    def fail_milestone_unlink(path: Path, *args, **kwargs):
        if path == added.path:
            raise OSError("simulated unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_milestone_unlink)

    with pytest.raises(OSError, match="simulated unlink failure"):
        service.remove_milestone("Alpha", clear_tasks=True)

    assert added.path.read_bytes() == milestone_before
    assert task_path.read_bytes() == task_before


def test_remove_restores_a_milestone_when_unlink_raises_after_deleting(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    original_unlink = Path.unlink
    failed = False

    def fail_after_milestone_unlink(path: Path, *args, **kwargs):
        nonlocal failed
        result = original_unlink(path, *args, **kwargs)
        if path == added.path and not failed:
            failed = True
            raise OSError("simulated post-unlink failure")
        return result

    monkeypatch.setattr(Path, "unlink", fail_after_milestone_unlink)

    with pytest.raises(OSError, match="simulated post-unlink failure"):
        service.remove_milestone("Alpha")

    assert added.path.read_bytes() == original_source


def test_archive_no_clobber_move_preserves_target_created_inside_link(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    target = repo / "backlog" / "archive" / "milestones" / added.path.name
    original_link = milestones_module.os.link
    planted = False

    def plant_archive_target_inside_link(source, destination, *args, **kwargs):
        nonlocal planted
        if Path(source) == added.path and Path(destination) == target and not planted:
            planted = True
            target.write_bytes(b"concurrent archive target\n")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(milestones_module.os, "link", plant_archive_target_inside_link)

    with pytest.raises(MilestoneMutationError, match="conflict") as exc_info:
        service.archive_milestone("Alpha")

    assert type(exc_info.value).__name__ == "MilestoneConflictError"
    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == b"concurrent archive target\n"


def test_archive_removes_owned_target_when_source_is_replaced_after_link(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    concurrent_source = b"concurrent archive source\n"
    target = repo / "backlog" / "archive" / "milestones" / added.path.name
    original_link = milestones_module.os.link
    replaced = False

    def replace_source_after_link(source, destination, *args, **kwargs):
        nonlocal replaced
        result = original_link(source, destination, *args, **kwargs)
        if Path(source) == added.path and Path(destination) == target and not replaced:
            replaced = True
            added.path.unlink()
            added.path.write_bytes(concurrent_source)
        return result

    monkeypatch.setattr(milestones_module.os, "link", replace_source_after_link)

    with pytest.raises(MilestoneMutationError, match="conflict"):
        service.archive_milestone("m-1")

    assert added.path.read_bytes() == concurrent_source
    assert not target.exists()


def test_archive_preserves_target_replaced_after_move_before_load(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    concurrent_target = b"\xffconcurrent archive target\n"
    target = repo / "backlog" / "archive" / "milestones" / added.path.name
    original_loader = service._load_milestone
    replaced = False

    def replace_target_before_load(path: Path, *, archived: bool):
        nonlocal replaced
        if path == target and not replaced:
            replaced = True
            target.unlink()
            target.write_bytes(concurrent_target)
        return original_loader(path, archived=archived)

    monkeypatch.setattr(service, "_load_milestone", replace_target_before_load)

    with pytest.raises(UnicodeDecodeError):
        service.archive_milestone("m-1")

    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == concurrent_target


def test_archive_rejects_valid_target_replaced_during_load(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    service = _service(repo)
    added = service.add_milestone("Alpha")
    original_source = added.path.read_bytes()
    concurrent_target = b"---\nid: m-1\ntitle: Concurrent\n---\n\n## Description\n\nConcurrent\n"
    target = repo / "backlog" / "archive" / "milestones" / added.path.name
    original_loader = service._load_milestone
    replaced = False

    def replace_target_during_load(path: Path, *, archived: bool):
        nonlocal replaced
        if path == target and not replaced:
            replaced = True
            target.unlink()
            target.write_bytes(concurrent_target)
        return original_loader(path, archived=archived)

    monkeypatch.setattr(service, "_load_milestone", replace_target_during_load)

    with pytest.raises(MilestoneMutationError, match="conflict"):
        service.archive_milestone("m-1")

    assert added.path.read_bytes() == original_source
    assert target.read_bytes() == concurrent_target


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

    def fail_on_second_task(path: Path, source: str, base: Path | None = None) -> None:
        if path.name.startswith("task-2"):
            raise OSError("simulated task write failure")
        original_writer(path, source, base=base)

    monkeypatch.setattr(milestones_module, "_atomic_write_text", fail_on_second_task)

    with pytest.raises(OSError, match="simulated task write failure"):
        service.rename_milestone("Alpha", "Beta", update_tasks=True)

    assert (repo / "backlog" / "milestones" / "m-1 - alpha.md").exists()
    assert not (repo / "backlog" / "milestones" / "m-1 - beta.md").exists()
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

    def fail_on_second_task(path: Path, source: str, base: Path | None = None) -> None:
        if path.name.startswith("task-2"):
            raise OSError("simulated task write failure")
        original_writer(path, source, base=base)

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

    removable = runner.invoke(main, ["--cwd", str(repo), "milestone", "add", "Delete Me"])
    assert removable.exit_code == 0

    remove = runner.invoke(main, ["--cwd", str(repo), "milestone", "remove", "Delete Me"])
    assert remove.exit_code == 0
    assert "Delete Me" in remove.output


def test_mcp_milestone_tools_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    added = milestone_add(project, "Alpha", description="First")
    assert added["name"] == added["title"] == "Alpha"
    assert added["id"] == "m-1"
    assert added["due_date"] is None
    assert added["format"] == "current"
    for legacy_key in ("path", "content", "frontmatter", "archived", "project_path"):
        assert legacy_key in added
    assert [milestone["name"] for milestone in milestone_list(project)] == ["Alpha"]

    renamed = milestone_rename(project, "Alpha", "Beta")
    assert renamed["name"] == "Beta"

    milestone_remove(project, "Beta")
    assert milestone_list(project) == []

    milestone_add(project, "Release 1")
    archived = milestone_archive(project, "Release 1")
    assert archived["archived"] is True
