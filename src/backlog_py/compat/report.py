from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Any

from backlog_py.compat.inventory import CompatibilityInventory, CompatibilityItem

RELEASE_EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_RELEASE_EVIDENCE_MAX_AGE_DAYS = 14
# Parity statuses are a maintained declaration, not automated verification.
VERIFICATION_METHOD = "self-declared"
UPSTREAM_BASELINE = {
    "package": "backlog.md",
    "version": "1.50.1",
    "audit_date": "2026-09-01",
}


@dataclass(frozen=True)
class ReleaseGate:
    name: str
    status: str
    scope: str
    requirement: str
    evidence: str
    artifacts: tuple[str, ...] = ()
    evidence_error: str | None = None


@dataclass(frozen=True)
class ReleaseEvidence:
    status: str
    path: str | None
    generated_at: str | None
    age_days: int | None
    max_age_days: int | None
    upstream_baseline: dict[str, str] | None
    command: dict[str, object] | None
    error: str | None
    release_gates: Mapping[str, object]


def build_release_evidence_manifest(
    *,
    rich_edit_artifacts: tuple[str, ...] = (),
    desktop_artifacts: tuple[str, ...] = (),
    mobile_artifacts: tuple[str, ...] = (),
    command_argv: tuple[str, ...] = (),
    generated_at: date | None = None,
    max_age_days: int = DEFAULT_RELEASE_EVIDENCE_MAX_AGE_DAYS,
) -> dict[str, object]:
    """Create a portable browser release-evidence manifest template."""
    generated_date = generated_at or date.today()
    command = tuple(command_argv) or ("backlog-py", "compat", "evidence-template")
    artifacts = (*rich_edit_artifacts, *desktop_artifacts, *mobile_artifacts)
    if _has_absolute_artifacts(artifacts):
        raise ValueError("Release evidence requires relative artifact paths.")
    return {
        "schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION,
        "generated_at": generated_date.isoformat(),
        "upstream_baseline": dict(UPSTREAM_BASELINE),
        "command": {
            "argv": list(command),
            "cwd": ".",
        },
        "freshness": {
            "max_age_days": max_age_days,
        },
        "release_gates": {
            "browser:rich-edit-e2e-release-check": {
                "status": "passed" if rich_edit_artifacts else "required",
                "artifacts": list(rich_edit_artifacts),
            },
            "browser:desktop-mobile-screenshot-release-check": {
                "status": "passed" if desktop_artifacts and mobile_artifacts else "required",
                "artifacts": [*desktop_artifacts, *mobile_artifacts],
            },
        },
    }


def build_compatibility_report(
    inventory: CompatibilityInventory,
    *,
    release_evidence_path: str | Path | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build a stable machine-readable compatibility report."""
    summary = _status_counts(inventory.items)
    release_gates = _release_gates()
    release_evidence = _missing_release_evidence()
    if release_evidence_path is not None:
        release_evidence = _load_release_evidence(
            Path(release_evidence_path),
            today=today or date.today(),
        )
        if release_evidence.status == "fresh":
            release_gates = _apply_release_evidence(release_gates, release_evidence.release_gates)
        else:
            release_gates = _apply_release_evidence_error(
                release_gates,
                release_evidence.error or "Release evidence is not fresh.",
            )
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
        # The inventory statuses are a maintained declaration of parity, not
        # the result of automated per-item verification. Surface that honestly
        # so consumers do not read "implemented" as "measured".
        "verification": VERIFICATION_METHOD,
        "agent_cutover_ready": _agent_cutover_ready(inventory.items),
        "full_browser_release_ready": _full_browser_release_ready(release_gates),
        "upstream_baseline": dict(UPSTREAM_BASELINE),
        "summary": summary,
        "categories": categories,
        "items": [_item_to_dict(item) for item in inventory.items],
        "deferred_items": deferred_items,
        "release_evidence": _release_evidence_to_dict(release_evidence),
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
        "verification": VERIFICATION_METHOD,
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
    raw_evidence: Mapping[str, object],
) -> tuple[ReleaseGate, ...]:
    return tuple(_apply_gate_evidence(gate, raw_evidence.get(gate.name)) for gate in gates)


def _apply_release_evidence_error(
    gates: tuple[ReleaseGate, ...],
    evidence_error: str,
) -> tuple[ReleaseGate, ...]:
    return tuple(
        _gate_with_error(gate, evidence_error) if gate.status == "required" else gate
        for gate in gates
    )


def _missing_release_evidence() -> ReleaseEvidence:
    return ReleaseEvidence(
        status="missing",
        path=None,
        generated_at=None,
        age_days=None,
        max_age_days=None,
        upstream_baseline=None,
        command=None,
        error=None,
        release_gates={},
    )


def _load_release_evidence(evidence_path: Path, *, today: date) -> ReleaseEvidence:
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read release evidence: {evidence_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Release evidence is not valid JSON: {evidence_path}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Release evidence must be a JSON object.")
    generated_at = _required_string(raw, "generated_at")
    _schema_version(raw.get("schema_version"))
    generated_date = _parse_evidence_date(generated_at, "generated_at")
    upstream_baseline = _upstream_baseline(raw.get("upstream_baseline"))
    command = _command_provenance(raw.get("command"))
    max_age_days = _max_age_days(raw.get("freshness"))
    gates = raw.get("release_gates")
    if not isinstance(gates, Mapping):
        raise ValueError("Release evidence must contain a release_gates object.")
    age_days = (today - generated_date).days
    errors: list[str] = []
    baseline_error = _upstream_baseline_error(upstream_baseline)
    if baseline_error is not None:
        errors.append(baseline_error)
    if age_days < 0:
        errors.append("Release evidence generated_at is in the future.")
    elif age_days > max_age_days:
        errors.append(f"Release evidence is stale: age {age_days} days exceeds maxAgeDays {max_age_days}.")
    status = "stale" if errors else "fresh"
    error = " ".join(errors) if errors else None
    return ReleaseEvidence(
        status=status,
        path=evidence_path.as_posix(),
        generated_at=generated_date.isoformat(),
        age_days=age_days,
        max_age_days=max_age_days,
        upstream_baseline=upstream_baseline,
        command=command,
        error=error,
        release_gates=gates,
    )


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Release evidence must contain a non-empty {key} string.")
    return value.strip()


def _schema_version(raw: object) -> None:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw != RELEASE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            f"Release evidence schema_version must be {RELEASE_EVIDENCE_SCHEMA_VERSION}."
        )


def _parse_evidence_date(value: str, key: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"Release evidence {key} must be an ISO date.") from exc


def _upstream_baseline(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("Release evidence must contain an upstream_baseline object.")
    baseline = {
        "package": _mapping_string(raw, "package", "upstream_baseline"),
        "version": _mapping_string(raw, "version", "upstream_baseline"),
        "audit_date": _mapping_string(raw, "audit_date", "upstream_baseline"),
    }
    _parse_evidence_date(baseline["audit_date"], "upstream_baseline.audit_date")
    return baseline


def _upstream_baseline_error(baseline: Mapping[str, str]) -> str | None:
    expected = dict(UPSTREAM_BASELINE)
    if dict(baseline) == expected:
        return None
    expected_text = (
        f"{expected['package']} {expected['version']} "
        f"audited {expected['audit_date']}"
    )
    actual_text = (
        f"{baseline['package']} {baseline['version']} "
        f"audited {baseline['audit_date']}"
    )
    return (
        "Release evidence upstream_baseline does not match current "
        f"compatibility baseline: expected {expected_text}, got {actual_text}."
    )


def _command_provenance(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("Release evidence must contain a command object.")
    argv = raw.get("argv")
    if not isinstance(argv, list):
        raise ValueError("Release evidence command.argv must be a list.")
    command_argv = [item.strip() for item in argv if isinstance(item, str) and item.strip()]
    if not command_argv:
        raise ValueError("Release evidence command.argv must contain at least one command item.")
    cwd = raw.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("Release evidence command.cwd must be a string when provided.")
    return {
        "argv": command_argv,
        "cwd": cwd.strip(),
    }


def _max_age_days(raw: object) -> int:
    if not isinstance(raw, Mapping):
        raise ValueError("Release evidence must contain a freshness object.")
    value = raw.get("max_age_days")
    if not isinstance(value, int) or value <= 0:
        raise ValueError("Release evidence freshness.max_age_days must be a positive integer.")
    return value


def _mapping_string(raw: Mapping[object, object], key: str, object_name: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Release evidence {object_name}.{key} must be a non-empty string.")
    return value.strip()


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
    if _has_absolute_artifacts(artifacts):
        return _gate_with_error(
            gate,
            "Release evidence requires relative artifact paths.",
            artifacts=artifacts,
        )
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


def _has_absolute_artifacts(artifacts: tuple[str, ...]) -> bool:
    return any(
        Path(artifact).is_absolute() or PureWindowsPath(artifact).is_absolute()
        for artifact in artifacts
    )


def _release_evidence_to_dict(evidence: ReleaseEvidence) -> dict[str, object]:
    return {
        "status": evidence.status,
        "path": evidence.path,
        "generated_at": evidence.generated_at,
        "age_days": evidence.age_days,
        "max_age_days": evidence.max_age_days,
        "upstream_baseline": evidence.upstream_baseline,
        "command": evidence.command,
        "error": evidence.error,
    }


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
