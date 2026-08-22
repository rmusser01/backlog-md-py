"""Report — and mechanically repair — task files the reader silently drops.

`_load_tasks_from_dir` skips an unparsable file with one log line and carries on,
which is the right behaviour for a read command and a poor way to learn that a
task is gone. In a real 2318-task project 26 files were invisible this way, all
from the same three malformed shapes, none of them producible by this package's
own writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.markdown.task_parser import parse_task_markdown

_TITLE_LINE_RE = re.compile(r"^title:(?P<value>.*)$")
_SECTION_BEGIN_RE = re.compile(r"^<!-- SECTION:(?P<name>[A-Z0-9_ -]+):BEGIN -->\s*$")
_SECTION_END_RE = re.compile(r"^<!-- SECTION:(?P<name>[A-Z0-9_ -]+):END -->\s*$")
_HEADING_RE = re.compile(r"^#{1,6} ")
# Frontmatter is short; a `title:` beyond this is body text, not a key.
_FRONTMATTER_SCAN_LINES = 40


@dataclass(frozen=True)
class BrokenTaskFile:
    path: Path
    reason: str


@dataclass(frozen=True)
class DuplicateTaskId:
    task_id: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class DoctorReport:
    unreadable: tuple[BrokenTaskFile, ...] = ()
    duplicate_ids: tuple[DuplicateTaskId, ...] = ()
    repaired: tuple[Path, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.unreadable and not self.duplicate_ids


def task_directories(project: BacklogProject) -> tuple[Path, ...]:
    """Every bucket a task file can legitimately live in."""
    return (
        project.backlog_dir / "tasks",
        project.backlog_dir / "completed",
        project.backlog_dir / "archive" / "tasks",
        project.backlog_dir / "drafts",
    )


def diagnose(project: BacklogProject) -> DoctorReport:
    """Find task files that cannot be parsed, and ids claimed by several files."""
    unreadable: list[BrokenTaskFile] = []
    by_id: dict[str, list[Path]] = {}

    for path in _task_files(project):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            unreadable.append(BrokenTaskFile(path, str(exc)))
            continue
        try:
            parsed = parse_task_markdown(source)
        except (ValueError, OSError) as exc:
            unreadable.append(BrokenTaskFile(path, str(exc)))
            continue
        task_id = str(parsed.frontmatter.get("id", "")).strip()
        if task_id:
            by_id.setdefault(task_id.casefold(), []).append(path)

    duplicates = tuple(
        DuplicateTaskId(task_id=_display_id(paths), paths=tuple(paths))
        for _, paths in sorted(by_id.items())
        if len(paths) > 1
    )
    return DoctorReport(unreadable=tuple(unreadable), duplicate_ids=duplicates)


def repair(project: BacklogProject) -> DoctorReport:
    """Repair every mechanically fixable file, then re-diagnose."""
    repaired: list[Path] = []
    for broken in diagnose(project).unreadable:
        try:
            source = broken.path.read_text(encoding="utf-8")
        except OSError:
            continue
        candidate = repair_task_source(source)
        if candidate is None:
            continue
        _atomic_write_text(broken.path, candidate, base=project.backlog_dir)
        repaired.append(broken.path)

    report = diagnose(project)
    return DoctorReport(
        unreadable=report.unreadable,
        duplicate_ids=report.duplicate_ids,
        repaired=tuple(repaired),
    )


def repair_task_source(source: str) -> str | None:
    """Return a repaired copy of ``source``, or None if it parses or cannot be fixed.

    Only mechanical, intent-preserving repairs: re-quote a frontmatter title that
    breaks YAML, and close an owned section whose END marker is missing. Anything
    else needs a human.
    """
    try:
        parse_task_markdown(source)
        return None
    except (ValueError, OSError):
        pass

    candidate = _close_unterminated_sections(_requote_title(source))
    if candidate == source:
        return None
    try:
        parse_task_markdown(candidate)
    except (ValueError, OSError):
        return None
    return candidate


def _requote_title(source: str) -> str:
    """Re-emit the frontmatter title as a YAML scalar the parser accepts.

    Covers both shapes seen in the wild: an unquoted title containing ": ", and a
    single-quoted title whose own apostrophe was never doubled.
    """
    lines = source.split("\n")
    for index, line in enumerate(lines[:_FRONTMATTER_SCAN_LINES]):
        match = _TITLE_LINE_RE.match(line)
        if match is None:
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        # safe_dump appends a document-end marker for a plain scalar; the first
        # line is the scalar itself.
        scalar = yaml.safe_dump(value, allow_unicode=True, width=10**6).split("\n")[0].strip()
        lines[index] = f"title: {scalar}"
        break
    return "\n".join(lines)


def _close_unterminated_sections(source: str) -> str:
    """Insert a missing SECTION END marker before the next heading, or at the end."""
    lines = source.split("\n")
    open_name: str | None = None
    result: list[str] = []
    for line in lines:
        begin = _SECTION_BEGIN_RE.match(line)
        end = _SECTION_END_RE.match(line)
        if open_name is not None and (
            _HEADING_RE.match(line) or (begin is not None and begin.group("name") != open_name)
        ):
            # Close the run of blank lines *before* the heading, not after it.
            while result and not result[-1].strip():
                result.pop()
            result.append(f"<!-- SECTION:{open_name}:END -->")
            result.append("")
            open_name = None
        if begin is not None:
            open_name = begin.group("name")
        elif end is not None and end.group("name") == open_name:
            open_name = None
        result.append(line)
    if open_name is not None:
        while result and not result[-1].strip():
            result.pop()
        result.append(f"<!-- SECTION:{open_name}:END -->")
        result.append("")
    return "\n".join(result)


def _task_files(project: BacklogProject) -> list[Path]:
    files: list[Path] = []
    for directory in task_directories(project):
        if not directory.is_dir():
            continue
        resolved_dir = directory.resolve()
        for path in sorted(directory.glob("*.md")):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            # Same containment rule the reader applies: a symlink out of the
            # bucket is not this project's file to diagnose or rewrite.
            if resolved != resolved_dir and not resolved.is_relative_to(resolved_dir):
                continue
            files.append(path)
    return files


def _display_id(paths: list[Path]) -> str:
    for path in paths:
        try:
            parsed = parse_task_markdown(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        task_id = str(parsed.frontmatter.get("id", "")).strip()
        if task_id:
            return task_id
    return ""
