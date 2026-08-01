"""Regression tests for the core data-layer review findings.

Each test reproduces a concrete failure verified against the code as of 1.0.1
before the corresponding fix landed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backlog_py.core.init import init_project
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.storage.config import load_config


def _project(tmp_path: Path, **config_lines: str) -> BacklogProject:
    project = init_project(tmp_path, no_git=True).project
    if config_lines:
        text = project.config_path.read_text()
        for key, value in config_lines.items():
            text += f"\n{key}: {value}\n"
        project.config_path.write_text(text)
        project = BacklogProject(
            root=project.root,
            backlog_dir=project.backlog_dir,
            config_path=project.config_path,
            config=load_config(project.config_path),
        )
    return project


def _tasks_dir(project: BacklogProject) -> Path:
    return project.backlog_dir / "tasks"


# --------------------------------------------------------------------------
# UTF-8 BOM
# --------------------------------------------------------------------------


def test_bom_prefixed_task_file_parses_frontmatter(tmp_path: Path) -> None:
    """A leading BOM must not hide the frontmatter block."""
    source = "---\nid: TASK-1\ntitle: Bom test\nstatus: In Progress\n---\n\n## Description\n\nBody\n"
    parsed = parse_task_markdown("﻿" + source)
    assert parsed.frontmatter.get("id") == "TASK-1"
    assert parsed.frontmatter.get("title") == "Bom test"
    assert parsed.frontmatter.get("status") == "In Progress"


def test_editing_bom_task_keeps_one_frontmatter_block_and_status(tmp_path: Path) -> None:
    """Editing a BOM-prefixed task must not append a second frontmatter block."""
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Bom.md"
    path.write_text(
        "﻿---\nid: TASK-1\ntitle: Bom\nstatus: In Progress\ncreated_date: '2026-01-01'\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", labels=["x"])

    text = path.read_text(encoding="utf-8")
    assert text.count("\n---\n") <= 1, f"frontmatter was duplicated:\n{text}"
    assert not text.lstrip("﻿").lstrip().startswith("---\nlabels:")

    reloaded = ReadOnlyRepository(project, refresh_remote_refs=False).get_task("TASK-1")
    assert reloaded.status == "In Progress"
    assert reloaded.title == "Bom"


# --------------------------------------------------------------------------
# Zero-padded id collisions
# --------------------------------------------------------------------------


def test_zero_padded_id_collides_with_existing_unpadded_id(tmp_path: Path) -> None:
    """TASK-007 must not be creatable alongside an existing TASK-7."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Seven", task_id="TASK-7")

    with pytest.raises(TaskMutationError):
        repo.create_task(title="Padded seven", task_id="TASK-007")


def test_unpadded_id_collides_with_existing_padded_id(tmp_path: Path) -> None:
    """The collision check must work in both directions."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Padded", task_id="TASK-007")

    with pytest.raises(TaskMutationError):
        repo.create_task(title="Unpadded", task_id="TASK-7")


# --------------------------------------------------------------------------
# Duplicate section headings
# --------------------------------------------------------------------------


def test_edit_with_duplicate_description_sections_reads_back(tmp_path: Path) -> None:
    """A duplicated section block must not make an edit invisible to readers."""
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Dup.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Dup\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        "## Description\n\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\n<!-- SECTION:DESCRIPTION:END -->\n\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\n<!-- SECTION:DESCRIPTION:END -->\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    updated = repo.edit_task("TASK-1", description="MY NEW DESCRIPTION")

    assert "MY NEW DESCRIPTION" in updated.description
    reloaded = ReadOnlyRepository(project, refresh_remote_refs=False).get_task("TASK-1")
    assert "MY NEW DESCRIPTION" in reloaded.description


# --------------------------------------------------------------------------
# Duplicate ids across files
# --------------------------------------------------------------------------


def test_duplicate_task_ids_warn_and_resolve_deterministically(tmp_path: Path) -> None:
    """Two files claiming one id must warn and pick a stable winner."""
    from loguru import logger as loguru_logger

    project = _project(tmp_path)
    tasks = _tasks_dir(project)
    body = "---\nid: TASK-1\ntitle: {title}\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n## Description\n\n{title}\n"
    (tasks / "task-1 - Alpha.md").write_text(body.format(title="Alpha"), encoding="utf-8")
    (tasks / "task-1 - Zulu.md").write_text(body.format(title="Zulu"), encoding="utf-8")

    messages, sink_id = _captured_warnings()
    try:
        first = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()
        second = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()
    finally:
        loguru_logger.remove(sink_id)

    assert len([task for task in first if task.id.casefold() == "task-1"]) == 1
    assert [task.title for task in first] == [task.title for task in second]
    assert any("Duplicate task id" in message for message in messages), (
        "a duplicate id was resolved silently"
    )


# --------------------------------------------------------------------------
# Completed tasks remain referenceable
# --------------------------------------------------------------------------


def test_dependency_may_reference_a_completed_task(tmp_path: Path) -> None:
    """Completing a task must not break later edits that depend on it."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Dependency")
    repo.create_task(title="Holder")
    repo.edit_task("TASK-1", status="Done")
    repo.complete_task("TASK-1")

    updated = repo.edit_task("TASK-2", dependencies=["TASK-1"])
    recorded = updated.parsed.frontmatter.get("dependencies") or []
    assert any("TASK-1" in str(dep).upper() for dep in recorded)


def test_parent_task_id_may_reference_a_completed_task(tmp_path: Path) -> None:
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Parent")
    repo.edit_task("TASK-1", status="Done")
    repo.complete_task("TASK-1")

    child = repo.create_task(title="Child", parent_task_id="TASK-1")
    parent = child.parsed.frontmatter.get("parent_task_id") or child.parsed.frontmatter.get("parent")
    assert parent is not None and "TASK-1" in str(parent).upper()


# --------------------------------------------------------------------------
# Line endings
# --------------------------------------------------------------------------


def test_crlf_task_file_stays_crlf_after_section_edit(tmp_path: Path) -> None:
    """Section writers must honour the file's existing newline style."""
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Crlf.md"
    source = (
        "---\nid: TASK-1\ntitle: Crlf\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        "## Description\n\n<!-- SECTION:DESCRIPTION:BEGIN -->\nBody\n<!-- SECTION:DESCRIPTION:END -->\n"
    ).replace("\n", "\r\n")
    path.write_bytes(source.encode("utf-8"))

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", plan="A plan")

    raw = path.read_bytes()
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    assert bare_lf == 0, f"{bare_lf} bare LF line endings leaked into a CRLF file"


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def test_modified_files_filter_requires_every_value(tmp_path: Path) -> None:
    """--modified-files a,b must match tasks touching both, like every other filter."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Only A", modified_files=["src/a.py"])
    repo.create_task(title="Both", modified_files=["src/a.py", "src/b.py"])

    results = ReadOnlyRepository(project, refresh_remote_refs=False).search_tasks(
        "", modified_files=["src/a.py", "src/b.py"]
    )
    assert [task.title for task in results] == ["Both"]


# --------------------------------------------------------------------------
# onStatusChange sourced from task frontmatter
# --------------------------------------------------------------------------


def test_task_frontmatter_status_callback_is_not_executed_by_default(tmp_path: Path) -> None:
    """A command carried in task frontmatter must not run without explicit opt-in."""
    project = _project(tmp_path)
    sentinel = tmp_path / "pwned.txt"
    path = _tasks_dir(project) / "task-1 - Hostile.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Hostile\nstatus: To Do\ncreated_date: '2026-01-01'\n"
        f"onStatusChange: touch {sentinel}\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", status="In Progress")

    assert not sentinel.exists(), "task frontmatter onStatusChange executed without opt-in"


def test_task_frontmatter_status_callback_runs_when_opted_in(tmp_path: Path) -> None:
    """The opt-in flag restores upstream-compatible behaviour."""
    project = _project(tmp_path, taskFrontmatterStatusCallbacks="true")
    sentinel = tmp_path / "ran.txt"
    path = _tasks_dir(project) / "task-1 - Allowed.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Allowed\nstatus: To Do\ncreated_date: '2026-01-01'\n"
        f"onStatusChange: touch {sentinel}\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", status="In Progress")

    assert sentinel.exists(), "opt-in flag did not re-enable the callback"


def test_config_status_callback_still_runs(tmp_path: Path) -> None:
    """Config-sourced callbacks are trusted and must keep working."""
    sentinel = tmp_path / "config-ran.txt"
    project = _project(tmp_path, onStatusChange=f"touch {sentinel}")

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Normal")
    repo.edit_task("TASK-1", status="In Progress")

    assert sentinel.exists(), "config-level onStatusChange stopped running"


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def test_create_task_git_subprocess_count_is_bounded(tmp_path: Path, monkeypatch) -> None:
    """One mutation must not fork a git process per task file."""
    from backlog_py.runtime import git as git_module

    project = _project(tmp_path)
    seed = MutableRepository(project, refresh_remote_refs=False)
    for index in range(20):
        seed.create_task(title=f"Seed {index}")

    calls: list[tuple[str, ...]] = []
    original = git_module._run_git

    def counting(work_dir, *args, **kwargs):
        calls.append(tuple(args))
        return original(work_dir, *args, **kwargs)

    monkeypatch.setattr(git_module, "_run_git", counting)

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Measured")

    assert len(calls) < 20, f"{len(calls)} git subprocesses for one create_task over 21 tasks"


def test_repeated_reads_do_not_rescan_within_one_mutable_repository(tmp_path: Path, monkeypatch) -> None:
    """MutableRepository must not invalidate its cache on every single read."""
    from backlog_py.core import repository as repo_module

    project = _project(tmp_path)
    seed = MutableRepository(project, refresh_remote_refs=False)
    for index in range(5):
        seed.create_task(title=f"Seed {index}")

    repo = MutableRepository(project, refresh_remote_refs=False)
    loads = {"count": 0}
    original = repo_module._load_task

    def counting(path):
        loads["count"] += 1
        return original(path)

    monkeypatch.setattr(repo_module, "_load_task", counting)

    repo.list_tasks()
    after_first = loads["count"]
    repo.list_tasks()
    repo.list_tasks()

    assert loads["count"] == after_first, "repeated reads re-parsed every task file"


# --------------------------------------------------------------------------
# Review follow-ups
# --------------------------------------------------------------------------


def _captured_warnings():
    """Collect loguru warnings; caplog does not receive loguru output."""
    from loguru import logger as loguru_logger

    messages: list[str] = []
    sink_id = loguru_logger.add(lambda message: messages.append(message), level="WARNING")
    return messages, sink_id


def test_task_frontmatter_disable_is_honoured_even_when_gated(tmp_path: Path) -> None:
    """`onStatusChange: false` is an opt-OUT, not attacker-executable content."""
    sentinel = tmp_path / "config-ran.txt"
    project = _project(tmp_path, onStatusChange=f"touch {sentinel}")
    path = _tasks_dir(project) / "task-1 - Optout.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Optout\nstatus: To Do\ncreated_date: '2026-01-01'\n"
        "onStatusChange: false\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", status="In Progress")

    assert not sentinel.exists(), "a per-task opt-out was overridden by the config command"


def test_create_draft_dependency_may_reference_a_completed_task(tmp_path: Path) -> None:
    from backlog_py.core.drafts import DraftService

    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Dependency")
    repo.edit_task("TASK-1", status="Done")
    repo.complete_task("TASK-1")

    draft = DraftService(project).create_draft(title="Later", dependencies=["TASK-1"])
    recorded = draft.parsed.frontmatter.get("dependencies") or []
    assert any("TASK-1" in str(dep).upper() for dep in recorded)


def test_crlf_survives_clearing_a_section(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Crlf.md"
    source = (
        "---\nid: TASK-1\ntitle: Crlf\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        "## Description\n\n<!-- SECTION:DESCRIPTION:BEGIN -->\nBody\n<!-- SECTION:DESCRIPTION:END -->\n\n"
        "## Implementation Plan\n\n<!-- SECTION:PLAN:BEGIN -->\nA plan\n<!-- SECTION:PLAN:END -->\n"
    ).replace("\n", "\r\n")
    path.write_bytes(source.encode("utf-8"))

    MutableRepository(project, refresh_remote_refs=False).edit_task("TASK-1", clear_plan=True)

    raw = path.read_bytes()
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    assert bare_lf == 0, f"{bare_lf} bare LF endings leaked into a CRLF file when clearing a section"


def test_duplicate_task_ids_warn_when_timestamps_differ(tmp_path: Path) -> None:
    """The common case is two files committed at different times, not an exact tie."""
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }

    def git(*args, **kwargs):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env={**env, **kwargs})

    git("init", "-q")
    project = init_project(root).project
    tasks = _tasks_dir(project)
    body = "---\nid: TASK-1\ntitle: {title}\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n## Description\n\nx\n"
    (tasks / "task-1 - Alpha.md").write_text(body.format(title="Alpha"), encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "first", GIT_AUTHOR_DATE="2026-01-01T00:00:00", GIT_COMMITTER_DATE="2026-01-01T00:00:00")
    (tasks / "task-1 - Zulu.md").write_text(body.format(title="Zulu"), encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "second", GIT_AUTHOR_DATE="2026-01-02T00:00:00", GIT_COMMITTER_DATE="2026-01-02T00:00:00")

    messages, sink_id = _captured_warnings()
    try:
        tasks_found = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()
    finally:
        from loguru import logger as loguru_logger

        loguru_logger.remove(sink_id)

    assert len([t for t in tasks_found if t.id.casefold() == "task-1"]) == 1
    assert any("Duplicate task id" in message for message in messages), (
        "a duplicate id resolved by commit time was hidden with no warning"
    )


def test_unparsable_decision_still_reserves_its_id(tmp_path: Path) -> None:
    """Skipping a bad file must not let its id be reissued to a new file."""
    from backlog_py.core.decisions import DecisionService

    project = _project(tmp_path)
    service = DecisionService(project)
    first = service.create_decision(title="Alpha")
    first.path.write_text("---\nbroken: [unterminated\n", encoding="utf-8")

    second = service.create_decision(title="Beta")
    assert second.id != first.id, f"id {first.id} was reissued after the original became unparsable"


def test_unparsable_document_still_reserves_its_id(tmp_path: Path) -> None:
    from backlog_py.core.documents import DocumentService

    project = _project(tmp_path)
    service = DocumentService(project)
    first = service.create_document_from_title("Alpha", content="a")
    first.path.write_text("---\nbroken: [unterminated\n", encoding="utf-8")

    second = service.create_document_from_title("Beta", content="b")
    assert second.id != first.id, f"id {first.id} was reissued after the original became unparsable"


def test_section_edit_ignores_a_nested_duplicate_block(tmp_path: Path) -> None:
    """A DESCRIPTION block nested inside PLAN must not receive the edit."""
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Nested.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Nested\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        "## Implementation Plan\n\n"
        "<!-- SECTION:PLAN:BEGIN -->\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\nnested decoy\n<!-- SECTION:DESCRIPTION:END -->\n"
        "<!-- SECTION:PLAN:END -->\n\n"
        "## Description\n\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\nreal\n<!-- SECTION:DESCRIPTION:END -->\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", description="NEW TEXT")

    text = path.read_text(encoding="utf-8")
    plan_block = text.split("## Description")[0]
    assert "NEW TEXT" not in plan_block, "the edit was spliced into the nested block inside PLAN"
    assert "NEW TEXT" in text


def test_disabling_a_callback_does_not_execute_a_snake_case_alias(tmp_path: Path) -> None:
    """Disabling the hook must not run an attacker-planted alias key.

    The caller-supplied trust flag was set for any non-None value including a
    disable, while the write only cleared the camelCase key — leaving a planted
    `on_status_change:` to execute with the gate skipped.
    """
    project = _project(tmp_path)
    sentinel = tmp_path / "pwned.txt"
    path = _tasks_dir(project) / "task-1 - Alias.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Alias\nstatus: To Do\ncreated_date: '2026-01-01'\n"
        f"on_status_change: touch {sentinel}\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.edit_task("TASK-1", status="In Progress", on_status_change=False)

    assert not sentinel.exists(), "a planted on_status_change alias executed while disabling the hook"


# --------------------------------------------------------------------------
# Malformed onStatusChange in task frontmatter
# --------------------------------------------------------------------------


def _task_record(source: str):
    """Build a TaskRecord straight from markdown, without touching disk."""
    from backlog_py.core.repository import TaskRecord, _task_record_from_parsed

    record: TaskRecord = _task_record_from_parsed(Path("task-1 - X.md"), parse_task_markdown(source))
    return record


_CALLBACK_TASK_SOURCE = (
    "---\nid: TASK-1\ntitle: Shape\nstatus: To Do\ncreated_date: '2026-01-01'\n"
    "{line}---\n\n## Description\n\nBody\n"
)


@pytest.mark.parametrize(
    ("frontmatter_line", "gated_command", "opted_in_command"),
    [
        # A literal `true` carries no command; it must degrade to the config
        # command instead of aborting the status change.
        ("onStatusChange: true\n", "config-command", "config-command"),
        # An explicit opt-out is honoured whether or not the gate is open.
        ("onStatusChange: false\n", None, None),
        # A command string is attacker-supplied content: gated by default.
        ("onStatusChange: task-command\n", "config-command", "task-command"),
        # Absent: the config command applies unchanged.
        ("", "config-command", "config-command"),
    ],
)
def test_frontmatter_status_callback_resolution_covers_every_shape(
    frontmatter_line: str, gated_command: str | None, opted_in_command: str | None
) -> None:
    """All four frontmatter shapes resolve without raising, on both sides of the gate."""
    from backlog_py.core.models import BacklogConfig
    from backlog_py.core.repository import _task_status_callback_command

    task = _task_record(_CALLBACK_TASK_SOURCE.format(line=frontmatter_line))
    gated = BacklogConfig(project_name="p", on_status_change="config-command")
    opted_in = BacklogConfig(
        project_name="p", on_status_change="config-command", task_frontmatter_status_callbacks=True
    )

    assert _task_status_callback_command(task, gated) == gated_command
    assert _task_status_callback_command(task, opted_in) == opted_in_command


def test_literal_true_on_status_change_does_not_abort_a_status_change(tmp_path: Path) -> None:
    """`onStatusChange: true` must not turn a status change into a hard failure."""
    from loguru import logger as loguru_logger

    sentinel = tmp_path / "config-ran.txt"
    project = _project(tmp_path, onStatusChange=f"touch {sentinel}")
    path = _tasks_dir(project) / "task-1 - Bool.md"
    path.write_text(
        "---\nid: TASK-1\ntitle: Bool\nstatus: To Do\ncreated_date: '2026-01-01'\n"
        "onStatusChange: true\n---\n\n## Description\n\nBody\n",
        encoding="utf-8",
    )

    repo = MutableRepository(project, refresh_remote_refs=False)
    messages, sink_id = _captured_warnings()
    try:
        updated = repo.edit_task("TASK-1", status="In Progress")
    finally:
        loguru_logger.remove(sink_id)

    assert updated.status == "In Progress"
    assert any("TASK-1" in message for message in messages), (
        "a malformed onStatusChange was swallowed without naming the task"
    )
    assert sentinel.exists(), "the config command did not run as the fallback"


# --------------------------------------------------------------------------
# Newline handling
# --------------------------------------------------------------------------


def test_crlf_in_supplied_content_does_not_flip_an_lf_file(tmp_path: Path) -> None:
    """CRLF inside caller-supplied content must not convert an LF task file."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    created = repo.create_task(title="Lf")

    repo.edit_task(created.id, description="a\r\nb")

    crlf_count = created.path.read_bytes().count(b"\r\n")
    assert crlf_count == 0, f"{crlf_count} CRLF endings were written into a pure-LF file"


def test_a_stray_crlf_does_not_convert_an_lf_dominant_file(tmp_path: Path) -> None:
    """One stray CRLF must not make every new section CRLF."""
    project = _project(tmp_path)
    path = _tasks_dir(project) / "task-1 - Stray.md"
    path.write_bytes(
        b"---\nid: TASK-1\ntitle: Stray\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        b"## Description\n\n<!-- SECTION:DESCRIPTION:BEGIN -->\n"
        b"line one\r\nline two\n"
        b"<!-- SECTION:DESCRIPTION:END -->\n"
    )

    MutableRepository(project, refresh_remote_refs=False).edit_task("TASK-1", plan="A plan")

    crlf_count = path.read_bytes().count(b"\r\n")
    assert crlf_count == 1, (
        f"a single stray CRLF pulled {crlf_count - 1} more CRLF endings into an LF file"
    )


# --------------------------------------------------------------------------
# Lookup cache
# --------------------------------------------------------------------------


def test_get_task_reuses_the_lookup_cache(tmp_path: Path, monkeypatch) -> None:
    """Resolving an archived task twice must not re-read every bucket twice."""
    from backlog_py.core import repository as repo_module

    project = _project(tmp_path)
    seed = MutableRepository(project, refresh_remote_refs=False)
    seed.create_task(title="Archived")
    seed.archive_task("TASK-1")

    repo = MutableRepository(project, refresh_remote_refs=False)
    loads = {"count": 0}
    original = repo_module._load_task

    def counting(path):
        loads["count"] += 1
        return original(path)

    monkeypatch.setattr(repo_module, "_load_task", counting)

    assert repo.get_task("TASK-1").id == "TASK-1"
    after_first = loads["count"]
    assert repo.get_task("TASK-1").id == "TASK-1"

    assert loads["count"] == after_first, "get_task re-read the task directories instead of using the cache"


def test_create_task_reads_each_task_directory_once(tmp_path: Path, monkeypatch) -> None:
    """Id reservation must not scan tasks/ and completed/ a second time."""
    from backlog_py.core import repository as repo_module

    project = _project(tmp_path)
    seed = MutableRepository(project, refresh_remote_refs=False)
    for index in range(6):
        seed.create_task(title=f"Seed {index}")

    repo = MutableRepository(project, refresh_remote_refs=False)
    loads = {"count": 0}
    original = repo_module._load_task

    def counting(path):
        loads["count"] += 1
        return original(path)

    monkeypatch.setattr(repo_module, "_load_task", counting)
    repo.create_task(title="Measured")

    # Six seeds read once, plus the freshly written file read back.
    assert loads["count"] <= 7, f"{loads['count']} task loads for one create_task over 6 tasks"


# --------------------------------------------------------------------------
# Zero-padding equivalence for references
# --------------------------------------------------------------------------


def test_dependency_may_use_a_differently_padded_id(tmp_path: Path) -> None:
    """`--dep TASK-7` must resolve the on-disk TASK-007 that get_task resolves."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Padded", task_id="TASK-007")
    holder = repo.create_task(title="Holder", task_id="TASK-2")

    updated = repo.edit_task(holder.id, dependencies=["TASK-7"])

    recorded = updated.parsed.frontmatter.get("dependencies") or []
    assert any("TASK-7" in str(dep).upper() for dep in recorded)


def test_parent_task_id_may_use_a_differently_padded_id(tmp_path: Path) -> None:
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Padded parent", task_id="TASK-007")

    child = repo.create_task(title="Child", parent_task_id="TASK-7")

    parent = child.parsed.frontmatter.get("parent_task_id")
    assert parent is not None and "TASK-" in str(parent).upper()
    assert ReadOnlyRepository(project, refresh_remote_refs=False).get_task(str(parent)).title == "Padded parent"


def test_circular_dependency_detected_across_zero_padding(tmp_path: Path) -> None:
    """A cycle must not be invisible just because one hop is written unpadded."""
    project = _project(tmp_path)
    repo = MutableRepository(project, refresh_remote_refs=False)
    repo.create_task(title="Two", task_id="TASK-2")
    repo.create_task(title="Padded seven", task_id="TASK-007", dependencies=["TASK-2"])

    with pytest.raises(TaskMutationError, match="Circular dependency"):
        repo.edit_task("TASK-2", dependencies=["TASK-7"])


# --------------------------------------------------------------------------
# Atomic write containment
# --------------------------------------------------------------------------


def test_atomic_write_text_anchors_on_a_supplied_base(tmp_path: Path) -> None:
    """A trusted base must reject writes that land outside it."""
    from backlog_py.core.repository import _atomic_write_text

    base = tmp_path / "base"
    base.mkdir()
    inside = base / "ok.md"
    _atomic_write_text(inside, "kept", base=base)
    assert inside.read_text(encoding="utf-8") == "kept"

    with pytest.raises(TaskMutationError):
        _atomic_write_text(tmp_path / "escaped.md", "nope", base=base)


def test_atomic_write_text_without_a_base_still_writes(tmp_path: Path) -> None:
    """The base parameter is optional; other modules still call the old way."""
    from backlog_py.core.repository import _atomic_write_text

    target = tmp_path / "plain.md"
    _atomic_write_text(target, "plain")
    assert target.read_text(encoding="utf-8") == "plain"
