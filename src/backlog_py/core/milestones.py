from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

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
        return resolve_milestone_from_records(
            reference,
            self.list_milestones(include_archived=include_archived),
        )

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
        new_title = existing.title if title is None else _required_title(title)
        if title is None:
            target = existing.path
        elif existing.format == "current":
            if existing.id is None:
                raise MilestoneMutationError(
                    f"Current milestone is missing an id: {existing.path.name}"
                )
            target = self._current_active_path(existing.id, new_title)
        else:
            target = self._active_path(new_title)
        if title is not None:
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
        source_bytes = source.encode("utf-8")
        parse_task_markdown(source)
        original_source = safe_existing.read_bytes()
        task_updates = self._task_reference_updates(existing, new_title, known) if update_tasks else []
        written_updates: list[_TaskReferenceUpdate] = []
        move_identity: tuple[int, int] | None = None
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
                move_identity = _move_no_clobber(move_source, move_target, base=self.project.backlog_dir)
            result = self._load_milestone(safe_target, archived=False)
            if move_identity is not None:
                _assert_published_milestone(
                    safe_target,
                    move_identity,
                    source_bytes,
                    base=self.project.backlog_dir,
                )
        except Exception:
            _rollback_task_updates(written_updates, base=self.project.backlog_dir)
            _rollback_milestone_edit(
                safe_existing,
                safe_target,
                original_source,
                source_bytes,
                same_path=same_path,
                published_identity=move_identity,
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
        target = self._archive_path(existing.path.name)
        if target.exists():
            raise MilestoneConflictError(f"Archived milestone already exists: {existing.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_existing = self._mutation_path(existing.path)
        safe_target = self._mutation_path(target)
        original_source = safe_existing.read_bytes()
        move_identity: tuple[int, int] | None = None
        try:
            move_source = self._mutation_path(safe_existing)
            move_target = self._mutation_path(safe_target)
            move_identity = _move_no_clobber(move_source, move_target, base=self.project.backlog_dir)
            result = self._load_milestone(safe_target, archived=True)
            _assert_published_milestone(
                safe_target,
                move_identity,
                original_source,
                base=self.project.backlog_dir,
            )
        except Exception:
            _rollback_milestone_move(
                safe_existing,
                safe_target,
                original_source,
                published_identity=move_identity,
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
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(?P<fence>`+|~+)[ \t]*$")
_DESCRIPTION_H2_RE = re.compile(r"^ {0,3}##[ \t]+Description(?:[ \t]+#+)?[ \t]*$")
_H2_RE = re.compile(r"^ {0,3}##(?:[ \t]+|$)")


def _description_from_body(content: str) -> str:
    bounds = _description_section_bounds(content)
    return "" if bounds is None else content[bounds[1] : bounds[2]].strip()


def _replace_first_description(content: str, description: str) -> str:
    replacement = f"## Description\n\n{description.strip()}".rstrip()
    bounds = _description_section_bounds(content)
    if bounds is None:
        return f"{replacement}\n\n{content}".rstrip()
    section_start, _, section_end = bounds
    suffix = content[section_end:]
    separator = "\n\n" if suffix else ""
    return f"{content[:section_start]}{replacement}{separator}{suffix}".rstrip()


def _description_section_bounds(content: str) -> tuple[int, int, int] | None:
    description: tuple[int, int] | None = None
    fence_character: str | None = None
    fence_length = 0
    offset = 0
    for line in content.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if fence_character is not None:
            closing = _FENCE_CLOSE_RE.fullmatch(text)
            if (
                closing is not None
                and closing.group("fence")[0] == fence_character
                and len(closing.group("fence")) >= fence_length
            ):
                fence_character = None
            offset += len(line)
            continue

        opening = _FENCE_OPEN_RE.fullmatch(text)
        if opening is not None:
            fence = opening.group("fence")
            rest = opening.group("rest")
            if fence[0] == "~" or "`" not in rest:
                fence_character = fence[0]
                fence_length = len(fence)
                offset += len(line)
                continue

        if description is None and _DESCRIPTION_H2_RE.fullmatch(text):
            description = (offset, offset + len(line))
        elif description is not None and _H2_RE.match(text):
            return description[0], description[1], offset
        offset += len(line)
    if description is None:
        return None
    return description[0], description[1], len(content)


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


def resolve_milestone_from_records(
    reference: str,
    records: Sequence[MilestoneRecord],
) -> MilestoneRecord:
    return _resolve_milestone(reference, records)


def _matching_milestones(
    reference: str,
    records: Sequence[MilestoneRecord],
) -> list[MilestoneRecord]:
    requested = reference.strip().casefold()
    return [record for record in records if requested in _milestone_aliases(record)]


def _resolve_milestone(reference: str, records: Sequence[MilestoneRecord]) -> MilestoneRecord:
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


def _move_no_clobber(source: Path, target: Path, *, base: Path) -> tuple[int, int]:
    safe_source = _mutation_path(base, source)
    safe_target = _mutation_path(base, target)
    source_identity = _path_identity(safe_source)
    try:
        os.link(safe_source, safe_target, follow_symlinks=False)
    except FileExistsError as exc:
        raise MilestoneConflictError(f"Milestone path conflicts with an existing file: {safe_target.name}") from exc
    except Exception:
        _remove_created_link(
            safe_source,
            safe_target,
            source_identity,
            publication_succeeded=False,
            base=base,
        )
        raise
    try:
        safe_source = _mutation_path(base, safe_source)
        safe_target = _mutation_path(base, safe_target)
        if _path_identity(safe_source) != source_identity or _path_identity(safe_target) != source_identity:
            raise MilestoneConflictError(f"Milestone path conflict during move: {safe_target.name}")
        safe_source.unlink()
    except Exception:
        _remove_created_link(
            safe_source,
            safe_target,
            source_identity,
            publication_succeeded=True,
            base=base,
        )
        raise
    return source_identity


def _path_identity(path: Path) -> tuple[int, int]:
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino


def _path_has_identity(path: Path, identity: tuple[int, int], *, base: Path) -> bool:
    try:
        return _path_identity(_mutation_path(base, path)) == identity
    except FileNotFoundError:
        return False


def _published_milestone_matches(
    path: Path,
    identity: tuple[int, int],
    expected_source: bytes,
    *,
    base: Path,
) -> bool:
    try:
        safe_path = _mutation_path(base, path)
        identity_before = _path_identity(safe_path)
        safe_path = _mutation_path(base, safe_path)
        source = safe_path.read_bytes()
        safe_path = _mutation_path(base, safe_path)
        identity_after = _path_identity(safe_path)
    except FileNotFoundError:
        return False
    return identity_before == identity_after == identity and source == expected_source


def _assert_published_milestone(
    path: Path,
    identity: tuple[int, int],
    expected_source: bytes,
    *,
    base: Path,
) -> None:
    if not _published_milestone_matches(path, identity, expected_source, base=base):
        raise MilestoneConflictError(f"Milestone path conflict after move: {path.name}")


def _unlink_owned_path(path: Path, identity: tuple[int, int], *, base: Path) -> bool:
    safe_path = _mutation_path(base, path)
    if not _path_has_identity(safe_path, identity, base=base):
        return False
    safe_path = _mutation_path(base, safe_path)
    if not _path_has_identity(safe_path, identity, base=base):
        return False
    safe_path.unlink()
    return True


def _unlink_published_milestone(
    path: Path,
    identity: tuple[int, int],
    expected_source: bytes,
    *,
    base: Path,
) -> bool:
    if not _published_milestone_matches(path, identity, expected_source, base=base):
        return False
    return _unlink_owned_path(path, identity, base=base)


def _restore_no_clobber(target: Path, source: bytes, *, base: Path) -> bool:
    safe_target = _mutation_path(base, target)
    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=safe_target.parent,
            prefix=".milestone-rollback-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            stat = os.fstat(handle.fileno())
            temporary_identity = (stat.st_dev, stat.st_ino)
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        safe_temporary = _mutation_path(base, temporary)
        safe_target = _mutation_path(base, safe_target)
        try:
            os.link(safe_temporary, safe_target, follow_symlinks=False)
        except FileExistsError:
            return False
        except Exception:
            _remove_created_link(
                safe_temporary,
                safe_target,
                temporary_identity,
                publication_succeeded=False,
                base=base,
            )
            raise
        return _path_has_identity(safe_target, temporary_identity, base=base)
    finally:
        if temporary is not None and temporary_identity is not None:
            try:
                _unlink_owned_path(temporary, temporary_identity, base=base)
            except Exception as exc:
                logger.warning("Failed to clean up milestone rollback file {}: {}", temporary, exc)


def _remove_created_link(
    source: Path,
    target: Path,
    identity: tuple[int, int],
    *,
    publication_succeeded: bool,
    base: Path,
) -> None:
    try:
        safe_source = _mutation_path(base, source)
        safe_target = _mutation_path(base, target)
        if _path_identity(safe_target) != identity:
            return
        try:
            source_identity = _path_identity(safe_source)
        except FileNotFoundError:
            safe_source = _mutation_path(base, safe_source)
            safe_target = _mutation_path(base, safe_target)
            os.link(safe_target, safe_source, follow_symlinks=False)
            source_identity = _path_identity(safe_source)
        if (source_identity != identity and not publication_succeeded) or _path_identity(safe_target) != identity:
            return
        _mutation_path(base, safe_target).unlink()
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("Failed to rollback milestone link {}: {}", target, exc)


def _rollback_milestone_edit(
    original: Path,
    target: Path,
    original_source: bytes,
    attempted_source: bytes,
    *,
    same_path: bool,
    published_identity: tuple[int, int] | None,
    base: Path,
) -> None:
    try:
        safe_original = _mutation_path(base, original)
    except Exception as exc:
        logger.warning("Failed to rollback milestone edit {}: {}", original, exc)
        return
    original_exists = safe_original.exists()
    if same_path and not original_exists:
        return
    if same_path or original_exists:
        if published_identity is not None:
            try:
                _unlink_published_milestone(target, published_identity, attempted_source, base=base)
            except Exception as exc:
                logger.warning("Failed to clean up moved milestone {}: {}", target, exc)
        try:
            safe_original = _mutation_path(base, safe_original)
            if safe_original.read_bytes() != attempted_source:
                return
            if published_identity is not None and _path_identity(safe_original) != published_identity:
                return
            _atomic_write_text(safe_original, original_source.decode("utf-8"), base=base)
        except Exception as exc:
            logger.warning("Failed to rollback milestone edit {}: {}", original, exc)
        return
    target_owned = False
    if published_identity is not None:
        try:
            target_owned = _published_milestone_matches(
                target,
                published_identity,
                attempted_source,
                base=base,
            )
        except Exception as exc:
            logger.warning("Failed to inspect moved milestone {}: {}", target, exc)
    if target_owned and published_identity is not None:
        try:
            _move_no_clobber(target, safe_original, base=base)
            _atomic_write_text(safe_original, original_source.decode("utf-8"), base=base)
            return
        except Exception as exc:
            logger.warning("Failed to move back milestone {}: {}", original, exc)
            try:
                _unlink_published_milestone(target, published_identity, attempted_source, base=base)
            except Exception as cleanup_error:
                logger.warning("Failed to clean up moved milestone {}: {}", target, cleanup_error)
    try:
        _restore_no_clobber(safe_original, original_source, base=base)
    except Exception as exc:
        logger.warning("Failed to rollback milestone edit {}: {}", original, exc)


def _rollback_milestone_move(
    original: Path,
    target: Path,
    original_source: bytes,
    *,
    published_identity: tuple[int, int] | None,
    base: Path,
) -> None:
    try:
        safe_original = _mutation_path(base, original)
    except Exception as exc:
        logger.warning("Failed to rollback milestone move {}: {}", original, exc)
        return
    if safe_original.exists():
        if published_identity is not None:
            try:
                _unlink_published_milestone(target, published_identity, original_source, base=base)
            except Exception as exc:
                logger.warning("Failed to clean up moved milestone {}: {}", target, exc)
        return
    target_owned = False
    if published_identity is not None:
        try:
            target_owned = _published_milestone_matches(
                target,
                published_identity,
                original_source,
                base=base,
            )
        except Exception as exc:
            logger.warning("Failed to inspect moved milestone {}: {}", target, exc)
    if target_owned and published_identity is not None:
        try:
            _move_no_clobber(target, safe_original, base=base)
            return
        except Exception as exc:
            logger.warning("Failed to move back milestone {}: {}", original, exc)
            try:
                _unlink_published_milestone(target, published_identity, original_source, base=base)
            except Exception as cleanup_error:
                logger.warning("Failed to clean up moved milestone {}: {}", target, cleanup_error)
    try:
        _restore_no_clobber(safe_original, original_source, base=base)
    except Exception as exc:
        logger.warning("Failed to restore milestone {}: {}", original, exc)


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
