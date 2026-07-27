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
        "### Project lessons\n\n"
        "- If `backlog/docs/lessons-*.md` files exist, list them with "
        "`backlog-py --cwd <repo> doc list lessons` and read the relevant themed "
        "file with `backlog-py --cwd <repo> doc view <path>` before starting.\n"
        "- Before completing a task, if it exposed a reusable trap, costly wrong "
        "assumption, or verification constraint, record the guidance in the relevant "
        "themed lesson through the normal document workflow. Record a concise claim, the "
        "actual incident that proves it, and the resulting action, only when the "
        "guidance is reusable and evidence-backed. Most tasks produce no lesson; "
        "do not invent one to satisfy this hook.\n\n"
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
        "\n"
        "### Orchestration coordination\n\n"
        "- When orchestration metadata exists, check `project_status`, "
        "`orchestration_status`, and `orchestration_eligible` before choosing "
        "work. CLI fallback: "
        "`backlog-py --cwd <repo> orchestration status --plain` and "
        "`backlog-py --cwd <repo> orchestration eligible --plain`.\n"
        "- Always claim before editing with `orchestration_claim_task` and the "
        "latest expected version: "
        "`backlog-py --cwd <repo> orchestration claim <id> --actor <agent> "
        "--expected-version <version> --plain`.\n"
        "- Always record files and verification commands with "
        "`orchestration_record_run` before handing off or completing work: "
        "`backlog-py --cwd <repo> orchestration record-run <id> --actor <agent> "
        "--result succeeded --summary \"...\" --file <path> --verification "
        "\"pytest ...\" --plain`.\n"
        "- After verification, use `orchestration_transition_task` to "
        "transition to `review` or `triage`; do not jump directly to terminal "
        "states unless project policy allows it. CLI fallback: "
        "`backlog-py --cwd <repo> orchestration transition <id> review "
        "--actor <agent> --expected-version <version> --plain`.\n"
        "- Release blocked or paused work with `orchestration_release_task`, "
        "then document the reason in run history.\n"
        "- Troubleshoot stale leases with `orchestration_stale_leases`; fix "
        "version conflicts by refreshing queue/status and retrying with the "
        "latest expected version; fix malformed run history before recording "
        "new events; handle MCP discovery with an explicit `project` argument, "
        "`BACKLOG_CWD`, or CLI `--cwd`; check daemon health with "
        "`backlog-py daemon status --json`.\n"
        f"{END_MARKER}\n"
    )
