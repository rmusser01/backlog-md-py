from __future__ import annotations

from pathlib import Path

import click

from backlog_py import __version__
from backlog_py.core.documents import DocumentRecord, DocumentService
from backlog_py.core.milestones import MilestoneRecord, MilestoneService
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import MutableRepository, ReadOnlyRepository, TaskRecord
from backlog_py.storage.config import get_definition_of_done_defaults, replace_definition_of_done_defaults
from backlog_py.storage.project import discover_project


@click.group()
@click.option("--cwd", type=click.Path(path_type=Path), default=None, help="Backlog project directory.")
@click.version_option(__version__, prog_name="backlog-py")
@click.pass_context
def main(ctx: click.Context, cwd: Path | None) -> None:
    """Python compatibility clone of Backlog.md."""
    ctx.obj = {"cwd": cwd}


@main.command("task")
@click.argument("args", nargs=-1)
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.option("--id", "task_id", default=None, help="Task id for task creation.")
@click.option("--title", default=None, help="Replacement task title for task edit.")
@click.option("--status", default=None, help="Task status for create/edit/list.")
@click.option("--description", default=None, help="Description for task creation.")
@click.option("--plan", default=None, help="Implementation plan for task create/edit.")
@click.option("--append-plan", multiple=True, help="Append text to the implementation plan.")
@click.option("--clear-plan", is_flag=True, help="Clear the implementation plan section.")
@click.option("--notes", default=None, help="Implementation notes for task create/edit.")
@click.option(
    "--ac",
    "--acceptance-criteria",
    "acceptance_criteria",
    multiple=True,
    help="Acceptance criterion for task creation.",
)
@click.option("--definition-of-done", multiple=True, help="Definition of Done item for task creation.")
@click.option(
    "--dod",
    "--definition-of-done-add",
    "definition_of_done_add",
    multiple=True,
    help="Additional Definition of Done item for task creation.",
)
@click.option("--disable-definition-of-done-defaults", is_flag=True, help="Do not inherit project Definition of Done defaults.")
@click.option("--dep", "--dependency", "dependencies", multiple=True, help="Task dependency id for task create/edit.")
@click.option("-a", "--assignee", "assignees", multiple=True, help="Task assignee for create/edit/list.")
@click.option("-l", "--label", "labels", multiple=True, help="Task label for create/edit/list.")
@click.option("--priority", default=None, help="Task priority for create/edit/list.")
@click.option("-m", "--milestone", default=None, help="Task milestone for create/edit/list.")
@click.option("--clear-milestone", is_flag=True, help="Clear the task milestone on edit.")
@click.option("--ref", "references", multiple=True, help="Task reference URL or file path for create/edit.")
@click.option("--doc", "documentation", multiple=True, help="Task documentation URL or file path for create/edit.")
@click.option("--modified-file", "modified_files", multiple=True, help="Modified file path for create/edit.")
@click.option("--append-notes", default=None, help="Append text to implementation notes.")
@click.option("--check-ac", multiple=True, type=int, help="Mark acceptance criteria index complete.")
@click.option("--check-dod", multiple=True, type=int, help="Mark Definition of Done index complete.")
@click.option("--uncheck-ac", multiple=True, type=int, help="Mark acceptance criteria index incomplete.")
@click.option("--uncheck-dod", multiple=True, type=int, help="Mark Definition of Done index incomplete.")
@click.option("--remove-ac", multiple=True, type=int, help="Remove acceptance criteria by index.")
@click.option("--remove-dod", multiple=True, type=int, help="Remove Definition of Done items by index.")
@click.option("--final-summary", default=None, help="Replace the final summary section.")
@click.option("--append-final-summary", multiple=True, help="Append text to the final summary section.")
@click.option("--clear-final-summary", is_flag=True, help="Clear the final summary section.")
@click.pass_context
def task_command(
    ctx: click.Context,
    args: tuple[str, ...],
    plain: bool,
    task_id: str | None,
    title: str | None,
    status: str | None,
    description: str | None,
    plan: str | None,
    append_plan: tuple[str, ...],
    clear_plan: bool,
    notes: str | None,
    acceptance_criteria: tuple[str, ...],
    definition_of_done: tuple[str, ...],
    definition_of_done_add: tuple[str, ...],
    disable_definition_of_done_defaults: bool,
    dependencies: tuple[str, ...],
    assignees: tuple[str, ...],
    labels: tuple[str, ...],
    priority: str | None,
    milestone: str | None,
    clear_milestone: bool,
    references: tuple[str, ...],
    documentation: tuple[str, ...],
    modified_files: tuple[str, ...],
    append_notes: str | None,
    check_ac: tuple[int, ...],
    check_dod: tuple[int, ...],
    uncheck_ac: tuple[int, ...],
    uncheck_dod: tuple[int, ...],
    remove_ac: tuple[int, ...],
    remove_dod: tuple[int, ...],
    final_summary: str | None,
    append_final_summary: tuple[str, ...],
    clear_final_summary: bool,
) -> None:
    """View tasks."""
    if args and args[0] == "archive":
        if len(args) != 2:
            raise click.UsageError("Usage: task archive TASK_ID")
        task_record = _mutable_repository(ctx).archive_task(args[1])
        click.echo(f"{_format_task_line(task_record, plain=plain)} archived")
        return
    if args and args[0] == "create":
        if len(args) != 2:
            raise click.UsageError("Usage: task create TITLE")
        if clear_milestone:
            raise click.UsageError("Cannot use --clear-milestone with task create.")
        task_record = _mutable_repository(ctx).create_task(
            title=args[1],
            task_id=task_id,
            status=status,
            description=description or "",
            plan=plan or "",
            notes=notes or "",
            acceptance_criteria=acceptance_criteria,
            definition_of_done=definition_of_done or None,
            definition_of_done_add=definition_of_done_add,
            disable_definition_of_done_defaults=disable_definition_of_done_defaults,
            dependencies=dependencies,
            assignees=assignees,
            labels=labels,
            priority=priority,
            milestone=milestone,
            references=references,
            documentation=documentation,
            modified_files=modified_files,
        )
        click.echo(_format_task_line(task_record, plain=plain))
        return
    if args and args[0] == "edit":
        if len(args) != 2:
            raise click.UsageError("Usage: task edit TASK_ID")
        if milestone is not None and clear_milestone:
            raise click.UsageError("Cannot use --milestone and --clear-milestone together.")
        task_record = _mutable_repository(ctx).edit_task(
            args[1],
            title=title,
            description=description,
            plan=plan,
            append_plan=append_plan,
            clear_plan=clear_plan,
            notes=notes,
            status=status,
            append_notes=append_notes,
            acceptance_criteria_add=acceptance_criteria,
            definition_of_done_add=definition_of_done_add,
            check_ac=check_ac,
            check_dod=check_dod,
            uncheck_ac=uncheck_ac,
            uncheck_dod=uncheck_dod,
            remove_ac=remove_ac,
            remove_dod=remove_dod,
            dependencies=dependencies if dependencies else None,
            assignees=assignees if assignees else None,
            labels=labels if labels else None,
            priority=priority,
            milestone=milestone,
            clear_milestone=clear_milestone,
            references=references if references else None,
            documentation=documentation if documentation else None,
            modified_files=modified_files if modified_files else None,
            final_summary=final_summary,
            append_final_summary=append_final_summary,
            clear_final_summary=clear_final_summary,
        )
        click.echo(_format_task_line(task_record, plain=plain))
        return
    if args == ("list",):
        for task_record in _repository(ctx).list_tasks(
            status=status,
            assignee=assignees,
            labels=labels,
            priority=_priority_filter(priority),
            milestone=milestone,
        ):
            click.echo(_format_task_line(task_record, plain=plain))
        return
    if len(args) != 1:
        raise click.UsageError("Missing task id.")
    task_id = args[0]
    task_record = _repository(ctx).get_task(task_id)
    click.echo(_format_task_detail(task_record, plain=plain))


@main.command("search")
@click.argument("query")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def search_command(ctx: click.Context, query: str, plain: bool) -> None:
    """Search active tasks."""
    for task_record in _repository(ctx).search_tasks(query):
        click.echo(_format_task_line(task_record, plain=plain))


@main.command("board")
@click.pass_context
def board_command(ctx: click.Context) -> None:
    """Print task board grouped by status."""
    for status, tasks in _repository(ctx).board().items():
        click.echo(f"{status}:")
        for task_record in tasks:
            click.echo(f"  {_format_task_line(task_record, plain=True)}")


@main.group("config")
def config_group() -> None:
    """Inspect Backlog.md configuration."""


@config_group.command("list")
@click.pass_context
def config_list(ctx: click.Context) -> None:
    """Print effective configuration."""
    project = _project(ctx)
    config = project.config
    click.echo(f"projectName: {config.project_name}")
    click.echo(f"defaultStatus: {config.default_status}")
    click.echo(f"remoteOperations: {_bool_text(config.remote_operations)}")
    click.echo(f"autoCommit: {_bool_text(config.auto_commit)}")
    click.echo(f"bypassGitHooks: {_bool_text(config.bypass_git_hooks)}")
    click.echo(f"checkActiveBranches: {_bool_text(config.check_active_branches)}")
    click.echo(f"activeBranchDays: {config.active_branch_days}")
    if config.statuses is not None:
        click.echo("statuses:")
        for status in config.statuses:
            click.echo(f"  - {status}")
    if config.definition_of_done is not None:
        click.echo("definitionOfDone:")
        for item in config.definition_of_done:
            click.echo(f"  - {item}")


@config_group.command("dod-defaults-get")
@click.pass_context
def config_dod_defaults_get(ctx: click.Context) -> None:
    """Print project Definition of Done defaults."""
    for item in get_definition_of_done_defaults(_project(ctx)):
        click.echo(item)


@config_group.command("dod-defaults-upsert")
@click.argument("items", nargs=-1, required=False)
@click.pass_context
def config_dod_defaults_upsert(ctx: click.Context, items: tuple[str, ...]) -> None:
    """Replace project Definition of Done defaults."""
    config = replace_definition_of_done_defaults(_project(ctx), list(items))
    for item in config.definition_of_done or []:
        click.echo(item)


@main.group("doc")
def document_group() -> None:
    """Create and inspect Backlog.md documents."""


@document_group.command("list")
@click.argument("query", required=False)
@click.pass_context
def document_list_command(ctx: click.Context, query: str | None) -> None:
    """List documents, optionally filtered by query."""
    service = _document_service(ctx)
    documents = service.list_documents() if query is None else service.search_documents(query)
    for document in documents:
        click.echo(_format_document_line(document))


@document_group.command("view")
@click.argument("path_or_id")
@click.pass_context
def document_view_command(ctx: click.Context, path_or_id: str) -> None:
    """Print a document by docs-relative path or frontmatter id."""
    document = _document_service(ctx).view_document(path_or_id)
    click.echo(document.raw_source.rstrip())


@document_group.command("create")
@click.argument("path")
@click.option("--title", required=True, help="Document title.")
@click.option("--content", required=True, help="Document body content.")
@click.pass_context
def document_create_command(ctx: click.Context, path: str, title: str, content: str) -> None:
    """Create a document under backlog/docs."""
    document = _document_service(ctx).create_document(path, title=title, content=content)
    click.echo(_format_document_line(document))


@document_group.command("update")
@click.argument("path_or_id")
@click.option("--title", default=None, help="Replacement document title.")
@click.option("--content", default=None, help="Replacement document body content.")
@click.pass_context
def document_update_command(
    ctx: click.Context,
    path_or_id: str,
    title: str | None,
    content: str | None,
) -> None:
    """Update a document while preserving omitted metadata."""
    document = _document_service(ctx).update_document(path_or_id, title=title, content=content)
    click.echo(_format_document_line(document))


@main.group("milestone")
def milestone_group() -> None:
    """Create and inspect milestone files."""


@milestone_group.command("list")
@click.pass_context
def milestone_list_command(ctx: click.Context) -> None:
    """List active milestones."""
    for milestone in _milestone_service(ctx).list_milestones():
        click.echo(_format_milestone_line(milestone))


@milestone_group.command("add")
@click.argument("name")
@click.option("--description", default="", help="Milestone body content.")
@click.pass_context
def milestone_add_command(ctx: click.Context, name: str, description: str) -> None:
    """Create a milestone file."""
    milestone = _milestone_service(ctx).add_milestone(name, description=description)
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("rename")
@click.argument("old_name")
@click.argument("new_name")
@click.option("--update-tasks", is_flag=True, help="Update task milestone frontmatter references.")
@click.pass_context
def milestone_rename_command(ctx: click.Context, old_name: str, new_name: str, update_tasks: bool) -> None:
    """Rename a milestone file."""
    milestone = _milestone_service(ctx).rename_milestone(old_name, new_name, update_tasks=update_tasks)
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("remove")
@click.argument("name")
@click.option("--clear-tasks", is_flag=True, help="Clear matching task milestone frontmatter references.")
@click.pass_context
def milestone_remove_command(ctx: click.Context, name: str, clear_tasks: bool) -> None:
    """Remove a milestone file."""
    milestone = _milestone_service(ctx).remove_milestone(name, clear_tasks=clear_tasks)
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("archive")
@click.argument("name")
@click.pass_context
def milestone_archive_command(ctx: click.Context, name: str) -> None:
    """Move a milestone file to backlog/archive/milestones."""
    milestone = _milestone_service(ctx).archive_milestone(name)
    click.echo(f"{_format_milestone_line(milestone)} archived")


def _project(ctx: click.Context) -> BacklogProject:
    cwd = ctx.obj.get("cwd") if ctx.obj else None
    return discover_project(Path.cwd(), explicit_cwd=cwd)


def _repository(ctx: click.Context) -> ReadOnlyRepository:
    return ReadOnlyRepository(_project(ctx))


def _mutable_repository(ctx: click.Context) -> MutableRepository:
    return MutableRepository(_project(ctx))


def _document_service(ctx: click.Context) -> DocumentService:
    return DocumentService(_project(ctx))


def _milestone_service(ctx: click.Context) -> MilestoneService:
    return MilestoneService(_project(ctx))


def _format_task_line(task_record: TaskRecord, *, plain: bool) -> str:
    if plain:
        line = f"{task_record.id} [{task_record.status}] {task_record.title}"
        metadata = _format_task_metadata_lines(task_record)
        return "\n".join([line, *metadata])
    return f"{task_record.id} - {task_record.title} ({task_record.status})"


def _format_task_detail(task_record: TaskRecord, *, plain: bool) -> str:
    if plain:
        parts = [
            f"{task_record.id} [{task_record.status}] {task_record.title}",
            "",
            task_record.description or task_record.body.strip(),
        ]
        return "\n".join(parts).rstrip()
    return task_record.raw_source


def _format_document_line(document: DocumentRecord) -> str:
    return f"{document.path_relative} {document.title}".rstrip()


def _format_milestone_line(milestone: MilestoneRecord) -> str:
    return f"{milestone.name} {milestone.path_relative}".rstrip()


def _format_task_metadata_lines(task_record: TaskRecord) -> list[str]:
    lines: list[str] = []
    milestone = task_record.parsed.frontmatter.get("milestone")
    references = _frontmatter_string_list(task_record, "references")
    documentation = _frontmatter_string_list(task_record, "documentation")
    modified_files = _frontmatter_string_list(task_record, "modified_files")
    if milestone:
        lines.append(f"Milestone: {milestone}")
    if references:
        lines.append(f"References: {', '.join(references)}")
    if documentation:
        lines.append(f"Documentation: {', '.join(documentation)}")
    if modified_files:
        lines.append(f"Modified files: {', '.join(modified_files)}")
    return lines


def _frontmatter_string_list(task_record: TaskRecord, key: str) -> list[str]:
    value = task_record.parsed.frontmatter.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _priority_filter(priority: str | None) -> str | None:
    if priority is None:
        return None
    normalized = priority.strip().casefold()
    if not normalized:
        return None
    valid_priorities = {"high", "medium", "low"}
    if normalized not in valid_priorities:
        raise click.ClickException(
            f"Invalid priority: {priority}\nValid values are: high, medium, low"
        )
    return normalized


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
