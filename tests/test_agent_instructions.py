from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"
START_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_START -->"
END_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_END -->"


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
