from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from backlog_py.core.models import BacklogProject, ParsedTaskMarkdown
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskMutationError, TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.orchestration.history import (
    MAX_RUN_HISTORY_METADATA_CHARS,
    append_run_history_entry,
    find_idempotency_match,
    parse_run_history,
)
from backlog_py.orchestration.models import (
    OrchestrationActorContext,
    OrchestrationClaimTaskRequest,
    OrchestrationIdempotencyConflict,
    OrchestrationLeaseConflict,
    OrchestrationMutationResult,
    OrchestrationPolicy,
    OrchestrationQueueReport,
    OrchestrationRecordRunRequest,
    OrchestrationReleaseTaskRequest,
    OrchestrationRunEvent,
    OrchestrationStateUpdate,
    OrchestrationTransitionError,
    OrchestrationTransitionTaskRequest,
    OrchestrationValidationError,
    OrchestrationVersionConflict,
    RunHistoryParseError,
    TaskSplitError,
    TaskSplitItem,
    TaskSplitRequest,
    TaskSplitResult,
    parse_orchestration,
    validate_orchestration,
)
from backlog_py.orchestration.policy import load_orchestration_policy
from backlog_py.orchestration.reports import queue_report
from backlog_py.runtime.locks import with_project_write_lock


class OrchestrationService:
    def __init__(self, project: BacklogProject, *, now: Callable[[], datetime] | None = None) -> None:
        self.project = project
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record_run(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        result: str,
        summary: str,
        files: Sequence[str] = (),
        verification: Sequence[str] = (),
        idempotency_key: str | None = None,
        expected_version: int | None = None,
        state_update: OrchestrationStateUpdate | None = None,
        actor_context: OrchestrationActorContext | None = None,
    ) -> OrchestrationMutationResult:
        request = OrchestrationRecordRunRequest(
            task_id=task_id,
            actor=actor,
            result=_required_text(result, field="result"),
            summary=summary,
            files=tuple(files),
            verification=tuple(verification),
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            state_update=state_update,
            actor_context=actor_context,
        )

        def mutate() -> OrchestrationMutationResult:
            return self._record_run_locked(request)

        return with_project_write_lock(self.project, "orchestration_record_run", mutate)

    def claim_task(
        self,
        task_id: str,
        *,
        actor: str,
        expected_version: int,
        idempotency_key: str | None = None,
        lease_ttl_seconds: int | None = None,
        reason: str | None = None,
    ) -> OrchestrationMutationResult:
        request = OrchestrationClaimTaskRequest(
            task_id=task_id,
            actor=_required_text(actor, field="actor"),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            lease_ttl_seconds=lease_ttl_seconds,
            reason=reason,
        )

        def mutate() -> OrchestrationMutationResult:
            return self._claim_task_locked(request)

        return with_project_write_lock(self.project, "orchestration_claim_task", mutate)

    def release_task(
        self,
        task_id: str,
        *,
        actor: str,
        expected_version: int,
        idempotency_key: str | None = None,
        reason: str | None = None,
    ) -> OrchestrationMutationResult:
        request = OrchestrationReleaseTaskRequest(
            task_id=task_id,
            actor=_required_text(actor, field="actor"),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            reason=reason,
        )

        def mutate() -> OrchestrationMutationResult:
            return self._release_task_locked(request)

        return with_project_write_lock(self.project, "orchestration_release_task", mutate)

    def transition_task(
        self,
        task_id: str,
        to_status: str,
        *,
        actor: str,
        expected_version: int,
        idempotency_key: str | None = None,
        reason: str | None = None,
    ) -> OrchestrationMutationResult:
        request = OrchestrationTransitionTaskRequest(
            task_id=task_id,
            to_status=_required_text(to_status, field="to_status"),
            actor=_required_text(actor, field="actor"),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            reason=reason,
        )

        def mutate() -> OrchestrationMutationResult:
            return self._transition_task_locked(request)

        return with_project_write_lock(self.project, "orchestration_transition_task", mutate)

    def split_task(
        self,
        task_id: str,
        *,
        mode: str,
        items: Sequence[TaskSplitItem],
        actor: str,
        expected_version: int,
        idempotency_key: str | None = None,
        inherit_dependencies: bool = True,
        link_sequence: bool = True,
        transition_to_status: str | None = None,
        reason: str | None = None,
    ) -> TaskSplitResult:
        request = TaskSplitRequest(
            task_id=task_id,
            mode=_normalize_split_mode(mode),
            items=_normalize_split_items(items),
            actor=_required_text(actor, field="actor"),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            inherit_dependencies=inherit_dependencies,
            link_sequence=link_sequence,
            transition_to_status=_optional_text(transition_to_status),
            reason=reason,
        )

        def mutate() -> TaskSplitResult:
            return self._split_task_locked(request)

        return with_project_write_lock(self.project, "orchestration_split_task", mutate)

    def queue(self, *, include_completed: bool = False) -> OrchestrationQueueReport:
        repository = ReadOnlyRepository(self.project, refresh_remote_refs=False)
        return queue_report(
            repository,
            policy=load_orchestration_policy(self.project),
            now=self._now(),
            include_completed=include_completed,
        )

    def _record_run_locked(self, request: OrchestrationRecordRunRequest) -> OrchestrationMutationResult:
        repository = MutableRepository(self.project, refresh_remote_refs=False)
        task = repository.get_task(request.task_id)
        policy = load_orchestration_policy(self.project)
        parsed_history = parse_run_history(task.raw_source)
        if parsed_history.issues:
            issue = parsed_history.issues[0]
            raise RunHistoryParseError(issue.code, issue.message, issue.location)

        state = parse_orchestration(task)
        current_version = state.version if state is not None and state.version is not None else 0
        candidate = self._record_run_event(task, request, state.raw if state is not None else {})
        existing_event = find_idempotency_match(parsed_history.events, candidate)
        if existing_event is not None:
            return OrchestrationMutationResult(
                task_id=task.id,
                path=_project_relative_path(self.project, task.path),
                version=current_version,
                event=existing_event,
                idempotent_replay=True,
            )

        if request.state_update is not None and request.expected_version is None:
            raise OrchestrationVersionConflict(
                "State updates require expected_version",
                details={"task_id": task.id, "actual_version": current_version},
            )
        if request.expected_version is not None and request.expected_version != current_version:
            raise OrchestrationVersionConflict(
                "Orchestration version conflict",
                details={
                    "task_id": task.id,
                    "expected_version": request.expected_version,
                    "actual_version": current_version,
                },
            )

        source = task.raw_source
        next_version = current_version
        if request.state_update is not None:
            self._validate_record_run_state_update(task, state, policy, request, current_version)
            updated_orchestration = _apply_state_update(state.raw if state is not None else {}, request.state_update)
            current_orchestration = state.raw if state is not None else {}
            if updated_orchestration != current_orchestration:
                next_version = current_version + 1
                updated_orchestration["version"] = next_version
                source = _replace_frontmatter_value(source, task.parsed, "orchestration", updated_orchestration)
                task = _task_from_source(task, source)

        source = append_run_history_entry(source, candidate)
        updated = repository.replace_task_source(task.id, source)
        return OrchestrationMutationResult(
            task_id=updated.id,
            path=_project_relative_path(self.project, updated.path),
            version=next_version,
            event=candidate,
            idempotent_replay=False,
        )

    def _validate_record_run_state_update(
        self,
        task: TaskRecord,
        state: Any,
        policy: OrchestrationPolicy,
        request: OrchestrationRecordRunRequest,
        current_version: int,
    ) -> None:
        """Hold record_run state updates to the same policy as transition_task.

        Without this, record_run could jump a task to any status (including a
        status the policy does not define) or steal another agent's active
        lease, routing around the workflow state machine entirely.
        """
        actor = resolve_orchestration_actor(request.actor)
        _require_lease_owner(task, state, current_version, actor, self._now())
        update = request.state_update
        if update is None or update.status_key is None:
            return
        current_status = _state_status_key(task, state)
        target_status = _normalize_key(update.status_key)
        if not policy.has_state(target_status):
            raise OrchestrationTransitionError(
                "Unknown orchestration status",
                details={"task_id": task.id, "to_status": update.status_key},
            )
        if target_status != current_status and not policy.can_transition(current_status, target_status):
            raise OrchestrationTransitionError(
                "Orchestration transition is not allowed",
                details={
                    "task_id": task.id,
                    "from_status": current_status,
                    "to_status": update.status_key,
                },
            )

    def _split_task_locked(self, request: TaskSplitRequest) -> TaskSplitResult:
        repository = MutableRepository(self.project, refresh_remote_refs=False)
        task = repository.get_task(request.task_id)
        policy = load_orchestration_policy(self.project)
        parsed_history = parse_run_history(task.raw_source)
        if parsed_history.issues:
            issue = parsed_history.issues[0]
            raise RunHistoryParseError(issue.code, issue.message, issue.location)

        state = parse_orchestration(task)
        current_version = state.version if state is not None and state.version is not None else 0
        current_status = _state_status_key(task, state)
        candidate = self._split_task_event(task, request, current_status)
        existing_event = _find_split_idempotency_match(repository, candidate)
        if existing_event is not None:
            created_task_ids = _created_task_ids_from_event(existing_event)
            return TaskSplitResult(
                task_id=task.id,
                path=_project_relative_path(self.project, task.path),
                version=current_version,
                event=existing_event,
                created_task_ids=created_task_ids,
                parent_event_id=existing_event.event_id,
                idempotent_replay=True,
                details=_split_result_details(request.mode, created_task_ids, existing_event.event_id),
            )

        if request.expected_version != current_version:
            raise OrchestrationVersionConflict(
                "Orchestration version conflict",
                details={
                    "task_id": task.id,
                    "expected_version": request.expected_version,
                    "actual_version": current_version,
                },
            )

        _validate_current_orchestration(task, policy)
        if request.transition_to_status and not policy.can_transition(current_status, request.transition_to_status):
            raise OrchestrationTransitionError(
                "Orchestration split transition is not allowed",
                details={
                    "task_id": task.id,
                    "from_status": current_status,
                    "to_status": request.transition_to_status,
                },
            )
        parent_dependencies = _task_dependency_ids(task)
        _reject_split_dependency_cycle(task.id, parent_dependencies, request)

        try:
            created_tasks = self._create_split_tasks(repository, task, request, parent_dependencies)
        except TaskMutationError as exc:
            raise TaskSplitError(
                str(exc),
                details={"task_id": task.id, "mode": request.mode},
            ) from exc

        created_task_ids = [created.id for created in created_tasks]
        event = replace(
            candidate,
            metadata={
                **candidate.metadata,
                "created_task_ids": json.dumps(created_task_ids, separators=(",", ":"), ensure_ascii=True),
            },
        )
        next_version = current_version + 1
        next_orchestration = dict(state.raw if state is not None else {})
        if request.transition_to_status:
            next_orchestration["status_key"] = request.transition_to_status
        if request.idempotency_key:
            next_orchestration["idempotency_key"] = request.idempotency_key
        next_orchestration["version"] = next_version
        try:
            source = _replace_frontmatter_value(task.raw_source, task.parsed, "orchestration", next_orchestration)
            source = append_run_history_entry(source, event)
            updated = repository.replace_task_source(task.id, source)
        except TaskMutationError as exc:
            _rollback_created_tasks(created_tasks)
            raise TaskSplitError(
                str(exc),
                details={"task_id": task.id, "mode": request.mode},
            ) from exc
        except Exception:
            _rollback_created_tasks(created_tasks)
            raise
        return TaskSplitResult(
            task_id=updated.id,
            path=_project_relative_path(self.project, updated.path),
            version=next_version,
            event=event,
            created_task_ids=created_task_ids,
            parent_event_id=event.event_id,
            idempotent_replay=False,
            details=_split_result_details(request.mode, created_task_ids, event.event_id),
        )

    def _split_task_event(
        self,
        task: TaskRecord,
        request: TaskSplitRequest,
        current_status: str,
    ) -> OrchestrationRunEvent:
        metadata = _split_event_metadata(request)
        return OrchestrationRunEvent(
            event_id=f"run-{uuid.uuid4().hex}",
            type="split_task",
            actor=resolve_orchestration_actor(request.actor),
            timestamp=_utc_timestamp(self._now()),
            result="succeeded",
            summary=request.reason or "",
            idempotency_key=request.idempotency_key or "",
            task_id=task.id,
            from_status=current_status if request.transition_to_status else "",
            to_status=request.transition_to_status or "",
            split_mode=request.mode,
            metadata=metadata,
        )

    def _create_split_tasks(
        self,
        repository: MutableRepository,
        parent: TaskRecord,
        request: TaskSplitRequest,
        parent_dependencies: Sequence[str],
    ) -> list[TaskRecord]:
        created: list[TaskRecord] = []
        try:
            for index, item in enumerate(request.items, start=1):
                description = _split_item_description(parent, item)
                if request.mode == "child":
                    dependencies = list(parent_dependencies) if request.inherit_dependencies else []
                    created.append(
                        repository.create_task(
                            title=item.title,
                            description=description,
                            plan=item.plan,
                            parent_task_id=parent.id,
                            dependencies=dependencies,
                        )
                    )
                    continue

                dependencies = list(parent_dependencies) if request.inherit_dependencies else []
                if request.link_sequence:
                    dependencies.append(parent.id if not created else created[-1].id)
                created.append(
                    repository.create_task(
                        title=item.title,
                        description=description,
                        plan=item.plan,
                        dependencies=_dedupe_task_ids(dependencies),
                        ordinal=index,
                    )
                )
        except Exception:
            _rollback_created_tasks(created)
            raise
        return created

    def _record_run_event(
        self,
        task: TaskRecord,
        request: OrchestrationRecordRunRequest,
        current_orchestration: Mapping[str, Any],
    ) -> OrchestrationRunEvent:
        state_update_metadata = _state_update_metadata(request.state_update)
        status_key = _string_or_empty(current_orchestration.get("status_key"))
        to_status = request.state_update.status_key if request.state_update is not None else None
        metadata = {"state_update": state_update_metadata} if state_update_metadata else {}
        return OrchestrationRunEvent(
            event_id=f"run-{uuid.uuid4().hex}",
            type="record_run",
            actor=resolve_orchestration_actor(request.actor, request.actor_context),
            timestamp=_utc_timestamp(self._now()),
            result=request.result,
            summary=request.summary,
            idempotency_key=request.idempotency_key or "",
            task_id=task.id,
            from_status=status_key if to_status and to_status != status_key else "",
            to_status=to_status or "",
            files=list(request.files),
            verification=list(request.verification),
            metadata=metadata,
        )

    def _claim_task_locked(self, request: OrchestrationClaimTaskRequest) -> OrchestrationMutationResult:
        def build(
            task: TaskRecord,
            state: Any,
            current_version: int,
            policy: OrchestrationPolicy,
        ) -> tuple[OrchestrationRunEvent, dict[str, Any]]:
            _ = current_version
            current_orchestration = state.raw if state is not None else {}
            current_status = _state_status_key(task, state)
            actor = resolve_orchestration_actor(request.actor)
            ttl_seconds = policy.default_lease_ttl_seconds if request.lease_ttl_seconds is None else request.lease_ttl_seconds
            if ttl_seconds < 1:
                raise OrchestrationValidationError(
                    "lease_ttl_seconds must be at least 1",
                    details={"field": "lease_ttl_seconds"},
                )
            expires_at = _utc_timestamp(self._now() + timedelta(seconds=ttl_seconds))
            next_orchestration = dict(current_orchestration)
            next_orchestration["status_key"] = "inprogress"
            next_orchestration["lease_owner"] = actor
            next_orchestration["lease_expires_at"] = expires_at
            if request.idempotency_key:
                next_orchestration["idempotency_key"] = request.idempotency_key
            event = OrchestrationRunEvent(
                event_id=f"run-{uuid.uuid4().hex}",
                type="claim_task",
                actor=actor,
                timestamp=_utc_timestamp(self._now()),
                result="succeeded",
                summary=request.reason or "",
                idempotency_key=request.idempotency_key or "",
                task_id=task.id,
                from_status=current_status if current_status != "inprogress" else "",
                to_status="inprogress",
                metadata={"lease_ttl_seconds": str(ttl_seconds)},
            )
            return event, next_orchestration

        def validate(task: TaskRecord, state: Any, current_version: int, policy: OrchestrationPolicy) -> None:
            _validate_current_orchestration(task, policy)
            current_status = _state_status_key(task, state)
            actor = resolve_orchestration_actor(request.actor)
            lease_owner = state.lease_owner if state is not None else None
            lease_expires_at = state.lease_expires_at if state is not None else None
            if _lease_is_active(lease_owner, lease_expires_at, self._now()) and lease_owner != actor:
                raise OrchestrationLeaseConflict(
                    "Task already has an active orchestration lease",
                    details={
                        "task_id": task.id,
                        "actual_version": current_version,
                        "lease_owner": lease_owner,
                        "lease_expires_at": lease_expires_at,
                    },
                )
            if not policy.is_claimable(current_status) or not policy.can_transition(current_status, "inprogress"):
                raise OrchestrationTransitionError(
                    "Orchestration claim transition is not allowed",
                    details={
                        "task_id": task.id,
                        "from_status": current_status,
                        "to_status": "inprogress",
                    },
                )

        return self._workflow_mutation_locked(
            request.task_id,
            expected_version=request.expected_version,
            build=build,
            validate=validate,
        )

    def _release_task_locked(self, request: OrchestrationReleaseTaskRequest) -> OrchestrationMutationResult:
        def build(
            task: TaskRecord,
            state: Any,
            current_version: int,
            policy: OrchestrationPolicy,
        ) -> tuple[OrchestrationRunEvent, dict[str, Any]]:
            _ = current_version, policy
            current_orchestration = state.raw if state is not None else {}
            current_status = _state_status_key(task, state)
            actor = resolve_orchestration_actor(request.actor)
            next_orchestration = dict(current_orchestration)
            next_orchestration.pop("lease_owner", None)
            next_orchestration.pop("lease_expires_at", None)
            if request.idempotency_key:
                next_orchestration["idempotency_key"] = request.idempotency_key
            event = OrchestrationRunEvent(
                event_id=f"run-{uuid.uuid4().hex}",
                type="release_task",
                actor=actor,
                timestamp=_utc_timestamp(self._now()),
                result="succeeded",
                summary=request.reason or "",
                idempotency_key=request.idempotency_key or "",
                task_id=task.id,
                from_status=current_status,
                to_status=current_status,
            )
            return event, next_orchestration

        def validate(task: TaskRecord, state: Any, current_version: int, policy: OrchestrationPolicy) -> None:
            _ = policy
            _validate_current_orchestration(task, policy)
            _require_lease_owner(task, state, current_version, resolve_orchestration_actor(request.actor), self._now())

        return self._workflow_mutation_locked(
            request.task_id,
            expected_version=request.expected_version,
            build=build,
            validate=validate,
        )

    def _transition_task_locked(self, request: OrchestrationTransitionTaskRequest) -> OrchestrationMutationResult:
        def build(
            task: TaskRecord,
            state: Any,
            current_version: int,
            policy: OrchestrationPolicy,
        ) -> tuple[OrchestrationRunEvent, dict[str, Any]]:
            _ = current_version, policy
            current_orchestration = state.raw if state is not None else {}
            current_status = _state_status_key(task, state)
            actor = resolve_orchestration_actor(request.actor)
            next_orchestration = dict(current_orchestration)
            next_orchestration["status_key"] = request.to_status
            if request.idempotency_key:
                next_orchestration["idempotency_key"] = request.idempotency_key
            event = OrchestrationRunEvent(
                event_id=f"run-{uuid.uuid4().hex}",
                type="transition_task",
                actor=actor,
                timestamp=_utc_timestamp(self._now()),
                result="succeeded",
                summary=request.reason or "",
                idempotency_key=request.idempotency_key or "",
                task_id=task.id,
                from_status=current_status,
                to_status=request.to_status,
            )
            return event, next_orchestration

        def validate(task: TaskRecord, state: Any, current_version: int, policy: OrchestrationPolicy) -> None:
            _validate_current_orchestration(task, policy)
            _require_lease_owner(task, state, current_version, resolve_orchestration_actor(request.actor), self._now())
            current_status = _state_status_key(task, state)
            if not policy.can_transition(current_status, request.to_status):
                raise OrchestrationTransitionError(
                    "Orchestration transition is not allowed",
                    details={
                        "task_id": task.id,
                        "from_status": current_status,
                        "to_status": request.to_status,
                    },
                )

        return self._workflow_mutation_locked(
            request.task_id,
            expected_version=request.expected_version,
            build=build,
            validate=validate,
        )

    def _workflow_mutation_locked(
        self,
        task_id: str,
        *,
        expected_version: int,
        build: Callable[[TaskRecord, Any, int, OrchestrationPolicy], tuple[OrchestrationRunEvent, dict[str, Any]]],
        validate: Callable[[TaskRecord, Any, int, OrchestrationPolicy], None] | None = None,
    ) -> OrchestrationMutationResult:
        repository = MutableRepository(self.project, refresh_remote_refs=False)
        task = repository.get_task(task_id)
        policy = load_orchestration_policy(self.project)
        parsed_history = parse_run_history(task.raw_source)
        if parsed_history.issues:
            issue = parsed_history.issues[0]
            raise RunHistoryParseError(issue.code, issue.message, issue.location)
        state = parse_orchestration(task)
        current_version = state.version if state is not None and state.version is not None else 0
        candidate, next_orchestration = build(task, state, current_version, policy)
        existing_event = find_idempotency_match(parsed_history.events, candidate)
        if existing_event is not None:
            return OrchestrationMutationResult(
                task_id=task.id,
                path=_project_relative_path(self.project, task.path),
                version=current_version,
                event=existing_event,
                idempotent_replay=True,
            )
        if expected_version != current_version:
            raise OrchestrationVersionConflict(
                "Orchestration version conflict",
                details={
                    "task_id": task.id,
                    "expected_version": expected_version,
                    "actual_version": current_version,
                },
            )
        if validate is not None:
            validate(task, state, current_version, policy)
        next_version = current_version + 1
        next_orchestration = dict(next_orchestration)
        next_orchestration["version"] = next_version
        source = _replace_frontmatter_value(task.raw_source, task.parsed, "orchestration", next_orchestration)
        source = append_run_history_entry(source, candidate)
        updated = repository.replace_task_source(task.id, source)
        return OrchestrationMutationResult(
            task_id=updated.id,
            path=_project_relative_path(self.project, updated.path),
            version=next_version,
            event=candidate,
            idempotent_replay=False,
        )


_SPLIT_MODES = {"child", "continuation"}
_MAX_SPLIT_ITEMS = 25


def _normalize_split_mode(value: str) -> str:
    normalized = value.strip().casefold().replace("_", "-")
    if normalized not in _SPLIT_MODES:
        raise TaskSplitError(
            "Invalid split mode",
            details={"mode": value, "allowed": sorted(_SPLIT_MODES)},
        )
    return normalized


def _normalize_split_items(items: Sequence[TaskSplitItem]) -> tuple[TaskSplitItem, ...]:
    normalized: list[TaskSplitItem] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, TaskSplitItem):
            raise TaskSplitError(
                "Split items must be TaskSplitItem values",
                details={"index": index},
            )
        title = _required_text(item.title, field=f"items[{index}].title")
        normalized.append(
            TaskSplitItem(
                title=title,
                description=item.description.strip(),
                plan=item.plan.strip(),
            )
        )
    if not normalized:
        raise TaskSplitError("Split requires at least one item", details={"field": "items"})
    if len(normalized) > _MAX_SPLIT_ITEMS:
        raise TaskSplitError(
            "Split item limit exceeded",
            details={"count": len(normalized), "limit": _MAX_SPLIT_ITEMS},
        )
    return tuple(normalized)


def _split_event_metadata(request: TaskSplitRequest) -> dict[str, str]:
    items_json = _split_items_json(request.items)
    metadata = {
        "split_items_hash": hashlib.sha256(items_json.encode("utf-8")).hexdigest(),
        "split_items_count": str(len(request.items)),
        "inherit_dependencies": _bool_text(request.inherit_dependencies),
        "link_sequence": _bool_text(request.link_sequence),
        "transition_to_status": request.transition_to_status or "",
    }
    too_large = {
        key: len(value)
        for key, value in metadata.items()
        if len(value) > MAX_RUN_HISTORY_METADATA_CHARS
    }
    if too_large:
        raise TaskSplitError(
            "Split metadata is too large for run history",
            details={"limits": too_large, "max_chars": MAX_RUN_HISTORY_METADATA_CHARS},
        )
    return metadata


def _split_items_json(items: Sequence[TaskSplitItem]) -> str:
    return json.dumps(
        [
            {
                "title": item.title,
                "description": item.description,
                "plan": item.plan,
            }
            for item in items
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _find_split_idempotency_match(
    repository: MutableRepository,
    candidate: OrchestrationRunEvent,
) -> OrchestrationRunEvent | None:
    if not candidate.idempotency_key:
        return None
    for task in [*repository.list_tasks(), *repository.list_completed_tasks()]:
        if candidate.idempotency_key not in task.raw_source:
            continue
        parsed_history = parse_run_history(task.raw_source)
        if parsed_history.issues:
            issue = parsed_history.issues[0]
            raise RunHistoryParseError(issue.code, issue.message, issue.location)
        match = find_idempotency_match(parsed_history.events, candidate)
        if match is not None:
            return match
    return None


def _created_task_ids_from_event(event: OrchestrationRunEvent) -> list[str]:
    raw_value = event.metadata.get("created_task_ids", "[]")
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(value) for value in parsed]


def _split_result_details(mode: str, created_task_ids: Sequence[str], parent_event_id: str) -> dict[str, object]:
    return {
        "mode": mode,
        "created_task_ids": list(created_task_ids),
        "parent_event_id": parent_event_id,
    }


def _task_dependency_ids(task: TaskRecord) -> list[str]:
    raw_dependencies = task.parsed.frontmatter.get("dependencies")
    if not isinstance(raw_dependencies, Sequence) or isinstance(raw_dependencies, (str, bytes, bytearray)):
        return []
    return [str(dependency) for dependency in raw_dependencies]


def _reject_split_dependency_cycle(
    parent_task_id: str,
    parent_dependencies: Sequence[str],
    request: TaskSplitRequest,
) -> None:
    if not request.inherit_dependencies:
        return
    normalized_parent_id = parent_task_id.casefold()
    if any(dependency.casefold() == normalized_parent_id for dependency in parent_dependencies):
        raise TaskSplitError(
            "Cannot inherit circular parent dependencies",
            details={"task_id": parent_task_id, "dependency_ids": list(parent_dependencies)},
        )


def _split_item_description(parent: TaskRecord, item: TaskSplitItem) -> str:
    lines = [f"Split from {parent.id}: {parent.title}."]
    if item.description:
        lines.extend(("", item.description))
    return "\n".join(lines)


def _dedupe_task_ids(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _rollback_created_tasks(tasks: Sequence[TaskRecord]) -> None:
    for task in reversed(tasks):
        with suppress(OSError):
            task.path.unlink()


def resolve_orchestration_actor(
    actor: str | None = None,
    context: OrchestrationActorContext | None = None,
) -> str:
    explicit_actor = _clean_actor(actor)
    if explicit_actor:
        return explicit_actor
    adapter_identity = _clean_actor(context.adapter_identity if context is not None else None)
    if adapter_identity:
        return adapter_identity
    env_actor = _clean_actor(os.environ.get("BACKLOG_ACTOR"))
    if env_actor:
        return env_actor
    try:
        username = _clean_actor(getpass.getuser())
        hostname = _clean_actor(socket.gethostname())
    except Exception:
        return "unknown"
    if username and hostname:
        return f"{username}@{hostname}"
    return "unknown"


def _apply_state_update(
    current_orchestration: Mapping[str, Any],
    state_update: OrchestrationStateUpdate,
) -> dict[str, Any]:
    updated = dict(current_orchestration)
    for key in ("status_key", "lease_owner", "lease_expires_at", "correlation_id"):
        value = getattr(state_update, key)
        if value is not None:
            updated[key] = value

    review_updates = {
        "state": state_update.review_state,
        "reviewer": state_update.reviewer,
        "attempts": state_update.review_attempts,
        "max_attempts": state_update.review_max_attempts,
    }
    if any(value is not None for value in review_updates.values()):
        raw_review = updated.get("review")
        review = dict(raw_review) if isinstance(raw_review, Mapping) else {}
        for key, value in review_updates.items():
            if value is not None:
                review[key] = value
        updated["review"] = review
    return updated


def _replace_frontmatter_value(source: str, parsed: ParsedTaskMarkdown, key: str, value: object) -> str:
    frontmatter = dict(parsed.frontmatter)
    frontmatter[key] = value
    newline = "\r\n" if "\r\n" in source else "\n"
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    yaml_text = yaml_text.replace("\n", newline)
    return f"---{newline}{yaml_text}{newline}---{newline}{parsed.body}"


def _task_from_source(task: TaskRecord, source: str) -> TaskRecord:
    parsed = parse_task_markdown(source)
    return TaskRecord(
        id=task.id,
        title=task.title,
        status=task.status,
        path=task.path,
        parsed=parsed,
    )


def _state_update_metadata(state_update: OrchestrationStateUpdate | None) -> str:
    if state_update is None:
        return ""
    payload = {key: value for key, value in asdict(state_update).items() if value is not None}
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _project_relative_path(project: BacklogProject, path: object) -> str:
    task_path = path if hasattr(path, "relative_to") else None
    if task_path is None:
        return str(path)
    try:
        return task_path.relative_to(project.root).as_posix()
    except ValueError:
        return task_path.as_posix()


def _validate_current_orchestration(task: TaskRecord, policy: OrchestrationPolicy) -> None:
    issues = validate_orchestration(task, policy)
    if not issues:
        return
    raise OrchestrationValidationError(
        "Task orchestration metadata is invalid",
        details={
            "task_id": task.id,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                    "severity": issue.severity,
                }
                for issue in issues
            ],
        },
    )


def _state_status_key(task: TaskRecord, state: Any) -> str:
    if state is not None and state.status_key:
        return _normalize_key(state.status_key)
    return _normalize_key(task.status)


def _lease_is_active(owner: str | None, expires_at: str | None, now: datetime) -> bool:
    if not owner:
        return False
    parsed = _parse_datetime(expires_at)
    return parsed is not None and parsed > _coerce_datetime(now)


def _require_lease_owner(task: TaskRecord, state: Any, current_version: int, actor: str, now: datetime) -> None:
    lease_owner = state.lease_owner if state is not None else None
    lease_expires_at = state.lease_expires_at if state is not None else None
    if _lease_is_active(lease_owner, lease_expires_at, now) and lease_owner != actor:
        raise OrchestrationLeaseConflict(
            "Task already has an active orchestration lease",
            details={
                "task_id": task.id,
                "actual_version": current_version,
                "lease_owner": lease_owner,
                "lease_expires_at": lease_expires_at,
            },
        )


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
    return _coerce_datetime(parsed)


def _coerce_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_actor(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _required_text(value: str, *, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise OrchestrationValidationError(
            f"Orchestration record-run field {field} is required",
            details={"field": field},
        )
    return cleaned


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _normalize_key(value: str) -> str:
    return "".join(character for character in value.strip().casefold() if character.isalnum())
