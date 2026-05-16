from __future__ import annotations


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
