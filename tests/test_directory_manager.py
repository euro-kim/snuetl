import json
import stat
from dataclasses import replace
from pathlib import Path

from snuetl.cli import main
from snuetl.config import load_config, save_config
from snuetl.directory_manager import (
    configured_video_directory,
    directory_permission_data,
    directory_permission_report,
    execute_directory_migration,
    has_separate_video_directory,
    plan_directory_migration,
    secure_managed_directory,
)
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


def test_directory_permissions_are_audited_and_normalized(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir(mode=0o755)
    root.chmod(0o755)

    unsafe = directory_permission_report(root)
    assert unsafe.safe is False
    assert unsafe.mode == 0o755
    assert "group or other users have access" in " ".join(unsafe.issues)
    assert "chmod 700" in " ".join(unsafe.remediation)

    secured = secure_managed_directory(root)
    assert secured.safe is True
    assert secured.changed is True
    assert secured.previous_mode == 0o755
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert directory_permission_data(secured)["mode"] == "0700"


def test_directory_list_warns_without_changing_unsafe_permissions(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    root = tmp_path / "shared"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    config = replace(load_config(tmp_path / "missing.toml"), download_dir=root)
    save_config(config, config_path)

    assert main(["--config", str(config_path), "directory", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["permissions_safe"] is False
    assert payload["data"]["permissions"][0]["mode"] == "0755"
    assert payload["warnings"][0]["code"] == "UNSAFE_DIRECTORY_PERMISSIONS"
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_directory_set_secures_existing_destination_and_reports_change(
    tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.toml"
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    save_config(load_config(tmp_path / "missing.toml"), config_path)

    result = main(
        [
            "--config",
            str(config_path),
            "directory",
            "set",
            str(target),
            "--no-migrate",
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["permissions_safe"] is True
    assert payload["data"]["permissions"][0]["changed"] is True
    assert payload["data"]["permissions"][0]["previous_mode"] == "0755"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_dedicated_video_directory_can_be_set_and_reset(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    regular = tmp_path / "regular"
    regular.mkdir(mode=0o700)
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=regular,
    )
    save_config(config, config_path)
    videos = tmp_path / "large-videos"

    result = main(
        [
            "--config",
            str(config_path),
            "directory",
            "videos",
            str(videos),
            "--no-migrate",
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    configured = load_config(config_path)
    assert configured_video_directory(configured) == videos
    assert has_separate_video_directory(configured) is True
    assert payload["data"]["video_directory"] == str(videos)
    assert payload["data"]["video_directory_separate"] is True
    assert stat.S_IMODE(videos.stat().st_mode) == 0o700

    result = main(
        [
            "--config",
            str(config_path),
            "directory",
            "videos",
            "--default",
            "--no-migrate",
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    configured = load_config(config_path)
    assert configured_video_directory(configured) == regular
    assert has_separate_video_directory(configured) is False
    assert payload["data"]["video_directory_separate"] is False


def test_changing_video_directory_migrates_tracked_videos(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    regular = tmp_path / "regular"
    regular.mkdir(mode=0o700)
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=regular,
    )
    save_config(config, config_path)
    course = Course("101", "Systems", "https://lms.test/courses/101")
    source = regular / "Systems-101" / "videos" / "lecture.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.record_artifact(
            artifact_type="video",
            course_id=course.remote_id,
            source_id="v1",
            local_path=source,
            source_revision="revision-1",
            sha256="digest",
            size_bytes=5,
            status="ok",
        )
    videos = tmp_path / "large-videos"

    result = main(
        [
            "--config",
            str(config_path),
            "directory",
            "videos",
            str(videos),
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    destination = videos / "unknown-semester" / "Systems--101" / "videos" / "lecture.mp4"
    assert payload["data"]["migration"]["moved"] == 1
    assert destination.read_bytes() == b"video"
    assert not source.exists()
    with StateStore(config.database_path) as store:
        artifact = store.get_artifact("video", "101", "v1")
        assert artifact is not None
        assert artifact["local_path"] == str(destination)
