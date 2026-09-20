from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .canvas_api import live_data, token_status
from .config import Config
from .discord_jobs import RemoteJobQueue
from .discord_operations import status_data
from .logging_utils import redact, redacted_exc_info
from .state import DiscordJob, StateStore
from .telegram_api import TelegramAPI, TelegramAPIError, load_telegram_token

LOGGER = logging.getLogger(__name__)
KOREA = ZoneInfo("Asia/Seoul")
HELP = (
    "SNUETL commands:\n"
    "/status — account and synchronization status\n"
    "/upcoming — incomplete work due in the next 30 days\n"
    "/missing — overdue missing submissions\n"
    "/activity [course] — new course activity\n"
    "/announcements [course] — recent announcements\n"
    "/modules [course] — module progress\n"
    "/feedback [course] — recent grades and instructor comments\n"
    "/dashboard [course] — course workload and grades\n"
    "/sync — download changed course files\n"
    "/jobs — recent Telegram jobs\n"
    "/cancel <job ID> — cancel a queued or running job"
)


def _local_due(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "No due date"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return value
        return parsed.astimezone(KOREA).strftime("%b %-d, %H:%M KST")
    except ValueError:
        return value


def format_items(label: str, rows: list[dict[str, Any]], *, limit: int = 20) -> str:
    if not rows:
        return f"{label}: none."
    sorted_rows = sorted(rows, key=lambda item: str(item.get("due_at") or "9999"))
    lines = [f"{label}: {len(rows)}"]
    for item in sorted_rows[:limit]:
        title = str(item.get("title") or "Untitled").replace("\n", " ")
        lines.append(f"• {title} — {_local_due(item.get('due_at'))}")
        if item.get("course_id") is not None:
            lines.append(f"  Course {item['course_id']}")
        if item.get("url"):
            lines.append(f"  {item['url']}")
    if len(rows) > limit:
        command = "upcoming" if label.startswith("Upcoming") else "missing"
        lines.append(f"…and {len(rows) - limit} more. Run snuetl {command} locally for all items.")
    return "\n".join(lines)


def format_digest(upcoming: list[dict[str, Any]], missing: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        (
            "SNUETL daily digest · 09:00 KST",
            format_items("Upcoming (7 days)", upcoming, limit=10),
            format_items("Missing submissions", missing, limit=10),
        )
    )


def format_canvas_view(kind: str, rows: list[dict[str, Any]], *, limit: int = 12) -> str:
    if not rows:
        return f"{kind.capitalize()}: none."
    lines = [f"{kind.capitalize()}: {len(rows)}"]
    for row in rows[:limit]:
        title = str(
            row.get("title") or row.get("item_title") or row.get("module_name")
            or row.get("course_name") or "Untitled"
        )
        title = title.replace("\n", " ")
        course = None if kind == "dashboard" else row.get("course_name") or row.get("course_id")
        lines.append(f"• {title}" + (f" · {course}" if course else ""))
        if kind == "dashboard":
            lines.append(
                f"  Due soon: {row.get('due_soon', 0)}; missing: {row.get('missing', 0)}"
            )
            if row.get("current_grade") is not None or row.get("current_score") is not None:
                grade = row.get("current_grade")
                grade_text = str(grade) if grade is not None else "—"
                score = row.get("current_score")
                lines.append(f"  Grade: {grade_text}" + (f" ({score}%)" if score is not None else ""))
        elif kind == "announcements" and row.get("summary"):
            lines.append(f"  {str(row['summary'])[:300]}")
        elif kind == "feedback":
            score = row.get("score")
            points = row.get("points_possible")
            if score is not None:
                lines.append(f"  Score: {score}" + (f" / {points}" if points is not None else ""))
            for comment in (row.get("comments") or [])[:2]:
                if isinstance(comment, dict) and comment.get("text"):
                    lines.append(f"  Comment: {str(comment['text'])[:300]}")
            for criterion in (row.get("rubric") or [])[:2]:
                if isinstance(criterion, dict):
                    lines.append(
                        f"  Rubric: {criterion.get('criterion') or 'Criterion'}"
                        f" · {criterion.get('points') if criterion.get('points') is not None else 'unscored'}"
                    )
        elif kind == "modules":
            if row.get("module_name"):
                lines.append(f"  Module: {row['module_name']}")
            state = row.get("module_state")
            completed = row.get("item_completed")
            if state or completed is not None:
                lines.append(f"  State: {state or 'unknown'}; item complete: {completed}")
        date_value = (
            row.get("due_at")
            or row.get("next_due_at")
            or row.get("posted_at")
            or row.get("graded_at")
        )
        if date_value:
            lines.append(f"  {_local_due(date_value)}")
        if row.get("url"):
            lines.append(f"  {row['url']}")
    if len(rows) > limit:
        lines.append(f"…and {len(rows) - limit} more. Run snuetl {kind} locally for all items.")
    return "\n".join(lines)


def _authorized(config: Config, message: dict[str, Any]) -> bool:
    chat = message.get("chat")
    sender = message.get("from")
    return bool(
        isinstance(chat, dict)
        and isinstance(sender, dict)
        and chat.get("type") == "private"
        and not sender.get("is_bot")
        and chat.get("id") == config.telegram.chat_id
        and sender.get("id") == config.telegram.owner_id
    )


class TelegramBot:
    def __init__(self, config: Config, api: TelegramAPI):
        self.config = config
        self.api = api
        self.queue = RemoteJobQueue(
            config,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            gateway="telegram",
        )
        self._digest_retry_at = 0.0

    async def _on_progress(self, _job: DiscordJob, _message: str) -> None:
        # Progress is persisted and available in /jobs. Avoid a message for every file.
        return

    async def _on_finished(self, job: DiscordJob, result: dict[str, object] | None) -> None:
        chat_id = job.channel_id
        if chat_id is None or chat_id != self.config.telegram.chat_id:
            return
        if job.status == "completed" and result is not None:
            summary = ", ".join(
                f"{key}={result[key]}"
                for key in ("courses", "downloaded", "updated", "failed")
                if key in result
            )
            message = f"Sync {job.job_id} completed. {summary}"
        elif job.status == "cancelled":
            message = f"Sync {job.job_id} cancelled."
        else:
            message = f"Sync {job.job_id} {job.status}: {job.error or job.progress or 'No details'}"
        await self.api.send_message(chat_id, message)

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not _authorized(self.config, message):
            return
        chat_id = self.config.telegram.chat_id
        assert chat_id is not None
        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return
        command, _, argument = text.partition(" ")
        command = command.split("@", maxsplit=1)[0].casefold()
        if command in {"/start", "/help"}:
            await self.api.send_message(chat_id, HELP)
        elif command == "/status":
            data = await asyncio.to_thread(status_data, self.config)
            api_state = token_status(self.config)
            last = data.get("last_sync")
            last_text = (
                str(last.get("finished_at") or last.get("started_at") or "never")
                if isinstance(last, dict)
                else "never"
            )
            await self.api.send_message(
                chat_id,
                "\n".join(
                    (
                        f"Authentication: {'ready' if data['authentication_ready'] else 'login required'}",
                        f"Canvas API: {'ready' if api_state.get('ready') else 'run snuetl api setup'}",
                        f"Courses: {data['courses']}; downloaded files: {data['downloaded_files']}",
                        f"Last sync: {last_text}",
                    )
                ),
            )
        elif command in {"/upcoming", "/missing"}:
            kind = command[1:]
            rows = await asyncio.to_thread(live_data, self.config, kind)
            await self.api.send_message(chat_id, format_items(kind.capitalize(), rows))
        elif command in {"/activity", "/announcements", "/modules", "/feedback", "/dashboard"}:
            kind = command[1:]
            course = argument.strip() or None
            rows = await asyncio.to_thread(live_data, self.config, kind, course=course)
            await self.api.send_message(chat_id, format_canvas_view(kind, rows))
        elif command == "/sync":
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                return
            job_id = f"t{update_id:x}"
            try:
                job = self.queue.enqueue(
                    "sync", {}, self.config.telegram.owner_id or 0, chat_id, job_id=job_id
                )
            except sqlite3.IntegrityError:
                with StateStore(self.config.database_path) as store:
                    found = store.get_discord_job(job_id)
                if found is None:
                    raise
                job = found
            await self.api.send_message(
                chat_id, f"Sync queued as {job.job_id}. Use /jobs for progress."
            )
        elif command == "/jobs":
            with StateStore(self.config.database_path) as store:
                jobs = store.list_remote_jobs("telegram", limit=10)
            if not jobs:
                await self.api.send_message(chat_id, "No Telegram jobs yet.")
            else:
                await self.api.send_message(
                    chat_id,
                    "Recent jobs:\n"
                    + "\n".join(
                        f"{job.job_id} · {job.command} · {job.status} · {(job.progress or '')[:80]}"
                        for job in jobs
                    ),
                )
        elif command == "/cancel":
            job_id = argument.strip().split(maxsplit=1)[0] if argument.strip() else ""
            with StateStore(self.config.database_path) as store:
                target_job = store.get_discord_job(job_id) if job_id else None
            if (
                target_job is None
                or target_job.gateway != "telegram"
                or target_job.requester_id != self.config.telegram.owner_id
            ):
                await self.api.send_message(
                    chat_id, "Telegram job not found. Use /jobs to see IDs."
                )
            elif self.queue.cancel(job_id):
                await self.api.send_message(chat_id, f"Cancellation requested for {job_id}.")
            else:
                await self.api.send_message(chat_id, f"Job {job_id} has already finished.")
        else:
            await self.api.send_message(chat_id, HELP)

    async def poll(self) -> None:
        while True:
            try:
                with StateStore(self.config.database_path) as store:
                    offset = store.telegram_poll_offset()
                updates = await self.api.get_updates(offset=offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int):
                        continue
                    with StateStore(self.config.database_path) as store:
                        seen = store.telegram_update_seen(update_id)
                    if not seen:
                        try:
                            await self.handle_update(update)
                        except TelegramAPIError:
                            raise
                        except Exception as exc:
                            LOGGER.error(
                                "Telegram command failed: %s",
                                redact(exc),
                                exc_info=redacted_exc_info(exc),
                            )
                            message = update.get("message")
                            if isinstance(message, dict) and _authorized(self.config, message):
                                await self.api.send_message(
                                    self.config.telegram.chat_id or 0,
                                    f"SNUETL command failed: {redact(exc)}",
                                )
                    with StateStore(self.config.database_path) as store:
                        store.record_telegram_update(update_id)
            except asyncio.CancelledError:
                raise
            except TelegramAPIError as exc:
                LOGGER.warning("Telegram polling error: %s", exc)
                await asyncio.sleep(5)

    async def send_digest_if_due(self, now: datetime) -> None:
        now = now.astimezone(KOREA)
        day = now.date().isoformat()
        with StateStore(self.config.database_path) as store:
            sent_date, error_date = store.telegram_digest_dates()
        due = now.hour >= 9 and sent_date != day and self.config.telegram.alerts_enabled
        if not due or asyncio.get_running_loop().time() < self._digest_retry_at:
            return
        try:
            if not token_status(self.config).get("ready"):
                raise RuntimeError("Canvas API access is unavailable; run snuetl api setup")
            upcoming = await asyncio.to_thread(
                live_data,
                self.config,
                "upcoming",
                start=now.date(),
                end=now.date() + timedelta(days=7),
            )
            missing = await asyncio.to_thread(live_data, self.config, "missing")
            await self.api.send_message(
                self.config.telegram.chat_id or 0, format_digest(upcoming, missing)
            )
            with StateStore(self.config.database_path) as store:
                store.mark_telegram_digest(day)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("Telegram digest failed: %s", redact(exc))
            if error_date != day:
                try:
                    await self.api.send_message(
                        self.config.telegram.chat_id or 0,
                        f"SNUETL daily digest unavailable: {redact(exc)}",
                    )
                    with StateStore(self.config.database_path) as store:
                        store.mark_telegram_digest(day, error=True)
                except TelegramAPIError:
                    pass
            self._digest_retry_at = asyncio.get_running_loop().time() + 3600

    async def digest(self) -> None:
        while True:
            await self.send_digest_if_due(datetime.now(KOREA))
            await asyncio.sleep(60)


async def _run(config: Config, token: str) -> None:
    async with TelegramAPI(token) as api:
        await api.get_me()
        await api.require_polling()
        await api.set_commands(config.telegram.chat_id or 0)
        bot = TelegramBot(config, api)
        bot.queue.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signum, stop.set)
        tasks = [asyncio.create_task(bot.poll()), asyncio.create_task(bot.digest())]
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _pending = await asyncio.wait(
                [stop_task, *tasks], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not stop_task:
                    task.result()
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(stop_task, *tasks, return_exceptions=True)
            await bot.queue.stop()


def run_telegram_bot(config: Config) -> None:
    token = load_telegram_token(config)
    if not token or not config.telegram.enabled or not config.telegram.configured:
        raise RuntimeError("Telegram is not enabled or paired; run snuetl telegram setup")
    asyncio.run(_run(config, token))
