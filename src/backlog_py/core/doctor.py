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
from backlog_py.markdown.task_parser import TaskMarkdownParseError, parse_task_markdown

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
    candidate = source
    while True:
        try:
            parse_task_markdown(candidate)
        except TaskMarkdownParseError as exc:
            if exc.code == "invalid_frontmatter":
                repaired = _requote_title(candidate)
            elif exc.code == "unterminated_section":
                repaired = _close_unterminated_sections(candidate)
            else:
                return None
            if repaired == candidate:
                return None
            candidate = repaired
        else:
            return candidate if candidate != source else None


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
    """Insert an END marker only for a section that never closes.

    An owned section may legitimately span headings — a NOTES block with its own
    ``###`` sub-headings is common — so the presence of a heading says nothing.
    Only the absence of a matching END anywhere below the BEGIN does. Closing at
    the first heading instead corrupted two real task files that already carried
    their END at the bottom, giving them one BEGIN and two ENDs.
    """
    lines = source.split("\n")
    # Line index -> marker to insert *before* that line. len(lines) means EOF.
    insertions: dict[int, str] = {}
    index = 0
    while index < len(lines):
        begin = _SECTION_BEGIN_RE.match(lines[index])
        if begin is None:
            index += 1
            continue
        name = begin.group("name")
        closing = _find_section_end(lines, index + 1, name)
        if closing is not None:
            index = closing + 1
            continue
        # Unterminated. The author's intent ends where the next heading starts;
        # running to EOF would swallow every following section into this one.
        stop = next(
            (i for i in range(index + 1, len(lines)) if _HEADING_RE.match(lines[i])),
            len(lines),
        )
        insertions[stop] = f"<!-- SECTION:{name}:END -->"
        index += 1

    if not insertions:
        return source

    closed: list[str] = []
    for index, line in enumerate(lines):
        if index in insertions:
            _append_marker(closed, insertions[index])
        closed.append(line)
    if len(lines) in insertions:
        _append_marker(closed, insertions[len(lines)])
    return "\n".join(closed)


def _find_section_end(lines: list[str], start: int, name: str) -> int | None:
    for index in range(start, len(lines)):
        end = _SECTION_END_RE.match(lines[index])
        if end is not None and end.group("name") == name:
            return index
    return None


def _append_marker(closed: list[str], marker: str) -> None:
    """Place the marker against the section's last line, not after its blank run."""
    while closed and not closed[-1].strip():
        closed.pop()
    closed.append(marker)
    closed.append("")


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
