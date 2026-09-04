from __future__ import annotations

import csv
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .catalog import CatalogResult
from .query import SCHEMA, QueryResult

console = Console()


def _date(value: str | None) -> str:
    return value[:10] if value else "—"


def _size(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value:,} B"


def print_banner(subtitle: str = "Your SNU eTL, synchronized") -> None:
    console.print(
        Panel.fit(
            "[bold blue]snuetl[/bold blue]\n[dim]" + subtitle + "[/dim]",
            border_style="blue",
            padding=(1, 4),
        )
    )


def print_commands() -> None:
    table = Table(title="Available commands", show_header=False, box=None, padding=(0, 2))
    rows = (
        ("snuetl setup", "Run or repair guided configuration"),
        ("snuetl login", "Refresh SNU login and trusted-browser enrollment"),
        ("snuetl courses", "List active courses"),
        ("snuetl files [COURSE]", "List file titles and folders"),
        ("snuetl articles [COURSE]", "List announcement and course-page titles"),
        ("snuetl assignments [COURSE]", "List assignments and due dates"),
        ("snuetl refresh", "Cache all paginated catalog data for SQL queries"),
        ("snuetl sql", "Open the interactive read-only SQL shell"),
        ("snuetl sql --execute SQL", "Run one query over canonical catalog views"),
        ("snuetl schema", "Show canonical SQL tables and field names"),
        ("snuetl pull [KIND]", "Pull files, articles, syllabi, videos, or all non-video content"),
        ("snuetl sync", "Compatibility alias for pulling course files"),
        ("snuetl directory [PATH]", "Show or change the managed pull directory"),
        ("snuetl profile [NAME]", "List or switch the active eTL identity"),
        ("snuetl capabilities --json", "Describe the stable agent-facing interface"),
        ("snuetl status", "Show local synchronization status"),
        ("snuetl doctor", "Check dependencies and configuration"),
        ("snuetl version", "Show the installed version and source"),
        ("snuetl update", "Update this pipx installation"),
        ("snuetl logout", "Remove browser authentication and saved credentials"),
        ("snuetl uninstall", "Safely uninstall and optionally remove local data"),
    )
    for command, description in rows:
        table.add_row(f"[cyan]{command}[/cyan]", description)
    console.print(table)


def print_catalog(result: CatalogResult, kind: str) -> None:
    if kind == "courses":
        table = Table(title=f"Active courses ({len(result.courses)})")
        table.add_column("Semester", style="magenta", no_wrap=True)
        table.add_column("Course ID", style="cyan", no_wrap=True)
        table.add_column("Course")
        for course in result.courses:
            semester = course.semester.semester_code if course.semester else "—"
            table.add_row(semester, course.remote_id, course.display_name)
        console.print(table)
        return

    if kind == "files":
        table = Table(title=f"Course files ({len(result.files)})")
        table.add_column("Semester", style="magenta", no_wrap=True)
        table.add_column("Course ID", style="cyan", no_wrap=True)
        table.add_column("Course")
        table.add_column("File / folder", style="cyan")
        table.add_column("Size", justify="right")
        table.add_column("Updated")
        for course, remote in result.files:
            semester = course.semester.semester_code if course.semester else "—"
            table.add_row(
                semester,
                course.remote_id,
                course.display_name,
                remote.display_path,
                _size(remote.size),
                _date(remote.updated_at),
            )
        console.print(table)
        return

    title = "Articles and announcements" if kind == "articles" else "Assignments"
    table = Table(title=f"{title} ({len(result.items)})")
    table.add_column("Semester", style="magenta", no_wrap=True)
    table.add_column("Course ID", style="cyan", no_wrap=True)
    table.add_column("Course")
    table.add_column("Type", style="magenta")
    table.add_column("Title", style="cyan")
    table.add_column("Published" if kind == "articles" else "Due")
    for course, item in result.items:
        semester = course.semester.semester_code if course.semester else "—"
        table.add_row(
            semester,
            course.remote_id,
            course.display_name,
            item.kind,
            item.title,
            _date(item.published_at if kind == "articles" else item.due_at),
        )
    console.print(table)


def print_schema(table_name: str | None = None) -> None:
    selected = tuple(item for item in SCHEMA if table_name is None or item.name == table_name)
    if not selected:
        console.print(f"[red]Unknown canonical table: {table_name}[/red]")
        return
    title = f"Canonical SQL schema: {table_name}" if table_name else "Canonical SQL schema"
    table = Table(title=title, show_lines=True)
    table.add_column("Table", style="cyan", no_wrap=True)
    table.add_column("Canonical fields", overflow="fold")
    table.add_column("Contents")
    for item in selected:
        table.add_row(item.name, item.columns, item.description)
    console.print(table)
    if table_name is None:
        console.print(
            "Example: [cyan]snuetl sql --refresh --execute "
            "\"SELECT semester_code, course_id, file_name FROM files "
            "WHERE semester_code = '2026-2' ORDER BY updated_at DESC\"[/cyan]"
        )


def print_query_result(
    result: QueryResult,
    output_format: str,
    *,
    limit_hint: str = "--limit 0",
) -> None:
    if output_format == "json":
        rows = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    elif output_format == "jsonl":
        for row in result.rows:
            print(
                json.dumps(
                    dict(zip(result.columns, row, strict=True)),
                    ensure_ascii=False,
                    default=str,
                )
            )
    elif output_format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(result.columns)
        writer.writerows(result.rows)
    else:
        row_count = len(result.rows)
        noun = "row" if row_count == 1 else "rows"
        table = Table(title=f"Query result ({row_count} {noun})", show_lines=False)
        for column in result.columns:
            table.add_column(column, overflow="fold")
        for row in result.rows:
            table.add_row(*("—" if value is None else str(value) for value in row))
        console.print(table)
    if result.truncated:
        message = f"Output limit reached; use {limit_hint} or add a SQL LIMIT clause."
        if output_format == "table":
            console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
