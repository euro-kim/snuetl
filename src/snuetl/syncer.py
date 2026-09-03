from __future__ import annotations

import logging
import os
from pathlib import Path

from .adapters import DiscoveryService
from .browser import (
    authenticated_entry_url,
    browser_looks_authenticated,
    open_authenticated_browser,
    persist_auth_state,
)
from .config import Config, ensure_private_directory
from .downloader import AuthenticatedDownloader
from .errors import AuthenticationRequired
from .logging_utils import redact
from .models import Course, RemoteFile, SyncSummary
from .paths import destination_path
from .profile import profile_lock
from .state import StateStore

LOGGER = logging.getLogger(__name__)


def _desired_path(
    config: Config,
    store: StateStore,
    course: Course,
    remote: RemoteFile,
    current_path: Path | None,
) -> Path:
    def claimed(candidate: Path) -> bool:
        database_claim = store.path_claimed(
            candidate, course_id=course.remote_id, remote_id=remote.remote_id
        )
        filesystem_claim = candidate.exists() and candidate != current_path
        return database_claim or filesystem_claim

    return destination_path(
        config.download_dir,
        course.name,
        course.remote_id,
        remote.folder_path,
        remote.name,
        remote.remote_id,
        claimed,
    )


def _sync_file(
    config: Config,
    store: StateStore,
    downloader: AuthenticatedDownloader,
    course: Course,
    remote: RemoteFile,
    summary: SyncSummary,
) -> None:
    stored = store.get_file(course.remote_id, remote.remote_id)
    current_path = Path(stored.local_path) if stored is not None else None
    destination = _desired_path(config, store, course, remote, current_path)
    unchanged = (
        stored is not None
        and stored.status == "ok"
        and stored.revision == remote.revision
        and current_path is not None
        and current_path.is_file()
    )
    if unchanged:
        if current_path != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current_path, destination)
        with store.transaction():
            store.record_file(
                remote,
                destination,
                sha256=stored.sha256,
                etag=stored.etag,
                status="ok",
            )
        summary.unchanged += 1
        LOGGER.info("course=%s path=%s action=unchanged", course.name, remote.display_path)
        return

    action = (
        "updated"
        if stored is not None and current_path is not None and current_path.exists()
        else "downloaded"
    )
    try:
        result = downloader.download(remote, destination)
        with store.transaction():
            store.record_file(
                remote,
                destination,
                sha256=result.sha256,
                etag=result.etag,
                status="ok",
            )
        if current_path is not None and current_path != destination:
            try:
                current_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning(
                    "course=%s path=%s downloaded the new location but could not remove "
                    "the previous local path error=%s",
                    course.name,
                    remote.display_path,
                    exc,
                )
        if action == "updated":
            summary.updated += 1
        else:
            summary.downloaded += 1
        LOGGER.info("course=%s path=%s action=%s", course.name, remote.display_path, action)
    except AuthenticationRequired:
        raise
    except Exception as exc:
        with store.transaction():
            store.record_failure(remote, destination, redact(exc))
        summary.failed += 1
        LOGGER.error(
            "course=%s path=%s action=failed error=%s",
            course.name,
            remote.display_path,
            exc,
        )


def synchronize(config: Config, *, headless: bool | None = None) -> SyncSummary:
    ensure_private_directory(config.state_dir)
    config.download_dir.mkdir(parents=True, exist_ok=True)
    if not config.profile_dir.exists() or not config.auth_state_path.exists():
        raise AuthenticationRequired("no saved authentication session; run 'snuetl login' first")

    summary = SyncSummary()
    run_id: int | None = None
    with profile_lock(config.lock_path), StateStore(config.database_path) as store:
        run_id = store.begin_run()
        try:
            effective_headless = config.headless if headless is None else headless
            with open_authenticated_browser(config, headless=effective_headless) as (context, page):
                page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
                if not browser_looks_authenticated(context, page):
                    raise AuthenticationRequired(
                        "saved browser trust is missing or expired; rerun 'snuetl login' visibly"
                    )
                landing_url = page.url
                # Capture the session immediately so a later course-level failure
                # cannot discard cookies refreshed while opening the LMS.
                persist_auth_state(context, config, landing_url)
                discovery = DiscoveryService(context, page, timeout_seconds=config.timeout_seconds)
                courses = [
                    course
                    for course in discovery.discover_courses()
                    if course.remote_id not in config.excluded_course_ids
                ]
                summary.courses = len(courses)
                catalog_file_count = 0
                catalog_complete = True
                with store.transaction():
                    store.deactivate_courses()
                    for course in courses:
                        store.upsert_course(course)

                user_agent = page.evaluate("navigator.userAgent")
                with AuthenticatedDownloader(
                    context,
                    user_agent=user_agent,
                    timeout_seconds=config.timeout_seconds,
                    retry_count=config.retry_count,
                ) as downloader:
                    for course in courses:
                        try:
                            files = discovery.discover_files(course)
                            catalog_file_count += len(files)
                            with store.transaction():
                                store.replace_catalog_files(course, files)
                            for remote in files:
                                _sync_file(config, store, downloader, course, remote, summary)
                        except AuthenticationRequired:
                            raise
                        except Exception as exc:
                            catalog_complete = False
                            summary.failed += 1
                            LOGGER.error("course=%s action=failed error=%s", course.name, exc)
                with store.transaction():
                    store.record_catalog_refresh(
                        "files", catalog_file_count, complete=catalog_complete
                    )
                # DOM discovery may leave the page inside the final course. Keep
                # the verified LMS landing URL as the stable entry point.
                persist_auth_state(context, config, landing_url)
            status = "success" if summary.failed == 0 else "partial"
            store.finish_run(
                run_id,
                status,
                downloaded=summary.downloaded,
                updated=summary.updated,
                unchanged=summary.unchanged,
                failed=summary.failed,
            )
            return summary
        except Exception:
            if run_id is not None:
                store.finish_run(
                    run_id,
                    "failed",
                    downloaded=summary.downloaded,
                    updated=summary.updated,
                    unchanged=summary.unchanged,
                    failed=max(summary.failed, 1),
                )
            raise
