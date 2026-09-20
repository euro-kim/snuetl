"""Explicit local Markdown and SQLite snapshots of personal Canvas views."""

from __future__ import annotations

import hashlib
import html
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .canvas_api import live_data
from .config import Config
from .logging_utils import redact
from .state import StateStore


def _cell(value: Any) -> str:
    if value is None:
        return ""
    rendered = (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, (dict, list))
        else str(value)
    )
    return (
        html.escape(rendered, quote=False)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _replace_rows(
    config: Config, command: str, scope: str, rows: list[dict[str, Any]], fetched_at: str
) -> None:
    with StateStore(config.database_path) as store, store.transaction():
        store.db.execute(
            "DELETE FROM canvas_snapshots WHERE command=? AND scope=?", (command, scope)
        )
        for index, row in enumerate(rows):
            item_id = next(
                (
                    row[key]
                    for key in ("item_id", "assignment_id", "announcement_id", "topic_id")
                    if row.get(key) is not None
                ),
                None,
            )
            store.db.execute(
                """INSERT INTO canvas_snapshots
                   (command, scope, row_number, course_id, item_id, title, due_at, data_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command,
                    scope,
                    index,
                    str(row.get("course_id")) if row.get("course_id") is not None else None,
                    str(item_id) if item_id is not None else None,
                    str(row.get("title") or row.get("item_title") or "") or None,
                    str(row.get("due_at") or row.get("start_at") or "") or None,
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                    fetched_at,
                ),
            )
        store.db.execute(
            """INSERT INTO canvas_snapshot_scopes(command, scope, fetched_at, row_count)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(command, scope) DO UPDATE SET
                 fetched_at=excluded.fetched_at, row_count=excluded.row_count""",
            (command, scope, fetched_at, len(rows)),
        )


def refresh_saved_snapshots(config: Config) -> tuple[int, list[dict[str, str]]]:
    """Refresh only scopes the user previously chose to save."""
    with StateStore(config.database_path) as store:
        scopes = store.db.execute(
            """SELECT command, scope FROM canvas_snapshot_scopes
               UNION SELECT DISTINCT command, scope FROM canvas_snapshots
               ORDER BY command, scope"""
        ).fetchall()
    updated = 0
    warnings: list[dict[str, str]] = []
    for command, scope in scopes:
        try:
            options = json.loads(scope)
            if not isinstance(options, dict):
                raise ValueError("saved scope is invalid")
            start = date.fromisoformat(options["from"]) if options.get("from") else None
            end = date.fromisoformat(options["to"]) if options.get("to") else None
            rows = live_data(
                config, command, course=options.get("course"), start=start, end=end
            )
            _replace_rows(config, command, scope, rows, datetime.now(UTC).isoformat())
            updated += 1
        except Exception as exc:
            warnings.append(
                {
                    "code": "SNAPSHOT_REFRESH_FAILED",
                    "command": command,
                    "message": str(redact(exc)),
                }
            )
    return updated, warnings


def save_live_snapshot(
    config: Config,
    command: str,
    rows: list[dict[str, Any]],
    *,
    course: str | None,
    start: str | None,
    end: str | None,
) -> Path:
    scope = json.dumps(
        {"course": course, "from": start, "to": end},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fetched_at = datetime.now(UTC).isoformat()
    root = config.download_dir / "canvas"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise PermissionError(
            f"Canvas snapshot directory is accessible to others: {root}; "
            "choose a private download root or change its permissions"
        )
    _replace_rows(config, command, scope, rows, fetched_at)
    suffix = hashlib.sha256(scope.encode()).hexdigest()[:10]
    path = root / f"{command}--{suffix}.md"
    columns = list(dict.fromkeys(key for row in rows for key in row))
    lines = [
        f"# {command.capitalize()}",
        "",
        f"Fetched: {fetched_at}",
        f"Scope: {html.escape(scope)}",
        "",
    ]
    if rows:
        lines.extend(
            [
                "| " + " | ".join(columns) + " |",
                "| " + " | ".join("---" for _ in columns) + " |",
            ]
        )
        lines.extend(
            "| " + " | ".join(_cell(row.get(key)) for key in columns) + " |"
            for row in rows
        )
    else:
        lines.append("No results.")
    body = "\n".join(lines) + "\n"
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=root)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
