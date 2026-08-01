import json
import re
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.orchestration import parse_orchestration, parse_run_history
from backlog_py.core.repository import MutableRepository


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    task_dir = repo / "backlog" / "tasks"
    task_dir.mkdir(parents=True)
    (repo / "backlog" / "config.yml").write_text("projectName: cli-orchestration-test\n", encoding="utf-8")
    _task_path(repo).write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Example\n"
        "status: To Do\n"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    return repo


def _task_path(repo: Path) -> Path:
    return repo / "backlog" / "tasks" / "task-1 - Example.md"


def _set_orchestration(repo: Path, body: str) -> None:
    _task_path(repo).write_text(
        "---\n"
        "id: TASK-1\n"
        "title: Example\n"
        "status: To Do\n"
        "orchestration:\n"
        f"{body}"
        "---\n\n"
        "## Description\n\n"
        "Body\n",
        encoding="utf-8",
    )


def _invoke(repo: Path, *args: str):
    return CliRunner().invoke(main, ["--cwd", str(repo), *args])


def test_orchestration_record_run_plain_prints_new_event_id(tmp_path):
    repo = _repo(tmp_path)

    result = _invoke(
        repo,
        "orchestration",
        "record-run",
        "TASK-1",
        "--actor",
        "codex",
        "--result",
        "succeeded",
        "--summary",
        "done",
        "--plain",
    )

    assert result.exit_code == 0, result.output
    assert "TASK-1" in result.output
    match = re.search(r"run-[0-9a-f]+", result.output)
    assert match is not None
    parsed = parse_run_history(_task_path(repo).read_text(encoding="utf-8"))
    assert [event.event_id for event in parsed.events] == [match.group(0)]


def test_orchestration_record_run_json_reports_stable_fields(tmp_path):
    repo = _repo(tmp_path)

    result = _invoke(
        repo,
        "orchestration",
        "record-run",
        "TASK-1",
        "--actor",
        "codex",
        "--result",
        "succeeded",
        "--summary",
        "done",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taskId"] == "TASK-1"
    assert payload["path"] == "backlog/tasks/task-1 - Example.md"
    assert payload["version"] == 0
    assert payload["eventId"].startswith("run-")
    assert payload["runHistoryEventIds"] == [payload["eventId"]]
    assert payload["queueCategory"] == "eligible"
    assert payload["validationIssues"] == []


def test_orchestration_record_run_malformed_task_reports_actionable_error(tmp_path):
    repo = _repo(tmp_path)
    _task_path(repo).write_text(
        _task_path(repo).read_text(encoding="utf-8")
        + "\n## Run History\n"
        + "<!-- SECTION:RUN_HISTORY:BEGIN -->\n"
        + "<!-- RUN_HISTORY_ENTRY:BEGIN -->\n"
        + "<!-- SECTION:RUN_HISTORY:END -->\n",
        encoding="utf-8",
    )

    result = _invoke(
        repo,
        "orchestration",
        "record-run",
        "TASK-1",
        "--actor",
        "codex",
        "--result",
        "failed",
        "--summary",
        "could not continue",
        "--plain",
    )

    assert result.exit_code != 0
    assert "TASK-1" in result.output
    assert "run history" in result.output.lower()
    assert "fix" in result.output.lower()


def test_orchestration_claim_json_claims_task(tmp_path):
    repo = _repo(tmp_path)

    result = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--idempotency-key",
        "claim-task-1",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 1
    assert payload["eventId"].startswith("run-")
    assert payload["runHistoryEventIds"] == [payload["eventId"]]
    assert payload["queueCategory"] == "claimed"
    assert payload["validationIssues"] == []
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.status_key == "inprogress"
    assert orchestration.version == 1
    assert orchestration.lease_owner == "codex"
    assert orchestration.lease_expires_at is not None


def test_orchestration_release_json_clears_claim(tmp_path):
    repo = _repo(tmp_path)
    claim = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--json",
    )
    assert claim.exit_code == 0, claim.output

    result = _invoke(
        repo,
        "orchestration",
        "release",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "1",
        "--reason",
        "handoff",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 2
    assert payload["eventId"].startswith("run-")
    assert len(payload["runHistoryEventIds"]) == 2
    # Released tasks return to a claimable status, so they are eligible again.
    assert payload["queueCategory"] == "eligible"
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.version == 2
    assert orchestration.status_key == "todo"
    assert orchestration.lease_owner is None
    assert orchestration.lease_expires_at is None


def test_orchestration_transition_json_moves_state(tmp_path):
    repo = _repo(tmp_path)

    result = _invoke(
        repo,
        "orchestration",
        "transition",
        "TASK-1",
        "inprogress",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--reason",
        "started",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 1
    assert payload["eventId"].startswith("run-")
    assert payload["queueCategory"] == "in_workflow"
    task = MutableRepository.from_path(repo).get_task("TASK-1")
    orchestration = parse_orchestration(task)
    assert orchestration is not None
    assert orchestration.status_key == "inprogress"
    assert orchestration.version == 1


def test_orchestration_split_json_creates_child_tasks(tmp_path):
    repo = _repo(tmp_path)

    result = _invoke(
        repo,
        "orchestration",
        "split",
        "TASK-1",
        "--mode",
        "child",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--idempotency-key",
        "split-task-1",
        "--item",
        "Add parser coverage",
        "--item",
        "Update docs",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taskId"] == "TASK-1"
    assert payload["version"] == 1
    assert payload["eventId"].startswith("run-")
    assert payload["parentEventId"] == payload["eventId"]
    assert payload["createdTaskIds"] == ["TASK-1.1", "TASK-1.2"]
    assert payload["queueCategory"] == "eligible"
    repository = MutableRepository.from_path(repo)
    assert repository.get_task("TASK-1.1").parsed.frontmatter["parent_task_id"] == "TASK-1"
    assert repository.get_task("TASK-1.2").parsed.frontmatter["parent_task_id"] == "TASK-1"


def test_orchestration_read_commands_json_report_queue_slices(tmp_path):
    repo = _repo(tmp_path)

    status = _invoke(repo, "orchestration", "status", "--json")
    queue = _invoke(repo, "orchestration", "queue", "--json")
    eligible = _invoke(repo, "orchestration", "eligible", "--json")

    assert status.exit_code == 0, status.output
    assert queue.exit_code == 0, queue.output
    assert eligible.exit_code == 0, eligible.output
    status_payload = json.loads(status.output)
    queue_payload = json.loads(queue.output)
    eligible_payload = json.loads(eligible.output)
    assert status_payload["byCategory"]["eligible"] == 1
    assert queue_payload["items"][0]["taskId"] == "TASK-1"
    assert eligible_payload["items"][0]["queueCategory"] == "eligible"

    claim = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--json",
    )
    assert claim.exit_code == 0, claim.output
    claims = _invoke(repo, "orchestration", "claims", "--json")
    assert claims.exit_code == 0, claims.output
    assert json.loads(claims.output)["items"][0]["leaseOwner"] == "codex"

    _set_orchestration(
        repo,
        "  status_key: inprogress\n"
        "  version: 2\n"
        "  lease_owner: old-agent\n"
        "  lease_expires_at: '2026-01-01T00:00:00Z'\n",
    )
    stale = _invoke(repo, "orchestration", "stale-leases", "--json")
    assert stale.exit_code == 0, stale.output
    assert json.loads(stale.output)["items"][0]["queueCategory"] == "stale_claim"


def test_orchestration_claim_conflict_reports_actionable_error(tmp_path):
    repo = _repo(tmp_path)
    first = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "agent-a",
        "--expected-version",
        "0",
    )
    assert first.exit_code == 0, first.output

    conflict = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "agent-b",
        "--expected-version",
        "1",
    )

    assert conflict.exit_code != 0
    assert "TASK-1" in conflict.output
    assert "lease_owner=agent-a" in conflict.output
    assert "actual_version=1" in conflict.output


def test_orchestration_status_plain_prints_tab_separated_counts(tmp_path):
    repo = _repo(tmp_path)

    default = _invoke(repo, "orchestration", "status")
    plain = _invoke(repo, "orchestration", "status", "--plain")

    assert default.exit_code == 0, default.output
    assert plain.exit_code == 0, plain.output
    assert default.output == "eligible: 1\n"
    assert plain.output == "eligible\t1\n"


def test_orchestration_queue_plain_prints_tab_separated_records(tmp_path):
    repo = _repo(tmp_path)

    default = _invoke(repo, "orchestration", "queue")
    plain = _invoke(repo, "orchestration", "queue", "--plain")

    assert default.exit_code == 0, default.output
    assert plain.exit_code == 0, plain.output
    assert default.output == "TASK-1 [eligible] v0 Example (backlog/tasks/task-1 - Example.md)\n"
    assert plain.output == "TASK-1\teligible\t0\ttodo\t\tbacklog/tasks/task-1 - Example.md\tExample\n"


def test_orchestration_eligible_claims_and_stale_leases_support_plain(tmp_path):
    repo = _repo(tmp_path)

    eligible = _invoke(repo, "orchestration", "eligible", "--plain")
    assert eligible.exit_code == 0, eligible.output
    assert eligible.output.split("\t")[:2] == ["TASK-1", "eligible"]

    claim = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
    )
    assert claim.exit_code == 0, claim.output

    claims = _invoke(repo, "orchestration", "claims", "--plain")
    assert claims.exit_code == 0, claims.output
    fields = claims.output.rstrip("\n").split("\t")
    assert fields[0] == "TASK-1"
    assert fields[1] == "claimed"
    assert fields[4] == "codex"

    _set_orchestration(
        repo,
        "  status_key: inprogress\n"
        "  version: 2\n"
        "  lease_owner: old-agent\n"
        "  lease_expires_at: '2026-01-01T00:00:00Z'\n",
    )
    stale = _invoke(repo, "orchestration", "stale-leases", "--plain")
    assert stale.exit_code == 0, stale.output
    assert stale.output.rstrip("\n").split("\t")[1] == "stale_claim"


def test_orchestration_mutation_plain_differs_from_default_and_reports_version(tmp_path):
    repo = _repo(tmp_path)

    record = _invoke(
        repo,
        "orchestration",
        "record-run",
        "TASK-1",
        "--actor",
        "codex",
        "--result",
        "succeeded",
        "--summary",
        "done",
        "--plain",
    )
    assert record.exit_code == 0, record.output
    record_fields = record.output.rstrip("\n").split("\t")
    assert record_fields[0] == "TASK-1"
    assert record_fields[1] == "recorded"
    assert record_fields[2].startswith("run-")
    assert record_fields[3] == "0"

    claim = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--plain",
    )
    assert claim.exit_code == 0, claim.output
    claim_fields = claim.output.rstrip("\n").split("\t")
    assert claim_fields[0] == "TASK-1"
    assert claim_fields[1] == "claimed"
    assert claim_fields[3] == "1"

    default_claim = _invoke(
        repo,
        "orchestration",
        "release",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "1",
    )
    assert default_claim.exit_code == 0, default_claim.output
    assert "\t" not in default_claim.output
    assert "released via" in default_claim.output


def test_orchestration_mutation_payload_scans_the_project_once(tmp_path, monkeypatch):
    from backlog_py.cli import main as cli_main
    from backlog_py.orchestration import OrchestrationService

    repo = _repo(tmp_path)
    counts = {"repository": 0, "queue": 0}
    real_repository_cls = cli_main.ReadOnlyRepository
    real_queue = OrchestrationService.queue

    class CountingReadOnlyRepository(real_repository_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            counts["repository"] += 1
            super().__init__(*args, **kwargs)

    def counting_queue(self, **kwargs):
        counts["queue"] += 1
        return real_queue(self, **kwargs)

    monkeypatch.setattr(cli_main, "ReadOnlyRepository", CountingReadOnlyRepository)
    monkeypatch.setattr(OrchestrationService, "queue", counting_queue)

    result = _invoke(
        repo,
        "orchestration",
        "record-run",
        "TASK-1",
        "--actor",
        "codex",
        "--result",
        "succeeded",
        "--summary",
        "done",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["queueCategory"] == "eligible"
    assert payload["runHistoryEventIds"] == [payload["eventId"]]
    # The CLI decorates the mutation result without re-fetching the task itself.
    assert counts == {"repository": 0, "queue": 1}


def test_orchestration_plain_mutation_skips_the_decorating_queue_scan(tmp_path, monkeypatch):
    from backlog_py.orchestration import OrchestrationService

    repo = _repo(tmp_path)
    counts = {"queue": 0}
    real_queue = OrchestrationService.queue

    def counting_queue(self, **kwargs):
        counts["queue"] += 1
        return real_queue(self, **kwargs)

    monkeypatch.setattr(OrchestrationService, "queue", counting_queue)

    result = _invoke(
        repo,
        "orchestration",
        "claim",
        "TASK-1",
        "--actor",
        "codex",
        "--expected-version",
        "0",
        "--plain",
    )

    assert result.exit_code == 0, result.output
    fields = result.output.rstrip("\n").split("\t")
    assert fields[0] == "TASK-1"
    assert fields[1] == "claimed"
    assert fields[3] == "1"
    # Plain output needs nothing from the queue report, so it must not scan for it.
    assert counts["queue"] == 0
