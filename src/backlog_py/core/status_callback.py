from __future__ import annotations

import os
# onStatusChange intentionally runs user-configured commands.
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StatusCallbackResult:
    success: bool
    output: str | None = None
    error: str | None = None
    exit_code: int | None = None


def execute_status_callback(
    *,
    command: str,
    task_id: str,
    old_status: str,
    new_status: str,
    task_title: str,
    cwd: Path,
) -> StatusCallbackResult:
    """Run an onStatusChange command with upstream-compatible environment variables."""
    if not command.strip():
        return StatusCallbackResult(success=False, error="Empty command")

    env = os.environ.copy()
    env.update(
        {
            "TASK_ID": task_id,
            "OLD_STATUS": old_status,
            "NEW_STATUS": new_status,
            "TASK_TITLE": task_title,
        }
    )
    try:
        # onStatusChange is explicitly a user-configured shell command.
        completed = subprocess.run(  # nosec B603
            _shell_command(command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return StatusCallbackResult(success=False, error=str(exc))

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return StatusCallbackResult(
        success=completed.returncode == 0,
        output=output or None,
        error=(completed.stderr.strip() or None) if completed.returncode != 0 else None,
        exit_code=completed.returncode,
    )


def _shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    return ["/bin/sh", "-c", command]
