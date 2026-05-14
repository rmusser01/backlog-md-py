from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from backlog_py.compat.inventory import CompatibilityInventory, CompatibilityItem


def build_compatibility_report(inventory: CompatibilityInventory) -> dict[str, Any]:
    """Build a stable machine-readable compatibility report."""
    summary = _status_counts(inventory.items)
    categories: dict[str, dict[str, int]] = {}
    by_category: dict[str, list[CompatibilityItem]] = defaultdict(list)
    for item in inventory.items:
        category = item.name.split(":", 1)[0]
        by_category[category].append(item)
    for category in sorted(by_category):
        categories[category] = _status_counts(tuple(by_category[category]))

    deferred_items = [
        {
            "name": item.name,
            "classification": item.classification,
            "expected": item.expected,
            "reason": item.deferred_reason or "",
        }
        for item in inventory.items
        if item.status == "deferred"
    ]

    return {
        "agent_cutover_ready": _agent_cutover_ready(inventory.items),
        "summary": summary,
        "categories": categories,
        "items": [_item_to_dict(item) for item in inventory.items],
        "deferred_items": deferred_items,
    }


def _item_to_dict(item: CompatibilityItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "classification": item.classification,
        "upstream_reference": item.upstream_reference,
        "expected": item.expected,
        "status": item.status,
        "fixture": item.fixture,
        "deferred_reason": item.deferred_reason,
    }


def _status_counts(items: tuple[CompatibilityItem, ...]) -> dict[str, int]:
    counts = Counter(item.status for item in items)
    return {
        "implemented": counts.get("implemented", 0),
        "deferred": counts.get("deferred", 0),
        "total": len(items),
    }


def _agent_cutover_ready(items: tuple[CompatibilityItem, ...]) -> bool:
    return all(
        item.status == "implemented"
        for item in items
        if item.classification == "golden-required"
    )
