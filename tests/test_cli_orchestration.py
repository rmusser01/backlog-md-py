import json
import re
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.orchestration import parse_run_history


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
