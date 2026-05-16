import json
import shutil
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main
from backlog_py.storage.config import set_config_value
from backlog_py.storage.project import discover_project


FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def test_browser_service_serves_health_board_json_and_html(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        health = _get_json(f"{service.root_url}/health")
        board = _get_json(f"{service.root_url}/api/board")
        html = _get_text(service.root_url)
    finally:
        service.shutdown()

    assert health == {"ok": True, "projectName": "basic-fixture"}
    assert board["project"]["name"] == "basic-fixture"
    assert board["statuses"] == ["To Do", "In Progress", "Done"]
    assert board["columns"]["In Progress"][0]["id"] == "TASK-1"
    assert board["columns"]["In Progress"][0]["title"] == "Example task"
    assert "basic-fixture" in html
    assert "In Progress" in html
    assert "TASK-1" in html
    assert "Example task" in html


def test_browser_service_rejects_occupied_port(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    service = start_browser_service(project, host="127.0.0.1", port=0)
    try:
        with pytest.raises(OSError):
            start_browser_service(project, host="127.0.0.1", port=service.port)
    finally:
        service.shutdown()


def test_browser_service_rejects_non_loopback_host(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import start_browser_service

    with pytest.raises(ValueError, match="loopback"):
        start_browser_service(project, host="0.0.0.0", port=0)


def test_browser_command_uses_config_default_port_and_no_open(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    project = discover_project(Path.cwd(), explicit_cwd=repo)
    set_config_value(project, "defaultPort", "45678")
    calls = []

    def fake_run_browser_service_foreground(project, *, host, port, open_browser):
        calls.append(
            {
                "project_root": project.root,
                "host": host,
                "port": port,
                "open_browser": open_browser,
            }
        )

    monkeypatch.setattr("backlog_py.cli.main.run_browser_service_foreground", fake_run_browser_service_foreground)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "browser", "--no-open"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "project_root": repo,
            "host": "127.0.0.1",
            "port": 45678,
            "open_browser": False,
        }
    ]


def test_browser_command_port_option_overrides_config_and_auto_open(tmp_path, monkeypatch):
    repo = _copy_fixture_repo(tmp_path)
    calls = []

    def fake_run_browser_service_foreground(project, *, host, port, open_browser):
        calls.append((project.root, host, port, open_browser))

    monkeypatch.setattr("backlog_py.cli.main.run_browser_service_foreground", fake_run_browser_service_foreground)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "browser", "--port", "45679"])

    assert result.exit_code == 0, result.output
    assert calls == [(repo, "127.0.0.1", 45679, True)]


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _get_json(url: str) -> object:
    return json.loads(_get_text(url))


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read().decode("utf-8")
