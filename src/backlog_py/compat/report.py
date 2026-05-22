from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from backlog_py.compat.inventory import CompatibilityInventory, CompatibilityItem


@dataclass(frozen=True)
class ReleaseGate:
    name: str
    status: str
    scope: str
    requirement: str
    evidence: str


def build_compatibility_report(inventory: CompatibilityInventory) -> dict[str, Any]:
    """Build a stable machine-readable compatibility report."""
    summary = _status_counts(inventory.items)
    release_gates = _release_gates()
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
        "full_browser_release_ready": _full_browser_release_ready(release_gates),
        "summary": summary,
        "categories": categories,
        "items": [_item_to_dict(item) for item in inventory.items],
        "deferred_items": deferred_items,
        "release_gates": {
            "summary": _release_gate_counts(release_gates),
            "gates": [_release_gate_to_dict(gate) for gate in release_gates],
        },
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


def _full_browser_release_ready(gates: tuple[ReleaseGate, ...]) -> bool:
    return all(gate.status != "required" for gate in gates)


def _release_gates() -> tuple[ReleaseGate, ...]:
    return (
        ReleaseGate(
            name="browser:rich-edit-e2e-release-check",
            status="required",
            scope="full-browser-release",
            requirement="Run browser E2E coverage for rich edit flows before advertising full browser parity.",
            evidence="docs/browser-parity.md",
        ),
        ReleaseGate(
            name="browser:desktop-mobile-screenshot-release-check",
            status="required",
            scope="full-browser-release",
            requirement="Capture desktop and mobile browser screenshots before advertising full browser parity.",
            evidence="docs/browser-parity.md",
        ),
        ReleaseGate(
            name="browser:complex-wysiwyg-round-trip",
            status="not_applicable",
            scope="deferred-until-full-wysiwyg-scope",
            requirement="Only required if a future milestone claims full WYSIWYG editing for complex Markdown.",
            evidence="docs/browser-parity.md",
        ),
        ReleaseGate(
            name="browser:shell-hook-settings",
            status="passed",
            scope="rejected-in-browser",
            requirement="Keep shell-hook execution and hook-bypass settings out of the browser API.",
            evidence="docs/upstream-feature-parity.md",
        ),
        ReleaseGate(
            name="browser:service-transport-shutdown",
            status="passed",
            scope="implemented-sse-contract",
            requirement="Document shutdown policy for the implemented SSE/polling service transport.",
            evidence="docs/browser-parity.md",
        ),
    )


def _release_gate_counts(gates: tuple[ReleaseGate, ...]) -> dict[str, int]:
    counts = Counter(gate.status for gate in gates)
    return {
        "passed": counts.get("passed", 0),
        "required": counts.get("required", 0),
        "not_applicable": counts.get("not_applicable", 0),
        "total": len(gates),
    }


def _release_gate_to_dict(gate: ReleaseGate) -> dict[str, Any]:
    return {
        "name": gate.name,
        "status": gate.status,
        "scope": gate.scope,
        "requirement": gate.requirement,
        "evidence": gate.evidence,
    }
