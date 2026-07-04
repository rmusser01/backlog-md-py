from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path

import tomllib

import yaml

from backlog_py import __version__


MCP_SMOKE_STEP_NAME = "Smoke test SDK-free MCP entry point"
MCP_SMOKE_STATE_DIR = "${{ runner.temp }}/backlog-md-py-mcp-smoke-state-${{ github.run_id }}-${{ github.run_attempt }}"
MCP_SERVER_NAME_ASSERTION = 'response["result"]["serverInfo"]["name"] == "backlog-md-py"'


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


def test_stable_release_metadata_and_docs_are_declared():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert __version__ == "1.0.1"
    assert "Development Status :: 5 - Production/Stable" in pyproject["project"]["classifiers"]
    assert "Development Status :: 3 - Alpha" not in pyproject["project"]["classifiers"]
    assert "Development Status :: 4 - Beta" not in pyproject["project"]["classifiers"]

    stability_policy = Path("docs/stability-policy.md").read_text()
    changelog = Path("CHANGELOG.md").read_text()
    release_checklist = Path("docs/release-checklist.md").read_text()

    assert "Supported Contract" in stability_policy
    assert "Stable Release Gate" in stability_policy
    assert "## 1.0.1" in changelog
    assert "## 1.0.0" in changelog
    assert "## 0.2.0" in changelog
    assert "Stable" in changelog
    assert "Do not merge the release-prep PR until" in release_checklist


def test_pyproject_exposes_sdk_free_mcp_script_without_mcp_extra():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "mcp" not in pyproject["project"]["optional-dependencies"]
    assert pyproject["project"]["scripts"]["backlog-py-mcp"] == "backlog_py.mcp.server:main"


def test_pyproject_declares_textual_tui_as_optional_extra_only():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]
    optional = pyproject["project"]["optional-dependencies"]

    assert not any("textual" in dependency.casefold() for dependency in dependencies)
    assert "tui" in optional
    assert any(dependency.startswith("textual>=") for dependency in optional["tui"])


def test_pyproject_packages_tui_stylesheet():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    package_data = pyproject["tool"]["setuptools"]["package-data"]["backlog_py"]

    assert "py.typed" in package_data
    assert "tui/styles.tcss" in package_data


def test_pyproject_packages_browser_static_assets():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")

    package_data = pyproject["tool"]["setuptools"]["package-data"]["backlog_py"]

    assert "browser/templates/*.html" in package_data
    assert "browser/assets/*.css" in package_data
    assert "browser/assets/*.js" in package_data
    assert "recursive-include src/backlog_py/browser/templates *.html" in manifest
    assert "recursive-include src/backlog_py/browser/assets *.css *.js" in manifest


def test_browser_board_assets_are_available_from_package_resources():
    browser_package = files("backlog_py.browser")

    assert browser_package.joinpath("templates", "board.html").is_file()
    assert browser_package.joinpath("assets", "board.css").is_file()
    assert browser_package.joinpath("assets", "board.js").is_file()


def test_ci_smokes_sdk_free_mcp_entry_point_without_mcp_extra():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    package_steps = workflow["jobs"]["package"]["steps"]
    mcp_step = next(step for step in package_steps if step["name"] == MCP_SMOKE_STEP_NAME)
    package_runs = "\n".join(str(step.get("run", "")) for step in package_steps)

    assert "[mcp]" not in package_runs
    assert "FastMCP" not in package_runs
    assert "is_mcp_sdk_available()" not in package_runs
    assert "backlog-py-mcp" in package_runs
    assert mcp_step["env"]["BACKLOG_PY_STATE_DIR"] == MCP_SMOKE_STATE_DIR
    assert "export MCP_RESPONSE_PATH" in mcp_step["run"]
    assert '"error" not in response' in mcp_step["run"]
    assert MCP_SERVER_NAME_ASSERTION in mcp_step["run"]


def test_release_workflow_publishes_github_release_assets_and_pypi():
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text())

    assert workflow["name"] == "Release"
    release_trigger = workflow[True]
    assert release_trigger["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in release_trigger
    assert workflow["permissions"] == {"contents": "write", "id-token": "write"}

    release_job = workflow["jobs"]["release"]
    assert release_job["runs-on"] == "ubuntu-latest"
    assert release_job["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert release_job["environment"]["name"] == "pypi"
    assert release_job["environment"]["url"] == "https://pypi.org/p/backlog-md-py"

    steps = release_job["steps"]
    step_names = [step["name"] for step in steps]
    assert step_names == [
        "Checkout",
        "Set up Python",
        "Install build tools",
        "Build distribution",
        "Check distribution metadata",
        "Smoke test wheel install",
        "Smoke test SDK-free MCP entry point",
        "Upload distribution artifact",
        "Create GitHub Release",
        "Publish to PyPI",
    ]

    setup_step = next(step for step in steps if step["name"] == "Set up Python")
    assert setup_step["with"]["python-version"] == "3.13"

    run_script = "\n".join(str(step.get("run", "")) for step in steps)
    mcp_step = next(step for step in steps if step["name"] == MCP_SMOKE_STEP_NAME)
    assert "python -m build" in run_script
    assert "python -m twine check dist/*" in run_script
    assert "backlog-py --version" in run_script
    assert "python -m backlog_py --version" in run_script
    assert "backlog-py-mcp" in run_script
    assert "[mcp]" not in run_script
    assert mcp_step["env"]["BACKLOG_PY_STATE_DIR"] == MCP_SMOKE_STATE_DIR
    assert "export MCP_RESPONSE_PATH" in mcp_step["run"]
    assert '"error" not in response' in mcp_step["run"]
    assert MCP_SERVER_NAME_ASSERTION in mcp_step["run"]

    actions = {step["name"]: step["uses"] for step in steps if "uses" in step}
    assert actions["Upload distribution artifact"] == "actions/upload-artifact@v4"
    assert actions["Create GitHub Release"] == "softprops/action-gh-release@v2"
    assert actions["Publish to PyPI"] == "pypa/gh-action-pypi-publish@release/v1"
    assert steps[-1]["with"]["packages-dir"] == "dist/"


def test_auto_release_tag_workflow_tags_merged_main_versions():
    workflow = yaml.safe_load(Path(".github/workflows/auto-release-tag.yml").read_text())

    assert workflow["name"] == "Auto Release Tag"
    trigger = workflow[True]
    assert trigger["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in trigger
    assert workflow["permissions"] == {"actions": "write", "contents": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] is False

    tag_job = workflow["jobs"]["tag"]
    assert tag_job["name"] == "Tag merged package version"
    assert tag_job["runs-on"] == "ubuntu-latest"
    assert tag_job["if"] == "github.ref == 'refs/heads/main'"

    steps = tag_job["steps"]
    assert [step["name"] for step in steps] == [
        "Checkout",
        "Resolve package version",
        "Create release tag",
        "Dispatch release workflow",
    ]
    assert steps[0]["uses"] == "actions/checkout@v4"
    assert steps[0]["with"]["fetch-depth"] == 0

    run_script = "\n".join(str(step.get("run", "")) for step in steps)
    assert "src/backlog_py/__init__.py" in run_script
    assert "git ls-remote --exit-code --tags origin" in run_script
    assert 'git tag -a "${tag}"' in run_script
    assert 'git push origin "${tag}"' in run_script
    assert 'gh workflow run release.yml --ref "${{ steps.version.outputs.tag }}"' in run_script
    assert steps[-1]["if"] == "steps.tag.outputs.created == 'true'"
    assert steps[-1]["env"]["GH_TOKEN"] == "${{ github.token }}"


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
