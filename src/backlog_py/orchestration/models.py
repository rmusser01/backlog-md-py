from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class OrchestrationWorkspace:
    path: str | None = None
    branch: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationRunner:
    kind: str | None = None
    profile: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationReview:
    state: str | None = None
    reviewer: str | None = None
    attempts: int | None = None
    max_attempts: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationState:
    status_key: str | None = None
    version: int | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    workspace: OrchestrationWorkspace | None = None
    runner: OrchestrationRunner | None = None
    review: OrchestrationReview | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


_STATE_KEYS = {
    "status_key",
    "version",
    "lease_owner",
    "lease_expires_at",
    "correlation_id",
    "idempotency_key",
    "workspace",
    "runner",
    "review",
}
_WORKSPACE_KEYS = {"path", "branch"}
_RUNNER_KEYS = {"kind", "profile"}
_REVIEW_KEYS = {"state", "reviewer", "attempts", "max_attempts"}


def parse_orchestration(task_or_frontmatter: Any) -> OrchestrationState | None:
    frontmatter = _frontmatter(task_or_frontmatter)
    raw_orchestration = frontmatter.get("orchestration")
    if raw_orchestration is None or not isinstance(raw_orchestration, Mapping):
        return None

    orchestration = dict(raw_orchestration)
    workspace = _parse_workspace(orchestration.get("workspace"))
    runner = _parse_runner(orchestration.get("runner"))
    review = _parse_review(orchestration.get("review"))
    return OrchestrationState(
        status_key=_optional_string(orchestration.get("status_key")),
        version=orchestration.get("version") if isinstance(orchestration.get("version"), int) else None,
        lease_owner=_optional_string(orchestration.get("lease_owner")),
        lease_expires_at=_optional_string(orchestration.get("lease_expires_at")),
        correlation_id=_optional_string(orchestration.get("correlation_id")),
        idempotency_key=_optional_string(orchestration.get("idempotency_key")),
        workspace=workspace,
        runner=runner,
        review=review,
        raw=orchestration,
        extra={key: value for key, value in orchestration.items() if key not in _STATE_KEYS},
    )


def _frontmatter(task_or_frontmatter: Any) -> Mapping[str, Any]:
    parsed = getattr(task_or_frontmatter, "parsed", None)
    if parsed is not None:
        return parsed.frontmatter
    if isinstance(task_or_frontmatter, Mapping):
        return task_or_frontmatter
    return {}


def _parse_workspace(value: object) -> OrchestrationWorkspace | None:
    if not isinstance(value, Mapping):
        return None
    raw = dict(value)
    return OrchestrationWorkspace(
        path=_optional_string(raw.get("path")),
        branch=_optional_string(raw.get("branch")),
        extra={key: item for key, item in raw.items() if key not in _WORKSPACE_KEYS},
    )


def _parse_runner(value: object) -> OrchestrationRunner | None:
    if not isinstance(value, Mapping):
        return None
    raw = dict(value)
    return OrchestrationRunner(
        kind=_optional_string(raw.get("kind")),
        profile=_optional_string(raw.get("profile")),
        extra={key: item for key, item in raw.items() if key not in _RUNNER_KEYS},
    )


def _parse_review(value: object) -> OrchestrationReview | None:
    if not isinstance(value, Mapping):
        return None
    raw = dict(value)
    return OrchestrationReview(
        state=_optional_string(raw.get("state")),
        reviewer=_optional_string(raw.get("reviewer")),
        attempts=raw.get("attempts") if isinstance(raw.get("attempts"), int) else None,
        max_attempts=raw.get("max_attempts") if isinstance(raw.get("max_attempts"), int) else None,
        extra={key: item for key, item in raw.items() if key not in _REVIEW_KEYS},
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
