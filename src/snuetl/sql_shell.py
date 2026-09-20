from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from .catalog import CatalogRefreshSummary
from .query import SCHEMA, QueryError, execute_query
from .ui import console, print_query_result, print_schema

DOT_COMMANDS = (
    ".tables",
    ".schema",
    ".columns",
    ".limit",
    ".mode",
    ".format",
    ".refresh",
    ".status",
    ".help",
    ".quit",
    ".exit",
)
SQL_WORDS = (
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "LEFT JOIN",
    "GROUP BY",
    "ORDER BY",
    "LIMIT",
    "OFFSET",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "DISTINCT",
    "WITH",
    "AS",
    "AND",
    "OR",
)


def _install_completion() -> None:
    try:
        import readline
    except ImportError:  # pragma: no cover - platform dependent
        return
    candidates = (*DOT_COMMANDS, *(item.name for item in SCHEMA), *SQL_WORDS)

    def complete(text: str, state: int) -> str | None:
        matches = [value for value in candidates if value.casefold().startswith(text.casefold())]
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")


def _help() -> None:
    console.print(
        """[bold]SQL shell commands[/bold]
  [cyan].tables[/cyan]               list canonical tables
  [cyan].schema [TABLE][/cyan]       show fields for one or every table
  [cyan].columns TABLE[/cyan]        show a table's canonical fields
  [cyan].limit [N][/cyan]            show or set output limit; 0 means all
  [cyan].mode FORMAT[/cyan]          table, json, jsonl, or csv
  [cyan].refresh[/cyan]              refresh catalog and saved personal scopes (no downloads)
  [cyan].status[/cyan]               show catalog refresh timestamps
  [cyan].quit[/cyan] / [cyan].exit[/cyan]        leave the shell

Use [cyan]SHOW TABLES;[/cyan] as a SQL alternative to [cyan].tables[/cyan]. Terminate SQL with a semicolon.
Press Enter on an empty continuation line to run the buffered statement without a
semicolon. Only read-only SQL is accepted. Ctrl+C or Ctrl+D exits the shell."""
    )


def _print_refresh(summary: CatalogRefreshSummary) -> None:
    console.print(
        "[green]Catalog refreshed:[/green] "
        f"{summary.courses} courses, {summary.files} files, "
        f"{summary.articles} articles, {summary.assignments} assignments, {summary.quizzes} quizzes"
    )


def run_sql_shell(
    database_path: Path,
    *,
    refresh: Callable[[], CatalogRefreshSummary] | None = None,
    output_format: str = "table",
    limit: int = 200,
    input_fn: Callable[[str], str] = input,
) -> int:
    _install_completion()
    console.print(
        "[bold blue]snuetl SQL[/bold blue] — read-only, refreshed eTL catalog\n"
        "Enter SQL ending with [cyan];[/cyan], or [cyan].help[/cyan] for commands."
    )
    buffer: list[str] = []
    while True:
        try:
            line = input_fn("   ...> " if buffer else "snuetl> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Leaving snuetl SQL.[/dim]")
            return 130

        stripped = line.strip()
        if not buffer and stripped.startswith("."):
            parts = stripped.split()
            command = parts[0].casefold()
            arguments = parts[1:]
            if command in {".quit", ".exit"}:
                return 0
            if command == ".help":
                _help()
            elif command == ".tables":
                console.print("  ".join(item.name for item in SCHEMA))
            elif command in {".schema", ".columns"}:
                if command == ".columns" and not arguments:
                    console.print("[red]Usage: .columns TABLE[/red]")
                else:
                    print_schema(arguments[0] if arguments else None)
            elif command == ".limit":
                if not arguments:
                    console.print(f"Output limit: {limit}")
                else:
                    try:
                        new_limit = int(arguments[0])
                        if new_limit < 0:
                            raise ValueError
                        limit = new_limit
                        label = "all rows" if limit == 0 else f"{limit} rows"
                        console.print(f"Output limit: {label}")
                    except ValueError:
                        console.print("[red]Limit must be a non-negative integer.[/red]")
            elif command in {".mode", ".format"}:
                if not arguments:
                    console.print(f"Output mode: {output_format}")
                elif arguments[0].casefold() not in {"table", "json", "jsonl", "csv"}:
                    console.print("[red]Mode must be table, json, jsonl, or csv.[/red]")
                else:
                    output_format = arguments[0].casefold()
                    console.print(f"Output mode: {output_format}")
            elif command == ".refresh":
                if refresh is None:
                    console.print("[red]Remote refresh is unavailable in this shell.[/red]")
                else:
                    try:
                        _print_refresh(refresh())
                    except Exception as exc:
                        console.print(f"[red]Refresh failed: {exc}[/red]")
            elif command == ".status":
                console.print("[bold]Catalog[/bold]")
                result = execute_query(
                    database_path,
                    "SELECT scope, refreshed_at, row_count, complete FROM catalog_status "
                    "ORDER BY scope",
                    limit=limit,
                )
                print_query_result(result, output_format, limit_hint=".limit 0")
                console.print("[bold]Saved personal scopes[/bold]")
                snapshots = execute_query(
                    database_path,
                    "SELECT command, scope, fetched_at, row_count "
                    "FROM canvas_snapshot_status ORDER BY command, scope",
                    limit=limit,
                )
                print_query_result(snapshots, output_format, limit_hint=".limit 0")
            else:
                console.print(f"[red]Unknown shell command: {command}[/red]")
            continue

        if not stripped and not buffer:
            continue
        if stripped:
            buffer.append(line)
        statement = "\n".join(buffer)
        if buffer and not (sqlite3.complete_statement(statement) or not stripped):
            continue
        buffer.clear()
        try:
            result = execute_query(database_path, statement, limit=limit)
            print_query_result(result, output_format, limit_hint=".limit 0")
        except QueryError as exc:
            console.print(f"[red]{exc}[/red]")
