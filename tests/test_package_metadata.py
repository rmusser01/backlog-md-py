from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

from backlog_py import __version__


def test_package_declares_inline_typing_support():
    assert files("backlog_py").joinpath("py.typed").is_file()


def test_distribution_version_matches_package_version():
    assert metadata("backlog-md-py")["Version"] == __version__


def test_pyproject_derives_distribution_version_from_package_attribute():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "version" not in pyproject["project"]
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "backlog_py.__version__",
    }


def test_pyproject_exposes_mcp_server_extra_and_script():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "mcp" in pyproject["project"]["optional-dependencies"]
    assert any(dependency.startswith("mcp>=") for dependency in pyproject["project"]["optional-dependencies"]["mcp"])
    assert pyproject["project"]["scripts"]["backlog-py-mcp"] == "backlog_py.mcp.server:main"
