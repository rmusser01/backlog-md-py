from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
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


class MilestoneConflictError(MilestoneMutationError):
    """Raised for duplicate or ambiguous milestone state."""


_UNSET = object()


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
        self.archive_dir = assert_trusted_subpath(project.root, project.backlog_dir / "archive" / "milestones")

    def add_milestone(self, name: str, description: str = "", *, due_date: str | None = None) -> MilestoneRecord:
        title = _required_title(name)
        milestone_id = self._next_milestone_id()
        normalized_due_date = None if due_date is None or due_date == "" else _normalize_due_date(due_date)
        frontmatter = {"id": milestone_id, "title": title}
        if normalized_due_date is not None:
            frontmatter["due_date"] = normalized_due_date
        source = _render_milestone(frontmatter, f"## Description\n\n{description.strip()}")
        parse_task_markdown(source)
        target = self._current_active_path(milestone_id, title)
        self._assert_aliases_available(
            _current_aliases(milestone_id, title, target.stem),
            self.list_milestones(),
        )
        if target.exists():
            raise MilestoneConflictError(f"Milestone path conflicts with an existing file: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _atomic_write_text(target, source, base=self.project.backlog_dir)
        except TaskMutationError as exc:
            raise MilestoneMutationError(str(exc)) from exc
        return self._load_milestone(target, archived=False)

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

    def resolve_milestone(self, reference: str, *, include_archived: bool = True) -> MilestoneRecord:
        return _resolve_milestone(reference, self.list_milestones(include_archived=include_archived))

    def edit_milestone(
        self,
        reference: str,
        *,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None | object = _UNSET,
        update_tasks: bool = False,
    ) -> MilestoneRecord:
        known = self.list_milestones(include_archived=update_tasks)
        active = [record for record in known if not record.archived]
        existing = _resolve_milestone(reference, active)
        self._assert_aliases_available(_milestone_aliases(existing), active, ignore=existing)
        new_title = existing.title if title is None else _required_title(title)
        if title is None:
            target = existing.path
        elif existing.format == "current":
            assert existing.id is not None
            target = self._current_active_path(existing.id, new_title)
        else:
            target = self._active_path(new_title)
        candidate_aliases = (
            _current_aliases(existing.id, new_title, target.stem)
            if existing.format == "current" and existing.id is not None
            else {new_title.casefold(), target.stem.casefold()}
        )
        self._assert_aliases_available(candidate_aliases, active, ignore=existing)

        safe_existing = self._mutation_path(existing.path)
        safe_target = self._mutation_path(target)
        same_path = _paths_are_same(safe_existing, safe_target)
        if target.exists() and not same_path:
            raise MilestoneConflictError(f"Milestone path conflicts with an existing file: {target.name}")

        frontmatter = dict(existing.frontmatter)
        content = existing.content
        if existing.format == "current":
            if title is not None:
                frontmatter["title"] = new_title
            if due_date is not _UNSET:
                if due_date is None or due_date == "":
                    frontmatter.pop("due_date", None)
                else:
                    frontmatter["due_date"] = _normalize_due_date(due_date)
            if description is not None:
                content = _replace_first_description(content, description)
        else:
            if title is not None:
                frontmatter["name"] = new_title
            if description is not None:
                content = description.strip()
        source = _render_milestone(frontmatter, content)
        parse_task_markdown(source)
        original_source = safe_existing.read_text(encoding="utf-8")
        task_updates = self._task_reference_updates(existing, new_title, known) if update_tasks else []
        written_updates: list[_TaskReferenceUpdate] = []
        try:
            _atomic_write_text(safe_existing, source, base=self.project.backlog_dir)
            _write_task_updates(
                task_updates,
                written_updates,
                base=self.project.backlog_dir,
            )
            if not same_path:
                move_source = self._mutation_path(safe_existing)
                move_target = self._mutation_path(safe_target)
                if move_target.exists():
                    raise MilestoneConflictError(f"Milestone path conflicts with an existing file: {move_target.name}")
                os.replace(move_source, move_target)
            result = self._load_milestone(safe_target, archived=False)
        except Exception:
            _rollback_task_updates(written_updates, base=self.project.backlog_dir)
            _rollback_milestone_edit(
                safe_existing,
                safe_target,
                original_source,
                same_path=same_path,
                base=self.project.backlog_dir,
            )
            raise
        return result

    def rename_milestone(self, old_name: str, new_name: str, *, update_tasks: bool = False) -> MilestoneRecord:
        return self.edit_milestone(old_name, title=new_name, update_tasks=update_tasks)

    def remove_milestone(self, name: str, *, clear_tasks: bool = False) -> MilestoneRecord:
        known = self.list_milestones(include_archived=clear_tasks)
        active = [record for record in known if not record.archived]
        existing = _resolve_milestone(name, active)
        self._assert_aliases_available(_milestone_aliases(existing), active, ignore=existing)
        task_updates = self._task_reference_updates(existing, None, known) if clear_tasks else []
        safe_existing = self._mutation_path(existing.path)
        original_source = safe_existing.read_text(encoding="utf-8")
        written_updates: list[_TaskReferenceUpdate] = []
        try:
            _write_task_updates(
                task_updates,
                written_updates,
                base=self.project.backlog_dir,
            )
            self._mutation_path(safe_existing).unlink()
        except Exception:
            if not safe_existing.exists():
                try:
                    _atomic_write_text(
                        safe_existing,
                        original_source,
                        base=self.project.backlog_dir,
                    )
                except Exception as rollback_error:
                    logger.warning(
                        "Failed to rollback removed milestone {}: {}",
                        safe_existing,
                        rollback_error,
                    )
            _rollback_task_updates(written_updates, base=self.project.backlog_dir)
            raise
        return existing

    def archive_milestone(self, name: str) -> MilestoneRecord:
        active = self.list_milestones()
        existing = _resolve_milestone(name, active)
        self._assert_aliases_available(_milestone_aliases(existing), active, ignore=existing)
        target = self._archive_path(existing.path.name)
        if target.exists():
            raise MilestoneConflictError(f"Archived milestone already exists: {existing.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_existing = self._mutation_path(existing.path)
        safe_target = self._mutation_path(target)
        try:
            move_source = self._mutation_path(safe_existing)
            move_target = self._mutation_path(safe_target)
            if move_target.exists():
                raise MilestoneConflictError(f"Archived milestone already exists: {existing.name}")
            os.replace(move_source, move_target)
            result = self._load_milestone(safe_target, archived=True)
        except Exception:
            _rollback_milestone_move(
                safe_existing,
                safe_target,
                base=self.project.backlog_dir,
            )
            raise
        return result

    def _find_active(self, name: str) -> MilestoneRecord:
        return self.resolve_milestone(name, include_archived=False)

    def _assert_aliases_available(
        self,
        aliases: set[str],
        records: list[MilestoneRecord],
        *,
        ignore: MilestoneRecord | None = None,
    ) -> None:
        for record in records:
            if ignore is not None and record.path == ignore.path:
                continue
            overlap = aliases & _milestone_aliases(record)
            if overlap:
                alias = sorted(overlap)[0]
                raise MilestoneConflictError(f"Milestone alias conflict for {alias!r} with {record.name!r}")

    def _mutation_path(self, path: Path) -> Path:
        try:
            return _mutation_path(self.project.backlog_dir, path)
        except TaskMutationError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _active_path(self, name: str) -> Path:
        path = self.active_dir / f"{_slug_name(name)}.md"
        try:
            return assert_path_within_base(self.active_dir, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _current_active_path(self, milestone_id: str, title: str) -> Path:
        path = self.active_dir / f"{milestone_id} - {_safe_current_filename_title(title)}.md"
        try:
            return assert_path_within_base(self.active_dir, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _next_milestone_id(self) -> str:
        reserved: set[int] = set()
        for directory in (self.active_dir, self.archive_dir):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.md"):
                if path.name.casefold() == "readme.md":
                    continue
                filename_match = _CURRENT_FILENAME_RE.match(path.stem)
                if filename_match:
                    reserved.add(int(filename_match.group(1)))
                try:
                    trusted_path = assert_path_within_base(directory, path)
                except PathContainmentError as exc:
                    raise MilestoneMutationError(str(exc)) from exc
                try:
                    source = trusted_path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError) as exc:
                    raise MilestoneMutationError(f"Unable to read milestone file {trusted_path}: {exc}") from exc
                try:
                    frontmatter = parse_task_markdown(source).frontmatter
                except ValueError:
                    continue
                raw_id = frontmatter.get("id")
                if "name" not in frontmatter and isinstance(raw_id, str) and _CURRENT_ID_RE.fullmatch(raw_id):
                    reserved.add(int(raw_id[2:]))
        return f"m-{max(reserved, default=0) + 1}"

    def _archive_path(self, filename: str) -> Path:
        path = self.archive_dir / filename
        try:
            return assert_path_within_base(self.archive_dir, path)
        except PathContainmentError as exc:
            raise MilestoneMutationError(str(exc)) from exc

    def _task_reference_updates(
        self,
        existing: MilestoneRecord,
        new_name: str | None,
        records: list[MilestoneRecord],
    ) -> list[_TaskReferenceUpdate]:
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
            if isinstance(milestone, bool) or not isinstance(milestone, (str, int)):
                continue
            milestone_reference = str(milestone)
            matches = _matching_milestones(milestone_reference, records)
            if len(matches) != 1 or matches[0].path != existing.path:
                continue
            frontmatter = dict(task.parsed.frontmatter)
            if new_name is None:
                frontmatter.pop("milestone", None)
            elif (
                existing.format == "current"
                and existing.id is not None
                and milestone_reference.casefold() in {existing.id.casefold(), existing.id[2:]}
            ):
                continue
            elif existing.format == "current" and existing.id is not None:
                frontmatter["milestone"] = existing.id
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


_CURRENT_FILENAME_RE = re.compile(r"^m-([0-9]+)(?:\s+-|$)", re.IGNORECASE | re.ASCII)
_CURRENT_ID_RE = re.compile(r"^m-[0-9]+$", re.IGNORECASE | re.ASCII)
_FORBIDDEN_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\\\|?*]')


def _description_from_body(content: str) -> str:
    match = re.search(r"^## Description$(.*?)(?=^[ ]{0,3}##(?:[ \t]|$)|\Z)", content, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _replace_first_description(content: str, description: str) -> str:
    heading = re.search(r"^## Description(?:\r?\n|$)", content, re.MULTILINE)
    replacement = f"## Description\n\n{description.strip()}".rstrip()
    if heading is None:
        return f"{replacement}\n\n{content}".rstrip()
    next_heading = re.search(
        r"^[ ]{0,3}##(?:[ \t]|$)",
        content[heading.end() :],
        re.MULTILINE,
    )
    section_end = heading.end() + next_heading.start() if next_heading is not None else len(content)
    suffix = content[section_end:].lstrip("\r\n")
    separator = "\n\n" if suffix else ""
    return f"{content[: heading.start()]}{replacement}{separator}{suffix}".rstrip()


def _current_aliases(milestone_id: str, title: str, path_stem: str) -> set[str]:
    return {
        milestone_id.casefold(),
        str(int(milestone_id[2:])),
        path_stem.casefold(),
        title.casefold(),
    }


def _milestone_aliases(record: MilestoneRecord) -> set[str]:
    if record.format == "current" and record.id is not None:
        return _current_aliases(record.id, record.title, record.path.stem)
    return {record.name.casefold(), record.path.stem.casefold()}


def _matching_milestones(
    reference: str,
    records: list[MilestoneRecord],
) -> list[MilestoneRecord]:
    requested = reference.strip().casefold()
    return [record for record in records if requested in _milestone_aliases(record)]


def _resolve_milestone(reference: str, records: list[MilestoneRecord]) -> MilestoneRecord:
    if not isinstance(reference, str) or not reference.strip():
        raise NotFoundError(f"Milestone not found: {reference}")
    matches = _matching_milestones(reference, records)
    if not matches:
        raise NotFoundError(f"Milestone not found: {reference}")
    if len(matches) > 1:
        raise MilestoneConflictError(f"Milestone reference is ambiguous: {reference}")
    return matches[0]


def _paths_are_same(first: Path, second: Path) -> bool:
    if first == second:
        return True
    try:
        return first.exists() and second.exists() and first.samefile(second)
    except OSError:
        return False


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


def _write_task_updates(
    updates: list[_TaskReferenceUpdate],
    written: list[_TaskReferenceUpdate],
    *,
    base: Path,
) -> None:
    for update in updates:
        written.append(update)
        _atomic_write_text(update.path, update.updated_source, base=base)


def _rollback_task_updates(updates: list[_TaskReferenceUpdate], *, base: Path) -> None:
    for update in reversed(updates):
        try:
            _atomic_write_text(update.path, update.original_source, base=base)
        except Exception as exc:
            logger.warning("Failed to rollback task milestone reference {}: {}", update.path, exc)


def _rollback_milestone_edit(
    original: Path,
    target: Path,
    original_source: str,
    *,
    same_path: bool,
    base: Path,
) -> None:
    try:
        safe_original = _mutation_path(base, original)
        safe_target = _mutation_path(base, target)
        if not same_path and not safe_original.exists() and safe_target.exists():
            os.replace(safe_target, safe_original)
        _atomic_write_text(safe_original, original_source, base=base)
    except Exception as exc:
        logger.warning("Failed to rollback milestone edit {}: {}", original, exc)


def _rollback_milestone_move(original: Path, target: Path, *, base: Path) -> None:
    try:
        safe_original = _mutation_path(base, original)
        safe_target = _mutation_path(base, target)
        if not safe_original.exists() and safe_target.exists():
            os.replace(safe_target, safe_original)
    except Exception as exc:
        logger.warning("Failed to rollback milestone move {}: {}", original, exc)


def _render_milestone(frontmatter: dict[str, Any], content: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = content.strip()
    return f"---\n{yaml_text}\n---\n\n{body}\n"


def _slug_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", name.strip()).strip("-")
    if not slug:
        raise MilestoneMutationError("Milestone name is required")
    return slug


def _required_title(name: str) -> str:
    if not isinstance(name, str) or not (title := name.strip()):
        raise MilestoneMutationError("Milestone title is required")
    return title


def _safe_current_filename_title(title: str) -> str:
    safe_title = _FORBIDDEN_FILENAME_CHARS_RE.sub("", title).strip()
    safe_title = re.sub(r"\s+", "-", safe_title).lower()[:50]
    return safe_title or "milestone"


def _normalize_due_date(due_date: object) -> str:
    if not isinstance(due_date, str):
        raise MilestoneMutationError("Milestone due date must be a date and time")
    value = due_date.strip()
    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}", value):
        raise MilestoneMutationError("Milestone due date must include a date and time")
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise MilestoneMutationError("Milestone due date must be a valid ISO date and time") from exc
    return parsed.strftime("%Y-%m-%d %H:%M")


def _name_from_filename(path: Path) -> str:
    return path.stem.replace("-", " ")
