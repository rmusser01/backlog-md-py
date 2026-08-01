from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import yaml

from backlog_py.orchestration.models import (
    OrchestrationIdempotencyConflict,
    OrchestrationRunEvent,
    RunHistoryParseError,
    RunHistoryParseIssue,
    RunHistoryParseResult,
)


MAX_RUN_HISTORY_SUMMARY_CHARS = 4000
MAX_RUN_HISTORY_METADATA_CHARS = 1000
MAX_RUN_HISTORY_FILES = 50
MAX_RUN_HISTORY_VERIFICATION_COMMANDS = 50
# Run history is append-only, so without a cap a long-lived task file grows
# forever and every mutation pays to parse and rewrite it. Older entries are
# dropped in place (rather than archived to a sidecar) so the task file stays
# the single atomically-rewritten source of truth, and a marker entry records
# how many entries were dropped plus the most recent idempotency keys that went
# with them.
MAX_RUN_HISTORY_ENTRIES = 50
MAX_RUN_HISTORY_DROPPED_KEYS = 25

RUN_HISTORY_TRUNCATION_TYPE = "run_history_truncated"
RUN_HISTORY_TRUNCATION_EVENT_ID = "run-history-truncated"
RUN_HISTORY_TRUNCATION_ACTOR = "backlog-md"
RUN_HISTORY_TRUNCATION_RESULT = "truncated"
RUN_HISTORY_DROPPED_ENTRIES_KEY = "dropped_entries"
RUN_HISTORY_DROPPED_KEYS_KEY = "dropped_idempotency_keys"

SECTION_BEGIN = "<!-- SECTION:RUN_HISTORY:BEGIN -->"
SECTION_END = "<!-- SECTION:RUN_HISTORY:END -->"
ENTRY_BEGIN = "<!-- RUN_HISTORY_ENTRY:BEGIN -->"
ENTRY_END = "<!-- RUN_HISTORY_ENTRY:END -->"

_REQUIRED_METADATA_KEYS = ("event_id", "type", "actor", "timestamp", "result")
_EVENT_METADATA_KEYS = {
    "event_id",
    "type",
    "actor",
    "timestamp",
    "result",
    "summary",
    "idempotency_key",
    "task_id",
    "from_status",
    "to_status",
    "split_mode",
    "files",
    "verification",
    "metadata",
}
_IDEMPOTENCY_REPLAY_METADATA_KEYS = {
    "created_task_ids",
}


def parse_run_history(source: str) -> RunHistoryParseResult:
    section_result = _extract_section(source)
    if section_result.issue is not None:
        return RunHistoryParseResult(events=[], issues=[section_result.issue])
    if section_result.body is None:
        return RunHistoryParseResult(events=[], issues=[])

    entries_result = _extract_entries(section_result.body)
    if entries_result.issues:
        return RunHistoryParseResult(events=[], issues=entries_result.issues)

    events: list[OrchestrationRunEvent] = []
    issues: list[RunHistoryParseIssue] = []
    for index, entry_source in enumerate(entries_result.entries, start=1):
        event, entry_issues = _parse_entry(entry_source, index)
        issues.extend(entry_issues)
        if event is not None:
            events.append(event)
    return RunHistoryParseResult(events=events, issues=issues)


def render_run_history_entry(event: OrchestrationRunEvent) -> str:
    _validate_entry_limits(event)
    metadata = _render_metadata(event)
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    summary = _cap_text(_normalize_summary(event.summary), MAX_RUN_HISTORY_SUMMARY_CHARS)
    body = f"{summary}\n" if summary else ""
    return f"{ENTRY_BEGIN}\n```yaml\n{yaml_text}\n```\n{body}{ENTRY_END}\n"


def append_run_history_entry(
    source: str,
    event: OrchestrationRunEvent,
    *,
    history: RunHistoryParseResult | None = None,
) -> str:
    """Append one run history entry, dropping the oldest entries past the cap.

    Callers that already parsed ``source`` should pass the result as
    ``history`` so the section is not parsed twice per mutation.
    """
    parsed = parse_run_history(source) if history is None else history
    if parsed.issues:
        issue = parsed.issues[0]
        raise RunHistoryParseError(issue.code, issue.message, issue.location)

    rendered = render_run_history_entry(event)
    if SECTION_BEGIN not in source and SECTION_END not in source:
        separator = "\n" if source.endswith("\n") else "\n\n"
        return f"{source}{separator}## Run History\n{SECTION_BEGIN}\n{rendered}{SECTION_END}\n"

    source = _apply_retention(source, parsed.events, event)
    insert_at = source.find(SECTION_END)
    prefix = source[:insert_at]
    suffix = source[insert_at:]
    if not prefix.endswith("\n"):
        prefix = f"{prefix}\n"
    return f"{prefix}{rendered}{suffix}"


def dropped_idempotency_keys(event: OrchestrationRunEvent) -> tuple[str, ...]:
    """Idempotency keys recorded on a truncation marker, newest last."""
    if event.type != RUN_HISTORY_TRUNCATION_TYPE:
        return ()
    raw_value = event.metadata.get(RUN_HISTORY_DROPPED_KEYS_KEY, "")
    if not raw_value:
        return ()
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(_as_string(value) for value in parsed)


def canonical_event_fingerprint(event: OrchestrationRunEvent) -> str:
    payload = {
        "type": _as_string(event.type),
        "actor": _as_string(event.actor),
        "result": _as_string(event.result),
        "task_id": _as_string(event.task_id),
        "to_status": _as_string(event.to_status),
        "split_mode": _as_string(event.split_mode),
        "summary": _cap_text(_normalize_summary(event.summary), MAX_RUN_HISTORY_SUMMARY_CHARS),
        "files": sorted(_as_string(value) for value in event.files),
        "verification": sorted(_as_string(value) for value in event.verification),
        "metadata": _normalize_idempotency_metadata(event),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def find_idempotency_match(
    events: Iterable[OrchestrationRunEvent],
    candidate: OrchestrationRunEvent,
) -> OrchestrationRunEvent | None:
    idempotency_key = _as_string(candidate.idempotency_key)
    if not idempotency_key:
        return None

    candidate_fingerprint = canonical_event_fingerprint(candidate)
    for event in events:
        if event.type == RUN_HISTORY_TRUNCATION_TYPE:
            # A replay whose entry aged out cannot be verified, so refuse it
            # loudly instead of silently re-running the operation. Keys older
            # than the marker's retained window are not remembered and are
            # treated as new work.
            if idempotency_key in dropped_idempotency_keys(event):
                raise OrchestrationIdempotencyConflict(
                    idempotency_key,
                    f"Idempotency key {idempotency_key!r} belongs to a run history entry that was "
                    "truncated, so the original result can no longer be replayed",
                )
            continue
        if event.idempotency_key != idempotency_key:
            continue
        if canonical_event_fingerprint(event) == candidate_fingerprint:
            return event
        raise OrchestrationIdempotencyConflict(
            idempotency_key,
            f"Idempotency key {idempotency_key!r} was already used for different run metadata",
        )
    return None


def _apply_retention(
    source: str,
    events: Sequence[OrchestrationRunEvent],
    incoming: OrchestrationRunEvent,
) -> str:
    """Drop the oldest entries so the section stays within its entry cap.

    The truncation marker lives at the top of the section (the oldest
    position) and occupies one of the retained slots, so appending past the cap
    for the first time costs two entries: the marker plus room for the new one.
    """
    marker = events[0] if events and events[0].type == RUN_HISTORY_TRUNCATION_TYPE else None
    reserved = 1 if marker is not None else 0
    retained = list(events[reserved:])
    if len(retained) + 1 + reserved <= MAX_RUN_HISTORY_ENTRIES:
        return source

    spans = _entry_spans(source)
    if len(spans) != len(events):
        # The parsed events and the rendered entries disagree; leave the file
        # alone rather than cutting the wrong text.
        return source

    drop_count = max(len(retained) + 2 - MAX_RUN_HISTORY_ENTRIES, 1)
    dropped = retained[:drop_count]
    rendered_marker = render_run_history_entry(_truncation_marker(marker, dropped, incoming))
    dropped_indexes = set(range(reserved, reserved + drop_count))

    pieces: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(spans):
        pieces.append(source[cursor:start])
        if index == 0:
            pieces.append(rendered_marker)
        if index >= reserved and index not in dropped_indexes:
            pieces.append(source[start:end])
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _entry_spans(source: str) -> list[tuple[int, int]]:
    begin_index = source.find(SECTION_BEGIN)
    end_index = source.find(SECTION_END)
    if begin_index == -1 or end_index == -1 or end_index < begin_index:
        return []
    spans: list[tuple[int, int]] = []
    cursor = begin_index + len(SECTION_BEGIN)
    while True:
        entry_begin = source.find(ENTRY_BEGIN, cursor, end_index)
        if entry_begin == -1:
            break
        entry_end = source.find(ENTRY_END, entry_begin, end_index)
        if entry_end == -1:
            break
        stop = entry_end + len(ENTRY_END)
        if source.startswith("\n", stop):
            stop += 1
        spans.append((entry_begin, stop))
        cursor = stop
    return spans


def _truncation_marker(
    previous: OrchestrationRunEvent | None,
    dropped: Sequence[OrchestrationRunEvent],
    incoming: OrchestrationRunEvent,
) -> OrchestrationRunEvent:
    previous_total = _positive_int(previous.metadata.get(RUN_HISTORY_DROPPED_ENTRIES_KEY)) if previous else 0
    total_dropped = previous_total + len(dropped)
    keys = [
        *(dropped_idempotency_keys(previous) if previous is not None else ()),
        *(_as_string(event.idempotency_key) for event in dropped if _as_string(event.idempotency_key)),
    ]
    timestamp = _as_string(incoming.timestamp) or (_as_string(dropped[-1].timestamp) if dropped else "")
    return OrchestrationRunEvent(
        event_id=RUN_HISTORY_TRUNCATION_EVENT_ID,
        type=RUN_HISTORY_TRUNCATION_TYPE,
        actor=RUN_HISTORY_TRUNCATION_ACTOR,
        timestamp=timestamp or _UNKNOWN_TIMESTAMP,
        result=RUN_HISTORY_TRUNCATION_RESULT,
        summary=(
            f"{total_dropped} older run history entries were dropped to keep this task file bounded "
            f"at {MAX_RUN_HISTORY_ENTRIES} entries."
        ),
        task_id=_as_string(incoming.task_id),
        metadata={
            RUN_HISTORY_DROPPED_ENTRIES_KEY: str(total_dropped),
            RUN_HISTORY_DROPPED_KEYS_KEY: _dropped_keys_json(_bounded_dropped_keys(keys)),
        },
    )


def _bounded_dropped_keys(keys: Sequence[str]) -> list[str]:
    unique = list(dict.fromkeys(keys))
    bounded = unique[-MAX_RUN_HISTORY_DROPPED_KEYS:]
    # Metadata values are capped on render; a truncated JSON string would not
    # parse back, so shrink the window until it fits whole.
    while bounded and len(_dropped_keys_json(bounded)) > MAX_RUN_HISTORY_METADATA_CHARS:
        bounded = bounded[1:]
    return bounded


def _dropped_keys_json(keys: Sequence[str]) -> str:
    return json.dumps(list(keys), separators=(",", ":"), ensure_ascii=True)


def _positive_int(value: Any) -> int:
    try:
        parsed = int(_as_string(value))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


_UNKNOWN_TIMESTAMP = "1970-01-01T00:00:00Z"


class _SectionResult:
    def __init__(self, body: str | None = None, issue: RunHistoryParseIssue | None = None) -> None:
        self.body = body
        self.issue = issue


class _EntriesResult:
    def __init__(self, entries: list[str] | None = None, issues: list[RunHistoryParseIssue] | None = None) -> None:
        self.entries = entries or []
        self.issues = issues or []


def _extract_section(source: str) -> _SectionResult:
    begin_index = source.find(SECTION_BEGIN)
    end_index = source.find(SECTION_END)
    if begin_index == -1 and end_index == -1:
        return _SectionResult()
    if begin_index == -1:
        return _SectionResult(
            issue=RunHistoryParseIssue(
                code="run_history_section_end_without_begin",
                message="RUN_HISTORY section end marker has no matching begin marker",
                location="SECTION:RUN_HISTORY",
            )
        )
    if end_index == -1 or end_index < begin_index:
        return _SectionResult(
            issue=RunHistoryParseIssue(
                code="run_history_section_unterminated",
                message="RUN_HISTORY section begin marker has no matching end marker",
                location="SECTION:RUN_HISTORY",
            )
        )
    section_start = begin_index + len(SECTION_BEGIN)
    return _SectionResult(body=source[section_start:end_index])


def _extract_entries(section_source: str) -> _EntriesResult:
    entries: list[str] = []
    issues: list[RunHistoryParseIssue] = []
    cursor = 0
    while True:
        begin_index = section_source.find(ENTRY_BEGIN, cursor)
        end_index = section_source.find(ENTRY_END, cursor)
        if begin_index == -1 and end_index == -1:
            break
        if begin_index == -1 or (end_index != -1 and end_index < begin_index):
            issues.append(
                RunHistoryParseIssue(
                    code="run_history_entry_end_without_begin",
                    message="RUN_HISTORY entry end marker has no matching begin marker",
                    location="RUN_HISTORY_ENTRY",
                )
            )
            break
        if end_index == -1:
            issues.append(
                RunHistoryParseIssue(
                    code="run_history_entry_unterminated",
                    message="RUN_HISTORY entry begin marker has no matching end marker",
                    location="RUN_HISTORY_ENTRY",
                )
            )
            break
        entry_start = begin_index + len(ENTRY_BEGIN)
        entries.append(section_source[entry_start:end_index])
        cursor = end_index + len(ENTRY_END)
    return _EntriesResult(entries=entries, issues=issues)


def _parse_entry(source: str, index: int) -> tuple[OrchestrationRunEvent | None, list[RunHistoryParseIssue]]:
    yaml_text, summary, issue = _split_entry(source)
    if issue is not None:
        return None, [issue]

    try:
        raw_metadata = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        return None, [
            RunHistoryParseIssue(
                code="run_history_entry_invalid_yaml",
                message=f"RUN_HISTORY entry YAML is invalid: {exc}",
                location=f"RUN_HISTORY_ENTRY[{index}]",
            )
        ]
    if not isinstance(raw_metadata, Mapping):
        return None, [
            RunHistoryParseIssue(
                code="run_history_entry_yaml_not_mapping",
                message="RUN_HISTORY entry YAML must be a mapping",
                location=f"RUN_HISTORY_ENTRY[{index}]",
            )
        ]

    missing_keys = [key for key in _REQUIRED_METADATA_KEYS if not _as_string(raw_metadata.get(key))]
    if missing_keys:
        return None, [
            RunHistoryParseIssue(
                code="run_history_entry_missing_required_metadata",
                message=f"RUN_HISTORY entry is missing required metadata: {', '.join(missing_keys)}",
                location=f"RUN_HISTORY_ENTRY[{index}]",
            )
        ]

    list_issues = _validate_parsed_list_limits(raw_metadata, index)
    if list_issues:
        return None, list_issues

    return _event_from_metadata(raw_metadata, summary), []


def _split_entry(source: str) -> tuple[str, str, RunHistoryParseIssue | None]:
    lines = source.lstrip("\n").splitlines()
    if not lines or lines[0].strip() != "```yaml":
        return (
            "",
            "",
            RunHistoryParseIssue(
                code="run_history_entry_missing_yaml_fence",
                message="RUN_HISTORY entry must start with a yaml fenced block",
                location="RUN_HISTORY_ENTRY",
            ),
        )
    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "```":
            closing_index = index
            break
    if closing_index is None:
        return (
            "",
            "",
            RunHistoryParseIssue(
                code="run_history_entry_unterminated_yaml_fence",
                message="RUN_HISTORY entry yaml fenced block has no closing fence",
                location="RUN_HISTORY_ENTRY",
            ),
        )

    yaml_text = "\n".join(lines[1:closing_index])
    summary = "\n".join(lines[closing_index + 1 :]).strip()
    return yaml_text, summary, None


def _event_from_metadata(metadata: Mapping[Any, Any], summary: str) -> OrchestrationRunEvent:
    nested_metadata = metadata.get("metadata")
    if isinstance(nested_metadata, Mapping):
        extra_metadata = _normalize_metadata(nested_metadata)
    else:
        extra_metadata = _normalize_metadata(
            {str(key): value for key, value in metadata.items() if str(key) not in _EVENT_METADATA_KEYS}
        )

    return OrchestrationRunEvent(
        event_id=_as_string(metadata.get("event_id")),
        type=_as_string(metadata.get("type")),
        actor=_as_string(metadata.get("actor")),
        timestamp=_as_string(metadata.get("timestamp")),
        result=_as_string(metadata.get("result")),
        summary=_cap_text(_normalize_summary(summary or _as_string(metadata.get("summary"))), MAX_RUN_HISTORY_SUMMARY_CHARS),
        idempotency_key=_as_string(metadata.get("idempotency_key")),
        task_id=_as_string(metadata.get("task_id")),
        from_status=_as_string(metadata.get("from_status")),
        to_status=_as_string(metadata.get("to_status")),
        split_mode=_as_string(metadata.get("split_mode")),
        files=_string_list(metadata.get("files"), MAX_RUN_HISTORY_FILES),
        verification=_string_list(metadata.get("verification"), MAX_RUN_HISTORY_VERIFICATION_COMMANDS),
        metadata=extra_metadata,
    )


def _render_metadata(event: OrchestrationRunEvent) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "event_id": _as_string(event.event_id),
        "type": _as_string(event.type),
        "actor": _as_string(event.actor),
        "timestamp": _as_string(event.timestamp),
        "result": _as_string(event.result),
    }
    optional_fields = {
        "idempotency_key": event.idempotency_key,
        "task_id": event.task_id,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "split_mode": event.split_mode,
    }
    for key, value in optional_fields.items():
        normalized = _as_string(value)
        if normalized:
            metadata[key] = normalized
    if event.files:
        metadata["files"] = [_as_string(value) for value in event.files]
    if event.verification:
        metadata["verification"] = [_as_string(value) for value in event.verification]
    normalized_metadata = _normalize_metadata(event.metadata)
    if normalized_metadata:
        metadata["metadata"] = normalized_metadata
    return metadata


_RESERVED_RUN_HISTORY_MARKERS = (SECTION_BEGIN, SECTION_END, ENTRY_BEGIN, ENTRY_END)


def _contains_reserved_marker(text: str) -> bool:
    return any(marker in text for marker in _RESERVED_RUN_HISTORY_MARKERS)


def _validate_entry_limits(event: OrchestrationRunEvent) -> None:
    for text in (event.summary, *event.files, *event.verification):
        if isinstance(text, str) and _contains_reserved_marker(text):
            raise RunHistoryParseError(
                "run_history_reserved_marker",
                "Run history text may not contain reserved run-history markers",
                "summary",
            )
    if len(event.files) > MAX_RUN_HISTORY_FILES:
        raise RunHistoryParseError(
            "run_history_files_limit_exceeded",
            f"Run history entries may list at most {MAX_RUN_HISTORY_FILES} files",
            "files",
        )
    if len(event.verification) > MAX_RUN_HISTORY_VERIFICATION_COMMANDS:
        raise RunHistoryParseError(
            "run_history_verification_limit_exceeded",
            f"Run history entries may list at most {MAX_RUN_HISTORY_VERIFICATION_COMMANDS} verification commands",
            "verification",
        )


def _validate_parsed_list_limits(metadata: Mapping[Any, Any], index: int) -> list[RunHistoryParseIssue]:
    issues: list[RunHistoryParseIssue] = []
    files_issue = _parsed_list_issue(
        metadata.get("files"),
        limit=MAX_RUN_HISTORY_FILES,
        field="files",
        code="run_history_files_limit_exceeded",
        index=index,
    )
    if files_issue is not None:
        issues.append(files_issue)
    verification_issue = _parsed_list_issue(
        metadata.get("verification"),
        limit=MAX_RUN_HISTORY_VERIFICATION_COMMANDS,
        field="verification",
        code="run_history_verification_limit_exceeded",
        index=index,
    )
    if verification_issue is not None:
        issues.append(verification_issue)
    return issues


def _parsed_list_issue(
    value: Any,
    *,
    limit: int,
    field: str,
    code: str,
    index: int,
) -> RunHistoryParseIssue | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return RunHistoryParseIssue(
            code=f"run_history_{field}_not_list",
            message=f"RUN_HISTORY entry {field} metadata must be a list",
            location=f"RUN_HISTORY_ENTRY[{index}].{field}",
        )
    if len(value) > limit:
        return RunHistoryParseIssue(
            code=code,
            message=f"RUN_HISTORY entry {field} metadata may contain at most {limit} items",
            location=f"RUN_HISTORY_ENTRY[{index}].{field}",
        )
    return None


def _string_list(value: Any, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_as_string(item) for item in list(value)[:limit]]


def _normalize_metadata(metadata: Mapping[Any, Any]) -> dict[str, str]:
    return {
        _as_string(key): _cap_text(_as_string(value), MAX_RUN_HISTORY_METADATA_CHARS)
        for key, value in sorted(metadata.items(), key=lambda item: _as_string(item[0]))
        if _as_string(key)
    }


def _normalize_idempotency_metadata(event: OrchestrationRunEvent) -> dict[str, str]:
    metadata = _normalize_metadata(event.metadata)
    if event.type != "split_task":
        return metadata
    return {key: value for key, value in metadata.items() if key not in _IDEMPOTENCY_REPLAY_METADATA_KEYS}


def _normalize_summary(summary: str) -> str:
    return "\n".join(line.rstrip() for line in _as_string(summary).strip().splitlines())


def _cap_text(value: str, limit: int) -> str:
    return value[:limit]


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
