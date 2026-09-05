import json
from dataclasses import replace
from pathlib import Path

from snuetl.cli import main
from snuetl.config import load_config, save_config
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


def test_directory_bind_automatically_migrates_only_tracked_files(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
    )
    save_config(config, config_path)
    course = Course(
        "101",
        "Database Systems",
        "https://lms.test/courses/101",
        semester=Semester("s1", "2026-2", "2026년 2학기", 2026),
    )
    remote = RemoteFile("f1", "101", "notes.pdf", ("x", "y"), "https://lms.test/f1")
    source = (
        config.download_dir / "2026-2" / "Database Systems--101" / "files" / "x" / "y" / "notes.pdf"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"remote")
    target = tmp_path / "A" / "B"
    personal = target / "D" / "assignment.txt"
    personal.parent.mkdir(parents=True)
    personal.write_bytes(b"user work")
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.replace_catalog_files(course, [remote])
        store.record_file(remote, source, sha256="", etag=None, status="ok")

    result = main(
        [
            "--config",
            str(config_path),
            "directory",
            "bind",
            str(target),
            "--name",
            "nested",
            "--course",
            "101",
            "--remote-folder",
            "x/y",
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["migration"]["moved"] == 1
    assert (target / "notes.pdf").read_bytes() == b"remote"
    assert personal.read_bytes() == b"user work"
    assert not source.exists()
    loaded = load_config(config_path)
    assert loaded.directory_routes[0].remote_folder == ("x", "y")
    with StateStore(config.database_path) as store:
        assert store.get_file("101", "f1").local_path == str(target / "notes.pdf")
