from __future__ import annotations

import re


_NUMERIC_ID_RE = re.compile(r"(?P<prefix>.*-)(?P<nums>\d+(?:\.\d+)*)")


def numeric_id_key(identifier: str) -> tuple[str, tuple[int, ...]] | None:
    """Return a zero-padding-insensitive comparison key for a prefixed numeric id.

    ``TASK-007`` and ``task-7`` both map to ``("task-", (7,))``; ``task-1.02`` maps
    to ``("task-", (1, 2))``. Returns ``None`` when the id has no ``prefix-number``
    shape, so callers fall back to plain string comparison.
    """
    match = _NUMERIC_ID_RE.fullmatch(identifier.strip())
    if match is None:
        return None
    prefix = match.group("prefix").casefold()
    nums = tuple(int(part) for part in match.group("nums").split("."))
    return (prefix, nums)


def ids_equivalent(left: str, right: str) -> bool:
    """True when two ids refer to the same entity, ignoring zero-padding and case."""
    if left.strip().casefold() == right.strip().casefold():
        return True
    left_key = numeric_id_key(left)
    return left_key is not None and left_key == numeric_id_key(right)


def format_numbered_id(prefix: str, number: int, zero_padded_ids: int | None) -> str:
    body = _format_number(number, zero_padded_ids)
    return f"{prefix}{body}"


def format_child_task_id(parent_task_id: str, number: int, zero_padded_ids: int | None) -> str:
    child_width = 2 if zero_padded_ids and zero_padded_ids > 0 else None
    return f"{parent_task_id}.{_format_number(number, child_width)}"


def _format_number(number: int, width: int | None) -> str:
    if width is not None and width > 0:
        return str(number).zfill(width)
    return str(number)
