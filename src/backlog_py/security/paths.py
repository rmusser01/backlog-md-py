from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathContainmentError(ValueError):
    base: Path
    candidate: Path

    def __str__(self) -> str:
        return f"Path {self.candidate} is outside allowed base {self.base}"


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
