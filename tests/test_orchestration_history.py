import json

import pytest

from backlog_py.orchestration import (
    MAX_RUN_HISTORY_DROPPED_KEYS,
    MAX_RUN_HISTORY_ENTRIES,
    MAX_RUN_HISTORY_FILES,
    MAX_RUN_HISTORY_METADATA_CHARS,
    MAX_RUN_HISTORY_SUMMARY_CHARS,
    MAX_RUN_HISTORY_VERIFICATION_COMMANDS,
    RUN_HISTORY_TRUNCATION_TYPE,
    OrchestrationIdempotencyConflict,
    OrchestrationRunEvent,
    RunHistoryParseError,
    append_run_history_entry,
    canonical_event_fingerprint,
    find_idempotency_match,
    parse_run_history,
    render_run_history_entry,
)


def _event(**overrides: object) -> OrchestrationRunEvent:
    values = {
        "event_id": "run-1",
        "type": "record_run",
        "actor": "codex",
        "timestamp": "2026-06-26T18:04:00Z",
        "result": "succeeded",
        "summary": "Implemented and verified.",
    }
    values.update(overrides)
    return OrchestrationRunEvent(**values)


def test_parse_run_history_empty_body_returns_no_events_or_issues():
    result = parse_run_history("")

    assert result.events == []
    assert result.issues == []


def test_parse_run_history_valid_section_with_one_entry():
    source = (
        "---\n"
        "id: TASK-1\n"
        "title: Task\n"
        "status: To Do\n"
        "---\n\n"
        "## Run History\n"
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        "```yaml\n"
        "event_id: run-1\n"
        "type: record_run\n"
        "actor: codex\n"
        'timestamp: "2026-06-26T18:04:00Z"\n'
        "result: succeeded\n"
        "files:\n"
        "  - src/backlog_py/orchestration/history.py\n"
        "verification:\n"
        "  - uv run --extra dev python -m pytest tests/test_orchestration_history.py -q\n"
        "```\n"
        "Implemented and verified.\n"
        "<!-- RUN_HISTORY_ENTRY:END -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )

    result = parse_run_history(source)

    assert result.issues == []
    assert result.events == [
        OrchestrationRunEvent(
            event_id="run-1",
            type="record_run",
            actor="codex",
            timestamp="2026-06-26T18:04:00Z",
            result="succeeded",
            summary="Implemented and verified.",
            files=["src/backlog_py/orchestration/history.py"],
            verification=["uv run --extra dev python -m pytest tests/test_orchestration_history.py -q"],
        )
    ]


def test_parse_run_history_malformed_markers_produce_stable_issues():
    source = (
        "---\n"
        "id: TASK-1\n"
        "---\n\n"
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        "```yaml\n"
        "event_id: run-1\n"
        "type: record_run\n"
        "actor: codex\n"
        "timestamp: 2026-06-26T18:04:00Z\n"
        "result: succeeded\n"
        "```\n"
        "Missing entry and section end markers.\n"
    )

    result = parse_run_history(source)

    assert result.events == []
    assert [(issue.code, issue.message, issue.location) for issue in result.issues] == [
        (
            "run_history_section_unterminated",
            "RUN_HISTORY section begin marker has no matching end marker",
            "SECTION:RUN_HISTORY",
        )
    ]


def test_append_run_history_creates_owned_section():
    source = "---\nid: TASK-1\ntitle: Task\nstatus: To Do\n---\n\n## Description\n\nBody\n"
    event = _event()

    updated = append_run_history_entry(source, event)

    assert "<!-- SECTION:RUN_HISTORY:BEGIN -->" in updated
    assert "<!-- RUN_HISTORY_ENTRY:BEGIN -->" in updated
    assert "Implemented and verified." in updated
    assert "type: record_run" in updated


def test_append_run_history_preserves_markdown_outside_owned_section():
    source = (
        "---\n"
        "id: TASK-1\n"
        "---\n\n"
        "Intro before section.\n\n"
        "## Run History\n"
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n\n"
        "Notes after section.\n"
    )

    updated = append_run_history_entry(source, _event())

    assert updated.startswith("---\nid: TASK-1\n---\n\nIntro before section.\n\n## Run History\n")
    assert updated.endswith("\n\nNotes after section.\n")
    assert updated.count("<!-- RUN_HISTORY_ENTRY:BEGIN -->") == 1


def test_render_run_history_entry_truncates_summary_to_named_cap():
    event = _event(summary="x" * (MAX_RUN_HISTORY_SUMMARY_CHARS + 5))

    rendered = render_run_history_entry(event)
    parsed = parse_run_history(
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        f"{rendered}"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )

    assert parsed.issues == []
    assert parsed.events[0].summary == "x" * MAX_RUN_HISTORY_SUMMARY_CHARS


def test_parse_run_history_preserves_summary_with_fenced_code_block():
    source = (
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        "```yaml\n"
        "event_id: run-1\n"
        "type: record_run\n"
        "actor: codex\n"
        "timestamp: 2026-06-26T18:04:00Z\n"
        "result: succeeded\n"
        "```\n"
        "Verified with:\n\n"
        "```bash\n"
        "uv run pytest\n"
        "```\n"
        "<!-- RUN_HISTORY_ENTRY:END -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )

    result = parse_run_history(source)

    assert result.issues == []
    assert result.events[0].summary == "Verified with:\n\n```bash\nuv run pytest\n```"


def test_render_run_history_entry_rejects_file_lists_over_named_cap():
    event = _event(files=[f"file-{index}.py" for index in range(MAX_RUN_HISTORY_FILES + 1)])

    with pytest.raises(RunHistoryParseError) as error:
        render_run_history_entry(event)

    assert error.value.code == "run_history_files_limit_exceeded"


def test_render_run_history_entry_rejects_verification_lists_over_named_cap():
    event = _event(
        verification=[f"uv run check-{index}" for index in range(MAX_RUN_HISTORY_VERIFICATION_COMMANDS + 1)]
    )

    with pytest.raises(RunHistoryParseError) as error:
        render_run_history_entry(event)

    assert error.value.code == "run_history_verification_limit_exceeded"


def test_render_run_history_entry_truncates_metadata_values_to_named_cap():
    event = _event(metadata={"note": "x" * (MAX_RUN_HISTORY_METADATA_CHARS + 5)})

    rendered = render_run_history_entry(event)
    parsed = parse_run_history(
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        f"{rendered}"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )

    assert parsed.issues == []
    assert parsed.events[0].metadata == {"note": "x" * MAX_RUN_HISTORY_METADATA_CHARS}


def test_parse_run_history_reports_over_limit_file_lists_without_event():
    source = (
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        "```yaml\n"
        "event_id: run-1\n"
        "type: record_run\n"
        "actor: codex\n"
        "timestamp: 2026-06-26T18:04:00Z\n"
        "result: succeeded\n"
        "files:\n"
        + "".join(f"  - file-{index}.py\n" for index in range(MAX_RUN_HISTORY_FILES + 1))
        + "```\n"
        "<!-- RUN_HISTORY_ENTRY:END -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )

    result = parse_run_history(source)

    assert result.events == []
    assert [issue.code for issue in result.issues] == ["run_history_files_limit_exceeded"]


def test_find_idempotency_match_returns_prior_event_for_matching_key_and_fingerprint():
    prior = _event(event_id="run-previous", timestamp="2026-06-26T18:04:00Z", idempotency_key="idem-1")
    candidate = _event(event_id="run-new", timestamp="2026-06-26T18:05:00Z", idempotency_key="idem-1")

    assert find_idempotency_match([prior], candidate) == prior


def test_find_idempotency_match_conflicts_on_same_key_with_different_summary():
    prior = _event(idempotency_key="idem-1")
    candidate = _event(event_id="run-2", idempotency_key="idem-1", summary="Different result.")

    with pytest.raises(OrchestrationIdempotencyConflict) as error:
        find_idempotency_match([prior], candidate)

    assert "idem-1" in str(error.value)


def test_canonical_event_fingerprint_excludes_generated_event_id_and_timestamp():
    first = _event(event_id="run-1", timestamp="2026-06-26T18:04:00Z")
    second = _event(event_id="run-2", timestamp="2026-06-26T19:00:00Z")

    assert canonical_event_fingerprint(first) == canonical_event_fingerprint(second)


def test_canonical_event_fingerprint_ignores_file_and_verification_order():
    first = _event(files=["b.py", "a.py"], verification=["pytest b", "pytest a"])
    second = _event(files=["a.py", "b.py"], verification=["pytest a", "pytest b"])

    assert canonical_event_fingerprint(first) == canonical_event_fingerprint(second)


def test_canonical_event_fingerprint_ignores_rendered_markdown_whitespace():
    source = (
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n\n"
        "```yaml\n"
        "result: succeeded\n"
        "timestamp: 2026-06-26T18:04:00Z\n"
        "actor: codex\n"
        "type: record_run\n"
        "event_id: run-1\n"
        "```\n\n"
        "Implemented and verified.\n\n"
        "<!-- RUN_HISTORY_ENTRY:END -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n"
    )
    parsed_event = parse_run_history(source).events[0]

    assert canonical_event_fingerprint(parsed_event) == canonical_event_fingerprint(_event())


def test_canonical_event_fingerprint_ignores_created_task_ids_only_for_split_events():
    first_split = _event(
        type="split_task",
        split_mode="child",
        metadata={"split_items_hash": "abc123", "created_task_ids": '["TASK-1.1"]'},
    )
    second_split = _event(
        type="split_task",
        split_mode="child",
        metadata={"split_items_hash": "abc123", "created_task_ids": '["TASK-1.2"]'},
    )
    first_record = _event(metadata={"created_task_ids": '["TASK-1.1"]'})
    second_record = _event(metadata={"created_task_ids": '["TASK-1.2"]'})

    assert canonical_event_fingerprint(first_split) == canonical_event_fingerprint(second_split)
    assert canonical_event_fingerprint(first_record) != canonical_event_fingerprint(second_record)


def test_render_run_history_entry_uses_stable_type_key_not_event_type():
    rendered = render_run_history_entry(_event())

    assert "type: record_run" in rendered
    assert "event_type:" not in rendered


# --- retention: run history must not grow without bound ---------------------

def _history_with_entries(count: int, *, key_prefix: str | None = None) -> str:
    source = "---\nid: TASK-1\ntitle: Task\nstatus: To Do\n---\n\n## Description\n\nBody\n"
    for index in range(count):
        source = append_run_history_entry(
            source,
            _event(
                event_id=f"run-{index}",
                summary=f"run {index}",
                idempotency_key="" if key_prefix is None else f"{key_prefix}-{index}",
            ),
        )
    return source


def test_append_run_history_entry_caps_retained_entries_with_truncation_marker():
    source = _history_with_entries(MAX_RUN_HISTORY_ENTRIES + 1)

    parsed = parse_run_history(source)

    assert parsed.issues == []
    assert len(parsed.events) == MAX_RUN_HISTORY_ENTRIES
    marker = parsed.events[0]
    assert marker.type == RUN_HISTORY_TRUNCATION_TYPE
    # One append over the cap costs two slots: one for the new entry and one
    # for the marker itself.
    assert marker.metadata["dropped_entries"] == "2"
    assert [event.summary for event in parsed.events[1:]][0] == "run 2"
    assert parsed.events[-1].summary == f"run {MAX_RUN_HISTORY_ENTRIES}"


def test_append_run_history_entry_accumulates_dropped_count_across_truncations():
    total = MAX_RUN_HISTORY_ENTRIES + 4

    parsed = parse_run_history(_history_with_entries(total))

    assert len(parsed.events) == MAX_RUN_HISTORY_ENTRIES
    assert parsed.events.count(parsed.events[0]) == 1
    assert parsed.events[0].metadata["dropped_entries"] == str(total - MAX_RUN_HISTORY_ENTRIES + 1)
    assert parsed.events[1].summary == f"run {total - MAX_RUN_HISTORY_ENTRIES + 1}"


def test_append_run_history_entry_keeps_body_markdown_outside_capped_section():
    source = (
        "---\nid: TASK-1\n---\n\nIntro before section.\n\n"
        "## Run History\n"
        "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        "<!-- SECTION:RUN_HISTORY:END -->\n\n"
        "Notes after section.\n"
    )
    for index in range(MAX_RUN_HISTORY_ENTRIES + 2):
        source = append_run_history_entry(source, _event(event_id=f"run-{index}", summary=f"run {index}"))

    assert source.startswith("---\nid: TASK-1\n---\n\nIntro before section.\n\n## Run History\n")
    assert source.endswith("\n\nNotes after section.\n")
    assert parse_run_history(source).issues == []


def test_append_run_history_entry_reuses_provided_parsed_history(monkeypatch):
    import backlog_py.orchestration.history as history_module

    source = _history_with_entries(2)
    parsed = parse_run_history(source)
    calls: list[str] = []
    monkeypatch.setattr(
        history_module,
        "parse_run_history",
        lambda text: calls.append(text) or parsed,
    )

    updated = history_module.append_run_history_entry(source, _event(event_id="run-next"), history=parsed)

    assert calls == []
    assert "run-next" in updated


def test_find_idempotency_match_reports_conflict_for_key_dropped_by_truncation():
    source = _history_with_entries(MAX_RUN_HISTORY_ENTRIES + 1, key_prefix="idem")
    events = parse_run_history(source).events

    with pytest.raises(OrchestrationIdempotencyConflict) as error:
        find_idempotency_match(events, _event(event_id="retry", idempotency_key="idem-0", summary="run 0"))

    assert "idem-0" in str(error.value)
    assert "truncat" in str(error.value).casefold()


def test_find_idempotency_match_allows_unknown_key_after_truncation():
    source = _history_with_entries(MAX_RUN_HISTORY_ENTRIES + 1, key_prefix="idem")
    events = parse_run_history(source).events

    assert find_idempotency_match(events, _event(event_id="fresh", idempotency_key="never-used")) is None


def test_truncation_marker_bounds_retained_dropped_idempotency_keys():
    total = MAX_RUN_HISTORY_ENTRIES + MAX_RUN_HISTORY_DROPPED_KEYS + 10
    source = _history_with_entries(total, key_prefix="idem")

    events = parse_run_history(source).events
    marker = events[0]
    dropped_keys = json.loads(marker.metadata["dropped_idempotency_keys"])
    # Keys older than the retained window are forgotten, so a replay that old
    # is treated as new work instead of being reported as a conflict.
    assert "idem-0" not in dropped_keys
    assert find_idempotency_match(events, _event(event_id="retry", idempotency_key="idem-0", summary="run 0")) is None

    assert len(dropped_keys) == MAX_RUN_HISTORY_DROPPED_KEYS
    assert len(marker.metadata["dropped_idempotency_keys"]) <= MAX_RUN_HISTORY_METADATA_CHARS
    # The most recently dropped keys are the ones worth remembering.
    dropped_total = int(marker.metadata["dropped_entries"])
    assert dropped_keys[-1] == f"idem-{dropped_total - 1}"
