from dataclasses import replace
from pathlib import Path

from snuetl.config import load_config
from snuetl.directory_manager import execute_directory_migration, plan_directory_migration
from snuetl.models import Course, RemoteFile, Semester
from snuetl.state import StateStore


def test_moves_only_tracked_file_and_updates_database(tmp_path: Path) -> None:
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "old",
    )
    course = Course(
        "101",
        "Database Systems",
        "https://lms.test/courses/101",
        semester=Semester("s1", "2026-2", "2026년 2학기", 2026),
    )
    remote = RemoteFile("f1", "101", "notes.pdf", ("Week 1",), "https://lms.test/f1")
    source = config.download_dir / "legacy" / "notes.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"notes")
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.replace_catalog_files(course, [remote])
        store.record_file(remote, source, sha256="", etag=None, status="ok")

    entries = plan_directory_migration(config, tmp_path / "new")
    summary = execute_directory_migration(config, entries)
    assert summary.moved == 1
    assert not source.exists()
    assert entries[0].destination.read_bytes() == b"notes"
    with StateStore(config.database_path) as store:
        assert store.get_file("101", "f1").local_path == str(entries[0].destination)
