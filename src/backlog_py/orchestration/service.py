from __future__ import annotations

import getpass
import json
import os
import socket
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import yaml

from backlog_py.core.models import BacklogProject, ParsedTaskMarkdown
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskRecord
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.orchestration.history import append_run_history_entry, find_idempotency_match, parse_run_history
from backlog_py.orchestration.models import (
    OrchestrationActorContext,
    OrchestrationMutationResult,
    OrchestrationQueueReport,
    OrchestrationRecordRunRequest,
    OrchestrationRunEvent,
    OrchestrationStateUpdate,
    OrchestrationValidationError,
    OrchestrationVersionConflict,
    RunHistoryParseError,
    parse_orchestration,
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
        load_orchestration_policy(self.project)
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


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""
