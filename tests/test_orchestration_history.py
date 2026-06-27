import pytest

from backlog_py.orchestration import (
    MAX_RUN_HISTORY_FILES,
    MAX_RUN_HISTORY_METADATA_CHARS,
    MAX_RUN_HISTORY_SUMMARY_CHARS,
    MAX_RUN_HISTORY_VERIFICATION_COMMANDS,
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


def test_render_run_history_entry_uses_stable_type_key_not_event_type():
    rendered = render_run_history_entry(_event())

    assert "type: record_run" in rendered
    assert "event_type:" not in rendered
