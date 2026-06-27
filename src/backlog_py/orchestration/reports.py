from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from backlog_py.core.repository import ReadOnlyRepository, TaskRecord
from backlog_py.orchestration.history import parse_run_history
from backlog_py.orchestration.models import (
    OrchestrationPolicy,
    OrchestrationQueueItem,
    OrchestrationQueueReport,
    OrchestrationSummary,
    ValidationIssue,
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


def queue_report(
    repository: ReadOnlyRepository,
    policy: OrchestrationPolicy | None = None,
    now: datetime | None = None,
    *,
    include_completed: bool = False,
) -> OrchestrationQueueReport:
    active_policy = policy or OrchestrationPolicy.default()
    current_time = _coerce_now(now)
    complete_ids = _complete_task_ids(repository)
    tasks = list(repository.list_tasks())
    if include_completed:
        tasks.extend(repository.list_completed_tasks())
    items = [
        categorize_task(
            task,
            policy=active_policy,
            complete_task_ids=complete_ids,
            now=current_time,
            run_history_issues=_run_history_validation_issues(task),
            project_root=repository.project.root,
        )
        for task in tasks
    ]
    items.sort(key=lambda item: (_natural_task_key(item.task_id), item.path))
    counts = Counter(item.category for item in items)
    return OrchestrationQueueReport(items=items, by_category=dict(sorted(counts.items())))


def categorize_task(
    task: TaskRecord,
    *,
    policy: OrchestrationPolicy,
    complete_task_ids: set[str],
    now: datetime,
    run_history_issues: Sequence[ValidationIssue],
    project_root: object | None = None,
) -> OrchestrationQueueItem:
    status_key = effective_status_key(task)
    orchestration = parse_orchestration(task)
    version = orchestration.version if orchestration is not None and orchestration.version is not None else 0
    validation_issues = [
        *validate_orchestration(task, policy),
        *run_history_issues,
    ]
    dependency_ids = _frontmatter_string_list(task.parsed.frontmatter.get("dependencies"))
    lease_owner = orchestration.lease_owner if orchestration is not None else None
    lease_expires_at = orchestration.lease_expires_at if orchestration is not None else None

    if validation_issues:
        category = "invalid"
    elif policy.is_terminal(status_key) or _is_complete_status(task.status) or "completed" in task.path.parts:
        category = "terminal"
    elif _has_active_lease(task, now):
        category = "claimed"
    elif _has_stale_lease(task, now):
        category = "stale_claim"
    elif policy.is_claimable(status_key) and not _dependencies_complete(task, complete_task_ids):
        category = "blocked_by_dependencies"
    elif policy.is_claimable(status_key):
        category = "eligible"
    else:
        category = "in_workflow"

    return OrchestrationQueueItem(
        task_id=task.id,
        path=_display_path(task, project_root),
        title=task.title,
        version=version,
        effective_status=status_key,
        category=category,
        validation_issues=validation_issues,
        dependency_ids=dependency_ids,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        run_history_issues=list(run_history_issues),
    )


def effective_status_key(task: TaskRecord) -> str:
    orchestration = parse_orchestration(task)
    if orchestration is not None and orchestration.status_key:
        return _normalize_key(orchestration.status_key)
    return _normalize_key(task.status)


def _display_path(task: TaskRecord, project_root: object | None) -> str:
    if project_root is not None:
        try:
            return task.path.relative_to(project_root).as_posix()
        except ValueError:
            pass
    return task.path.as_posix()


def _complete_task_ids(repository: ReadOnlyRepository) -> set[str]:
    return {
        task.id.upper()
        for task in repository.search_tasks("")
        if _is_complete_status(task.status) or "completed" in task.path.parts
    }


def _run_history_validation_issues(task: TaskRecord) -> list[ValidationIssue]:
    result = parse_run_history(task.raw_source)
    return [
        ValidationIssue(
            code=issue.code,
            message=issue.message,
            path=issue.location or "run_history",
        )
        for issue in result.issues
    ]


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


def _natural_task_key(task_id: str) -> tuple[str, tuple[int | str, ...]]:
    prefix, _, suffix = task_id.partition("-")
    if suffix and all(part.isdigit() for part in suffix.split(".")):
        return (prefix.casefold(), tuple(int(part) for part in suffix.split(".")))
    return (task_id.casefold(), (task_id,))
