from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.security.paths import PathContainmentError, assert_path_within_base


START_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_START -->"
END_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_END -->"
AGENT_INSTRUCTION_FILES = (
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("GEMINI.md"),
    Path(".github/copilot-instructions.md"),
)


class AgentInstructionError(ValueError):
    """Raised when agent instruction files cannot be updated safely."""


@dataclass(frozen=True)
class AgentInstructionUpdate:
    path: Path
    path_relative: str
    created: bool


def update_agent_instruction_files(project: BacklogProject) -> list[AgentInstructionUpdate]:
    """Create or refresh Backlog.md guidance in common agent instruction files."""
    updates: list[AgentInstructionUpdate] = []
    section = _instruction_section(project.config.project_name)
    for relative_path in AGENT_INSTRUCTION_FILES:
        target = _instruction_path(project.root, relative_path)
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, _replace_owned_section(previous, section))
        updates.append(
            AgentInstructionUpdate(
                path=target,
                path_relative=relative_path.as_posix(),
                created=not bool(previous),
            )
        )
    return updates


def _instruction_path(root: Path, relative_path: Path) -> Path:
    try:
        return assert_path_within_base(root, root / relative_path)
    except PathContainmentError as exc:
        raise AgentInstructionError(str(exc)) from exc


def _replace_owned_section(previous: str, section: str) -> str:
    start_index = previous.find(START_MARKER)
    end_index = previous.find(END_MARKER, start_index + len(START_MARKER))
    if start_index != -1 and end_index != -1:
        end_index += len(END_MARKER)
        return f"{previous[:start_index]}{section}{previous[end_index:].lstrip()}"
    if previous.strip():
        return f"{previous.rstrip()}\n\n{section}"
    return section


def _instruction_section(project_name: str) -> str:
    return (
        f"{START_MARKER}\n"
        "## Backlog.md Workflow\n\n"
        f"This project uses Backlog.md task tracking for {project_name}.\n\n"
        "### Search before creating tasks\n\n"
        "- Use `backlog-py --cwd <repo> search \"query\" --plain`, "
        "`backlog-py --cwd <repo> task list --plain`, or the MCP "
        "`task_search` / `task_list` tools to find related work first.\n"
        "- Create a new task only when no existing task covers the same "
        "reviewable unit: `backlog-py --cwd <repo> task create \"Title\" --plain`.\n\n"
        "### Task lifecycle\n\n"
        "- Before editing project files, link the work to a Backlog.md task.\n"
        "- Keep task status current: move it to `In Progress` when work starts "
        "and mark the task Done only after verification is recorded.\n"
        "- During execution, append implementation notes with important "
        "decisions, blockers, changed files, and verification commands.\n"
        "- Before completion, check all relevant Acceptance Criteria and "
        "Definition of Done items, then add a Final Summary explaining what "
        "changed and why.\n\n"
        "### MCP and CLI fallback\n\n"
        "- MCP agents should read `backlog://workflow/overview` and "
        "`backlog://docs/task-workflow`; if project discovery fails, read "
        "`backlog://init-required`.\n"
        "- Prefer explicit project paths for automation: pass the MCP `project` "
        "argument, set `BACKLOG_CWD`, or use CLI `--cwd <repo>`.\n"
        "- If MCP is unavailable, use the CLI fallback. For example, "
        "`backlog-py --cwd <repo> task edit <id> --status \"In Progress\" --plain`.\n"
        "- Do not manually edit files under `backlog/` unless MCP and CLI paths "
        "are unavailable and the human explicitly approves the exception.\n\n"
        "### Multi-agent guidance\n\n"
        "- In multi-agent environments, prefer one shared daemon: "
        "`backlog-py daemon ensure` and verify it with "
        "`backlog-py daemon status --json`.\n"
        "- Use the MCP `project_status` tool for a cheap coordination check "
        "before write-heavy work.\n"
        f"{END_MARKER}\n"
    )
