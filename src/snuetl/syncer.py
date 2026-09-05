from __future__ import annotations

import logging
from collections.abc import Callable

from .config import Config, ensure_private_directory
from .downloader import AuthenticatedDownloader
from .errors import AuthenticationRequired, OperationCancelled
from .file_sync import desired_file_path, sync_file
from .lms_session import AuthenticatedLmsSession
from .models import SyncSummary
from .state import StateStore

LOGGER = logging.getLogger(__name__)

# Retain the former private names for callers while the implementation lives in
# the shared service module.
_desired_path = desired_file_path
_sync_file = sync_file


def synchronize(
    config: Config,
    *,
    headless: bool | None = None,
    progress: Callable[[str], None] | None = None,
) -> SyncSummary:
    ensure_private_directory(config.state_dir)
    config.download_dir.mkdir(parents=True, exist_ok=True)
    summary = SyncSummary()
    run_id: int | None = None
    with StateStore(config.database_path) as store:
        run_id = store.begin_run()
        try:
            with AuthenticatedLmsSession(config, headless=headless) as session:
                context, page = session.context, session.page
                assert session.discovery is not None
                discovery = session.discovery
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
                        if progress is not None:
                            progress(f"Synchronizing {course.display_name}")
                        try:
                            files = discovery.discover_files(course)
                            catalog_file_count += len(files)
                            with store.transaction():
                                store.replace_catalog_files(course, files)
                            for remote in files:
                                if progress is not None:
                                    progress(f"Checking {course.display_name}: {remote.name}")
                                _sync_file(config, store, downloader, course, remote, summary)
                        except AuthenticationRequired:
                            raise
                        except OperationCancelled:
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
                session.persist()
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
