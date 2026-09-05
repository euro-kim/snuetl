from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import discord

from .errors import AuthenticationRequired, DiscoveryError, OperationCancelled, ProfileInUse
from .logging_utils import redact
from .state import DiscordJob

INFO_COLOR = 0x5865F2
SUCCESS_COLOR = 0x57F287
WARNING_COLOR = 0xFEE75C
ERROR_COLOR = 0xED4245
CATALOG_PAGE_SIZE = 5

_STATUS_ICONS = {
    "queued": "⏳",
    "running": "🔄",
    "completed": "✅",
    "failed": "❌",
    "cancel_requested": "🛑",
    "cancelled": "🛑",
    "interrupted": "⚠️",
}


def _truncate(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean(value: object, limit: int = 1024) -> str:
    return _truncate(discord.utils.escape_markdown(str(value or "")), limit)


def _inline_code(value: object) -> str:
    return f"`{str(value).replace('`', 'ˋ')}`"


def _number(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def discord_timestamp(value: object, style: str = "f") -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return "Not available"
    return f"<t:{int(parsed.timestamp())}:{style}>"


def human_size(value: object) -> str:
    size = float(max(0, _number(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return "0 B"


def notice_embed(
    title: str,
    description: str,
    *,
    tone: Literal["info", "success", "warning", "error"] = "info",
) -> discord.Embed:
    colors = {
        "info": INFO_COLOR,
        "success": SUCCESS_COLOR,
        "warning": WARNING_COLOR,
        "error": ERROR_COLOR,
    }
    return discord.Embed(
        title=title,
        description=_truncate(description, 4096),
        color=colors[tone],
        timestamp=datetime.now(UTC),
    )


def embed_to_text(embed: discord.Embed, limit: int = 1900) -> str:
    """Provide a useful fallback when Discord refuses embedded content."""
    parts: list[str] = []
    if embed.title:
        parts.append(f"**{embed.title}**")
    if embed.description:
        parts.append(str(embed.description))
    for field in embed.fields:
        parts.append(f"**{field.name}**\n{field.value}")
    if embed.footer.text:
        parts.append(f"_{embed.footer.text}_")
    return _truncate("\n\n".join(parts), limit)


def exception_embed(error: BaseException, reference: str) -> discord.Embed:
    """Turn internal and domain exceptions into safe, actionable user guidance."""
    detail = _clean(redact(error), 700)
    if isinstance(error, AuthenticationRequired):
        title = "SNU login required"
        description = (
            "The saved SNU eTL session is no longer valid. Run `/snuetl login`, complete "
            "verification, then retry the command."
        )
        tone: Literal["warning", "error"] = "warning"
    elif isinstance(error, ProfileInUse):
        title = "SNU session is busy"
        description = (
            "Another snuetl operation is using the browser profile. Check `/snuetl jobs` "
            "and retry after it finishes."
        )
        tone = "warning"
    elif isinstance(error, DiscoveryError):
        title = "Could not read SNU eTL"
        description = (
            f"{detail}\n\nRefresh the catalog or run `/snuetl login` if your session expired."
        )
        tone = "warning"
    elif isinstance(error, OperationCancelled):
        title = "Command cancelled"
        description = "The operation stopped safely. Completed writes, if any, were kept."
        tone = "warning"
    elif isinstance(error, TimeoutError):
        title = "Command timed out"
        description = "SNU eTL or Discord took too long to respond. Wait briefly and try again."
        tone = "warning"
    elif isinstance(error, discord.Forbidden):
        title = "Discord permission missing"
        description = (
            "I could not send or update this response. Allow View Channel, Send Messages, "
            "Embed Links, and Read Message History in the configured channel."
        )
        tone = "error"
    elif isinstance(error, discord.NotFound):
        title = "Interaction expired"
        description = "Discord no longer recognizes this control or message. Run the command again."
        tone = "warning"
    elif isinstance(error, discord.HTTPException):
        title = "Discord request failed"
        description = "Discord rejected or could not complete the request. Wait briefly and retry."
        tone = "error"
    elif isinstance(error, ValueError):
        title = "Invalid request"
        description = detail
        tone = "warning"
    else:
        title = "Unexpected command error"
        description = (
            "The command could not finish because of an internal error. No secret values are "
            "shown here; the server log contains the diagnostic details."
        )
        tone = "error"
    embed = notice_embed(title, description, tone=tone)
    embed.add_field(name="Error reference", value=_inline_code(reference), inline=False)
    embed.set_footer(text="Use /snuetl doctor if this keeps happening")
    return embed


def status_embed(data: dict[str, object]) -> discord.Embed:
    discord_data = data.get("discord")
    remote = discord_data if isinstance(discord_data, dict) else {}
    auth_ready = bool(data.get("authentication_ready"))
    service_active = bool(remote.get("service_active"))
    ready = auth_ready and service_active
    embed = discord.Embed(
        title="snuetl status",
        description=(
            "Checked authentication, local catalog, storage, and remote control. "
            "No files were changed."
        ),
        color=SUCCESS_COLOR if ready else WARNING_COLOR,
        timestamp=datetime.now(UTC),
    )
    saved_user = data.get("saved_username")
    auth_lines = ["✅ Signed in" if auth_ready else "⚠️ Login required"]
    if data.get("automatic_relogin"):
        auth_lines.append(
            f"Automatic re-login: enabled{f' for **{_clean(saved_user, 80)}**' if saved_user else ''}"
        )
    else:
        auth_lines.append("Automatic re-login: unavailable")
    embed.add_field(name="Authentication", value="\n".join(auth_lines), inline=True)
    embed.add_field(
        name="Remote control",
        value=(
            "🟢 Discord daemon online" if service_active else "🔴 Discord daemon is not running"
        ),
        inline=True,
    )
    embed.add_field(
        name="Catalog",
        value=(
            f"**{_number(data.get('courses'))}** courses\n"
            f"**{_number(data.get('remote_files'))}** files\n"
            f"**{_number(data.get('articles'))}** articles\n"
            f"**{_number(data.get('assignments'))}** assignments\n"
            f"**{_number(data.get('quizzes'))}** quizzes"
        ),
        inline=True,
    )
    last_sync = data.get("last_sync")
    if isinstance(last_sync, dict):
        finished = last_sync.get("finished_at") or last_sync.get("started_at")
        result = str(last_sync.get("status") or "unknown").replace("_", " ").title()
        detail = (
            f"**{result}** · {discord_timestamp(finished)}\n"
            f"{_number(last_sync.get('downloaded'))} downloaded · "
            f"{_number(last_sync.get('updated'))} updated · "
            f"{_number(last_sync.get('failed'))} failed"
        )
    else:
        detail = "No synchronization has completed yet."
    embed.add_field(name="Last sync", value=detail, inline=False)
    embed.add_field(
        name="Local storage",
        value=(
            f"**Downloaded:** {_number(data.get('downloaded_files'))} files\n"
            f"**Course files:** {_inline_code(data.get('download_dir') or 'Not configured')}\n"
            f"**Videos:** {_inline_code(data.get('video_directory') or data.get('download_dir') or 'Not configured')}"
            + (" (separate)" if data.get("video_directory_separate") else " (with course files)")
        ),
        inline=False,
    )
    embed.set_footer(text="Read-only status check")
    return embed


def doctor_embed(data: dict[str, object]) -> discord.Embed:
    checks = data.get("checks")
    rows = checks if isinstance(checks, list) else []
    failed = [item for item in rows if isinstance(item, dict) and not item.get("ok")]
    embed = discord.Embed(
        title="Diagnostics passed" if not failed else "Diagnostics found issues",
        description=(f"Ran {len(rows)} installation checks. No settings or files were changed."),
        color=SUCCESS_COLOR if not failed else WARNING_COLOR,
        timestamp=datetime.now(UTC),
    )
    for item in rows[:25]:
        if not isinstance(item, dict):
            continue
        ok = bool(item.get("ok"))
        name = str(item.get("name") or "check").replace("_", " ").title()
        if item.get("optional"):
            name += " · optional"
        embed.add_field(
            name=f"{'✅' if ok else '⚠️'} {name}",
            value=_clean(item.get("detail"), 160),
            inline=False,
        )
    embed.set_footer(text=f"{len(rows) - len(failed)} passed · {len(failed)} need attention")
    return embed


def catalog_page_count(rows: list[dict[str, object]]) -> int:
    return max(1, (len(rows) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)


def _catalog_item(kind: str, row: dict[str, object]) -> tuple[str, str]:
    course = _clean(row.get("course"), 180)
    if kind == "courses":
        name = course
        value = f"Semester: **{_clean(row.get('semester'), 80)}**\nID: {_inline_code(row.get('course_id'))}"
    elif kind == "files":
        name = f"📄 {_clean(row.get('name'), 220)}"
        path = row.get("path") or row.get("name")
        value = f"**{course}**\n{_clean(path, 500)}\nSize: {human_size(row.get('size_bytes'))}"
    else:
        icons = {
            "assignments": "📅",
            "quizzes": "❓",
        }
        icon = icons.get(kind, "📣")
        name = f"{icon} {_clean(row.get('title'), 220)}"
        dates: list[str] = []
        if row.get("due_at"):
            dates.append(
                f"Due {discord_timestamp(row.get('due_at'))} "
                f"({discord_timestamp(row.get('due_at'), 'R')})"
            )
        elif row.get("published_at"):
            dates.append(f"Published {discord_timestamp(row.get('published_at'))}")
        value = f"**{course}**" + ("\n" + "\n".join(dates) if dates else "")
    return _truncate(name, 256), _truncate(value, 1024)


def catalog_embed(
    kind: str,
    rows: list[dict[str, object]],
    *,
    page: int = 0,
    course: str | None = None,
) -> discord.Embed:
    pages = catalog_page_count(rows)
    page = min(max(page, 0), pages - 1)
    names = {
        "courses": "Active courses",
        "files": "Course files",
        "articles": "Announcements and pages",
        "assignments": "Assignments",
        "quizzes": "Quizzes",
    }
    title = names.get(kind, kind.replace("_", " ").title())
    scope = f" for **{_clean(course, 100)}**" if course else ""
    embed = discord.Embed(
        title=title,
        description=(
            f"Found **{len(rows)}** item{'s' if len(rows) != 1 else ''}{scope}. "
            "Nothing was downloaded."
            if rows
            else f"No {kind} were found{scope}. Nothing was changed."
        ),
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    start = page * CATALOG_PAGE_SIZE
    for row in rows[start : start + CATALOG_PAGE_SIZE]:
        name, value = _catalog_item(kind, row)
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"Page {page + 1} of {pages} · {len(rows)} total")
    return embed


def jobs_embed(jobs: list[DiscordJob]) -> discord.Embed:
    embed = discord.Embed(
        title="Recent jobs",
        description=(
            "The latest background commands on this server. Use `/snuetl cancel` for a "
            "queued or running job."
            if jobs
            else "No Discord jobs have been queued yet."
        ),
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    for job in jobs[:10]:
        icon = _STATUS_ICONS.get(job.status, "•")
        status = job.status.replace("_", " ").title()
        value = (
            f"ID: {_inline_code(job.job_id)} · {discord_timestamp(job.created_at, 'R')}\n"
            f"{_clean(job.progress, 360) if job.progress else 'No progress update yet.'}"
        )
        embed.add_field(
            name=f"{icon} {job.command.title()} · {status}",
            value=value,
            inline=False,
        )
    embed.set_footer(text=f"Showing {len(jobs)} most recent")
    return embed


def _request_summary(job: DiscordJob) -> str:
    arguments = job.arguments
    lines: list[str] = []
    if job.command == "pull":
        lines.append(f"Content: **{str(arguments.get('kind', 'all')).title()}**")
        if arguments.get("course"):
            lines.append(f"Course: **{_clean(arguments['course'], 120)}**")
        if arguments.get("semester"):
            lines.append(f"Semester: **{_clean(arguments['semester'], 80)}**")
        if arguments.get("video_ids"):
            selected = arguments["video_ids"]
            lines.append(
                f"Videos selected: **{len(selected) if isinstance(selected, list) else 0}**"
            )
        if arguments.get("dry_run"):
            lines.append("Mode: **Preview only**")
        if arguments.get("force"):
            lines.append("Overwrite protection: **Force enabled**")
        if str(arguments.get("kind")) == "videos":
            quality = (
                "Best available"
                if arguments.get("best")
                else f"Up to {arguments.get('max_height', 1080)}p"
            )
            lines.append(f"Quality: **{quality}**")
            lines.append(f"Captions: **{'Yes' if not arguments.get('no_captions') else 'No'}**")
    elif job.command == "login":
        lines.append(f"Verification method: **{str(arguments.get('method', 'email')).title()}**")
    return "\n".join(lines) or "Default options"


def queued_job_embed(job: DiscordJob, requester: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{job.command.title()} queued",
        description=(
            "Added this command to the background queue. This message will update with "
            "progress and the final result."
        ),
        color=INFO_COLOR,
        timestamp=parse_datetime(job.created_at) or datetime.now(UTC),
    )
    embed.add_field(name="Requested work", value=_request_summary(job), inline=False)
    embed.add_field(name="Job ID", value=_inline_code(job.job_id), inline=True)
    embed.add_field(name="Requested by", value=_clean(requester, 100), inline=True)
    embed.set_footer(text="Waiting for the worker")
    return embed


def progress_job_embed(job: DiscordJob, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{job.command.title()} in progress",
        description=_clean(message, 4096),
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    embed.add_field(name="Requested work", value=_request_summary(job), inline=False)
    embed.set_footer(text=f"Job {job.job_id} · Updates are automatic")
    return embed


def _completion_detail(
    job: DiscordJob, result: dict[str, object]
) -> tuple[str, list[tuple[str, str]]]:
    fields: list[tuple[str, str]] = []
    if job.command == "sync":
        changed = _number(result.get("downloaded")) + _number(result.get("updated"))
        description = (
            f"Checked **{_number(result.get('courses'))} courses** and saved "
            f"**{changed} new or changed files**."
        )
        fields.append(
            (
                "File results",
                f"🆕 {_number(result.get('downloaded'))} downloaded\n"
                f"♻️ {_number(result.get('updated'))} updated\n"
                f"➖ {_number(result.get('unchanged'))} unchanged\n"
                f"❌ {_number(result.get('failed'))} failed",
            )
        )
        return description, fields
    if job.command == "refresh":
        description = "Refreshed the local course catalog from SNU eTL."
        fields.append(
            (
                "Catalog now contains",
                f"📚 {_number(result.get('courses'))} courses\n"
                f"📄 {_number(result.get('files'))} files\n"
                f"📣 {_number(result.get('articles'))} articles\n"
                f"📅 {_number(result.get('assignments'))} assignments\n"
                f"❓ {_number(result.get('quizzes'))} quizzes\n"
                f"🎬 {_number(result.get('videos'))} videos",
            )
        )
        return description, fields
    if job.command == "login":
        return "Renewed the saved SNU eTL login session successfully.", fields
    if job.command == "pull":
        pull_result = result.get("result")
        summary = pull_result if isinstance(pull_result, dict) else {}
        preview = bool(job.arguments.get("dry_run"))
        description = (
            "Previewed the requested content without writing files."
            if preview
            else "Pulled the requested course content into local storage."
        )
        fields.append(
            (
                "What happened",
                f"✨ {_number(summary.get('created'))} created\n"
                f"♻️ {_number(summary.get('updated'))} updated\n"
                f"➖ {_number(summary.get('unchanged'))} unchanged\n"
                f"🛡️ {_number(summary.get('conflicts'))} local edits preserved\n"
                f"⏭️ {_number(summary.get('skipped'))} skipped\n"
                f"❌ {_number(summary.get('failed'))} failed",
            )
        )
        warnings = summary.get("warnings")
        if isinstance(warnings, list) and warnings:
            fields.append(("Warnings", f"⚠️ {len(warnings)} item(s) need attention."))
        return description, fields
    scalars = [
        f"**{str(key).replace('_', ' ').title()}:** {_clean(value, 200)}"
        for key, value in result.items()
        if isinstance(value, (str, int, float, bool))
    ]
    fallback = [("Result", "\n".join(scalars[:8]))] if scalars else []
    return "Completed the requested background command.", fallback


def finished_job_embed(
    job: DiscordJob,
    result: dict[str, object] | None,
    root: Path,
) -> discord.Embed:
    timestamp = parse_datetime(job.finished_at) or datetime.now(UTC)
    if job.status == "failed":
        embed = discord.Embed(
            title=f"{job.command.title()} failed",
            description="The command stopped before it could finish.",
            color=ERROR_COLOR,
            timestamp=timestamp,
        )
        embed.add_field(
            name="Reason",
            value=_clean(redact(job.error or "Unknown failure"), 1024),
            inline=False,
        )
    elif job.status in {"cancelled", "interrupted"}:
        embed = discord.Embed(
            title=f"{job.command.title()} {job.status}",
            description=_clean(job.progress or "The command did not complete.", 4096),
            color=WARNING_COLOR,
            timestamp=timestamp,
        )
    else:
        safe = result or {}
        description, fields = _completion_detail(job, safe)
        embed = discord.Embed(
            title=f"{job.command.title()} complete",
            description=description,
            color=SUCCESS_COLOR,
            timestamp=timestamp,
        )
        for name, value in fields:
            embed.add_field(name=name, value=_truncate(value, 1024), inline=False)
        pull_result = safe.get("result")
        summary = pull_result if isinstance(pull_result, dict) else {}
        artifacts = summary.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            relative: list[str] = []
            for raw in artifacts[:5]:
                path = Path(str(raw))
                try:
                    path = path.resolve().relative_to(root.resolve())
                except ValueError:
                    path = Path(path.name)
                relative.append(f"• {_clean(path, 150)}")
            extra = len(artifacts) - len(relative)
            if extra:
                relative.append(f"• …and {extra} more")
            embed.add_field(name="Files", value="\n".join(relative), inline=False)
    started = parse_datetime(job.started_at)
    finished = parse_datetime(job.finished_at)
    if started is not None and finished is not None:
        duration = max(0, int((finished - started).total_seconds()))
        embed.add_field(name="Duration", value=f"{duration // 60}m {duration % 60}s", inline=True)
    embed.add_field(name="Job ID", value=_inline_code(job.job_id), inline=True)
    footer = "Completed by snuetl" if job.status == "completed" else "snuetl background job"
    embed.set_footer(text=footer)
    return embed


def selection_embed(total: int, page: int, selected: int) -> discord.Embed:
    pages = max(1, (total + 24) // 25)
    embed = discord.Embed(
        title="Choose videos to pull",
        description=(
            "Select videos from the menu below. Your selections are preserved when you "
            "move between pages, then tap **Start**."
        ),
        color=INFO_COLOR,
    )
    embed.add_field(name="Selected", value=f"**{selected}** of {total}", inline=True)
    embed.add_field(name="Page", value=f"**{page + 1}** of {pages}", inline=True)
    return embed


def pull_confirmation_embed(arguments: dict[str, object]) -> discord.Embed:
    embed = notice_embed(
        "Confirm forced pull",
        "Force mode can overwrite generated files that were edited locally. "
        "User-created files are not removed.",
        tone="warning",
    )
    preview_job = DiscordJob("preview", "pull", arguments, 0, "queued", "")
    embed.add_field(name="Requested work", value=_request_summary(preview_job), inline=False)
    return embed
