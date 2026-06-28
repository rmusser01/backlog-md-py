from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from backlog_py.core.models import BacklogProject
from backlog_py.orchestration.models import (
    OrchestrationPolicy,
    OrchestrationPolicyError,
    ValidationIssue,
    WorkflowStatePolicy,
    _normalize_key,
    validate_policy,
)


def load_orchestration_policy(project: BacklogProject) -> OrchestrationPolicy:
    path = project.backlog_dir / "orchestration.yml"
    if not path.exists():
        return OrchestrationPolicy.default()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OrchestrationPolicyError(
            f"Unable to read orchestration policy: {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    except UnicodeDecodeError as exc:
        raise OrchestrationPolicyError(
            f"Unable to decode orchestration policy: {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    except yaml.YAMLError as exc:
        raise OrchestrationPolicyError(
            f"Invalid orchestration policy YAML: {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc

    if not isinstance(raw, Mapping):
        raise OrchestrationPolicyError(
            f"Orchestration policy must contain a mapping: {path}",
            details={"path": str(path)},
        )

    default_policy = OrchestrationPolicy.default()
    policy = OrchestrationPolicy(
        states=_parse_states(raw.get("states"), path=str(path)),
        transitions=_parse_transitions(raw.get("transitions"), path=str(path)),
        default_review_max_attempts=_optional_positive_int(
            raw.get("default_review_max_attempts", raw.get("defaultReviewMaxAttempts")),
            default_policy.default_review_max_attempts,
            field="default_review_max_attempts",
            path=str(path),
        ),
        default_lease_ttl_seconds=_optional_positive_int(
            raw.get("default_lease_ttl_seconds", raw.get("defaultLeaseTtlSeconds")),
            default_policy.default_lease_ttl_seconds,
            field="default_lease_ttl_seconds",
            path=str(path),
        ),
    )
    issues = validate_policy(policy)
    if issues:
        raise OrchestrationPolicyError(
            f"Invalid orchestration policy: {path}",
            details={"path": str(path), "issues": [_issue_details(issue) for issue in issues]},
        )
    return policy


def _parse_states(raw_states: Any, *, path: str) -> dict[str, WorkflowStatePolicy]:
    if raw_states is None:
        return OrchestrationPolicy.default().states
    if not isinstance(raw_states, Mapping):
        raise OrchestrationPolicyError(
            "Orchestration policy states must be a mapping",
            details={"path": path, "field": "states"},
        )

    states: dict[str, WorkflowStatePolicy] = {}
    normalized_keys: dict[str, str] = {}
    for raw_key, raw_value in raw_states.items():
        key = _non_empty_string(raw_key, field="states", path=path)
        _check_duplicate_normalized_key(normalized_keys, key, field="states", label="state", path=path)
        if raw_value is None:
            value: Mapping[Any, Any] = {}
        elif isinstance(raw_value, Mapping):
            value = raw_value
        else:
            raise OrchestrationPolicyError(
                f"Orchestration policy state {key!r} must be a mapping",
                details={"path": path, "field": f"states.{key}"},
            )
        states[key] = WorkflowStatePolicy(
            claimable=_optional_bool(value.get("claimable"), default=False, field=f"states.{key}.claimable", path=path),
            terminal=_optional_bool(value.get("terminal"), default=False, field=f"states.{key}.terminal", path=path),
        )
    return states


def _parse_transitions(raw_transitions: Any, *, path: str) -> dict[str, tuple[str, ...]]:
    if raw_transitions is None:
        return OrchestrationPolicy.default().transitions
    if not isinstance(raw_transitions, Mapping):
        raise OrchestrationPolicyError(
            "Orchestration policy transitions must be a mapping",
            details={"path": path, "field": "transitions"},
        )

    transitions: dict[str, tuple[str, ...]] = {}
    normalized_keys: dict[str, str] = {}
    for raw_key, raw_value in raw_transitions.items():
        key = _non_empty_string(raw_key, field="transitions", path=path)
        _check_duplicate_normalized_key(normalized_keys, key, field="transitions", label="transition", path=path)
        if raw_value is None:
            transitions[key] = ()
            continue
        if not isinstance(raw_value, list):
            raise OrchestrationPolicyError(
                f"Orchestration policy transitions for {key!r} must be a list",
                details={"path": path, "field": f"transitions.{key}"},
            )
        transitions[key] = tuple(
            _non_empty_string(value, field=f"transitions.{key}", path=path) for value in raw_value
        )
    return transitions


def _optional_bool(value: Any, *, default: bool, field: str, path: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise OrchestrationPolicyError(
        f"Orchestration policy value {field} must be a boolean",
        details={"path": path, "field": field},
    )


def _optional_positive_int(value: Any, default: int, *, field: str, path: str) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise OrchestrationPolicyError(
        f"Orchestration policy value {field} must be a positive integer",
        details={"path": path, "field": field},
    )


def _non_empty_string(value: Any, *, field: str, path: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise OrchestrationPolicyError(
        f"Orchestration policy value {field} must be a non-empty string",
        details={"path": path, "field": field},
    )


def _check_duplicate_normalized_key(
    normalized_keys: dict[str, str],
    key: str,
    *,
    field: str,
    label: str,
    path: str,
) -> None:
    normalized = _normalize_key(key)
    previous = normalized_keys.get(normalized)
    if previous is not None:
        raise OrchestrationPolicyError(
            f"Orchestration policy {field} contains duplicate normalized {label} key {key!r}",
            details={"path": path, "field": field, "key": key, "conflicts_with": previous},
        )
    normalized_keys[normalized] = key


def _issue_details(issue: ValidationIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "message": issue.message,
        "path": issue.path,
        "severity": issue.severity,
    }
