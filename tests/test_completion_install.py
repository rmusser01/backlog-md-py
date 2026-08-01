from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main


@pytest.mark.parametrize(
    ("shell", "relative_path", "source_marker"),
    [
        ("bash", ".local/share/bash-completion/completions/backlog-py", "_BACKLOG_PY_COMPLETE=bash_complete"),
        ("zsh", ".zsh/completions/_backlog-py", "_BACKLOG_PY_COMPLETE=zsh_complete"),
        ("fish", ".config/fish/completions/backlog-py.fish", "_BACKLOG_PY_COMPLETE=fish_complete"),
    ],
)
def test_completion_install_writes_click_completion_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell: str,
    relative_path: str,
    source_marker: str,
):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["completion", "install", "--shell", shell])

    assert result.exit_code == 0, result.output
    install_path = tmp_path / relative_path
    assert install_path.exists()
    script = install_path.read_text(encoding="utf-8")
    assert source_marker in script
    assert "backlog-py" in script
    assert f"Installed {shell} completion" in result.output
    assert str(install_path) in result.output


def test_completion_install_auto_detects_current_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")

    result = CliRunner().invoke(main, ["completion", "install"])

    assert result.exit_code == 0, result.output
    assert "Installed zsh completion" in result.output
    assert (tmp_path / ".zsh/completions/_backlog-py").exists()


def test_completion_install_supports_pwsh_without_running_pwsh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["completion", "install", "--shell", "pwsh"])

    assert result.exit_code == 0, result.output
    install_path = tmp_path / "Documents/PowerShell/Completions/backlog-py-completion.ps1"
    assert install_path.exists()
    script = install_path.read_text(encoding="utf-8")
    assert "Register-ArgumentCompleter" in script
    assert "backlog-py" in script
    assert "Installed pwsh completion" in result.output


def test_completion_install_rejects_unsupported_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["completion", "install", "--shell", "tcsh"])

    assert result.exit_code != 0
    assert "Unsupported shell" in result.output


def test_pwsh_completion_script_lists_every_top_level_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(main, ["completion", "install", "--shell", "pwsh"])

    assert result.exit_code == 0, result.output
    script = (tmp_path / "Documents/PowerShell/Completions/backlog-py-completion.ps1").read_text(encoding="utf-8")
    for command_name in main.commands:
        assert f'"{command_name}"' in script, command_name
    for expected in ("browser", "orchestration", "tui"):
        assert f'"{expected}"' in script
