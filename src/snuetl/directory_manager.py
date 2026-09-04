from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .paths import course_content_dir, destination_path
from .state import StateStore


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    record_type: str
    course_id: str
    source_id: str
    source: Path
    destination: Path


@dataclass(slots=True)
class MigrationSummary:
    planned: int = 0
    moved: int = 0
    missing: int = 0
    failed: int = 0


def validate_managed_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root in {Path("/"), Path.home().resolve()}:
        raise ValueError("the managed directory cannot be the filesystem root or home directory")
    return root


def plan_directory_migration(config: Config, new_root: Path) -> list[MigrationEntry]:
    root = validate_managed_root(new_root)
    if not config.database_path.exists():
        return []
    entries: list[MigrationEntry] = []
    with StateStore(config.database_path) as store:
        rows = store.db.execute(
            """SELECT f.course_id, f.remote_id, f.local_path, f.remote_path,
                      c.name AS course_name, s.semester_code,
                      cf.file_name, cf.folder_path
               FROM files AS f
               JOIN courses AS c ON c.remote_id=f.course_id
               LEFT JOIN semesters AS s ON s.semester_id=c.semester_id
               LEFT JOIN catalog_files AS cf
                 ON cf.course_id=f.course_id AND cf.file_id=f.remote_id
               WHERE f.local_path <> ''"""
        ).fetchall()
        for row in rows:
            remote_path = str(row["remote_path"] or "")
            filename = str(row["file_name"] or Path(remote_path).name or row["remote_id"])
            folder = str(row["folder_path"] or "")
            folders = tuple(part for part in folder.split("/") if part)
            destination = destination_path(
                root,
                str(row["course_name"]),
                str(row["course_id"]),
                folders,
                filename,
                str(row["remote_id"]),
                semester_code=str(row["semester_code"] or "") or None,
                category="files",
            )
            entries.append(
                MigrationEntry(
                    "file",
                    str(row["course_id"]),
                    str(row["remote_id"]),
                    Path(str(row["local_path"])),
                    destination,
                )
            )
        rows = store.db.execute(
            """SELECT a.artifact_type, a.course_id, a.source_id, a.local_path,
                      c.name AS course_name, s.semester_code
               FROM pull_artifacts AS a
               JOIN courses AS c ON c.remote_id=a.course_id
               LEFT JOIN semesters AS s ON s.semester_id=c.semester_id
               WHERE a.local_path <> ''"""
        ).fetchall()
        for row in rows:
            source = Path(str(row["local_path"]))
            artifact_type = str(row["artifact_type"])
            category = (
                "videos"
                if artifact_type.startswith("video")
                else "syllabus"
                if artifact_type.startswith("syllabus")
                else "articles"
            )
            destination = course_content_dir(
                root,
                str(row["course_name"]),
                str(row["course_id"]),
                str(row["semester_code"] or "") or None,
                category,
            ) / source.name
            entries.append(
                MigrationEntry(
                    artifact_type,
                    str(row["course_id"]),
                    str(row["source_id"]),
                    source,
                    destination,
                )
            )
    unique: dict[tuple[str, str], MigrationEntry] = {}
    for entry in entries:
        unique[(entry.record_type, f"{entry.course_id}:{entry.source_id}")] = entry
    return list(unique.values())


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def execute_directory_migration(
    config: Config, entries: list[MigrationEntry], *, dry_run: bool = False
) -> MigrationSummary:
    summary = MigrationSummary(planned=len(entries))
    if dry_run:
        return summary
    with StateStore(config.database_path) as store:
        for entry in entries:
            temporary: Path | None = None
            if entry.source == entry.destination:
                continue
            if not entry.source.is_file():
                summary.missing += 1
                continue
            try:
                entry.destination.parent.mkdir(parents=True, exist_ok=True)
                if entry.destination.exists():
                    if _digest(entry.source) != _digest(entry.destination):
                        raise OSError(f"destination already exists with different content: {entry.destination}")
                    with store.transaction():
                        if entry.record_type == "file":
                            store.db.execute(
                                "UPDATE files SET local_path=? WHERE course_id=? AND remote_id=?",
                                (str(entry.destination), entry.course_id, entry.source_id),
                            )
                        else:
                            store.db.execute(
                                """UPDATE pull_artifacts SET local_path=?
                                   WHERE artifact_type=? AND course_id=? AND source_id=?""",
                                (
                                    str(entry.destination),
                                    entry.record_type,
                                    entry.course_id,
                                    entry.source_id,
                                ),
                            )
                    entry.source.unlink()
                    summary.moved += 1
                    continue
                temporary = entry.destination.with_name(f".{entry.destination.name}.migrating")
                shutil.copy2(entry.source, temporary)
                if _digest(entry.source) != _digest(temporary):
                    raise OSError("copied file checksum does not match its source")
                os.replace(temporary, entry.destination)
                with store.transaction():
                    if entry.record_type == "file":
                        store.db.execute(
                            "UPDATE files SET local_path=? WHERE course_id=? AND remote_id=?",
                            (str(entry.destination), entry.course_id, entry.source_id),
                        )
                    else:
                        store.db.execute(
                            """UPDATE pull_artifacts SET local_path=?
                               WHERE artifact_type=? AND course_id=? AND source_id=?""",
                            (
                                str(entry.destination),
                                entry.record_type,
                                entry.course_id,
                                entry.source_id,
                            ),
                        )
                entry.source.unlink()
                summary.moved += 1
            except OSError:
                summary.failed += 1
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return summary
