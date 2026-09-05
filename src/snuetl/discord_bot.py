from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from .config import Config
from .discord_access import authorize_discord_interaction
from .discord_config import load_discord_token
from .discord_jobs import DiscordJobQueue
from .discord_operations import LoginCallbacks, catalog_rows, doctor_data, status_data
from .discord_ui import merge_video_page_selection, video_page, video_page_count
from .logging_utils import redact
from .puller import discover_pull_plan, plan_data
from .state import DiscordJob, StateStore

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE = 1900


class VerificationBroker:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._condition = threading.Condition()
        self._codes: list[str] = []
        self._closed = False

    def submit(self, code: str) -> bool:
        value = code.strip()
        if not value:
            return False
        with self._condition:
            if self._closed:
                return False
            self._codes.append(value)
            self._condition.notify()
        return True

    def provide(self) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        with self._condition:
            while not self._codes and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for a Discord verification code")
                self._condition.wait(remaining)
            if self._closed:
                raise RuntimeError("Discord verification request was closed")
            return self._codes.pop(0)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._codes.clear()
            self._condition.notify_all()


def _short_json(value: object) -> str:
    body = str(redact(json.dumps(value, ensure_ascii=False, indent=2, default=str)))
    if len(body) > MAX_MESSAGE - 10:
        body = body[: MAX_MESSAGE - 40] + "\n… output truncated"
    return f"```json\n{body}\n```"


def _job_summary(job: DiscordJob, result: dict[str, object] | None, root: Path) -> str:
    title = f"snuetl {job.command} `{job.job_id}` — {job.status}"
    if job.status == "failed":
        return f"{title}\n{job.error or 'Unknown failure'}"[:MAX_MESSAGE]
    if job.status in {"cancelled", "interrupted"}:
        return f"{title}\n{job.progress or job.status}"[:MAX_MESSAGE]
    if result is None:
        return f"{title}\n{job.progress or ''}"[:MAX_MESSAGE]
    safe = dict(result)
    pull_result = safe.get("result")
    if isinstance(pull_result, dict):
        pull_result = dict(pull_result)
        artifacts = pull_result.get("artifacts")
        if isinstance(artifacts, list):
            relative: list[str] = []
            for raw in artifacts[:20]:
                try:
                    relative.append(str(Path(str(raw)).resolve().relative_to(root.resolve())))
                except ValueError:
                    relative.append(Path(str(raw)).name)
            pull_result["artifacts"] = relative
        safe["result"] = pull_result
        safe.pop("managed_root", None)
    return (title + "\n" + _short_json(safe))[:MAX_MESSAGE]


def run_discord_bot(config: Config) -> None:
    token = load_discord_token(config)
    if token is None:
        raise RuntimeError("Discord bot token is missing; run 'snuetl discord setup'")
    if not config.discord.enabled or not config.discord.configured:
        raise RuntimeError("Discord is not enabled and fully configured")
    guild = discord.Object(id=config.discord.guild_id)
    intents = discord.Intents.none()
    intents.guilds = True

    class Bot(discord.Client):
        def __init__(self) -> None:
            super().__init__(intents=intents, application_id=config.discord.application_id)
            self.tree = app_commands.CommandTree(self)
            self.last_edits: dict[str, float] = {}
            self.brokers: dict[str, VerificationBroker] = {}
            self.queue = DiscordJobQueue(
                config,
                on_progress=self.on_job_progress,
                on_finished=self.on_job_finished,
                login_callbacks=self.login_callbacks,
            )

        async def setup_hook(self) -> None:
            self.tree.add_command(group, guild=guild)
            await self.tree.sync(guild=guild)
            self.queue.start()

        async def on_ready(self) -> None:
            for connected_guild in self.guilds:
                if connected_guild.id == config.discord.guild_id:
                    continue
                self.tree.clear_commands(guild=connected_guild)
                try:
                    await self.tree.sync(guild=connected_guild)
                except discord.HTTPException as exc:
                    LOGGER.warning(
                        "Could not remove Discord commands from unbound server %s: %s",
                        connected_guild.id,
                        redact(exc),
                    )

        async def close(self) -> None:
            for broker in self.brokers.values():
                broker.close()
            await self.queue.stop()
            await super().close()

        def login_callbacks(self, job: DiscordJob) -> LoginCallbacks | None:
            if job.command != "login":
                return None
            broker = self.brokers.get(job.job_id)
            if broker is None:
                raise RuntimeError("Discord login controls expired; queue login again")
            method = str(job.arguments.get("method", "email"))
            return LoginCallbacks(method=method, code_provider=broker.provide)

        async def _job_message(self, job: DiscordJob):
            if job.channel_id is None or job.message_id is None:
                return None
            channel = self.get_channel(job.channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(job.channel_id)
                except discord.HTTPException:
                    return None
            try:
                return await channel.fetch_message(job.message_id)
            except (discord.HTTPException, AttributeError):
                return None

        async def on_job_progress(self, job: DiscordJob, message: str) -> None:
            now = time.monotonic()
            if now - self.last_edits.get(job.job_id, 0) < 4:
                return
            self.last_edits[job.job_id] = now
            target = await self._job_message(job)
            if target is not None:
                await target.edit(
                    content=f"snuetl {job.command} `{job.job_id}` — {message}"[:MAX_MESSAGE],
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        async def on_job_finished(self, job: DiscordJob, result: dict[str, object] | None) -> None:
            broker = self.brokers.pop(job.job_id, None)
            if broker is not None:
                broker.close()
            target = await self._job_message(job)
            if target is not None:
                await target.edit(
                    content=_job_summary(job, result, config.download_dir),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    bot = Bot()

    async def guard(interaction: discord.Interaction) -> bool:
        decision = authorize_discord_interaction(
            config.discord,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
        )
        if decision.allowed:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(decision.reason, ephemeral=True)
        return False

    async def owns_control(interaction: discord.Interaction, owner_id: int) -> bool:
        if not await guard(interaction):
            return False
        if interaction.user.id == owner_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "This private control belongs to the owner who opened it.", ephemeral=True
            )
        return False

    async def course_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        decision = authorize_discord_interaction(
            config.discord,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
        )
        if not decision.allowed or not config.database_path.exists():
            return []
        folded = current.casefold()
        with StateStore(config.database_path) as store:
            rows = store.db.execute(
                "SELECT remote_id, name FROM courses WHERE active=1 ORDER BY name"
            ).fetchall()
        return [
            app_commands.Choice(name=str(row["name"])[:100], value=str(row["remote_id"]))
            for row in rows
            if not folded
            or folded in str(row["name"]).casefold()
            or folded in str(row["remote_id"]).casefold()
        ][:25]

    async def semester_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        decision = authorize_discord_interaction(
            config.discord,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
        )
        if not decision.allowed or not config.database_path.exists():
            return []
        folded = current.casefold()
        with StateStore(config.database_path) as store:
            rows = store.db.execute(
                "SELECT DISTINCT semester_code, semester_name FROM semesters ORDER BY semester_code DESC"
            ).fetchall()
        return [
            app_commands.Choice(
                name=f"{row['semester_code']} — {row['semester_name']}"[:100],
                value=str(row["semester_code"]),
            )
            for row in rows
            if not folded
            or folded in str(row["semester_code"]).casefold()
            or folded in str(row["semester_name"]).casefold()
        ][:25]

    async def post_job(interaction: discord.Interaction, job: DiscordJob) -> None:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            raise RuntimeError("The configured Discord channel is unavailable")
        message = await channel.send(
            f"snuetl {job.command} `{job.job_id}` — queued by {interaction.user.mention}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        with StateStore(config.database_path) as store:
            store.update_discord_job(job.job_id, message_id=message.id)

    async def enqueue(
        interaction: discord.Interaction, command: str, arguments: dict[str, object]
    ) -> DiscordJob:
        assert interaction.channel_id is not None
        job = bot.queue.enqueue(command, arguments, interaction.user.id, interaction.channel_id)
        try:
            await post_job(interaction, job)
        except Exception:
            bot.queue.cancel(job.job_id)
            raise
        return job

    group = app_commands.Group(name="snuetl", description="Control this snuetl installation")

    @group.command(name="status", description="Show local authentication, catalog, and sync state")
    async def status_command(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        await interaction.response.send_message(_short_json(status_data(config)), ephemeral=True)

    @group.command(name="doctor", description="Check this snuetl installation")
    async def doctor_command(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        value = await asyncio.to_thread(doctor_data, config)
        await interaction.followup.send(_short_json(value), ephemeral=True)

    async def send_catalog(
        interaction: discord.Interaction, kind: str, course: str | None = None
    ) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await asyncio.to_thread(catalog_rows, config, kind, course)
        await interaction.followup.send(
            _short_json({"count": len(rows), "items": rows}), ephemeral=True
        )

    @group.command(name="courses", description="List active courses")
    async def courses_command(interaction: discord.Interaction) -> None:
        await send_catalog(interaction, "courses")

    @group.command(name="files", description="List course files without downloading")
    @app_commands.autocomplete(course=course_autocomplete)
    async def files_command(interaction: discord.Interaction, course: str | None = None) -> None:
        await send_catalog(interaction, "files", course)

    @group.command(name="articles", description="List announcements and course pages")
    @app_commands.autocomplete(course=course_autocomplete)
    async def articles_command(interaction: discord.Interaction, course: str | None = None) -> None:
        await send_catalog(interaction, "articles", course)

    @group.command(name="assignments", description="List assignments and due dates")
    @app_commands.autocomplete(course=course_autocomplete)
    async def assignments_command(
        interaction: discord.Interaction, course: str | None = None
    ) -> None:
        await send_catalog(interaction, "assignments", course)

    async def enqueue_simple(interaction: discord.Interaction, command: str) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        job = await enqueue(interaction, command, {})
        await interaction.followup.send(f"Queued `{job.job_id}`.", ephemeral=True)

    @group.command(name="refresh", description="Refresh the complete cached catalog")
    async def refresh_command(interaction: discord.Interaction) -> None:
        await enqueue_simple(interaction, "refresh")

    @group.command(name="sync", description="Download new and changed course files")
    async def sync_command(interaction: discord.Interaction) -> None:
        await enqueue_simple(interaction, "sync")

    class ExpiringView(discord.ui.View):
        def __init__(self, *, timeout: float = 600) -> None:
            super().__init__(timeout=timeout)
            self.message = None

        async def on_timeout(self) -> None:
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True
            if self.message is not None:
                with suppress(discord.HTTPException):
                    await self.message.edit(view=self)

    class VideoSelect(discord.ui.Select):
        def __init__(self, view: VideoSelectionView) -> None:
            start = view.page * 25
            values = video_page(view.videos, view.page)
            options = [
                discord.SelectOption(
                    label=str(item.get("title") or item["video_id"])[:100],
                    value=str(item["video_id"]),
                    description=str(item.get("course_name") or "")[:100] or None,
                    default=str(item["video_id"]) in view.selected,
                )
                for item in values
            ]
            super().__init__(
                placeholder=f"Videos {start + 1}–{start + len(values)}",
                min_values=0,
                max_values=len(options),
                options=options,
                custom_id=f"snuetl:videos:{view.nonce}:{view.page}",
            )

        async def callback(self, interaction: discord.Interaction) -> None:
            parent = self.view
            assert isinstance(parent, VideoSelectionView)
            if not await owns_control(interaction, parent.owner_id):
                return
            merge_video_page_selection(
                parent.selected,
                video_page(parent.videos, parent.page),
                list(self.values),
            )
            await parent.render(interaction)

    class VideoSelectionView(ExpiringView):
        def __init__(
            self,
            arguments: dict[str, object],
            videos: list[dict[str, object]],
            owner_id: int,
        ) -> None:
            super().__init__(timeout=600)
            self.arguments = arguments
            self.videos = videos
            self.owner_id = owner_id
            self.page = 0
            self.selected: set[str] = set()
            self.nonce = secrets.token_hex(6)
            self.rebuild()

        def rebuild(self) -> None:
            self.clear_items()
            self.add_item(VideoSelect(self))

            previous = discord.ui.Button(label="Previous", disabled=self.page == 0)
            next_button = discord.ui.Button(
                label="Next", disabled=(self.page + 1) * 25 >= len(self.videos)
            )
            review = discord.ui.Button(label="Review", style=discord.ButtonStyle.secondary)
            start = discord.ui.Button(label="Start", style=discord.ButtonStyle.success)
            cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)

            async def move(interaction: discord.Interaction, amount: int) -> None:
                if not await owns_control(interaction, self.owner_id):
                    return
                self.page += amount
                self.rebuild()
                await self.render(interaction)

            previous.callback = lambda interaction: move(interaction, -1)
            next_button.callback = lambda interaction: move(interaction, 1)

            async def show_review(interaction: discord.Interaction) -> None:
                if not await owns_control(interaction, self.owner_id):
                    return
                await interaction.response.send_message(
                    f"Selected {len(self.selected)} of {len(self.videos)} videos.", ephemeral=True
                )

            async def start_job(interaction: discord.Interaction) -> None:
                if not await owns_control(interaction, self.owner_id):
                    return
                if not self.selected:
                    await interaction.response.send_message(
                        "Select at least one video.", ephemeral=True
                    )
                    return
                self.arguments["video_ids"] = sorted(self.selected)
                if bool(self.arguments.get("force")):
                    self.stop()
                    confirmation = ConfirmPullView(self.arguments, self.owner_id)
                    confirmation.message = self.message
                    await interaction.response.edit_message(
                        content="Force may overwrite locally edited generated content. Confirm?",
                        view=confirmation,
                    )
                    return
                await interaction.response.defer()
                job = await enqueue(interaction, "pull", self.arguments)
                self.stop()
                await interaction.edit_original_response(
                    content=f"Queued `{job.job_id}` with {len(self.selected)} videos.", view=None
                )

            async def cancel_selection(interaction: discord.Interaction) -> None:
                if not await owns_control(interaction, self.owner_id):
                    return
                self.stop()
                await interaction.response.edit_message(
                    content="Video selection cancelled.", view=None
                )

            review.callback = show_review
            start.callback = start_job
            cancel.callback = cancel_selection
            for item in (previous, next_button, review, start, cancel):
                self.add_item(item)

        async def render(self, interaction: discord.Interaction) -> None:
            content = (
                f"Select videos — page {self.page + 1}/{(len(self.videos) + 24) // 25}; "
                f"{len(self.selected)} selected. Selections on other pages are preserved."
            )
            await interaction.response.edit_message(content=content, view=self)

    class ConfirmPullView(ExpiringView):
        def __init__(self, arguments: dict[str, object], owner_id: int) -> None:
            super().__init__(timeout=600)
            self.arguments = arguments
            self.owner_id = owner_id

        @discord.ui.button(label="Confirm overwrite", style=discord.ButtonStyle.danger)
        async def confirm(
            self, interaction: discord.Interaction, _button: discord.ui.Button
        ) -> None:
            if not await owns_control(interaction, self.owner_id):
                return
            await interaction.response.defer()
            job = await enqueue(interaction, "pull", self.arguments)
            self.stop()
            await interaction.edit_original_response(content=f"Queued `{job.job_id}`.", view=None)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel_button(
            self, interaction: discord.Interaction, _button: discord.ui.Button
        ) -> None:
            if not await owns_control(interaction, self.owner_id):
                return
            self.stop()
            await interaction.response.edit_message(content="Pull cancelled.", view=None)

    @group.command(name="pull", description="Pull selected course content to server storage")
    @app_commands.autocomplete(course=course_autocomplete, semester=semester_autocomplete)
    async def pull_command(
        interaction: discord.Interaction,
        kind: Literal["all", "files", "articles", "syllabus", "videos"] = "all",
        course: str | None = None,
        semester: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        best: bool = False,
        max_height: app_commands.Range[int, 144, 4320] = 1080,
        captions: bool = True,
    ) -> None:
        if not await guard(interaction):
            return
        arguments: dict[str, object] = {
            "kind": kind,
            "course": course or "",
            "semester": semester or "",
            "dry_run": dry_run,
            "force": force,
            "best": best,
            "max_height": max_height,
            "no_captions": not captions,
        }
        if kind == "videos":
            await interaction.response.defer(ephemeral=True, thinking=True)
            kinds = ("videos",)
            plan = await asyncio.to_thread(
                discover_pull_plan,
                config,
                kinds,
                course_selectors=(course,) if course else (),
                semesters=(semester,) if semester else (),
                headless=True,
            )
            videos = list(plan_data(plan)["videos"])
            if not videos:
                await interaction.followup.send("No selectable videos were found.", ephemeral=True)
                return
            view = VideoSelectionView(arguments, videos, interaction.user.id)
            view.message = await interaction.followup.send(
                f"Select videos — page 1/{video_page_count(videos)}; 0 selected.",
                view=view,
                ephemeral=True,
                wait=True,
            )
            return
        if force:
            view = ConfirmPullView(arguments, interaction.user.id)
            await interaction.response.send_message(
                "Force may overwrite locally edited generated content. Confirm?",
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        job = await enqueue(interaction, "pull", arguments)
        await interaction.followup.send(f"Queued `{job.job_id}`.", ephemeral=True)

    class CodeModal(discord.ui.Modal, title="SNU verification code"):
        code = discord.ui.TextInput(
            label="Verification code", style=discord.TextStyle.short, max_length=32
        )

        def __init__(self, job_id: str, owner_id: int) -> None:
            super().__init__(timeout=600)
            self.job_id = job_id
            self.owner_id = owner_id

        async def on_submit(self, interaction: discord.Interaction) -> None:
            if not await owns_control(interaction, self.owner_id):
                return
            broker = bot.brokers.get(self.job_id)
            accepted = broker is not None and broker.submit(str(self.code))
            await interaction.response.send_message(
                "Verification code submitted." if accepted else "This login request has expired.",
                ephemeral=True,
            )

    class LoginCodeView(ExpiringView):
        def __init__(self, job_id: str, owner_id: int) -> None:
            super().__init__(timeout=600)
            self.job_id = job_id
            self.owner_id = owner_id

        @discord.ui.button(label="Enter verification code", style=discord.ButtonStyle.primary)
        async def enter_code(
            self, interaction: discord.Interaction, _button: discord.ui.Button
        ) -> None:
            if not await owns_control(interaction, self.owner_id):
                return
            await interaction.response.send_modal(CodeModal(self.job_id, self.owner_id))

    @group.command(name="login", description="Re-login using saved SNU credentials and Discord 2FA")
    async def login_command(
        interaction: discord.Interaction, method: Literal["email", "phone"] = "email"
    ) -> None:
        if not await guard(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert interaction.channel_id is not None
        job = bot.queue.enqueue(
            "login", {"method": method}, interaction.user.id, interaction.channel_id
        )
        bot.brokers[job.job_id] = VerificationBroker(config.login_timeout_seconds)
        await post_job(interaction, job)
        view = LoginCodeView(job.job_id, interaction.user.id)
        view.message = await interaction.followup.send(
            f"Queued login `{job.job_id}`. When SNU sends the code, use this button.",
            view=view,
            ephemeral=True,
            wait=True,
        )

    @group.command(name="jobs", description="List recent Discord jobs")
    async def jobs_command(interaction: discord.Interaction) -> None:
        if not await guard(interaction):
            return
        with StateStore(config.database_path) as store:
            jobs = store.list_discord_jobs(limit=10)
        await interaction.response.send_message(
            _short_json(
                [
                    {
                        "job_id": job.job_id,
                        "command": job.command,
                        "status": job.status,
                        "progress": job.progress,
                        "created_at": job.created_at,
                    }
                    for job in jobs
                ]
            ),
            ephemeral=True,
        )

    @group.command(name="cancel", description="Cancel a queued or running Discord job")
    async def cancel_command(interaction: discord.Interaction, job_id: str) -> None:
        if not await guard(interaction):
            return
        changed = bot.queue.cancel(job_id.strip())
        await interaction.response.send_message(
            "Cancellation requested." if changed else "No queued or running job has that ID.",
            ephemeral=True,
        )

    @bot.tree.error
    async def command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        message = f"Command failed: {redact(error)}"[:MAX_MESSAGE]
        LOGGER.error("Discord application command failed: %s", redact(error))
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    bot.run(token, log_handler=None)
