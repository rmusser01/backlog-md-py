from __future__ import annotations

from pathlib import Path

import pytest

from backlog_py.orchestration import OrchestrationPolicy, OrchestrationPolicyError, load_orchestration_policy
from backlog_py.storage.project import discover_project


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    backlog_dir = repo / "backlog"
    backlog_dir.mkdir(parents=True)
    (backlog_dir / "config.yml").write_text("projectName: policy-test\n", encoding="utf-8")
    return repo


def test_load_orchestration_policy_missing_file_returns_default(tmp_path):
    project = discover_project(_repo(tmp_path))

    policy = load_orchestration_policy(project)

    assert policy == OrchestrationPolicy.default()


def test_load_orchestration_policy_valid_custom_states_and_transitions(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  Todo:",
                "    claimable: true",
                "  Review:",
                "    claimable: false",
                "  Complete:",
                "    terminal: true",
                "transitions:",
                "  Todo: [Review]",
                "  Review: [Complete]",
                "  Complete: []",
            ]
        ),
        encoding="utf-8",
    )
    project = discover_project(repo)

    policy = load_orchestration_policy(project)

    assert policy.is_claimable("todo")
    assert policy.is_terminal("complete")
    assert policy.can_transition("todo", "review")
    assert policy.can_transition("review", "complete")


def test_load_orchestration_policy_invalid_transition_raises_with_validation_details(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  todo:",
                "    claimable: true",
                "  complete:",
                "    terminal: true",
                "transitions:",
                "  todo: [missing]",
                "  complete: []",
            ]
        ),
        encoding="utf-8",
    )
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert {
        "code": "policy_unknown_transition_target",
        "message": "Transition target is not a known state: missing",
        "path": "transitions.todo",
        "severity": "error",
    } in error.value.details["issues"]


def test_load_orchestration_policy_non_mapping_yaml_raises_policy_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text("- not\n- a mapping\n", encoding="utf-8")
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert "must contain a mapping" in str(error.value)


def test_load_orchestration_policy_null_yaml_raises_policy_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text("null\n", encoding="utf-8")
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert "must contain a mapping" in str(error.value)


def test_load_orchestration_policy_invalid_yaml_raises_policy_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text("states: [unterminated\n", encoding="utf-8")
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert error.value.details["path"].endswith("orchestration.yml")


def test_load_orchestration_policy_non_utf8_file_raises_policy_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_bytes(b"\xff\xfe\x00")
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert "Unable to decode orchestration policy" in str(error.value)


def test_load_orchestration_policy_unreadable_file_raises_policy_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    policy_path = repo / "backlog" / "orchestration.yml"
    policy_path.write_text("states: {}\n", encoding="utf-8")
    project = discover_project(repo)
    original_read_text = Path.read_text

    def fail_policy_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == policy_path:
            raise OSError("permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_policy_read)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert "Unable to read orchestration policy" in str(error.value)
    assert error.value.details["error"] == "permission denied"


def test_load_orchestration_policy_rejects_duplicate_normalized_state_keys(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  todo:",
                "    claimable: true",
                "  To-Do:",
                "    claimable: true",
                "  complete:",
                "    terminal: true",
                "transitions:",
                "  todo: [complete]",
                "  complete: []",
            ]
        ),
        encoding="utf-8",
    )
    project = discover_project(repo)

    with pytest.raises(OrchestrationPolicyError) as error:
        load_orchestration_policy(project)

    assert "duplicate normalized state key" in str(error.value)


def test_load_orchestration_policy_preserves_defaults_when_optional_values_omitted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  todo:",
                "    claimable: true",
                "  complete:",
                "    terminal: true",
                "transitions:",
                "  todo: [complete]",
                "  complete: []",
            ]
        ),
        encoding="utf-8",
    )
    project = discover_project(repo)

    policy = load_orchestration_policy(project)

    default_policy = OrchestrationPolicy.default()
    assert policy.default_lease_ttl_seconds == default_policy.default_lease_ttl_seconds
    assert policy.default_review_max_attempts == default_policy.default_review_max_attempts


# --- claim target derivation (claim must not hardcode "inprogress") ---------

def test_default_policy_claim_target_status_is_inprogress():
    policy = OrchestrationPolicy.default()

    assert policy.claim_target_status("todo") == "inprogress"


def test_claim_target_status_uses_custom_working_state(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  todo:",
                "    claimable: true",
                "  doing: {}",
                "  review: {}",
                "  done:",
                "    terminal: true",
                "transitions:",
                "  todo: [doing]",
                "  doing: [review, todo]",
                "  review: [done, doing]",
                "  done: []",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_orchestration_policy(discover_project(repo))

    assert policy.claim_target_status("todo") == "doing"
    assert policy.claim_target_status() == "doing"


def test_claim_target_status_skips_claimable_and_terminal_targets(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  triage:",
                "    claimable: true",
                "  todo:",
                "    claimable: true",
                "  active: {}",
                "  done:",
                "    terminal: true",
                "transitions:",
                "  triage: [todo, done, active]",
                "  todo: [active]",
                "  active: [done]",
                "  done: []",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_orchestration_policy(discover_project(repo))

    # "todo" is claimable and "done" is terminal, so neither is a claim target.
    assert policy.claim_target_status("triage") == "active"


def test_claim_target_status_falls_back_to_first_claimable_target_for_other_statuses():
    policy = OrchestrationPolicy.default()

    # "complete" has no outgoing transitions; the reported target still comes
    # from the policy's claimable entry point so errors stay actionable.
    assert policy.claim_target_status("complete") == "inprogress"


def test_claim_target_status_returns_none_when_policy_has_no_working_state(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backlog" / "orchestration.yml").write_text(
        "\n".join(
            [
                "states:",
                "  todo:",
                "    claimable: true",
                "  done:",
                "    terminal: true",
                "transitions:",
                "  todo: [done]",
                "  done: []",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_orchestration_policy(discover_project(repo))

    assert policy.claim_target_status("todo") is None
