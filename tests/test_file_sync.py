from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from snuetl.config import load_config
from snuetl.file_sync import FileSyncService
from snuetl.models import Course, DownloadResult, RemoteFile, SyncSummary
from snuetl.state import StateStore


class _Downloader:
    def __init__(self, body: bytes = b"notes") -> None:
        self.body = body
        self.calls = 0

    def download(self, _remote, destination):
        self.calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.body)
        return DownloadResult(len(self.body), "digest", "etag")


def test_file_sync_service_is_shared_and_idempotent(tmp_path) -> None:
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
    )
    course = Course("101", "Course", "https://lms.test/courses/101")
    remote = RemoteFile(
        "file-1",
        course.remote_id,
        "notes.pdf",
        ("Week 1",),
        "https://lms.test/files/1",
        size=5,
        updated_at="2026-09-05",
    )
    downloader = _Downloader()
    summary = SyncSummary()

    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
    with StateStore(config.database_path) as store:
        service = FileSyncService(config, store, downloader)
        service.sync(course, remote, summary)
        service.sync(course, remote, summary)

    assert downloader.calls == 1
    assert summary.downloaded == 1
    assert summary.unchanged == 1
    with StateStore(config.database_path) as store:
        stored = store.get_file(course.remote_id, remote.remote_id)
    assert stored is not None
    assert stored.status == "ok"
    assert stored.local_path.endswith("Course-101/Week 1/notes.pdf")


def test_sync_never_overwrites_user_created_content(tmp_path) -> None:
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
    )
    course = Course("101", "Course", "https://lms.test/courses/101")
    remote = RemoteFile(
        "file-1",
        course.remote_id,
        "notes.pdf",
        ("Week 1",),
        "https://lms.test/files/1",
        size=5,
        updated_at="2026-09-05",
    )
    expected = config.download_dir / "Course-101" / "Week 1" / "notes.pdf"
    first_fallback = config.download_dir / "Course-101" / "Week 1" / "notes__snuetl-file-1.pdf"
    personal = config.download_dir / "Course-101" / "Week 1" / "D" / "assignment.txt"
    for path, body in (
        (expected, b"user notes"),
        (first_fallback, b"another user file"),
        (personal, b"my assignment"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
    with StateStore(config.database_path) as store:
        FileSyncService(config, store, _Downloader()).sync(course, remote, SyncSummary())
        stored = store.get_file(course.remote_id, remote.remote_id)

    assert stored is not None
    assert Path(stored.local_path).name == "notes__snuetl-file-1-2.pdf"
    assert Path(stored.local_path).read_bytes() == b"notes"
    assert expected.read_bytes() == b"user notes"
    assert first_fallback.read_bytes() == b"another user file"
    assert personal.read_bytes() == b"my assignment"
