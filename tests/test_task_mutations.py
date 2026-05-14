from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.repository import MutableRepository, TaskMutationError
from backlog_py.mcp.tools import task_create, task_edit
from backlog_py.storage.config import replace_definition_of_done_defaults
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _repository(repo: Path) -> MutableRepository:
    return MutableRepository.from_path(repo)


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _task_file(repo: Path, task_id: str = "task-1") -> Path:
    matches = sorted((repo / "backlog" / "tasks").glob(f"{task_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def _snapshot_tasks(repo: Path) -> dict[Path, str]:
    task_dir = repo / "backlog" / "tasks"
    return {
        path.relative_to(task_dir): path.read_text(encoding="utf-8")
        for path in sorted(task_dir.glob("*.md"))
    }


def _snapshot_backlog_markdown(repo: Path) -> dict[Path, str]:
    backlog_dir = repo / "backlog"
    return {
        path.relative_to(backlog_dir): path.read_text(encoding="utf-8")
        for path in sorted(backlog_dir.rglob("*.md"))
    }


def test_create_task_writes_valid_task_in_fixture_repo(tmp_path):
    repo = _copy_fixture(tmp_path)

    task = _repository(repo).create_task(
        title="New safe mutation task",
        task_id="TASK-2",
        description="Created through the safe mutation core.",
        notes="Initial implementation note.",
        acceptance_criteria=["Task can be viewed"],
        definition_of_done=["Tests pass"],
    )

    assert task.id == "TASK-2"
    assert task.title == "New safe mutation task"
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "id: TASK-2" in written
    assert "Created through the safe mutation core." in written
    assert "Initial implementation note." in written
    assert "- [ ] #1 Task can be viewed" in written
    assert "- [ ] #1 Tests pass" in written
    assert _repository(repo).get_task("TASK-2").description == "Created through the safe mutation core."


def test_create_task_writes_metadata_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)

    task = _repository(repo).create_task(
        title="Metadata task",
        task_id="TASK-2",
        assignees=["codex", "reviewer"],
        labels=["parity", "metadata"],
        priority="high",
        milestone="Release 1",
        ordinal=1000,
        references=["https://example.com/issue/1", "src/api.py,docs/design.md"],
        documentation=["https://docs.example.com", "docs/spec.md"],
        modified_files=["src/api.py", "src/ui.py"],
    )

    assert task.parsed.frontmatter["assignee"] == ["codex", "reviewer"]
    assert task.parsed.frontmatter["labels"] == ["parity", "metadata"]
    assert task.parsed.frontmatter["priority"] == "high"
    assert task.parsed.frontmatter["milestone"] == "Release 1"
    assert task.parsed.frontmatter["ordinal"] == 1000
    assert task.parsed.frontmatter["references"] == ["https://example.com/issue/1", "src/api.py", "docs/design.md"]
    assert task.parsed.frontmatter["documentation"] == ["https://docs.example.com", "docs/spec.md"]
    assert task.parsed.frontmatter["modified_files"] == ["src/api.py", "src/ui.py"]


def test_create_task_with_parent_generates_child_id_and_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)

    first_child = repository.create_task(title="First child task", parent_task_id="1")
    second_child = repository.create_task(title="Second child task", parent_task_id="TASK-1")

    assert first_child.id == "TASK-1.1"
    assert first_child.parsed.frontmatter["parent_task_id"] == "TASK-1"
    assert second_child.id == "TASK-1.2"
    assert second_child.parsed.frontmatter["parent_task_id"] == "TASK-1"
    assert (repo / "backlog" / "tasks" / "task-1.1 - First-child-task.md").is_file()
    assert (repo / "backlog" / "tasks" / "task-1.2 - Second-child-task.md").is_file()


def test_create_task_writes_plan_section_after_acceptance_criteria(tmp_path):
    repo = _copy_fixture(tmp_path)

    task = _repository(repo).create_task(
        title="Planned task",
        task_id="TASK-2",
        acceptance_criteria=["Plan is recorded"],
        plan="1. Research current behavior\n2. Implement focused patch",
    )

    source = task.raw_source
    assert "## Implementation Plan" in source
    assert "<!-- SECTION:PLAN:BEGIN -->" in source
    assert "1. Research current behavior" in task.parsed.sections["PLAN"].content
    assert source.index("## Acceptance Criteria") < source.index("## Implementation Plan")
    assert source.index("## Implementation Plan") < source.index("## Implementation Notes")


def test_edit_task_updates_owned_sections_and_checklists_without_rewriting_unowned_body(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)
    before = task_path.read_text(encoding="utf-8")

    edited = _repository(repo).edit_task(
        "TASK-1",
        description="Edited description.",
        notes="Replacement implementation note.",
        append_notes="- Added implementation note.",
        final_summary="Finalized through safe edit.",
        check_ac=[2],
        check_dod=[2],
    )

    after = task_path.read_text(encoding="utf-8")
    assert edited.description == "Edited description."
    assert "Unowned body content before acceptance criteria must be preserved." in after
    assert "Trailing unowned body content must also round trip." in after
    assert "custom_field: preserve-me" in after
    assert "- Keep unknown body text stable." not in after
    assert "Replacement implementation note." in after
    assert "- Added implementation note." in after
    assert "Finalized through safe edit." in after
    assert "- [x] #2 Preserve incomplete acceptance criteria raw line" in after
    assert "- [x] #2 Verification recorded" in after
    assert before != after


def test_edit_task_updates_title_and_renames_task_file_without_rewriting_unowned_body(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)

    edited = _repository(repo).edit_task("TASK-1", title="Renamed task")

    renamed_path = _task_file(repo)
    written = renamed_path.read_text(encoding="utf-8")
    assert edited.title == "Renamed task"
    assert renamed_path.name == "task-1 - Renamed-task.md"
    assert not task_path.exists()
    assert "title: Renamed task" in written
    assert "Unowned body content before acceptance criteria must be preserved." in written
    assert "Trailing unowned body content must also round trip." in written


def test_edit_task_updates_metadata_frontmatter_without_rewriting_unowned_body(tmp_path):
    repo = _copy_fixture(tmp_path)

    edited = _repository(repo).edit_task(
        "TASK-1",
        assignees=["maintainer"],
        labels=["updated", "parity"],
        priority="medium",
        milestone="Release 2",
        ordinal=2000,
        references=["ref-a.py,ref-b.py"],
        documentation=["doc-a.md"],
        modified_files=["src/edited.py,tests/test_edited.py"],
    )

    written = _task_file(repo).read_text(encoding="utf-8")
    assert edited.parsed.frontmatter["assignee"] == ["maintainer"]
    assert edited.parsed.frontmatter["labels"] == ["updated", "parity"]
    assert edited.parsed.frontmatter["priority"] == "medium"
    assert edited.parsed.frontmatter["milestone"] == "Release 2"
    assert edited.parsed.frontmatter["ordinal"] == 2000
    assert edited.parsed.frontmatter["references"] == ["ref-a.py", "ref-b.py"]
    assert edited.parsed.frontmatter["documentation"] == ["doc-a.md"]
    assert edited.parsed.frontmatter["modified_files"] == ["src/edited.py", "tests/test_edited.py"]
    assert "custom_field: preserve-me" in written
    assert "nested_unknown:" in written
    assert "Trailing unowned body content must also round trip." in written


def test_edit_task_can_clear_milestone_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    repository.create_task(title="Milestoned task", task_id="TASK-2", milestone="Release 1")

    edited = repository.edit_task("TASK-2", clear_milestone=True)

    assert "milestone" not in edited.parsed.frontmatter
    assert "milestone:" not in _task_file(repo, "task-2").read_text(encoding="utf-8")


def test_task_ordinal_rejects_invalid_values_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Invalid ordinal"):
        repository.create_task(title="Bad ordinal", task_id="TASK-2", ordinal=-1)

    with pytest.raises(TaskMutationError, match="Invalid ordinal"):
        repository.edit_task("TASK-1", ordinal="later")

    assert _snapshot_tasks(repo) == before


def test_edit_task_adds_and_removes_reference_and_documentation_frontmatter(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    repository.create_task(
        title="Referenced task",
        task_id="TASK-2",
        references=["ref-a.py", "ref-b.py"],
        documentation=["doc-a.md", "doc-b.md"],
    )

    edited = repository.edit_task(
        "TASK-2",
        add_references=["ref-c.py"],
        remove_references=["ref-a.py"],
        add_documentation=["doc-c.md"],
        remove_documentation=["doc-b.md"],
    )

    assert edited.parsed.frontmatter["references"] == ["ref-b.py", "ref-c.py"]
    assert edited.parsed.frontmatter["documentation"] == ["doc-a.md", "doc-c.md"]


def test_edit_task_sets_appends_and_clears_plan_without_rewriting_unowned_body(tmp_path):
    repo = _copy_fixture(tmp_path)

    edited = _repository(repo).edit_task("TASK-1", plan="1. Inspect\n2. Patch")

    written = _task_file(repo).read_text(encoding="utf-8")
    assert edited.parsed.sections["PLAN"].content.strip() == "1. Inspect\n2. Patch"
    assert written.index("## Acceptance Criteria") < written.index("## Implementation Plan")
    assert written.index("## Implementation Plan") < written.index("## Implementation Notes")
    assert "custom_field: preserve-me" in written
    assert "Trailing unowned body content must also round trip." in written

    appended = _repository(repo).edit_task("TASK-1", append_plan=["3. Verify"])

    assert appended.parsed.sections["PLAN"].content.strip() == "1. Inspect\n2. Patch\n3. Verify"

    cleared = _repository(repo).edit_task("TASK-1", clear_plan=True)

    assert "PLAN" not in cleared.parsed.sections
    assert "## Implementation Plan" not in _task_file(repo).read_text(encoding="utf-8")


def test_edit_task_can_uncheck_acceptance_criteria_and_definition_of_done(tmp_path):
    repo = _copy_fixture(tmp_path)

    _repository(repo).edit_task("TASK-1", uncheck_ac=[1], uncheck_dod=[1])

    after = _task_file(repo).read_text(encoding="utf-8")
    assert "- [ ] #1 Preserve completed acceptance criteria raw line" in after
    assert "- [ ] #1 Tests written" in after


def test_edit_task_can_add_and_remove_acceptance_criteria_and_definition_of_done(tmp_path):
    repo = _copy_fixture(tmp_path)

    _repository(repo).edit_task(
        "TASK-1",
        acceptance_criteria_add=["New acceptance criterion"],
        definition_of_done_add=["New verification item"],
    )

    after_add = _task_file(repo).read_text(encoding="utf-8")
    assert "- [ ] #4 New acceptance criterion" in after_add
    assert "- [ ] #3 New verification item" in after_add

    _repository(repo).edit_task("TASK-1", remove_ac=[2], remove_dod=[1])

    after_remove = _task_file(repo).read_text(encoding="utf-8")
    assert "Preserve incomplete acceptance criteria raw line" not in after_remove
    assert "Tests written" not in after_remove
    assert "New acceptance criterion" in after_remove
    assert "New verification item" in after_remove
    assert "Trailing unowned body content must also round trip." in after_remove


def test_edit_task_can_append_and_clear_final_summary(tmp_path):
    repo = _copy_fixture(tmp_path)

    _repository(repo).edit_task("TASK-1", append_final_summary=["Added final detail."])

    after_append = _task_file(repo).read_text(encoding="utf-8")
    assert "No final summary yet." in after_append
    assert "Added final detail." in after_append

    _repository(repo).edit_task("TASK-1", clear_final_summary=True)

    after_clear = _task_file(repo).read_text(encoding="utf-8")
    assert "No final summary yet." not in after_clear
    assert "Added final detail." not in after_clear
    assert "<!-- SECTION:FINAL_SUMMARY:BEGIN -->\n\n<!-- SECTION:FINAL_SUMMARY:END -->" in after_clear


def test_archive_task_moves_active_file_to_archive_without_rewrite(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)
    original = task_path.read_text(encoding="utf-8")

    archived = _repository(repo).archive_task("TASK-1")

    archived_path = repo / "backlog" / "archive" / "tasks" / task_path.name
    assert archived.id == "TASK-1"
    assert archived.path == archived_path
    assert not task_path.exists()
    assert archived_path.read_text(encoding="utf-8") == original
    assert _repository(repo).list_tasks() == []


def test_complete_task_moves_active_file_to_completed_without_rewrite(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    task_path = _task_file(repo)
    repository.edit_task("TASK-1", status="Done")
    before_complete = task_path.read_text(encoding="utf-8")

    completed = repository.complete_task("TASK-1")

    completed_path = repo / "backlog" / "completed" / task_path.name
    assert completed.id == "TASK-1"
    assert completed.path == completed_path
    assert not task_path.exists()
    assert completed_path.read_text(encoding="utf-8") == before_complete
    assert repository.list_tasks() == []
    assert [task.id for task in repository.search_tasks("fixture")] == ["TASK-1"]


def test_complete_task_rejects_non_done_task_before_move(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)

    with pytest.raises(TaskMutationError, match='Set status to "Done"'):
        _repository(repo).complete_task("TASK-1")

    assert task_path.is_file()
    assert not (repo / "backlog" / "completed" / task_path.name).exists()


def test_complete_task_rejects_symlinked_completed_directory_escape(tmp_path):
    repo = _copy_fixture(tmp_path)
    _repository(repo).edit_task("TASK-1", status="Done")
    outside = tmp_path / "outside"
    outside.mkdir()
    completed_root = repo / "backlog" / "completed"
    completed_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskMutationError, match="outside allowed base"):
        _repository(repo).complete_task("TASK-1")

    assert _task_file(repo).is_file()
    assert list(outside.iterdir()) == []


def test_archive_task_rejects_symlinked_archive_directory_escape(tmp_path):
    repo = _copy_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    archive_root = repo / "backlog" / "archive"
    archive_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskMutationError, match="outside allowed base"):
        _repository(repo).archive_task("TASK-1")

    assert _task_file(repo).is_file()
    assert list(outside.iterdir()) == []


def test_task_dependencies_accept_numeric_shorthand_and_comma_lists(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)

    second = repository.create_task(
        title="Numeric dependency task",
        task_id="TASK-2",
        dependencies=["1"],
    )
    assert second.parsed.frontmatter["dependencies"] == ["TASK-1"]

    third = repository.create_task(
        title="Comma dependency task",
        task_id="TASK-3",
        dependencies=["1,task-2"],
    )
    assert third.parsed.frontmatter["dependencies"] == ["TASK-1", "TASK-2"]

    edited = repository.edit_task("TASK-3", dependencies=["task-1"])
    assert edited.parsed.frontmatter["dependencies"] == ["TASK-1"]


def test_list_tasks_orders_by_ordinal_before_task_id(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    repository.create_task(title="Second ordered task", task_id="TASK-2", ordinal=20)
    repository.create_task(title="First ordered task", task_id="TASK-3", ordinal=10)

    assert [task.id for task in repository.list_tasks()] == ["TASK-3", "TASK-2", "TASK-1"]


def test_edit_task_preserves_crlf_when_toggling_checklists(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)
    original = task_path.read_text(encoding="utf-8")
    task_path.write_text(original.replace("\n", "\r\n"), encoding="utf-8")

    _repository(repo).edit_task("TASK-1", check_ac=[2])

    after = task_path.read_bytes().decode("utf-8")
    assert "- [x] #2 Preserve incomplete acceptance criteria raw line\r\n" in after
    assert "\n" not in after.replace("\r\n", "")


def test_invalid_checklist_index_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="AC checklist index 99"):
        _repository(repo).edit_task("TASK-1", check_ac=[99])

    assert _snapshot_tasks(repo) == before


def test_invalid_dod_checklist_index_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="DOD checklist index 99"):
        _repository(repo).edit_task("TASK-1", check_dod=[99])

    assert _snapshot_tasks(repo) == before


def test_invalid_remove_checklist_index_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="AC checklist index 99"):
        _repository(repo).edit_task("TASK-1", remove_ac=[99])

    assert _snapshot_tasks(repo) == before


def test_duplicate_task_id_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="already exists"):
        _repository(repo).create_task(title="Duplicate", task_id="TASK-1")

    assert _snapshot_tasks(repo) == before


def test_circular_dependencies_are_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    repository = _repository(repo)
    repository.create_task(title="Child", task_id="TASK-2", dependencies=["TASK-1"])
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Circular dependency"):
        repository.edit_task("TASK-1", dependencies=["TASK-2"])

    assert _snapshot_tasks(repo) == before


def test_nonexistent_dependencies_are_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Dependency not found: TASK-99"):
        _repository(repo).create_task(title="Missing dependency", task_id="TASK-2", dependencies=["TASK-99"])

    assert _snapshot_tasks(repo) == before


def test_edit_nonexistent_dependencies_are_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Dependency not found: TASK-99"):
        _repository(repo).edit_task("TASK-1", dependencies=["TASK-99"])

    assert _snapshot_tasks(repo) == before


def test_unknown_status_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Unknown status: Mystery"):
        _repository(repo).create_task(title="Bad status", task_id="TASK-2", status="Mystery")

    assert _snapshot_tasks(repo) == before


def test_edit_unknown_status_is_rejected_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)

    with pytest.raises(TaskMutationError, match="Unknown status: Mystery"):
        _repository(repo).edit_task("TASK-1", status="Mystery")

    assert _snapshot_tasks(repo) == before


def test_path_traversal_task_id_is_rejected_without_partial_file(tmp_path):
    repo = _copy_fixture(tmp_path)
    before = _snapshot_tasks(repo)
    before_backlog_markdown = _snapshot_backlog_markdown(repo)

    with pytest.raises(TaskMutationError, match="Invalid task id"):
        _repository(repo).create_task(title="Escape", task_id="../TASK-9")

    assert _snapshot_tasks(repo) == before
    assert _snapshot_backlog_markdown(repo) == before_backlog_markdown
    assert not (repo / "backlog" / "TASK-9.md").exists()


def test_symlinked_task_directory_escape_is_rejected_without_outside_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_dir = repo / "backlog" / "tasks"
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(task_dir)
    try:
        task_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(TaskMutationError, match="outside allowed base"):
        _repository(repo).create_task(title="Escaped", task_id="TASK-2")

    assert list(outside.iterdir()) == []


def test_symlinked_task_file_escape_is_rejected_without_outside_write(tmp_path):
    repo = _copy_fixture(tmp_path)
    task_path = _task_file(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_task = outside / task_path.name
    original = task_path.read_text(encoding="utf-8")
    outside_task.write_text(original, encoding="utf-8")
    task_path.unlink()
    try:
        task_path.symlink_to(outside_task)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(TaskMutationError, match="outside allowed base"):
        _repository(repo).edit_task("TASK-1", description="Escaped edit.")

    assert outside_task.read_text(encoding="utf-8") == original


def test_on_status_change_is_disabled_by_default(tmp_path):
    repo = _copy_fixture(tmp_path)

    with pytest.raises(TaskMutationError, match="onStatusChange is disabled"):
        _repository(repo).edit_task("TASK-1", status="Done", on_status_change=True)


def test_cli_task_create_and_edit_use_safe_core(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI mutation task",
            "--id",
            "TASK-2",
            "--description",
            "Created from CLI.",
            "--notes",
            "Initial CLI note.",
            "--plain",
        ],
    )
    assert create.exit_code == 0
    assert "TASK-2 [To Do] CLI mutation task" in create.output

    edit = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-2",
            "--title",
            "CLI renamed task",
            "--description",
            "Edited from CLI.",
            "--notes",
            "CLI replacement note.",
            "--append-notes",
            "- CLI note.",
            "--plan",
            "CLI plan.",
            "--append-plan",
            "CLI appended plan.",
            "--ref",
            "ref-a.py,ref-b.py",
            "--doc",
            "doc-a.md",
            "--doc",
            "doc-b.md",
            "--milestone",
            "Release CLI",
            "--ordinal",
            "2000",
            "--modified-file",
            "src/api.py",
            "--modified-file",
            "tests/test_api.py",
            "--final-summary",
            "CLI final summary.",
            "--append-final-summary",
            "CLI appended final summary.",
            "--dep",
            "1",
            "-a",
            "maintainer",
            "-l",
            "edited,metadata",
            "--priority",
            "medium",
            "--plain",
        ],
    )
    assert edit.exit_code == 0
    assert "TASK-2 [To Do] CLI renamed task" in edit.output
    assert "Milestone: Release CLI" in edit.output
    assert "References: ref-a.py, ref-b.py" in edit.output
    assert "Documentation: doc-a.md, doc-b.md" in edit.output
    assert "Modified files: src/api.py, tests/test_api.py" in edit.output
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "title: CLI renamed task" in written
    assert "assignee:\n- maintainer" in written
    assert "labels:\n- edited\n- metadata" in written
    assert "priority: medium" in written
    assert "milestone: Release CLI" in written
    assert "ordinal: 2000" in written
    assert "references:\n- ref-a.py\n- ref-b.py" in written
    assert "documentation:\n- doc-a.md\n- doc-b.md" in written
    assert "modified_files:\n- src/api.py\n- tests/test_api.py" in written
    assert "CLI plan." in written
    assert "CLI appended plan." in written
    assert "Edited from CLI." in written
    assert "dependencies:\n- TASK-1" in written
    assert "Initial CLI note." not in written
    assert "CLI replacement note." in written
    assert "- CLI note." in written
    assert "CLI final summary." in written
    assert "CLI appended final summary." in written

    clear_milestone = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-2",
            "--clear-milestone",
            "--plain",
        ],
    )
    assert clear_milestone.exit_code == 0
    assert "Milestone:" not in clear_milestone.output
    milestone_cleared = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "milestone:" not in milestone_cleared

    clear = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-2",
            "--clear-final-summary",
            "--plain",
        ],
    )
    assert clear.exit_code == 0
    cleared = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "CLI final summary." not in cleared
    assert "CLI appended final summary." not in cleared

    clear_plan = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-2",
            "--clear-plan",
            "--plain",
        ],
    )
    assert clear_plan.exit_code == 0
    plan_cleared = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "CLI plan." not in plan_cleared
    assert "CLI appended plan." not in plan_cleared

    uncheck = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-1",
            "--uncheck-ac",
            "1",
            "--uncheck-dod",
            "1",
            "--plain",
        ],
    )
    assert uncheck.exit_code == 0
    task_one = _task_file(repo).read_text(encoding="utf-8")
    assert "- [ ] #1 Preserve completed acceptance criteria raw line" in task_one
    assert "- [ ] #1 Tests written" in task_one

    edit_checklists = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "edit",
            "TASK-1",
            "--ac",
            "CLI edit AC",
            "--dod",
            "CLI edit DoD",
            "--remove-ac",
            "2",
            "--remove-dod",
            "1",
            "--plain",
        ],
    )
    assert edit_checklists.exit_code == 0
    checklist_written = _task_file(repo).read_text(encoding="utf-8")
    assert "Preserve incomplete acceptance criteria raw line" not in checklist_written
    assert "Tests written" not in checklist_written
    assert "CLI edit AC" in checklist_written
    assert "CLI edit DoD" in checklist_written


def test_cli_task_archive_uses_safe_core(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    active_path = repo / "backlog" / "tasks" / "task-1 - Example-task.md"

    archive = runner.invoke(main, ["--cwd", str(repo), "task", "archive", "TASK-1", "--plain"])

    assert archive.exit_code == 0
    assert "TASK-1 [In Progress] Example task archived" in archive.output
    assert not active_path.exists()
    assert (repo / "backlog" / "archive" / "tasks" / "task-1 - Example-task.md").is_file()


def test_cli_cleanup_moves_done_tasks_to_completed(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    repository = _repository(repo)
    repository.create_task(title="Done cleanup task", task_id="TASK-2", status="Done")
    repository.create_task(title="Active task", task_id="TASK-3", status="To Do")

    cleanup = runner.invoke(main, ["--cwd", str(repo), "cleanup"])

    assert cleanup.exit_code == 0
    assert "Moved 1 completed task to backlog/completed." in cleanup.output
    assert not (repo / "backlog" / "tasks" / "task-2 - Done-cleanup-task.md").exists()
    assert (repo / "backlog" / "completed" / "task-2 - Done-cleanup-task.md").is_file()
    assert (repo / "backlog" / "tasks" / "task-1 - Example-task.md").is_file()
    assert (repo / "backlog" / "tasks" / "task-3 - Active-task.md").is_file()


def test_cli_task_create_accepts_checklists_defaults_and_dependencies(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    runner = CliRunner()

    replace_definition_of_done_defaults(project, ["Project default"])

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI full create task",
            "--id",
            "TASK-2",
            "--description",
            "Created with richer CLI fields.",
            "--plan",
            "CLI create plan.",
            "--final-summary",
            "CLI create final summary.",
            "--ref",
            "https://example.com/issue/1",
            "--ref",
            "src/api.py,docs/design.md",
            "--doc",
            "https://docs.example.com",
            "--doc",
            "docs/spec.md",
            "--milestone",
            "Release Create",
            "--ordinal",
            "1000",
            "--modified-file",
            "src/create.py,tests/test_create.py",
            "-a",
            "codex",
            "-a",
            "reviewer",
            "-l",
            "cli,metadata",
            "--priority",
            "high",
            "--ac",
            "CLI AC one",
            "--ac",
            "CLI AC two",
            "--dod",
            "CLI specific DoD",
            "--dep",
            "1",
            "--plain",
        ],
    )

    assert create.exit_code == 0
    assert "TASK-2 [To Do] CLI full create task" in create.output
    assert "Milestone: Release Create" in create.output
    assert "References: https://example.com/issue/1, src/api.py, docs/design.md" in create.output
    assert "Documentation: https://docs.example.com, docs/spec.md" in create.output
    assert "Modified files: src/create.py, tests/test_create.py" in create.output
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "assignee:\n- codex\n- reviewer" in written
    assert "labels:\n- cli\n- metadata" in written
    assert "priority: high" in written
    assert "milestone: Release Create" in written
    assert "ordinal: 1000" in written
    assert "references:\n- https://example.com/issue/1\n- src/api.py\n- docs/design.md" in written
    assert "documentation:\n- https://docs.example.com\n- docs/spec.md" in written
    assert "modified_files:\n- src/create.py\n- tests/test_create.py" in written
    assert "## Implementation Plan" in written
    assert "CLI create plan." in written
    assert "CLI create final summary." in written
    assert "dependencies:\n- TASK-1" in written
    assert "- [ ] #1 CLI AC one" in written
    assert "- [ ] #2 CLI AC two" in written
    assert "- [ ] #1 Project default" in written
    assert "- [ ] #2 CLI specific DoD" in written

    comma = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI comma dependency task",
            "--id",
            "TASK-3",
            "--dep",
            "1,TASK-2",
            "--plain",
        ],
    )
    assert comma.exit_code == 0
    comma_written = _task_file(repo, "task-3").read_text(encoding="utf-8")
    assert "dependencies:\n- TASK-1\n- TASK-2" in comma_written

    explicit = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI explicit DoD task",
            "--id",
            "TASK-4",
            "--definition-of-done",
            "Explicit CLI DoD",
            "--plain",
        ],
    )
    assert explicit.exit_code == 0
    explicit_written = _task_file(repo, "task-4").read_text(encoding="utf-8")
    assert "- [ ] #1 Explicit CLI DoD" in explicit_written
    assert "Project default" not in explicit_written

    disabled = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI disabled defaults task",
            "--id",
            "TASK-5",
            "--disable-definition-of-done-defaults",
            "--plain",
        ],
    )
    assert disabled.exit_code == 0
    disabled_written = _task_file(repo, "task-5").read_text(encoding="utf-8")
    assert "Project default" not in disabled_written


def test_cli_task_create_accepts_upstream_short_aliases(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)
    runner = CliRunner()

    replace_definition_of_done_defaults(project, ["Project default"])

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "task",
            "create",
            "CLI alias task",
            "--id",
            "TASK-2",
            "-d",
            "Created with upstream aliases.",
            "-s",
            "In Progress",
            "--no-dod-defaults",
            "--dod",
            "Alias-specific DoD",
            "--plain",
        ],
    )

    assert create.exit_code == 0
    assert "TASK-2 [In Progress] CLI alias task" in create.output
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "Created with upstream aliases." in written
    assert "status: In Progress" in written
    assert "- [ ] #1 Alias-specific DoD" in written
    assert "Project default" not in written


def test_cli_task_create_and_edit_reject_invalid_ordinal(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()
    before = _snapshot_tasks(repo)

    create = runner.invoke(
        main,
        ["--cwd", str(repo), "task", "create", "Bad ordinal", "--id", "TASK-2", "--ordinal", "-1", "--plain"],
    )

    edit = runner.invoke(
        main,
        ["--cwd", str(repo), "task", "edit", "TASK-1", "--ordinal", "later", "--plain"],
    )

    assert create.exit_code != 0
    assert "Invalid ordinal: -1. Must be a non-negative number." in create.output
    assert edit.exit_code != 0
    assert "Invalid ordinal: later. Must be a non-negative number." in edit.output
    assert _snapshot_tasks(repo) == before


def test_mcp_task_create_and_edit_use_safe_core(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    created = task_create(
        project,
        title="MCP mutation task",
        description="Created from MCP.",
        notes="Initial MCP note.",
        acceptanceCriteria=["MCP create works"],
        assignee=["codex"],
        labels=["mcp", "metadata"],
        priority="high",
        milestone="Release MCP",
        ordinal=1000,
        implementationPlan="MCP create plan.",
        references=["ref1.py", "ref2.py"],
        documentation=["doc1.md", "doc2.md"],
        modifiedFiles=["src/mcp.py", "tests/test_mcp.py"],
        finalSummary="MCP create final summary.",
    )
    assert created["id"] == "TASK-2"
    assert created["description"] == "Created from MCP."
    assert created["milestone"] == "Release MCP"
    assert created["ordinal"] == 1000
    assert created["modifiedFiles"] == ["src/mcp.py", "tests/test_mcp.py"]
    assert "MCP create final summary." in _task_file(repo, "task-2").read_text(encoding="utf-8")

    edited = task_edit(
        project,
        task_id="TASK-2",
        title="MCP renamed task",
        notes="MCP replacement note.",
        appendNotes="- MCP note.",
        finalSummary="MCP final summary.",
        finalSummaryAppend=["MCP appended final summary."],
        acceptanceCriteriaAdd=["MCP added acceptance criterion"],
        definitionOfDoneAdd=["MCP added verification"],
        checkAc=[1],
        assignee=["reviewer"],
        labels=["mcp", "edited"],
        priority="medium",
        milestone="Release MCP Edit",
        ordinal=2000,
        planSet="MCP edited plan.",
        planAppend=["MCP appended plan."],
        addReferences=["ref3.py"],
        removeReferences=["ref1.py"],
        addDocumentation=["doc3.md"],
        removeDocumentation=["doc2.md"],
        modifiedFiles=["src/mcp_edit.py", "tests/test_mcp_edit.py"],
    )
    assert edited["id"] == "TASK-2"
    assert edited["title"] == "MCP renamed task"
    assert edited["milestone"] == "Release MCP Edit"
    assert edited["ordinal"] == 2000
    assert edited["modifiedFiles"] == ["src/mcp_edit.py", "tests/test_mcp_edit.py"]

    unchecked = task_edit(
        project,
        task_id="TASK-2",
        uncheckAc=[1],
        acceptanceCriteriaRemove=[1],
        definitionOfDoneRemove=[1],
    )
    assert unchecked["id"] == "TASK-2"
    written = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "title: MCP renamed task" in written
    assert "assignee:\n- reviewer" in written
    assert "labels:\n- mcp\n- edited" in written
    assert "priority: medium" in written
    assert "milestone: Release MCP Edit" in written
    assert "references:\n- ref2.py\n- ref3.py" in written
    assert "documentation:\n- doc1.md\n- doc3.md" in written
    assert "modified_files:\n- src/mcp_edit.py\n- tests/test_mcp_edit.py" in written
    assert "MCP edited plan." in written
    assert "MCP appended plan." in written
    assert "MCP create plan." not in written
    assert "Initial MCP note." not in written
    assert "MCP replacement note." in written
    assert "- MCP note." in written
    assert "MCP final summary." in written
    assert "MCP appended final summary." in written
    assert "MCP create works" not in written
    assert "MCP added acceptance criterion" in written
    assert "MCP added verification" not in written

    task_edit(project, task_id="TASK-2", finalSummaryClear=True)
    cleared = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "MCP final summary." not in cleared
    assert "MCP appended final summary." not in cleared

    cleared_milestone = task_edit(project, task_id="TASK-2", milestone=None)
    assert "milestone" not in cleared_milestone
    assert "milestone:" not in _task_file(repo, "task-2").read_text(encoding="utf-8")

    task_edit(project, task_id="TASK-2", planClear=True)
    plan_cleared = _task_file(repo, "task-2").read_text(encoding="utf-8")
    assert "MCP edited plan." not in plan_cleared
    assert "MCP appended plan." not in plan_cleared


def test_mcp_bool_string_values_are_parsed_explicitly(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    replace_definition_of_done_defaults(project, ["Project default"])

    created = task_create(
        project,
        title="MCP bool task",
        disableDefinitionOfDoneDefaults="false",
        definitionOfDoneAdd=["Specific"],
        onStatusChange="false",
    )

    edited = task_edit(project, task_id=created["id"], status="Done", onStatusChange="false")

    source = _task_file(repo, created["id"].lower()).read_text(encoding="utf-8")
    assert edited["status"] == "Done"
    assert "- [ ] #1 Project default" in source
    assert "- [ ] #2 Specific" in source


def test_mcp_invalid_bool_string_is_rejected(tmp_path):
    repo = _copy_fixture(tmp_path)
    project = _project(repo)

    with pytest.raises(TypeError, match="boolean"):
        task_create(project, title="Bad bool", onStatusChange="sometimes")
