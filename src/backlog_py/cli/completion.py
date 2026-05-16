from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import click
from click.shell_completion import get_completion_class

DEFAULT_COMMAND_NAME = "backlog-py"
SUPPORTED_SHELLS = ("bash", "zsh", "fish", "pwsh")


@dataclass(frozen=True)
class CompletionInstallResult:
    shell_name: str
    install_path: Path
    instructions: str


class CompletionInstallError(Exception):
    """Raised when shell completion cannot be installed."""


def install_completion(
    cli: click.Command,
    *,
    target: str | None = None,
    command_name: str = DEFAULT_COMMAND_NAME,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> CompletionInstallResult:
    """Install a user-scoped shell completion script."""
    detected_shell = target or detect_shell(environ=environ)
    target_shell = detected_shell.lower() if detected_shell else None
    if target_shell is None:
        raise CompletionInstallError(
            "Could not detect your shell. Specify one with --shell bash, --shell zsh, --shell fish, or --shell pwsh."
        )
    if target_shell not in SUPPORTED_SHELLS:
        raise CompletionInstallError(f"Unsupported shell: {target_shell}. Supported shells: bash, zsh, fish, pwsh.")

    home_dir = home or Path.home()
    install_path = completion_install_path(target_shell, command_name=command_name, home=home_dir)
    script = completion_script(cli, target_shell, command_name=command_name)

    install_path.parent.mkdir(parents=True, exist_ok=True)
    install_path.write_text(script, encoding="utf-8")

    return CompletionInstallResult(target_shell, install_path, completion_enable_instructions(target_shell, install_path))


def detect_shell(*, environ: Mapping[str, str] | None = None) -> str | None:
    """Detect the current shell from common shell environment variables."""
    env = environ or os.environ
    candidates = [env.get("SHELL", ""), env.get("COMSPEC", ""), env.get("ComSpec", "")]
    for candidate in candidates:
        normalized = Path(candidate).name.lower()
        if "bash" in normalized:
            return "bash"
        if "zsh" in normalized:
            return "zsh"
        if "fish" in normalized:
            return "fish"
        if "pwsh" in normalized or "powershell" in normalized:
            return "pwsh"
    return None


def completion_install_path(shell: str, *, command_name: str, home: Path) -> Path:
    paths = {
        "bash": home / ".local/share/bash-completion/completions" / command_name,
        "zsh": home / ".zsh/completions" / f"_{command_name}",
        "fish": home / ".config/fish/completions" / f"{command_name}.fish",
        "pwsh": home / "Documents/PowerShell/Completions" / f"{command_name}-completion.ps1",
    }
    return paths[shell]


def completion_script(cli: click.Command, shell: str, *, command_name: str) -> str:
    if shell == "pwsh":
        return powershell_completion_script(command_name)

    completion_class = get_completion_class(shell)
    if completion_class is None:
        raise CompletionInstallError(f"Unsupported shell: {shell}. Supported shells: bash, zsh, fish, pwsh.")

    complete_var = completion_env_var(command_name)
    return completion_class(cli, {}, command_name, complete_var).source()


def completion_env_var(command_name: str) -> str:
    return f"_{command_name.upper().replace('-', '_')}_COMPLETE"


def completion_enable_instructions(shell: str, install_path: Path) -> str:
    if shell == "bash":
        return f"To enable completions, add this to your ~/.bashrc:\nsource {install_path}"
    if shell == "zsh":
        return (
            "To enable completions, ensure the directory is in your fpath.\n"
            "Add this to your ~/.zshrc:\n"
            f"fpath=({install_path.parent} $fpath)\n"
            "autoload -Uz compinit && compinit"
        )
    if shell == "fish":
        return "Completions should be automatically loaded by fish after restarting the shell."
    return (
        "To enable completions, add this to your PowerShell profile:\n"
        f"if (Test-Path '{install_path}') {{ . '{install_path}' }}"
    )


def powershell_completion_script(command_name: str) -> str:
    commands = [
        "agents",
        "board",
        "cleanup",
        "compat",
        "completion",
        "config",
        "daemon",
        "decision",
        "doc",
        "draft",
        "init",
        "integration",
        "milestone",
        "overview",
        "search",
        "task",
    ]
    command_list = ", ".join(f'"{command}"' for command in commands)
    return f"""# PowerShell completion script for {command_name}
$__backlogPyCompletionScriptBlock = {{
    param($wordToComplete, $commandAst, $cursorPosition)

    $commands = @({command_list})
    foreach ($command in $commands) {{
        if ($command -like "$wordToComplete*") {{
            [System.Management.Automation.CompletionResult]::new(
                $command,
                $command,
                [System.Management.Automation.CompletionResultType]::ParameterValue,
                $command
            )
        }}
    }}
}}

Register-ArgumentCompleter -Native -CommandName @("{command_name}") -ScriptBlock $__backlogPyCompletionScriptBlock
"""
