import subprocess
from pathlib import Path

from click.testing import CliRunner

from backlog_py.cli.main import main


def test_install_legacy_mcp_shim_routes_only_mcp_start_to_python_shim(tmp_path):
    target = tmp_path / "backlog"
    backup = tmp_path / "backlog.original"
    mcp_command = tmp_path / "backlog-py-mcp"
    _write_executable(
        target,
        "#!/bin/sh\nprintf 'original:%s %s\\n' \"$1\" \"$2\"\n",
    )
    _write_executable(
        mcp_command,
        "#!/bin/sh\nprintf 'mcp:%s %s\\n' \"$1\" \"$2\"\n",
    )

    result = CliRunner().invoke(
        main,
        [
            "integration",
            "install-legacy-mcp-shim",
            "--target",
            str(target),
            "--mcp-command",
            str(mcp_command),
            "--backup",
            str(backup),
        ],
    )

    assert result.exit_code == 0, result.output
    assert backup.exists()
    assert "Installed legacy MCP shim" in result.output
    assert "backlog-md-py legacy MCP shim" in target.read_text()
    assert subprocess.check_output(
        [str(target), "mcp", "start", "--verbose"],
        text=True,
    ) == "mcp:--verbose \n"
    assert subprocess.check_output([str(target), "task", "list"], text=True) == "original:task list\n"


def test_install_legacy_mcp_shim_refuses_to_overwrite_existing_backup(tmp_path):
    target = tmp_path / "backlog"
    backup = tmp_path / "backlog.original"
    mcp_command = tmp_path / "backlog-py-mcp"
    _write_executable(target, "#!/bin/sh\n")
    _write_executable(backup, "#!/bin/sh\n")
    _write_executable(mcp_command, "#!/bin/sh\n")

    result = CliRunner().invoke(
        main,
        [
            "integration",
            "install-legacy-mcp-shim",
            "--target",
            str(target),
            "--mcp-command",
            str(mcp_command),
            "--backup",
            str(backup),
        ],
    )

    assert result.exit_code != 0
    assert "Backup path already exists" in result.output


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | 0o111)
