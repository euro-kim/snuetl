from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from contextlib import suppress
from typing import Any

from .config import Config
from .discord_operations import LoginCallbacks, execute_job
from .errors import OperationCancelled
from .logging_utils import redact, redacted_exc_info
from .state import DiscordJob, StateStore

LOGGER = logging.getLogger(__name__)


class DiscordJobCancelled(OperationCancelled):
    """Stop a long-running operation at its next cooperative checkpoint."""


ProgressCallback = Callable[[DiscordJob, str], Coroutine[Any, Any, None]]
FinishedCallback = Callable[[DiscordJob, dict[str, object] | None], Coroutine[Any, Any, None]]
LoginCallbackFactory = Callable[[DiscordJob], LoginCallbacks | None]


class DiscordJobQueue:
    def __init__(
        self,
        config: Config,
        *,
        on_progress: ProgressCallback,
        on_finished: FinishedCallback,
        login_callbacks: LoginCallbackFactory | None = None,
        gateway: str = "discord",
    ) -> None:
        self.config = config
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.login_callbacks = login_callbacks
        self.gateway = gateway
        self._wake = asyncio.Event()
        self._stopping = False
        self._worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        with StateStore(self.config.database_path) as store:
            store.interrupt_remote_jobs(self.gateway)
        self._worker = asyncio.create_task(self._run(), name="snuetl-discord-jobs")

    async def stop(self) -> None:
        self._stopping = True
        with StateStore(self.config.database_path) as store:
            for job in store.list_remote_jobs(self.gateway, limit=20):
                if job.status == "running":
                    store.request_remote_job_cancel(job.job_id, self.gateway)
        self._wake.set()
        if self._worker is not None:
            await self._worker
        with StateStore(self.config.database_path) as store:
            store.interrupt_remote_jobs(self.gateway)

    def enqueue(
        self,
        command: str,
        arguments: dict[str, object],
        requester_id: int,
        channel_id: int,
        *,
        job_id: str | None = None,
    ) -> DiscordJob:
        with StateStore(self.config.database_path) as store:
            job = store.create_remote_job(
                self.gateway,
                job_id or uuid.uuid4().hex[:12],
                command,
                arguments,
                requester_id,
                channel_id=channel_id,
            )
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> bool:
        with StateStore(self.config.database_path) as store:
            changed = store.request_remote_job_cancel(job_id, self.gateway)
            job = store.get_discord_job(job_id)
        if changed and job is not None and job.status == "cancelled":
            asyncio.create_task(self.on_finished(job, None))
        self._wake.set()
        return changed

    async def _run(self) -> None:
        while not self._stopping:
            with StateStore(self.config.database_path) as store:
                job = store.next_remote_job(self.gateway)
            if job is None:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                continue
            result: dict[str, object] | None = None
            loop = asyncio.get_running_loop()

            def progress(message: str) -> None:
                with StateStore(self.config.database_path) as store:
                    current = store.get_discord_job(job.job_id)
                    if current is not None and current.status == "cancel_requested":
                        raise DiscordJobCancelled()
                    store.update_discord_job(job.job_id, progress=str(redact(message)))
                    current = store.get_discord_job(job.job_id) or job
                future: Future[None] = asyncio.run_coroutine_threadsafe(
                    self.on_progress(current, str(redact(message))), loop
                )

                def report_publish_error(completed: Future[None]) -> None:
                    error = completed.exception()
                    if error is not None:
                        LOGGER.error(
                            "Could not publish %s job progress: %s", self.gateway, redact(error)
                        )

                future.add_done_callback(report_publish_error)

            try:
                callbacks = self.login_callbacks(job) if self.login_callbacks else None
                result = await asyncio.to_thread(
                    execute_job,
                    self.config,
                    job.command,
                    job.arguments,
                    progress=progress,
                    login_callbacks=callbacks,
                )
                with StateStore(self.config.database_path) as store:
                    store.update_discord_job(job.job_id, status="completed", progress="Completed")
                    finished = store.get_discord_job(job.job_id) or job
            except DiscordJobCancelled:
                with StateStore(self.config.database_path) as store:
                    store.update_discord_job(job.job_id, status="cancelled", progress="Cancelled")
                    finished = store.get_discord_job(job.job_id) or job
            except Exception as exc:
                message = str(redact(exc)) or type(exc).__name__
                LOGGER.error(
                    "%s job failed job=%s command=%s error=%s: %s",
                    self.gateway,
                    job.job_id,
                    job.command,
                    type(exc).__name__,
                    message,
                    exc_info=redacted_exc_info(exc),
                )
                with StateStore(self.config.database_path) as store:
                    store.update_discord_job(
                        job.job_id, status="failed", progress="Failed", error=message
                    )
                    finished = store.get_discord_job(job.job_id) or job
            try:
                await self.on_finished(finished, result)
            except Exception as exc:
                LOGGER.error("Could not publish %s job result: %s", self.gateway, redact(exc))


RemoteJobQueue = DiscordJobQueue
