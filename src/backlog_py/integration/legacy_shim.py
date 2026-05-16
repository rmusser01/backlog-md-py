from __future__ import annotations

import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shlex import quote


SHIM_MARKER = "backlog-md-py legacy MCP shim"


@dataclass(frozen=True)
class LegacyMcpShimInstallResult:
    """Result of installing the legacy Backlog.md MCP command shim."""

    target: Path
    backup: Path
    mcp_command: Path


def install_legacy_mcp_shim(
    *,
    target: Path,
    mcp_command: Path | None = None,
    backup: Path | None = None,
) -> LegacyMcpShimInstallResult:
    """Install a wrapper that routes ``backlog mcp start`` to ``backlog-py-mcp``."""
    target = target.expanduser().absolute()
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(f"Target command does not exist: {target}")
    if _is_installed_shim(target):
        raise FileExistsError(f"Target already contains a {SHIM_MARKER}: {target}")

    resolved_mcp_command = _resolve_mcp_command(mcp_command)
    backup_path = backup.expanduser().absolute() if backup is not None else _default_backup_path(target)
    if backup_path.exists() or backup_path.is_symlink():
        raise FileExistsError(f"Backup path already exists: {backup_path}")

    original_mode = target.lstat().st_mode
    target.rename(backup_path)
    try:
        target.write_text(_render_shim(mcp_command=resolved_mcp_command, original_command=backup_path))
        target.chmod(_executable_mode(original_mode))
    except Exception:
        if target.exists() or target.is_symlink():
            target.unlink()
        backup_path.rename(target)
        raise

    return LegacyMcpShimInstallResult(target=target, backup=backup_path, mcp_command=resolved_mcp_command)


def _resolve_mcp_command(mcp_command: Path | None) -> Path:
    if mcp_command is not None:
        resolved = mcp_command.expanduser().absolute()
        if not resolved.exists():
            raise FileNotFoundError(f"backlog-py-mcp command does not exist: {resolved}")
        return resolved

    sibling = Path(sys.argv[0]).resolve().with_name("backlog-py-mcp")
    if sibling.exists():
        return sibling.absolute()

    discovered = shutil.which("backlog-py-mcp")
    if discovered is None:
        raise FileNotFoundError("Could not find backlog-py-mcp on PATH; pass --mcp-command.")
    return Path(discovered).absolute()


def _default_backup_path(target: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return target.with_name(f"{target.name}.backlog-md-py-backup-{timestamp}")


def _is_installed_shim(target: Path) -> bool:
    if target.is_symlink() or not target.is_file():
        return False
    try:
        return SHIM_MARKER in target.read_text(errors="ignore")
    except OSError:
        return False


def _render_shim(*, mcp_command: Path, original_command: Path) -> str:
    return (
        "#!/bin/sh\n"
        f"# {SHIM_MARKER}\n"
        'if [ "$1" = "mcp" ] && [ "$2" = "start" ]; then\n'
        "  shift 2\n"
        f'  exec {quote(str(mcp_command))} "$@"\n'
        "fi\n"
        "\n"
        f'exec {quote(str(original_command))} "$@"\n'
    )


def _executable_mode(original_mode: int) -> int:
    return stat.S_IMODE(original_mode) | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
