from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backlog_py.compat.inventory import CompatibilityInventory, CompatibilityItem


@dataclass(frozen=True)
class ReleaseGate:
    name: str
    status: str
    scope: str
    requirement: str
    evidence: str
    artifacts: tuple[str, ...] = ()
    evidence_error: str | None = None


def build_compatibility_report(
    inventory: CompatibilityInventory,
    *,
    release_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a stable machine-readable compatibility report."""
    summary = _status_counts(inventory.items)
    release_gates = _release_gates()
    if release_evidence_path is not None:
        release_gates = _apply_release_evidence(release_gates, Path(release_evidence_path))
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
        "artifacts": list(gate.artifacts),
        "evidence_error": gate.evidence_error,
    }


def _apply_release_evidence(
    gates: tuple[ReleaseGate, ...],
    evidence_path: Path,
) -> tuple[ReleaseGate, ...]:
    raw_evidence = _load_release_evidence(evidence_path)
    return tuple(_apply_gate_evidence(gate, raw_evidence.get(gate.name)) for gate in gates)


def _load_release_evidence(evidence_path: Path) -> Mapping[str, object]:
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read release evidence: {evidence_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Release evidence is not valid JSON: {evidence_path}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Release evidence must be a JSON object.")
    gates = raw.get("release_gates")
    if not isinstance(gates, Mapping):
        raise ValueError("Release evidence must contain a release_gates object.")
    return gates


def _apply_gate_evidence(gate: ReleaseGate, raw: object) -> ReleaseGate:
    if gate.status != "required" or raw is None:
        return gate
    if not isinstance(raw, Mapping):
        return _gate_with_error(gate, "Release evidence entry must be an object.")
    artifacts = _artifact_strings(raw.get("artifacts"))
    if raw.get("status") != "passed":
        return _gate_with_error(gate, "Release evidence status must be passed.", artifacts=artifacts)
    if not artifacts:
        return _gate_with_error(gate, "Release evidence requires at least one artifact.")
    if (
        gate.name == "browser:desktop-mobile-screenshot-release-check"
        and not _has_desktop_and_mobile_artifacts(artifacts)
    ):
        return _gate_with_error(
            gate,
            "Screenshot release evidence requires desktop and mobile artifacts.",
            artifacts=artifacts,
        )
    return ReleaseGate(
        name=gate.name,
        status="passed",
        scope=gate.scope,
        requirement=gate.requirement,
        evidence=gate.evidence,
        artifacts=artifacts,
    )


def _artifact_strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    artifacts = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    return artifacts


def _has_desktop_and_mobile_artifacts(artifacts: tuple[str, ...]) -> bool:
    normalized = " ".join(artifacts).casefold()
    return "desktop" in normalized and "mobile" in normalized


def _gate_with_error(
    gate: ReleaseGate,
    evidence_error: str,
    *,
    artifacts: tuple[str, ...] = (),
) -> ReleaseGate:
    return ReleaseGate(
        name=gate.name,
        status=gate.status,
        scope=gate.scope,
        requirement=gate.requirement,
        evidence=gate.evidence,
        artifacts=artifacts,
        evidence_error=evidence_error,
    )
