from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    return f"`{str(value or '—').replace('`', 'ˋ')}`"


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
            "The saved SNU eTL session could not be used.\n\n"
            f"**Details:** {detail}\n"
            "**Next:** Run `/snuetl login`, choose email or phone verification, submit the "
            "new code, and then retry the original command."
        )
        tone: Literal["warning", "error"] = "warning"
    elif isinstance(error, ProfileInUse):
        title = "SNU session is busy"
        description = (
            f"Another operation is using the browser profile.\n\n**Details:** {detail}\n"
            "**Next:** Check `/snuetl jobs`; wait for the active browser job or cancel it "
            "before retrying."
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
        description = (
            f"The operation stopped safely. Completed writes, if any, were kept.\n\n"
            f"**Details:** {detail}"
        )
        tone = "warning"
    elif isinstance(error, TimeoutError):
        title = "Command timed out"
        description = (
            f"SNU eTL or Discord took too long to respond.\n\n**Details:** {detail}\n"
            "**Next:** Check `/snuetl jobs` before retrying so the same work is not queued twice."
        )
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
        description = (
            f"Discord rejected or could not complete the request.\n\n**Details:** {detail}\n"
            "**Next:** Wait briefly, retry once, then run `/snuetl doctor` if it continues."
        )
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
    service_enabled = bool(remote.get("service_enabled"))
    discord_configured = bool(remote.get("configured"))
    discord_enabled = bool(remote.get("enabled"))
    ready = auth_ready and service_active and discord_configured and discord_enabled
    embed = discord.Embed(
        title="snuetl status",
        description=(
            ("Ready for Discord commands." if ready else "One or more items need attention.")
            + " This read-only check inspected authentication, catalog state, storage, and "
            "the Discord service. No files were changed."
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
    auth_lines.append(f"Browser mode: **{'headless' if data.get('headless', True) else 'headed'}**")
    embed.add_field(name="Authentication", value="\n".join(auth_lines), inline=True)
    service_manager = str(remote.get("service_manager") or "unknown").replace("_", " ")
    embed.add_field(
        name="Remote control",
        value=(
            f"{'🟢 Online' if service_active else '🔴 Offline'}\n"
            f"Configuration: **{'ready' if discord_configured else 'incomplete'}**\n"
            f"Command access: **{'enabled' if discord_enabled else 'disabled'}**\n"
            f"Startup: **{'enabled' if service_enabled else 'not enabled'}**\n"
            f"Manager: **{_clean(service_manager, 50)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Cached catalog",
        value=(
            f"**{_number(data.get('courses'))}** courses\n"
            f"**{_number(data.get('remote_files'))}** files\n"
            f"**{_number(data.get('articles'))}** articles\n"
            f"**{_number(data.get('assignments'))}** assignments\n"
            f"**{_number(data.get('quizzes'))}** quizzes"
        ),
        inline=True,
    )
    refreshed_at = data.get("catalog_refreshed_at")
    embed.add_field(
        name="Catalog freshness",
        value=(
            f"Last refreshed {discord_timestamp(refreshed_at)}\n"
            f"({discord_timestamp(refreshed_at, 'R')})"
            if refreshed_at
            else "No completed catalog refresh is recorded.\nRun `/snuetl refresh` to build it."
        ),
        inline=True,
    )
    last_sync = data.get("last_sync")
    if isinstance(last_sync, dict):
        finished = last_sync.get("finished_at") or last_sync.get("started_at")
        result = str(last_sync.get("status") or "unknown").replace("_", " ").title()
        detail = (
            f"**{result}** · {discord_timestamp(finished)}\n"
            f"Started {discord_timestamp(last_sync.get('started_at'), 'R')}\n"
            f"{_number(last_sync.get('downloaded'))} downloaded · "
            f"{_number(last_sync.get('updated'))} updated · "
            f"{_number(last_sync.get('unchanged'))} unchanged · "
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
    active_jobs = _number(data.get("active_jobs"))
    queued_jobs = _number(data.get("queued_jobs"))
    embed.add_field(
        name="Background queue",
        value=(
            f"**{active_jobs}** active · **{queued_jobs}** waiting\n"
            + (
                "Use `/snuetl jobs` for IDs, progress, and cancellation."
                if active_jobs or queued_jobs
                else "No background work is pending."
            )
        ),
        inline=False,
    )
    actions: list[str] = []
    if not auth_ready:
        actions.append("• Run `/snuetl login` to renew SNU authentication.")
    if not discord_configured:
        actions.append("• Run `snuetl discord` on the host to finish Discord setup.")
    elif not service_active:
        actions.append("• Run `snuetl discord status` on the host and restart the service.")
    if not refreshed_at:
        actions.append("• Run `/snuetl refresh` to populate the complete catalog cache.")
    if actions:
        embed.add_field(name="Recommended next steps", value="\n".join(actions), inline=False)
    embed.set_footer(text="Read-only status check · use /snuetl doctor for installation details")
    return embed


def doctor_embed(data: dict[str, object]) -> discord.Embed:
    checks = data.get("checks")
    rows = checks if isinstance(checks, list) else []
    failed = [item for item in rows if isinstance(item, dict) and not item.get("ok")]
    required_failed = [item for item in failed if not item.get("optional")]
    optional_failed = [item for item in failed if item.get("optional")]
    remediation = {
        "configuration": "Run `snuetl setup` on the host.",
        "architecture": "Use a supported 64-bit x86-64 or ARM64 host.",
        "chromium": "Run `snuetl setup` to install and validate Chromium.",
        "ffmpeg": "Install FFmpeg, then rerun `/snuetl doctor`.",
        "authentication_state": "Run `/snuetl login` and complete SNU verification.",
        "managed_directory_permissions": "Repair the listed ownership or permissions on the host.",
        "saved_credentials": "Run `snuetl login` locally and opt in to saved credentials.",
        "discord_configuration": "Run `snuetl discord` on the host.",
        "discord_service": "Run `snuetl discord status`, then enable or restart the service.",
        "user_linger": "Run the linger command reported by `snuetl discord status`.",
        "container_restart_policy": "Recreate the container with `./docker-update.sh --force-rebuild`.",
    }
    embed = discord.Embed(
        title="Diagnostics passed" if not required_failed else "Diagnostics found issues",
        description=(
            f"Ran **{len(rows)}** installation checks: **{len(rows) - len(failed)} passed**, "
            f"**{len(required_failed)} required** and **{len(optional_failed)} optional** "
            "checks need attention. No settings or files were changed."
        ),
        color=SUCCESS_COLOR if not required_failed else WARNING_COLOR,
        timestamp=datetime.now(UTC),
    )
    for item in rows[:25]:
        if not isinstance(item, dict):
            continue
        ok = bool(item.get("ok"))
        name = str(item.get("name") or "check").replace("_", " ").title()
        key = str(item.get("name") or "")
        if item.get("optional"):
            name += " · optional"
        detail = _clean(item.get("detail"), 360)
        if not ok and key in remediation:
            detail += f"\n**Next:** {remediation[key]}"
        embed.add_field(
            name=f"{'✅' if ok else '⚠️'} {name}",
            value=_truncate(detail, 500),
            inline=False,
        )
    embed.set_footer(
        text=(
            f"{len(rows) - len(failed)} passed · {len(required_failed)} required issues · "
            f"{len(optional_failed)} optional issues"
        )
    )
    return embed


def catalog_page_count(rows: list[dict[str, object]]) -> int:
    return max(1, (len(rows) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)


def _catalog_item(kind: str, row: dict[str, object]) -> tuple[str, str]:
    course = _clean(row.get("course"), 180)
    if kind == "courses":
        name = course
        lines = [
            f"Semester: **{_clean(row.get('semester'), 80)}**",
            f"Course ID: {_inline_code(row.get('course_id'))}",
        ]
        if row.get("course_code"):
            lines.append(f"Course code: {_inline_code(row.get('course_code'))}")
        if row.get("starts_at") or row.get("ends_at"):
            lines.append(
                f"Dates: {discord_timestamp(row.get('starts_at'), 'd')} → "
                f"{discord_timestamp(row.get('ends_at'), 'd')}"
            )
        value = "\n".join(lines)
    elif kind == "files":
        name = f"📄 {_clean(row.get('name'), 220)}"
        path = row.get("path") or row.get("name")
        lines = [
            f"**{course}**",
            f"Path: {_clean(path, 420)}",
            f"Size: **{human_size(row.get('size_bytes'))}** · ID: {_inline_code(row.get('file_id'))}",
        ]
        if row.get("content_type"):
            lines.append(f"Type: {_inline_code(row.get('content_type'))}")
        if row.get("updated_at"):
            lines.append(
                f"Updated {discord_timestamp(row.get('updated_at'))} "
                f"({discord_timestamp(row.get('updated_at'), 'R')})"
            )
        value = "\n".join(lines)
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
        elif row.get("updated_at"):
            dates.append(f"Updated {discord_timestamp(row.get('updated_at'))}")
        content_type = str(row.get("content_type") or kind.rstrip("s")).replace("_", " ")
        value = (
            f"**{course}**\n"
            f"Type: **{_clean(content_type.title(), 80)}** · "
            f"ID: {_inline_code(row.get('content_id'))}"
            + ("\n" + "\n".join(dates) if dates else "")
        )
    return _truncate(name, 220), _truncate(value, 850)


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
            f"Found **{len(rows)}** item{'s' if len(rows) != 1 else ''}{scope}. Fetched current "
            "metadata from SNU eTL and refreshed the corresponding local cache. Nothing was downloaded."
            if rows
            else f"No {kind} were found{scope}. Try `/snuetl refresh` if the result is unexpected."
        ),
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    start = page * CATALOG_PAGE_SIZE
    for row in rows[start : start + CATALOG_PAGE_SIZE]:
        name, value = _catalog_item(kind, row)
        embed.add_field(name=name, value=value, inline=False)
    end = min(start + CATALOG_PAGE_SIZE, len(rows))
    shown = f"items {start + 1}–{end}" if rows else "no items"
    embed.set_footer(
        text=f"Page {page + 1} of {pages} · {shown} · {len(rows)} total · read-only listing"
    )
    return embed


def jobs_embed(jobs: list[DiscordJob]) -> discord.Embed:
    active = sum(job.status in {"running", "cancel_requested"} for job in jobs)
    waiting = sum(job.status == "queued" for job in jobs)
    failed = sum(job.status in {"failed", "interrupted"} for job in jobs)
    embed = discord.Embed(
        title="Recent jobs",
        description=(
            f"Showing the latest **{len(jobs)}** background command(s): **{active} active**, "
            f"**{waiting} waiting**, and **{failed} failed/interrupted**. Use `/snuetl cancel` "
            "with a job ID to stop queued or running work safely."
            if jobs
            else "No Discord jobs have been queued yet. Start with `/snuetl status`, "
            "`/snuetl refresh`, or `/snuetl sync`."
        ),
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    displayed = jobs[:7]
    for job in displayed:
        icon = _STATUS_ICONS.get(job.status, "•")
        status = job.status.replace("_", " ").title()
        timing = f"Queued {discord_timestamp(job.created_at, 'R')}"
        if job.started_at:
            timing += f" · started {discord_timestamp(job.started_at, 'R')}"
        if job.finished_at:
            timing += f" · finished {discord_timestamp(job.finished_at, 'R')}"
        detail = _clean(job.error if job.status == "failed" else job.progress, 300)
        value = (
            f"ID: {_inline_code(job.job_id)}\n{request_summary(job)}\n"
            f"{timing}\n**Latest:** {detail if detail != '—' else 'No progress update yet.'}"
        )
        embed.add_field(
            name=f"{icon} {job.command.title()} · {status}",
            value=value,
            inline=False,
        )
    embed.set_footer(
        text=f"Showing {len(displayed)} of {len(jobs)} recent · terminal states are retained for history"
    )
    return embed


def request_summary(job: DiscordJob) -> str:
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
        lines.append("Credential source: **saved owner-only credential file**")
    elif job.command == "sync":
        lines.extend(
            (
                "Content: **new and changed course files**",
                "Scope: **all active, non-excluded courses**",
            )
        )
    elif job.command == "refresh":
        lines.extend(
            (
                "Content: **complete metadata catalog**",
                "Includes: **courses, files, articles, assignments, quizzes, and videos**",
            )
        )
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
    embed.add_field(name="Requested work", value=request_summary(job), inline=False)
    embed.add_field(name="Job ID", value=_inline_code(job.job_id), inline=True)
    embed.add_field(name="Requested by", value=_clean(requester, 100), inline=True)
    embed.add_field(
        name="Queue controls",
        value=f"Track with `/snuetl jobs` · cancel with `/snuetl cancel {job.job_id}`",
        inline=False,
    )
    embed.set_footer(text=f"Queued {discord_timestamp(job.created_at, 'R')} · waiting for the worker")
    return embed


def progress_job_embed(job: DiscordJob, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{job.command.title()} in progress",
        description=f"**Current activity**\n{_clean(message, 3600)}",
        color=INFO_COLOR,
        timestamp=datetime.now(UTC),
    )
    embed.add_field(name="Requested work", value=request_summary(job), inline=False)
    embed.add_field(
        name="Timing",
        value=(
            f"Queued {discord_timestamp(job.created_at, 'R')}"
            + (
                f" · started {discord_timestamp(job.started_at, 'R')}"
                if job.started_at
                else ""
            )
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Job {job.job_id} · updates are automatic · use /snuetl cancel to stop safely"
    )
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
        fields.append(
            (
                "Result meaning",
                "Downloaded = first local copy · updated = remote revision replaced a tracked "
                "copy · unchanged = already current · failed = course-level errors.",
            )
        )
        return description, fields
    if job.command == "refresh":
        description = (
            "Refreshed the local metadata catalog from SNU eTL. This updates listings and "
            "autocomplete data; it does not download course content."
        )
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
        landing = str(result.get("landing_url") or "")
        host = urlsplit(landing).hostname if landing else None
        fields.append(
            (
                "Session",
                f"Authenticated: **yes**\nLanding host: {_inline_code(host or 'SNU eTL')}\n"
                "The reusable browser session was saved for later commands.",
            )
        )
        return "Renewed and saved the SNU eTL browser session successfully.", fields
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
        plan = result.get("plan")
        if isinstance(plan, dict):
            fields.append(
                (
                    "Planned scope",
                    f"🏫 {_number(plan.get('courses'))} courses\n"
                    f"📄 {_number(plan.get('files'))} files\n"
                    f"📣 {_number(plan.get('articles'))} articles\n"
                    f"📑 {_number(plan.get('syllabus_files'))} syllabus files\n"
                    f"🎬 {_number(plan.get('video_items'))} videos"
                    + (
                        f" ({human_size(plan.get('known_video_bytes'))} known size)"
                        if _number(plan.get("video_items"))
                        else ""
                    ),
                )
            )
        warnings = summary.get("warnings")
        if isinstance(warnings, list) and warnings:
            lines: list[str] = []
            for warning in warnings[:4]:
                if isinstance(warning, dict):
                    code = str(warning.get("code") or "WARNING").replace("_", " ").title()
                    message = _clean(redact(warning.get("message") or "Needs attention"), 180)
                    lines.append(f"• **{code}:** {message}")
                else:
                    lines.append(f"• {_clean(redact(warning), 200)}")
            if len(warnings) > len(lines):
                lines.append(f"• …and {len(warnings) - len(lines)} more warning(s)")
            fields.append((f"Warnings ({len(warnings)})", "\n".join(lines)))
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
    completion_warning = False
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
        embed.add_field(name="Requested work", value=request_summary(job), inline=False)
        if job.progress:
            embed.add_field(
                name="Last recorded activity",
                value=_clean(redact(job.progress), 700),
                inline=False,
            )
        embed.add_field(
            name="What to do next",
            value=(
                "Run `/snuetl doctor` for installation checks and `/snuetl status` for session "
                "state. If authentication is mentioned, run `/snuetl login`; otherwise retry "
                "once and use this job ID when checking server logs."
            ),
            inline=False,
        )
    elif job.status in {"cancelled", "interrupted"}:
        embed = discord.Embed(
            title=f"{job.command.title()} {job.status}",
            description=_clean(job.progress or "The command did not complete.", 4096),
            color=WARNING_COLOR,
            timestamp=timestamp,
        )
        embed.add_field(name="Requested work", value=request_summary(job), inline=False)
        embed.add_field(
            name="Result",
            value="Completed writes were kept; unfinished items can be retried safely.",
            inline=False,
        )
    else:
        safe = result or {}
        description, fields = _completion_detail(job, safe)
        pull_result = safe.get("result")
        summary = pull_result if isinstance(pull_result, dict) else {}
        partial = bool(
            _number(safe.get("failed"))
            or _number(summary.get("failed"))
            or _number(summary.get("conflicts"))
            or summary.get("warnings")
        )
        completion_warning = partial
        embed = discord.Embed(
            title=f"{job.command.title()} complete",
            description=description,
            color=WARNING_COLOR if partial else SUCCESS_COLOR,
            timestamp=timestamp,
        )
        embed.add_field(name="Requested work", value=request_summary(job), inline=False)
        for name, value in fields:
            embed.add_field(name=name, value=_truncate(value, 1024), inline=False)
        artifacts = summary.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            relative: list[str] = []
            for raw in artifacts[:8]:
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
        if job.command == "pull" and artifacts:
            embed.add_field(
                name="Path convention",
                value="The listed file paths are relative to the configured managed storage root.",
                inline=False,
            )
    started = parse_datetime(job.started_at)
    finished = parse_datetime(job.finished_at)
    if started is not None and finished is not None:
        duration = max(0, int((finished - started).total_seconds()))
        embed.add_field(name="Duration", value=f"{duration // 60}m {duration % 60}s", inline=True)
    embed.add_field(name="Job ID", value=_inline_code(job.job_id), inline=True)
    footer = (
        "Completed by snuetl · review warnings before retrying"
        if job.status == "completed" and completion_warning
        else "Completed by snuetl"
        if job.status == "completed"
        else "snuetl background job"
    )
    embed.set_footer(text=footer)
    return embed


def selection_embed(
    total: int,
    page: int,
    selected: int,
    arguments: dict[str, object] | None = None,
) -> discord.Embed:
    pages = max(1, (total + 24) // 25)
    start = page * 25
    end = min(start + 25, total)
    embed = discord.Embed(
        title="Choose videos to pull",
        description=(
            f"Showing videos **{start + 1}–{end}** of **{total}**. Select videos from the "
            "menu; selections are preserved across pages. Use **Review** to inspect the full "
            "selection, then **Start** to queue the download. This control expires after 10 minutes."
        ),
        color=INFO_COLOR,
    )
    embed.add_field(name="Selected", value=f"**{selected}** of {total}", inline=True)
    embed.add_field(name="Page", value=f"**{page + 1}** of {pages}", inline=True)
    if arguments is not None:
        preview_job = DiscordJob("preview", "pull", arguments, 0, "queued", "")
        embed.add_field(name="Download settings", value=request_summary(preview_job), inline=False)
    embed.set_footer(text="Selections are private to the person who opened this control")
    return embed


def video_review_embed(
    videos: list[dict[str, object]],
    selected: set[str],
    arguments: dict[str, object],
) -> discord.Embed:
    chosen = [item for item in videos if str(item.get("video_id")) in selected]
    embed = notice_embed(
        "Review video selection",
        f"Selected **{len(chosen)}** of **{len(videos)}** available videos. Return to the "
        "selection control to change choices or start the job.",
    )
    preview_job = DiscordJob("preview", "pull", arguments, 0, "queued", "")
    embed.add_field(name="Download settings", value=request_summary(preview_job), inline=False)
    lines = [
        f"• **{_clean(item.get('title') or item.get('video_id'), 130)}** — "
        f"{_clean(item.get('course_name') or 'Unknown course', 100)}"
        for item in chosen[:12]
    ]
    if len(chosen) > len(lines):
        lines.append(f"• …and {len(chosen) - len(lines)} more")
    embed.add_field(
        name="Selected videos",
        value="\n".join(lines) if lines else "No videos are selected yet.",
        inline=False,
    )
    return embed


def queue_receipt_embed(job: DiscordJob) -> discord.Embed:
    embed = notice_embed(
        f"{job.command.title()} queued",
        "The job was accepted and a persistent channel message will update automatically "
        "with its current activity and final result.",
        tone="success",
    )
    embed.add_field(name="Requested work", value=request_summary(job), inline=False)
    embed.add_field(name="Job ID", value=_inline_code(job.job_id), inline=True)
    embed.add_field(name="Queue state", value="Waiting for the background worker", inline=True)
    embed.add_field(
        name="Track or stop",
        value=f"Use `/snuetl jobs` or `/snuetl cancel {job.job_id}`.",
        inline=False,
    )
    return embed


def pull_confirmation_embed(arguments: dict[str, object]) -> discord.Embed:
    embed = notice_embed(
        "Confirm forced pull",
        "Force mode can overwrite generated files that were edited locally. "
        "User-created files are not removed.",
        tone="warning",
    )
    preview_job = DiscordJob("preview", "pull", arguments, 0, "queued", "")
    embed.add_field(name="Requested work", value=request_summary(preview_job), inline=False)
    embed.add_field(
        name="What force changes",
        value=(
            "Tracked generated artifacts may be replaced even when their local checksum changed. "
            "Untracked user-created files and directories are still left untouched."
        ),
        inline=False,
    )
    embed.set_footer(text="Review the scope above before confirming · control expires in 10 minutes")
    return embed
