from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import Config
from .downloader import AuthenticatedDownloader
from .errors import AuthenticationRequired
from .logging_utils import redact
from .models import Course, RemoteFile, SyncSummary
from .routing import routed_file_path
from .state import StateStore

LOGGER = logging.getLogger(__name__)


def desired_file_path(
    config: Config,
    store: StateStore,
    course: Course,
    remote: RemoteFile,
    current_path: Path | None,
) -> Path:
    """Resolve a stable destination without claiming another tracked path."""
    if current_path is not None:
        return current_path

    def claimed(candidate: Path) -> bool:
        return (
            store.path_claimed(
                candidate,
                course_id=course.remote_id,
                remote_id=remote.remote_id,
            )
            or candidate.exists()
        )

    return routed_file_path(
        config,
        course,
        remote.folder_path,
        remote.name,
        remote.remote_id,
        claimed,
    )


class FileSyncService:
    """Synchronize remote files and atomically update their local state."""

    def __init__(
        self,
        config: Config,
        store: StateStore,
        downloader: AuthenticatedDownloader,
    ) -> None:
        self.config = config
        self.store = store
        self.downloader = downloader

    def sync(self, course: Course, remote: RemoteFile, summary: SyncSummary) -> None:
        stored = self.store.get_file(course.remote_id, remote.remote_id)
        current_path = Path(stored.local_path) if stored is not None else None
        destination = desired_file_path(
            self.config,
            self.store,
            course,
            remote,
            current_path,
        )
        unchanged = (
            stored is not None
            and stored.status == "ok"
            and stored.revision == remote.revision
            and current_path is not None
            and current_path.is_file()
        )
        if unchanged:
            assert stored is not None
            assert current_path is not None
            if current_path != destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current_path, destination)
            with self.store.transaction():
                self.store.record_file(
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
            result = self.downloader.download(remote, destination)
            with self.store.transaction():
                self.store.record_file(
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
            with self.store.transaction():
                self.store.record_failure(remote, destination, redact(exc))
            summary.failed += 1
            LOGGER.error(
                "course=%s path=%s action=failed error=%s",
                course.name,
                remote.display_path,
                exc,
            )


def sync_file(
    config: Config,
    store: StateStore,
    downloader: AuthenticatedDownloader,
    course: Course,
    remote: RemoteFile,
    summary: SyncSummary,
) -> None:
    """Compatibility-friendly functional entry point for one file."""
    FileSyncService(config, store, downloader).sync(course, remote, summary)
