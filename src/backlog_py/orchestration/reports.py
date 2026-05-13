from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from backlog_py.core.repository import ReadOnlyRepository, TaskRecord
from backlog_py.orchestration.models import (
    OrchestrationPolicy,
    OrchestrationSummary,
    parse_orchestration,
    validate_orchestration,
)


def list_eligible_tasks(
    repository: ReadOnlyRepository,
    policy: OrchestrationPolicy | None = None,
    now: datetime | None = None,
) -> list[TaskRecord]:
    active_policy = policy or OrchestrationPolicy.default()
    current_time = _coerce_now(now)
    complete_ids = _complete_task_ids(repository)
    return [
        task
        for task in repository.list_tasks()
        if active_policy.is_claimable(effective_status_key(task))
        and not validate_orchestration(task, active_policy)
        and not _has_active_lease(task, current_time)
        and _dependencies_complete(task, complete_ids)
    ]


def list_active_claims(
    repository: ReadOnlyRepository,
    now: datetime | None = None,
) -> list[TaskRecord]:
    current_time = _coerce_now(now)
    return [task for task in repository.list_tasks() if _has_active_lease(task, current_time)]


def list_stale_leases(
    repository: ReadOnlyRepository,
    now: datetime | None = None,
) -> list[TaskRecord]:
    current_time = _coerce_now(now)
    return [task for task in repository.list_tasks() if _has_stale_lease(task, current_time)]


def summarize_orchestration(
    repository: ReadOnlyRepository,
    policy: OrchestrationPolicy | None = None,
    now: datetime | None = None,
) -> OrchestrationSummary:
    active_policy = policy or OrchestrationPolicy.default()
    current_time = _coerce_now(now)
    tasks = repository.list_tasks()
    by_status = Counter(effective_status_key(task) for task in tasks)
    validation_issue_count = sum(len(validate_orchestration(task, active_policy)) for task in tasks)
    return OrchestrationSummary(
        by_status=dict(sorted(by_status.items())),
        eligible_count=len(list_eligible_tasks(repository, active_policy, current_time)),
        active_claim_count=len(list_active_claims(repository, current_time)),
        stale_lease_count=len(list_stale_leases(repository, current_time)),
        validation_issue_count=validation_issue_count,
    )


def effective_status_key(task: TaskRecord) -> str:
    orchestration = parse_orchestration(task)
    if orchestration is not None and orchestration.status_key:
        return _normalize_key(orchestration.status_key)
    return _normalize_key(task.status)


def _complete_task_ids(repository: ReadOnlyRepository) -> set[str]:
    return {
        task.id.upper()
        for task in repository.search_tasks("")
        if _is_complete_status(task.status) or "completed" in task.path.parts
    }


def _dependencies_complete(task: TaskRecord, complete_ids: set[str]) -> bool:
    dependencies = _frontmatter_string_list(task.parsed.frontmatter.get("dependencies"))
    return all(dependency.strip().upper() in complete_ids for dependency in dependencies if dependency.strip())


def _has_active_lease(task: TaskRecord, now: datetime) -> bool:
    orchestration = parse_orchestration(task)
    if orchestration is None or not orchestration.lease_owner:
        return False
    expires_at = _parse_datetime(orchestration.lease_expires_at)
    return expires_at is not None and expires_at > now


def _has_stale_lease(task: TaskRecord, now: datetime) -> bool:
    orchestration = parse_orchestration(task)
    if orchestration is None or not orchestration.lease_owner:
        return False
    expires_at = _parse_datetime(orchestration.lease_expires_at)
    return expires_at is not None and expires_at <= now


def _frontmatter_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_values([value])
    if isinstance(value, Sequence):
        return _split_values(str(item) for item in value)
    return [str(value)]


def _split_values(values: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for raw_value in values:
        for value in str(raw_value).split(","):
            stripped = value.strip()
            if stripped:
                normalized.append(stripped)
    return normalized


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _is_complete_status(status: str) -> bool:
    normalized = status.strip().casefold()
    return "done" in normalized or "complete" in normalized


def _normalize_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())
