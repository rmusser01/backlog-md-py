from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathContainmentError(ValueError):
    base: Path
    candidate: Path

    def __str__(self) -> str:
        return f"Path {self.candidate} is outside allowed base {self.base}"


class PathComponentRedirectError(PathContainmentError):
    """A component of a trusted anchor is a symlink, even one that stays in the project.

    Subclasses ``PathContainmentError`` so every existing handler keeps catching
    it; only the message differs, because "outside allowed base" would be a
    false statement about a link that resolves within the project.
    """

    def __str__(self) -> str:
        return f"Path {self.candidate} is a symlink and cannot be trusted as a base directory"


def assert_path_within_base(base: Path, candidate: Path) -> Path:
    """Resolve both paths and reject candidates outside the real base directory.

    Both sides must be resolved the same way: comparing an unresolved base
    against a resolved candidate rejects every legitimate path whenever the
    project is reached through a symlink (macOS ``/tmp`` -> ``/private/tmp``, a
    symlinked checkout, a container bind mount). Resolving the candidate still
    defeats symlinks planted inside the tree that point outside it.
    """
    resolved_base = base.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate == resolved_base or resolved_candidate.is_relative_to(resolved_base):
        return resolved_candidate
    raise PathContainmentError(base=resolved_base, candidate=resolved_candidate)


def assert_trusted_subpath(root: Path, candidate: Path) -> Path:
    """Validate every component of ``candidate`` against its verified parent.

    ``assert_path_within_base`` resolves the base, which is only meaningful when
    the base is already trusted. A directory anchor taken straight from the
    project layout is not: a repository can ship ``backlog/docs`` as a symlink,
    and resolving it would make the attacker's target the base, so everything
    "inside" it passes. Walking down from ``root`` one component at a time means
    a symlink planted at any level is rejected instead of becoming the new base.

    Containment alone is too weak here. A link to a sibling *inside* the project
    resolves within the root and would pass, yet ``backlog/docs ->
    backlog/decisions`` silently redirects every document write into the
    decisions directory, where documents overwrite decision files. Each
    component is therefore checked directly for being a symlink before resolution.
    Missing entries remain valid because ``Path.is_symlink()`` uses ``lstat`` and
    returns false when the component does not exist. Deliberately relocating
    ``backlog/docs`` elsewhere on disk is refused too; a trusted base has to be
    the directory it names.

    ``root`` itself is resolved once up front and is exempt, which is what keeps
    a project reached through a symlink working (macOS ``/tmp`` ->
    ``/private/tmp``, a symlinked checkout, a container bind mount).
    """
    resolved_root = root.resolve()
    try:
        relative = candidate.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise PathContainmentError(base=resolved_root, candidate=candidate.absolute()) from exc
    current = resolved_root
    for part in relative.parts:
        next_path = current / part
        is_symlink = next_path.is_symlink()
        # Escapes keep their containment message; this only resolves the link.
        resolved_next = assert_path_within_base(current, next_path)
        if is_symlink or resolved_next != next_path:
            raise PathComponentRedirectError(base=current, candidate=next_path)
        current = resolved_next
    return current
