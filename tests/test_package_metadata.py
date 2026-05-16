from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

import tomllib

import yaml

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


def test_pyproject_exposes_sdk_free_mcp_script_without_mcp_extra():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "mcp" not in pyproject["project"]["optional-dependencies"]
    assert pyproject["project"]["scripts"]["backlog-py-mcp"] == "backlog_py.mcp.server:main"


def test_ci_smokes_sdk_free_mcp_entry_point_without_mcp_extra():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    package_steps = workflow["jobs"]["package"]["steps"]
    package_runs = "\n".join(str(step.get("run", "")) for step in package_steps)

    assert "[mcp]" not in package_runs
    assert "FastMCP" not in package_runs
    assert "is_mcp_sdk_available()" not in package_runs
    assert "backlog-py-mcp" in package_runs


def test_python_support_range_is_311_through_313():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())

    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert "Programming Language :: Python :: 3.10" not in pyproject["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.11" in pyproject["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.12" in pyproject["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.13" in pyproject["project"]["classifiers"]
    assert "tomli>=2.0.0; python_version < '3.11'" not in pyproject["project"]["optional-dependencies"]["dev"]
    assert workflow["jobs"]["tests"]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13"]
    package_python_step = next(
        step for step in workflow["jobs"]["package"]["steps"] if step["name"] == "Set up Python"
    )
    assert package_python_step["with"]["python-version"] == "3.13"
