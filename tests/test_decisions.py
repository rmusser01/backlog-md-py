from __future__ import annotations

import re
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from loguru import logger

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


@contextmanager
def _captured_warnings():
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _write_decision(repo: Path, filename: str, source: str) -> Path:
    decisions_dir = repo / "backlog" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    path = decisions_dir / filename
    path.write_text(source, encoding="utf-8")
    return path


def test_malformed_decision_file_does_not_disable_decision_operations(tmp_path):
    repo = _copy_fixture(tmp_path)
    _service(repo).create_decision("Use PostgreSQL")
    _write_decision(repo, "decision-9 - Broken.md", "---\nid: decision-9\ntitle: Broken\n\nNo closing fence.\n")

    with _captured_warnings() as warnings:
        listed = _service(repo).list_decisions()

    assert [decision.id for decision in listed] == ["decision-1"]
    assert any("Broken.md" in message for message in warnings), warnings
    assert _service(repo).view_decision("1").title == "Use PostgreSQL"
    # decision-9 is unparsable but still occupies its number, so allocation skips
    # past it. A gap is harmless; reissuing 9 would put two files under one id.
    assert _service(repo).create_decision("Another one").id == "decision-10"


def test_decision_reader_tolerates_utf8_bom(tmp_path):
    repo = _copy_fixture(tmp_path)
    _write_decision(
        repo,
        "decision-3 - Bom.md",
        "﻿---\nid: decision-3\ntitle: BOM decision\nstatus: accepted\n---\n\n## Context\n\nWith BOM.\n",
    )

    assert [decision.id for decision in _service(repo).list_decisions()] == ["decision-3"]
    viewed = _service(repo).view_decision("3")
    assert viewed.title == "BOM decision"
    assert viewed.status == "accepted"
    assert viewed.context == "With BOM."
    assert not viewed.raw_source.startswith("﻿")


def test_create_decision_refuses_to_overwrite_an_existing_file(tmp_path, monkeypatch):
    """Guards the write against a file appearing between id allocation and write.

    Id allocation now also scans filenames, so a natural collision is no longer
    reachable; this drives the remaining race directly by pinning the allocated
    id to one that is already taken.
    """
    repo = _copy_fixture(tmp_path)
    squatter = _write_decision(
        repo,
        "decision-1 - Same-title.md",
        "---\nid: adr-1\ntitle: Same title\n---\n\n## Context\n\nkeep me\n",
    )
    service = _service(repo)
    monkeypatch.setattr(type(service), "_next_decision_id", lambda self: "decision-1")

    with pytest.raises(DecisionMutationError, match="already exists"):
        service.create_decision("Same title")

    assert "keep me" in squatter.read_text(encoding="utf-8")


def test_list_decisions_orders_conforming_ids_numerically_then_others_lexicographically(tmp_path):
    repo = _copy_fixture(tmp_path)
    for filename, decision_id in (
        ("decision-10 - Ten.md", "decision-10"),
        ("decision-2 - Two.md", "decision-2"),
        ("decision-b - Bee.md", "adr-b"),
        ("decision-a - Ay.md", "adr-a"),
    ):
        _write_decision(repo, filename, f"---\nid: {decision_id}\ntitle: T\n---\n\n## Context\n")

    assert [decision.id for decision in _service(repo).list_decisions()] == [
        "decision-2",
        "decision-10",
        "adr-a",
        "adr-b",
    ]


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
