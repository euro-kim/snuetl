from __future__ import annotations

import csv
import json
import sys
import termios
import tty
from collections.abc import Sequence
from contextlib import contextmanager
from typing import TypeVar

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .catalog import CatalogResult
from .query import SCHEMA, QueryResult

console = Console()
ChoiceValue = TypeVar("ChoiceValue")


def interactive_terminal() -> bool:
    """Return whether full-screen key navigation is safe to use."""
    return sys.stdin.isatty() and sys.stdout.isatty()


@contextmanager
def _key_input() -> object:
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _read_key() -> str:
    key = sys.stdin.read(1)
    if key in {"", "\x03", "\x04"}:
        raise KeyboardInterrupt
    if key == "\x1b":
        return key + sys.stdin.read(2)
    return key


def _window_bounds(total: int, cursor: int, height: int) -> tuple[int, int]:
    """Return a terminal-sized row window that always contains the cursor."""
    visible = max(1, min(total, height - 2))
    start = min(max(0, cursor - visible // 2), max(0, total - visible))
    return start, start + visible


def _print_checkbox_screen(
    prompt: str,
    rows: Sequence[tuple[str, bool]],
    cursor: int,
    *,
    selected_count: int,
) -> None:
    start, end = _window_bounds(len(rows), cursor, console.size.height)
    status = f"{selected_count} selected"
    if start or end < len(rows):
        status += f"; showing {start + 1}-{end} of {len(rows)}"
    console.clear()
    console.print(
        f"{prompt} ({status})",
        markup=False,
        highlight=False,
        no_wrap=True,
        overflow="ellipsis",
    )
    for index in range(start, end):
        label, checked = rows[index]
        console.print(
            f"{'>' if index == cursor else ' '} {'[x]' if checked else '[ ]'} {label}",
            markup=False,
            highlight=False,
            no_wrap=True,
            overflow="ellipsis",
        )
    console.print(
        "↑/↓ move · Space check · Enter confirm · Ctrl+C/Ctrl+D cancel",
        style="dim",
        markup=False,
        highlight=False,
        no_wrap=True,
        overflow="ellipsis",
    )


def choose_checkbox(
    prompt: str,
    choices: Sequence[tuple[str, ChoiceValue]],
    *,
    default: int | None = None,
    require_space: bool = False,
) -> ChoiceValue | None:
    """Choose exactly one checkbox with arrows, Space, and Enter.

    Callers should first check :func:`interactive_terminal`. Keeping the TTY
    behavior in one place also ensures JSON and piped agent calls never enter
    cbreak mode accidentally.
    """
    if not choices:
        return None
    if not interactive_terminal():
        raise RuntimeError("interactive checkbox selection requires a terminal")
    if default is not None and not 0 <= default < len(choices):
        raise ValueError("checkbox default is out of range")

    cursor = default if default is not None else 0
    checked = None if require_space else default
    with _key_input(), console.screen():
        while True:
            _print_checkbox_screen(
                prompt,
                tuple((label, index == checked) for index, (label, _) in enumerate(choices)),
                cursor,
                selected_count=int(checked is not None),
            )
            key = _read_key()
            if key == "\x1b[A":
                cursor = (cursor - 1) % len(choices)
            elif key == "\x1b[B":
                cursor = (cursor + 1) % len(choices)
            elif key == " ":
                checked = cursor
            elif key in {"\r", "\n"} and checked is not None:
                return choices[checked][1]


def choose_checkboxes(
    prompt: str,
    choices: Sequence[tuple[str, ChoiceValue]],
    *,
    include_all: bool = False,
) -> tuple[ChoiceValue, ...]:
    """Choose zero or more values with a terminal-height-aware checklist."""
    if not choices:
        return ()
    if not interactive_terminal():
        raise RuntimeError("interactive checkbox selection requires a terminal")

    selected = [False] * len(choices)
    cursor = 0
    row_count = len(choices) + int(include_all)
    with _key_input(), console.screen():
        while True:
            all_selected = all(selected)
            rows: list[tuple[str, bool]] = []
            if include_all:
                rows.append(("All", all_selected))
            rows.extend((label, selected[index]) for index, (label, _) in enumerate(choices))
            _print_checkbox_screen(
                prompt,
                rows,
                cursor,
                selected_count=sum(selected),
            )
            key = _read_key()
            if key == "\x1b[A":
                cursor = (cursor - 1) % row_count
            elif key == "\x1b[B":
                cursor = (cursor + 1) % row_count
            elif key == " ":
                if include_all and cursor == 0:
                    selected = [not all_selected] * len(choices)
                else:
                    choice_index = cursor - int(include_all)
                    selected[choice_index] = not selected[choice_index]
            elif key in {"\r", "\n"}:
                return tuple(
                    value for (_, value), checked in zip(choices, selected, strict=True) if checked
                )


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
        ("snuetl api setup", "Create and verify personal Canvas API access"),
        ("snuetl api status", "Check Canvas API access without showing the token"),
        ("snuetl api rotate", "Replace the personal Canvas API token"),
        ("snuetl courses", "List active courses"),
        ("snuetl files [COURSE]", "List file titles and folders"),
        ("snuetl articles [COURSE]", "List announcement and course-page titles"),
        ("snuetl assignments [COURSE]", "List assignments and due dates"),
        ("snuetl refresh", "Cache all paginated catalog data for SQL queries"),
        ("snuetl quizzes [COURSE]", "List quizzes and due dates"),
        ("snuetl upcoming", "List upcoming incomplete work"),
        ("snuetl missing", "List overdue missing submissions"),
        ("snuetl submissions [COURSE]", "List submission states and scores"),
        ("snuetl grades [COURSE]", "Show available course grades"),
        ("snuetl calendar", "List course calendar events"),
        ("snuetl discussions [COURSE]", "List discussion topics"),
        ("snuetl activity [COURSE]", "Show new course activity"),
        ("snuetl announcements [COURSE]", "Read recent announcements"),
        ("snuetl modules [COURSE]", "Show modules, items, and progress"),
        ("snuetl feedback [COURSE]", "Show recent grades and instructor feedback"),
        ("snuetl dashboard [COURSE]", "Summarize workload, missing work, and grades"),
        ("snuetl sql", "Open the interactive read-only SQL shell"),
        ("snuetl sql --execute SQL", "Run one query over canonical catalog views"),
        ("snuetl schema", "Show canonical SQL tables and field names"),
        ("snuetl pull [KIND]", "Pull files, articles, syllabi, videos, or all non-video content"),
        ("snuetl sync", "Compatibility alias for pulling course files"),
        ("snuetl headless [on|off]", "Show or set the default browser mode"),
        ("snuetl directory", "Show or configure pull roots and routing rules"),
        ("snuetl directory videos [PATH]", "Show or set the large-video storage root"),
        ("snuetl discord", "Guided Discord bot setup and daemon installation"),
        ("snuetl discord guide", "Show the Developer Portal setup checklist"),
        ("snuetl telegram", "Pair a private Telegram bot and start daily digests"),
        ("snuetl profile [NAME]", "List or switch the active eTL identity"),
        ("snuetl capabilities --json", "Describe the stable agent-facing interface"),
        ("snuetl status", "Show local synchronization status"),
        ("snuetl doctor", "Check dependencies and configuration"),
        ("snuetl version", "Show the installed version and source"),
        ("snuetl update", "Update this pipx installation"),
        ("snuetl logout", "Revoke Canvas API access and remove local authentication"),
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

    titles = {
        "articles": "Articles and announcements",
        "assignments": "Assignments",
        "quizzes": "Quizzes",
    }
    title = titles[kind]
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
            '"SELECT semester_code, course_id, file_name FROM files '
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
