from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"
START_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_START -->"
END_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_END -->"
REQUIRED_WORKFLOW_FRAGMENTS = (
    "Search before creating tasks",
    '`backlog-py --cwd <repo> search "query" --plain`',
    '`backlog-py --cwd <repo> task create "Title" --plain`',
    "Keep task status current",
    "implementation notes",
    "Acceptance Criteria",
    "Final Summary",
    "mark the task Done",
    "`backlog://workflow/overview`",
    "`backlog://docs/task-workflow`",
    "`backlog://init-required`",
    "If MCP is unavailable, use the CLI fallback",
    "Do not manually edit files under `backlog/`",
    "`backlog-py daemon ensure`",
    "`backlog-py daemon status --json`",
    "`project_status`",
)


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def test_cli_agents_update_instructions_creates_common_agent_files(tmp_path):
    repo = _copy_fixture(tmp_path)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])

    assert result.exit_code == 0
    assert "Updated AGENTS.md" in result.output
    assert "Updated CLAUDE.md" in result.output
    assert "Updated GEMINI.md" in result.output
    assert "Updated .github/copilot-instructions.md" in result.output

    for path in [
        repo / "AGENTS.md",
        repo / "CLAUDE.md",
        repo / "GEMINI.md",
        repo / ".github" / "copilot-instructions.md",
    ]:
        content = path.read_text(encoding="utf-8")
        assert START_MARKER in content
        assert END_MARKER in content
        assert "backlog-py --cwd" in content
        assert "backlog://docs/task-workflow" in content


def test_cli_agents_update_instructions_writes_full_agent_workflow(tmp_path):
    repo = _copy_fixture(tmp_path)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])

    assert result.exit_code == 0
    content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    for fragment in REQUIRED_WORKFLOW_FRAGMENTS:
        assert fragment in content


def test_cli_agents_update_instructions_is_idempotent(tmp_path):
    repo = _copy_fixture(tmp_path)

    first = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])
    first_content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    second = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])
    second_content = (repo / "AGENTS.md").read_text(encoding="utf-8")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert second_content == first_content
    assert second_content.count(START_MARKER) == 1
    assert second_content.count(END_MARKER) == 1


def test_cli_agents_update_instructions_replaces_owned_section(tmp_path):
    repo = _copy_fixture(tmp_path)
    existing = "\n".join(
        [
            "# Existing Instructions",
            "",
            "Keep this line.",
            START_MARKER,
            "old generated content",
            END_MARKER,
            "Keep this tail.",
            "",
        ]
    )
    (repo / "AGENTS.md").write_text(existing, encoding="utf-8")

    result = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])

    assert result.exit_code == 0
    content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep this line." in content
    assert "Keep this tail." in content
    assert "old generated content" not in content
    assert content.count(START_MARKER) == 1
    assert content.count(END_MARKER) == 1
    for fragment in REQUIRED_WORKFLOW_FRAGMENTS:
        assert fragment in content
