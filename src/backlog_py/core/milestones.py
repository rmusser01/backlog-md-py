from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from backlog_py.core.errors import NotFoundError
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import ReadOnlyRepository, TaskMutationError, _atomic_write_text, _mutation_path
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.security.paths import (
    PathContainmentError,
    assert_path_within_base,
    assert_trusted_subpath,
)


class MilestoneMutationError(ValueError):
    """Raised when a milestone mutation request is invalid or unsafe."""


@dataclass(frozen=True)
class MilestoneRecord:
    name: str
    path: Path
    path_relative: str
    content: str
    frontmatter: dict[str, Any]
    archived: bool = False
    id: str | None = None
    title: str = ""
    due_date: str | None = None
    format: str = "legacy"

    @property
    def description(self) -> str:
        return _description_from_body(self.content) if self.format == "current" else self.content


@dataclass(frozen=True)
class _TaskReferenceUpdate:
    path: Path
    original_source: str
    updated_source: str


class MilestoneService:
    def __init__(self, project: BacklogProject) -> None:
        self.project = project
        # Validated per level: a symlinked milestones dir must not become the base.
        self.active_dir = assert_trusted_subpath(project.root, project.backlog_dir / "milestones")
        self.archive_dir = assert_trusted_subpath(
            project.root, project.backlog_dir / "archive" / "milestones"
        )

    def add_milestone(self, name: str, description: str = "") -> MilestoneRecord:
        target = self._active_path(name)
        if target.exists():
            raise MilestoneMutationError(f"Milestone already exists: {name}")
        source = _render_milestone({"name": name}, description)
        parse_task_markdown(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, source)
        return _load_milestone(self.project, target, archived=False)

    def list_milestones(self, *, include_archived: bool = False) -> list[MilestoneRecord]:
        milestones: list[MilestoneRecord] = []
        directories = [(self.active_dir, False)]
        if include_archived:
            directories.append((self.archive_dir, True))
        for directory, archived in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                if path.name.casefold() == "readme.md":
                    continue
                try:
                    milestones.append(self._load_milestone(path, archived=archived))
                except MilestoneMutationError:
                    # Containment failures are security signals, not bad content.
                    raise
                except (ValueError, OSError) as exc:
                    # A single unparsable file must not disable every milestone
                    # operation; skip it and warn, as the task repository does.
                    logger.warning("Skipping unreadable milestone file {}: {}", path, exc)
        return sorted(milestones, key=_milestone_sort_key)

    def rename_milestone(self, old_name: str, new_name: str, *, update_tasks: bool = False) -> MilestoneRecord:
        existing = self._find_active(old_name)
        target = self._active_path(new_name)
        same_path = target.exists() and target.resolve() == existing.path.resolve()
        if target.exists() and not same_path:
            raise MilestoneMutationError(f"Milestone already exists: {new_name}")
        task_updates = self._task_reference_updates(existing.name, new_name) if update_tasks else []
        frontmatter = dict(existing.frontmatter)
        frontmatter["name"] = new_name
        source = _render_milestone(frontmatter, existing.content)
        parse_task_markdown(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        original_source = existing.path.read_text(encoding="utf-8") if same_path else None
        target_written = False
        written_updates: list[_TaskReferenceUpdate] = []
        try:
            _atomic_write_text(target, source)
            target_written = True
            _write_task_updates(task_updates, written_updates)
            if not same_path:
                existing.path.unlink()
        except Exception:
            _rollback_task_updates(written_updates)
            if same_path and target_written and original_source is not None:
                _atomic_write_text(existing.path, original_source)
            elif target_written:
                _unlink_best_effort(target)
            raise
        return _load_milestone(self.project, target, archived=False)

    def remove_milestone(self, name: str, *, clear_tasks: bool = False) -> MilestoneRecord:
        existing = self._find_active(name)
        task_updates = self._task_reference_updates(existing.name, None) if clear_tasks else []
        written_updates: list[_TaskReferenceUpdate] = []
        try:
            _write_task_updates(task_updates, written_updates)
            existing.path.unlink()
        except Exception:
            _rollback_task_updates(written_updates)
            raise
        return existing

    def archive_milestone(self, name: str) -> MilestoneRecord:
        existing = self._find_active(name)
        target = self._archive_path(existing.path.name)
        if target.exists():
            raise MilestoneMutationError(f"Archived milestone already exists: {existing.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target = assert_path_within_base(self.archive_dir, target)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc
        os.replace(existing.path, target)
        return _load_milestone(self.project, target, archived=True)

    def _find_active(self, name: str) -> MilestoneRecord:
        requested = name.casefold()
        for milestone in self.list_milestones():
            if milestone.name.casefold() == requested or milestone.path.stem.casefold() == requested:
                return milestone
        raise NotFoundError(f"Milestone not found: {name}")

    def _active_path(self, name: str) -> Path:
        path = self.active_dir / f"{_slug_name(name)}.md"
        try:
            return assert_path_within_base(self.active_dir, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _archive_path(self, filename: str) -> Path:
        path = self.archive_dir / filename
        try:
            return assert_path_within_base(self.archive_dir, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _task_reference_updates(self, old_name: str, new_name: str | None) -> list[_TaskReferenceUpdate]:
        updates: list[_TaskReferenceUpdate] = []
        # Read the local working tree only. Active-branch snapshots can return a
        # record whose content is from another branch but whose path points at
        # the local file, which would overwrite local content on write.
        local_repository = ReadOnlyRepository(
            self.project,
            refresh_remote_refs=False,
            include_active_branch_snapshots=False,
        )
        for task in local_repository.list_tasks():
            milestone = task.parsed.frontmatter.get("milestone")
            if not isinstance(milestone, str) or milestone.casefold() != old_name.casefold():
                continue
            frontmatter = dict(task.parsed.frontmatter)
            if new_name is None:
                frontmatter.pop("milestone", None)
            else:
                frontmatter["milestone"] = new_name
            yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
            source = f"---\n{yaml_text}\n---\n{task.parsed.body}"
            parse_task_markdown(source)
            try:
                # Anchor on the backlog dir, not the file's own parent: a
                # symlinked tasks/ directory would otherwise pass containment.
                safe_task_path = _mutation_path(self.project.backlog_dir, task.path)
            except TaskMutationError as exc:
                raise MilestoneMutationError(str(exc)) from exc
            updates.append(
                _TaskReferenceUpdate(
                    path=safe_task_path,
                    original_source=task.raw_source,
                    updated_source=source,
                )
            )
        return updates

    def _load_milestone(self, path: Path, *, archived: bool) -> MilestoneRecord:
        base = self.archive_dir if archived else self.active_dir
        try:
            assert_path_within_base(base, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc
        return _load_milestone(self.project, path, archived=archived)


def _load_milestone(project: BacklogProject, path: Path, *, archived: bool) -> MilestoneRecord:
    raw_source = path.read_text(encoding="utf-8-sig")
    parsed = parse_task_markdown(raw_source)
    frontmatter = dict(parsed.frontmatter)
    content = parsed.body.strip()
    if "name" in frontmatter:
        name = str(frontmatter.get("name") or _name_from_filename(path))
        return MilestoneRecord(
            name=name,
            path=path,
            path_relative=path.relative_to(project.backlog_dir).as_posix(),
            content=content,
            frontmatter=frontmatter,
            archived=archived,
            title=name,
        )

    current_intent = _CURRENT_FILENAME_RE.match(path.stem) or {"id", "title"} & frontmatter.keys()
    if current_intent:
        raw_id = frontmatter.get("id")
        raw_title = frontmatter.get("title")
        if not isinstance(raw_id, str) or not _CURRENT_ID_RE.fullmatch(raw_id):
            raise ValueError("Current milestone id must match m-N")
        if not isinstance(raw_title, str) or not (title := raw_title.strip()):
            raise ValueError("Current milestone title is required")
        due_date = frontmatter.get("due_date")
        return MilestoneRecord(
            name=title,
            path=path,
            path_relative=path.relative_to(project.backlog_dir).as_posix(),
            content=content,
            frontmatter=frontmatter,
            archived=archived,
            id=f"m-{int(raw_id[2:])}",
            title=title,
            due_date=due_date.strip() or None if isinstance(due_date, str) else None,
            format="current",
        )

    name = _name_from_filename(path)
    return MilestoneRecord(
        name=name,
        path=path,
        path_relative=path.relative_to(project.backlog_dir).as_posix(),
        content=content,
        frontmatter=frontmatter,
        archived=archived,
        title=name,
    )


_CURRENT_FILENAME_RE = re.compile(r"^m-[0-9]+(?:$| - )", re.IGNORECASE)
_CURRENT_ID_RE = re.compile(r"^m-[0-9]+$", re.IGNORECASE)


def _description_from_body(content: str) -> str:
    match = re.search(
        r"^## Description$(.*?)(?=^[ ]{0,3}##(?:[ \t]|$)|\Z)", content, re.MULTILINE | re.DOTALL
    )
    return match.group(1).strip() if match else ""


def _milestone_sort_key(milestone: MilestoneRecord) -> tuple[int, int, int, str, str, str]:
    title_or_name = (milestone.title or milestone.name).casefold()
    if milestone.format == "current" and milestone.id is not None:
        return (
            int(milestone.archived),
            0,
            int(milestone.id[2:]),
            title_or_name,
            milestone.name.casefold(),
            milestone.path_relative.casefold(),
        )
    return (
        int(milestone.archived),
        1,
        0,
        title_or_name,
        milestone.name.casefold(),
        milestone.path_relative.casefold(),
    )


def _write_task_updates(updates: list[_TaskReferenceUpdate], written: list[_TaskReferenceUpdate]) -> None:
    for update in updates:
        _atomic_write_text(update.path, update.updated_source)
        written.append(update)


def _rollback_task_updates(updates: list[_TaskReferenceUpdate]) -> None:
    for update in reversed(updates):
        try:
            _atomic_write_text(update.path, update.original_source)
        except Exception as exc:
            logger.warning("Failed to rollback task milestone reference {}: {}", update.path, exc)


def _unlink_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Failed to remove partially written milestone {}: {}", path, exc)


def _render_milestone(frontmatter: dict[str, Any], content: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = content.strip()
    return f"---\n{yaml_text}\n---\n\n{body}\n"


def _slug_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", name.strip()).strip("-")
    if not slug:
        raise MilestoneMutationError("Milestone name is required")
    return slug


def _name_from_filename(path: Path) -> str:
    return path.stem.replace("-", " ")
