from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.core.decisions import DecisionMutationError, DecisionService
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _project(repo: Path):
    return discover_project(Path.cwd(), explicit_cwd=repo)


def _service(repo: Path) -> DecisionService:
    return DecisionService(_project(repo))


def _decision_file(repo: Path, decision_id: str = "decision-1") -> Path:
    matches = sorted((repo / "backlog" / "decisions").glob(f"{decision_id} -*.md"))
    assert len(matches) == 1
    return matches[0]


def _frontmatter(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    assert source.startswith("---\n")
    return yaml.safe_load(source.split("---\n", 2)[1])


def test_create_list_search_and_view_decision(tmp_path):
    repo = _copy_fixture(tmp_path)

    created = _service(repo).create_decision("Use PostgreSQL for primary database", status="accepted")

    assert created.id == "decision-1"
    assert created.status == "accepted"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", created.date)
    assert [decision.id for decision in _service(repo).list_decisions()] == ["decision-1"]
    assert [decision.id for decision in _service(repo).search_decisions("postgres")] == ["decision-1"]
    assert _service(repo).view_decision("1").id == "decision-1"
    assert _service(repo).view_decision("decision-1").title == "Use PostgreSQL for primary database"

    written = _decision_file(repo).read_text(encoding="utf-8")
    frontmatter = _frontmatter(_decision_file(repo))
    assert frontmatter["id"] == "decision-1"
    assert frontmatter["title"] == "Use PostgreSQL for primary database"
    assert frontmatter["status"] == "accepted"
    assert "## Context" in written
    assert "## Decision" in written
    assert "## Consequences" in written


def test_search_decisions_matches_acronym_style_fuzzy_query(tmp_path):
    repo = _copy_fixture(tmp_path)

    _service(repo).create_decision("Use PostgreSQL for primary database", status="accepted")

    assert [decision.id for decision in _service(repo).search_decisions("psql")] == ["decision-1"]


def test_decision_create_rejects_invalid_status_before_write(tmp_path):
    repo = _copy_fixture(tmp_path)

    with pytest.raises(DecisionMutationError, match="Invalid decision status"):
        _service(repo).create_decision("Bad status", status="maybe")

    assert not (repo / "backlog" / "decisions").exists()


def test_cli_decision_create_and_search_use_safe_service(tmp_path):
    repo = _copy_fixture(tmp_path)
    runner = CliRunner()

    create = runner.invoke(
        main,
        [
            "--cwd",
            str(repo),
            "decision",
            "create",
            "Migrate to TypeScript",
            "-s",
            "proposed",
        ],
    )

    assert create.exit_code == 0
    assert "Created decision decision-1" in create.output
    written = _decision_file(repo).read_text(encoding="utf-8")
    assert "title: Migrate to TypeScript" in written
    assert "status: proposed" in written

    search = runner.invoke(main, ["--cwd", str(repo), "search", "typescript", "--plain"])

    assert search.exit_code == 0
    assert "decision-1 [proposed] Migrate to TypeScript" in search.output
