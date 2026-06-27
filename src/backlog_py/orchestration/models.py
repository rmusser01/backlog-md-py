from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class OrchestrationRunEvent:
    event_id: str
    type: str
    actor: str
    timestamp: str
    result: str
    summary: str = ""
    idempotency_key: str = ""
    task_id: str = ""
    from_status: str = ""
    to_status: str = ""
    split_mode: str = ""
    files: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunHistoryParseIssue:
    code: str
    message: str
    location: str = ""


@dataclass(frozen=True)
class RunHistoryParseResult:
    events: list[OrchestrationRunEvent]
    issues: list[RunHistoryParseIssue]


@dataclass(frozen=True)
class OrchestrationIdempotencyConflict(ValueError):
    idempotency_key: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RunHistoryParseError(ValueError):
    code: str
    message: str
    location: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class OrchestrationError(ValueError):
    message: str
    details: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class OrchestrationPolicyError(OrchestrationError):
    pass


@dataclass(frozen=True)
class OrchestrationValidationError(OrchestrationError):
    pass


@dataclass(frozen=True)
class OrchestrationVersionConflict(OrchestrationError):
    pass


@dataclass(frozen=True)
class OrchestrationLeaseConflict(OrchestrationError):
    pass


@dataclass(frozen=True)
class OrchestrationTransitionError(OrchestrationError):
    pass


@dataclass(frozen=True)
class TaskSplitError(OrchestrationError):
    pass


@dataclass(frozen=True)
class OrchestrationStateUpdate:
    status_key: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    correlation_id: str | None = None
    review_state: str | None = None
    reviewer: str | None = None
    review_attempts: int | None = None
    review_max_attempts: int | None = None


@dataclass(frozen=True)
class OrchestrationActorContext:
    adapter_identity: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationRecordRunRequest:
    task_id: str
    actor: str | None
    result: str
    summary: str
    files: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    idempotency_key: str | None = None
    expected_version: int | None = None
    state_update: OrchestrationStateUpdate | None = None
    actor_context: OrchestrationActorContext | None = None


@dataclass(frozen=True)
class OrchestrationMutationResult:
    task_id: str
    path: str
    version: int
    event: OrchestrationRunEvent
    idempotent_replay: bool = False
    details: dict[str, object] = field(default_factory=dict)


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


@dataclass(frozen=True)
class OrchestrationSummary:
    by_status: dict[str, int]
    eligible_count: int
    active_claim_count: int
    stale_lease_count: int
    validation_issue_count: int


@dataclass(frozen=True)
class WorkflowStatePolicy:
    claimable: bool = False
    terminal: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


@dataclass(frozen=True)
class OrchestrationPolicy:
    states: dict[str, WorkflowStatePolicy]
    transitions: dict[str, tuple[str, ...]]
    default_review_max_attempts: int = 3
    default_lease_ttl_seconds: int = 3600

    @classmethod
    def default(cls) -> "OrchestrationPolicy":
        return cls(
            states={
                "todo": WorkflowStatePolicy(claimable=True),
                "inprogress": WorkflowStatePolicy(),
                "review": WorkflowStatePolicy(),
                "complete": WorkflowStatePolicy(terminal=True),
                "triage": WorkflowStatePolicy(),
            },
            transitions={
                "todo": ("inprogress",),
                "inprogress": ("review", "triage"),
                "review": ("complete", "inprogress", "triage"),
                "triage": ("todo", "inprogress"),
                "complete": (),
            },
        )

    def can_transition(self, from_status: str, to_status: str) -> bool:
        return _normalize_key(to_status) in _normalized_transitions(self).get(_normalize_key(from_status), ())

    def is_claimable(self, status_key: str) -> bool:
        state = _normalized_states(self).get(_normalize_key(status_key))
        return state is not None and state.claimable

    def is_terminal(self, status_key: str) -> bool:
        state = _normalized_states(self).get(_normalize_key(status_key))
        return state is not None and state.terminal


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
        version=_optional_int(orchestration.get("version")),
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


def validate_policy(policy: OrchestrationPolicy) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    states = _normalized_states(policy)
    transitions = _normalized_transitions(policy)
    if not any(state.terminal for state in states.values()):
        issues.append(
            ValidationIssue(
                code="policy_missing_terminal_state",
                message="Orchestration policy must define at least one terminal state",
                path="states",
            )
        )
    for source, targets in transitions.items():
        if source not in states:
            issues.append(
                ValidationIssue(
                    code="policy_unknown_transition_source",
                    message=f"Transition source is not a known state: {source}",
                    path=f"transitions.{source}",
                )
            )
        for target in targets:
            if target not in states:
                issues.append(
                    ValidationIssue(
                        code="policy_unknown_transition_target",
                        message=f"Transition target is not a known state: {target}",
                        path=f"transitions.{source}",
                    )
                )
    reachable = _reachable_states(states, transitions)
    for state_key in states:
        if state_key not in reachable:
            issues.append(
                ValidationIssue(
                    code="policy_unreachable_state",
                    message=f"Policy state is not reachable from any claimable state: {state_key}",
                    path=f"states.{state_key}",
                )
            )
    if policy.default_review_max_attempts < 1:
        issues.append(
            ValidationIssue(
                code="policy_invalid_review_max_attempts",
                message="Default review max attempts must be at least 1",
                path="review.max_attempts",
            )
        )
    if policy.default_lease_ttl_seconds < 1:
        issues.append(
            ValidationIssue(
                code="policy_invalid_lease_ttl",
                message="Default lease TTL must be at least 1 second",
                path="lease.default_ttl_seconds",
            )
        )
    return issues


def validate_orchestration(
    task_or_frontmatter: Any,
    policy: OrchestrationPolicy | None = None,
) -> list[ValidationIssue]:
    active_policy = policy or OrchestrationPolicy.default()
    frontmatter = _frontmatter(task_or_frontmatter)
    raw_orchestration = frontmatter.get("orchestration")
    if raw_orchestration is None:
        return []
    if not isinstance(raw_orchestration, Mapping):
        return [
            ValidationIssue(
                code="orchestration_not_mapping",
                message="orchestration frontmatter must be a mapping",
                path="orchestration",
            )
        ]

    raw = dict(raw_orchestration)
    issues: list[ValidationIssue] = []
    status_key = raw.get("status_key")
    if status_key is not None:
        if not isinstance(status_key, str) or not status_key.strip():
            issues.append(
                ValidationIssue(
                    code="invalid_status_key",
                    message="orchestration.status_key must be a non-empty string",
                    path="orchestration.status_key",
                )
            )
        elif _normalize_key(status_key) not in _normalized_states(active_policy):
            issues.append(
                ValidationIssue(
                    code="unknown_status_key",
                    message=f"Unknown orchestration status: {status_key}",
                    path="orchestration.status_key",
                )
            )

    version = raw.get("version")
    if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 0):
        issues.append(
            ValidationIssue(
                code="invalid_version",
                message="orchestration.version must be a non-negative integer",
                path="orchestration.version",
            )
            )

    lease_owner = raw.get("lease_owner")
    lease_expires_at = raw.get("lease_expires_at")
    if lease_owner is not None and (not isinstance(lease_owner, str) or not lease_owner.strip()):
        issues.append(
            ValidationIssue(
                code="invalid_lease_owner",
                message="orchestration.lease_owner must be a non-empty string",
                path="orchestration.lease_owner",
            )
        )
    if isinstance(lease_owner, str) and lease_owner.strip() and lease_expires_at is None:
        issues.append(
            ValidationIssue(
                code="missing_lease_expires_at",
                message="orchestration.lease_expires_at is required when lease_owner is set",
                path="orchestration.lease_expires_at",
            )
        )
    if lease_expires_at is not None and _parse_datetime(lease_expires_at) is None:
        issues.append(
            ValidationIssue(
                code="invalid_lease_expires_at",
                message="orchestration.lease_expires_at must be an ISO-8601 timestamp",
                path="orchestration.lease_expires_at",
            )
        )

    issues.extend(_validate_workspace(raw.get("workspace")))
    issues.extend(_validate_runner(raw.get("runner")))

    review = raw.get("review")
    if review is not None:
        if not isinstance(review, Mapping):
            issues.append(
                ValidationIssue(
                    code="invalid_review",
                    message="orchestration.review must be a mapping",
                    path="orchestration.review",
                )
            )
        else:
            issues.extend(_validate_review(dict(review), active_policy))
    return issues


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
        attempts=_optional_int(raw.get("attempts")),
        max_attempts=_optional_int(raw.get("max_attempts")),
        extra={key: item for key, item in raw.items() if key not in _REVIEW_KEYS},
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _validate_workspace(value: object) -> list[ValidationIssue]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return [
            ValidationIssue(
                code="invalid_workspace",
                message="orchestration.workspace must be a mapping",
                path="orchestration.workspace",
            )
        ]
    return [
        *_validate_optional_string(value, "path", "workspace"),
        *_validate_optional_string(value, "branch", "workspace"),
    ]


def _validate_runner(value: object) -> list[ValidationIssue]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return [
            ValidationIssue(
                code="invalid_runner",
                message="orchestration.runner must be a mapping",
                path="orchestration.runner",
            )
        ]
    return [
        *_validate_optional_string(value, "kind", "runner"),
        *_validate_optional_string(value, "profile", "runner"),
    ]


def _validate_optional_string(
    raw: Mapping[str, Any],
    field_name: str,
    section_name: str,
) -> list[ValidationIssue]:
    value = raw.get(field_name)
    if value is None:
        return []
    if isinstance(value, str) and value.strip():
        return []
    return [
        ValidationIssue(
            code=f"invalid_{section_name}_{field_name}",
            message=f"orchestration.{section_name}.{field_name} must be a non-empty string",
            path=f"orchestration.{section_name}.{field_name}",
        )
    ]


def _validate_review(raw: Mapping[str, Any], policy: OrchestrationPolicy) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    attempts = raw.get("attempts")
    max_attempts = raw.get("max_attempts")
    if attempts is not None and (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0):
        issues.append(
            ValidationIssue(
                code="invalid_review_attempts",
                message="orchestration.review.attempts must be a non-negative integer",
                path="orchestration.review.attempts",
            )
        )
    if (
        max_attempts is not None
        and (not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1)
    ):
        issues.append(
            ValidationIssue(
                code="invalid_review_max_attempts",
                message="orchestration.review.max_attempts must be a positive integer",
                path="orchestration.review.max_attempts",
            )
        )
    if (
        isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and attempts >= 0
        and isinstance(max_attempts, int)
        and not isinstance(max_attempts, bool)
        and max_attempts >= 1
        and attempts > max_attempts
    ):
        issues.append(
            ValidationIssue(
                code="review_attempts_exceed_max",
                message="orchestration.review.attempts cannot exceed max_attempts",
                path="orchestration.review.attempts",
            )
        )
    if max_attempts is None and isinstance(attempts, int) and attempts > policy.default_review_max_attempts:
        issues.append(
            ValidationIssue(
                code="review_attempts_exceed_max",
                message="orchestration.review.attempts cannot exceed policy default max attempts",
                path="orchestration.review.attempts",
            )
        )
    return issues


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())


def _normalized_states(policy: OrchestrationPolicy) -> dict[str, WorkflowStatePolicy]:
    return {_normalize_key(key): value for key, value in policy.states.items()}


def _normalized_transitions(policy: OrchestrationPolicy) -> dict[str, tuple[str, ...]]:
    return {
        _normalize_key(source): tuple(_normalize_key(target) for target in targets)
        for source, targets in policy.transitions.items()
    }


def _reachable_states(
    states: Mapping[str, WorkflowStatePolicy],
    transitions: Mapping[str, tuple[str, ...]],
) -> set[str]:
    pending = [state_key for state_key, state in states.items() if state.claimable]
    reachable: set[str] = set()
    while pending:
        state_key = pending.pop()
        if state_key in reachable or state_key not in states:
            continue
        reachable.add(state_key)
        pending.extend(target for target in transitions.get(state_key, ()) if target not in reachable)
    return reachable
