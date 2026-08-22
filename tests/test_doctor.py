"""Doctor reports and repairs the task files every other command silently drops.

Each broken shape here was found in a real 2318-task project (#162), where 26
files were invisible to every command behind one loguru WARNING apiece.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.doctor import diagnose, repair_task_source
from backlog_py.storage.project import discover_project

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"

UNQUOTED_COLON = """---
id: TASK-2
title: Console transcript: skip move_child for rows already in position
status: To Do
created_date: '2026-01-01'
---

## Description

x
"""

UNESCAPED_APOSTROPHE = """---
id: TASK-3
title: 'Impersonate drafts the user's next reply'
status: To Do
created_date: '2026-01-01'
---

## Description

x
"""

UNTERMINATED_SECTION = """---
id: TASK-4
title: Unterminated
status: To Do
created_date: '2026-01-01'
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Body text with no end marker.

## Acceptance Criteria

- [ ] #1 something
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _write(repo: Path, name: str, source: str) -> Path:
    path = repo / "backlog" / "tasks" / name
    path.write_text(source, encoding="utf-8")
    return path


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _invoke(repo: Path, *args: str):
    return CliRunner().invoke(main, ["--cwd", str(repo), *args])


def test_diagnose_reports_every_unreadable_file_with_a_reason(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "task-2 - colon.md", UNQUOTED_COLON)
    _write(repo, "task-4 - unterminated.md", UNTERMINATED_SECTION)

    report = diagnose(_project(repo))

    assert {broken.path.name for broken in report.unreadable} == {
        "task-2 - colon.md",
        "task-4 - unterminated.md",
    }
    assert all(broken.reason for broken in report.unreadable), "a finding with no reason is not actionable"
    assert not report.ok


def test_diagnose_reports_duplicate_ids_with_every_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    original = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    _write(repo, "task-1 - rival.md", original.read_text(encoding="utf-8"))

    report = diagnose(_project(repo))

    assert [duplicate.task_id for duplicate in report.duplicate_ids] == ["TASK-1"]
    assert {path.name for path in report.duplicate_ids[0].paths} == {
        "task-1 - Example-task.md",
        "task-1 - rival.md",
    }


def test_a_healthy_project_reports_nothing(tmp_path: Path) -> None:
    report = diagnose(_project(_repo(tmp_path)))

    assert report.ok
    assert not report.unreadable and not report.duplicate_ids


def test_repair_requotes_a_title_holding_a_colon() -> None:
    repaired = repair_task_source(UNQUOTED_COLON)

    assert repaired is not None
    assert "title: 'Console transcript: skip move_child for rows already in position'" in repaired


def test_repair_requotes_a_title_holding_an_apostrophe() -> None:
    repaired = repair_task_source(UNESCAPED_APOSTROPHE)

    assert repaired is not None
    assert "Impersonate drafts the user's next reply" in repaired


def test_repair_closes_an_unterminated_owned_section() -> None:
    repaired = repair_task_source(UNTERMINATED_SECTION)

    assert repaired is not None
    assert "<!-- SECTION:DESCRIPTION:END -->" in repaired
    assert repaired.index("<!-- SECTION:DESCRIPTION:END -->") < repaired.index("## Acceptance Criteria")


def test_repair_leaves_a_healthy_file_alone() -> None:
    healthy = (FIXTURE_REPO / "backlog" / "tasks" / "task-1 - Example-task.md").read_text(encoding="utf-8")

    assert repair_task_source(healthy) is None


def test_doctor_exits_non_zero_and_names_the_broken_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "task-2 - colon.md", UNQUOTED_COLON)

    result = _invoke(repo, "doctor")

    assert result.exit_code != 0
    assert "task-2 - colon.md" in result.output


def test_doctor_fix_repairs_files_and_then_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    broken = _write(repo, "task-2 - colon.md", UNQUOTED_COLON)
    _write(repo, "task-3 - apostrophe.md", UNESCAPED_APOSTROPHE)
    _write(repo, "task-4 - unterminated.md", UNTERMINATED_SECTION)

    fixed = _invoke(repo, "doctor", "--fix")

    assert fixed.exit_code == 0, fixed.output
    assert "Console transcript: skip move_child" in broken.read_text(encoding="utf-8")
    assert diagnose(_project(repo)).ok
    assert _invoke(repo, "task", "list", "--plain").output.count("TASK-") >= 4


def test_doctor_fix_does_not_touch_duplicate_ids(tmp_path: Path) -> None:
    """Choosing which of two files keeps an id is a human decision."""
    repo = _repo(tmp_path)
    original = repo / "backlog" / "tasks" / "task-1 - Example-task.md"
    rival = _write(repo, "task-1 - rival.md", original.read_text(encoding="utf-8"))

    result = _invoke(repo, "doctor", "--fix")

    assert result.exit_code != 0
    assert original.exists() and rival.exists(), "doctor deleted a file it was not asked to resolve"
    assert "TASK-1" in result.output


SECTION_SPANNING_HEADINGS = """---
id: TASK-5
title: Watchlists: profile the screen push
status: To Do
created_date: '2026-01-01'
---

## Investigation Notes

<!-- SECTION:NOTES:BEGIN -->
Opening paragraph.

### 1. A heading inside the notes

More prose that belongs to the same owned section.
<!-- SECTION:NOTES:END -->
"""


def test_repair_leaves_a_section_that_closes_after_an_inner_heading() -> None:
    """An owned section may span headings; only a missing END may be inserted.

    Closing at the first heading after BEGIN corrupted two real task files
    (task-15460, task-15462): each already had its END at the bottom, so the
    repair produced one BEGIN and two ENDs.
    """
    repaired = repair_task_source(SECTION_SPANNING_HEADINGS)

    assert repaired is not None, "the unquoted title still needs repairing"
    assert repaired.count("<!-- SECTION:NOTES:END -->") == 1
    assert repaired.count("<!-- SECTION:NOTES:BEGIN -->") == 1
    assert "title: 'Watchlists: profile the screen push'" in repaired


def test_repair_closes_an_unterminated_section_before_the_next_heading() -> None:
    """When there is no END anywhere, the section ends where the next one starts."""
    repaired = repair_task_source(UNTERMINATED_SECTION)

    assert repaired is not None
    assert repaired.count("<!-- SECTION:DESCRIPTION:END -->") == 1
    assert repaired.index("<!-- SECTION:DESCRIPTION:END -->") < repaired.index("## Acceptance Criteria")
