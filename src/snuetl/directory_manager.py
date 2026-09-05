from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .config import Config, DirectoryRoute
from .models import Course, Semester
from .routing import routed_category_dir, routed_file_path
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


_LEGACY_COURSE = re.compile(r"^(?P<semester>\d{4}-\d+)\s+(?P<title>.+?)(?P<sep>--|-)(?P<id>\d+)$")


def repair_legacy_layout(root: Path, *, dry_run: bool = False) -> MigrationSummary:
    """Move pre-0.7 top-level course folders into semester/course/files.

    This intentionally only considers directories with a semester prefix and a
    numeric course ID, leaving unrelated user files untouched.
    """
    root = validate_managed_root(root)
    summary = MigrationSummary()
    if not root.is_dir():
        return summary
    candidates = list(root.iterdir())
    # A previous release occasionally created ROOT/<semester>/<semester course>.
    for semester_dir in list(root.iterdir()):
        if semester_dir.is_dir() and re.fullmatch(r"\d{4}-\d+", semester_dir.name):
            candidates.extend(semester_dir.iterdir())
    for source_dir in candidates:
        if not source_dir.is_dir():
            continue
        match = _LEGACY_COURSE.match(source_dir.name)
        if not match:
            continue
        title = match.group("title").rstrip(" -")
        destination = root / match.group("semester") / f"{title}--{match.group('id')}" / "files"
        if source_dir.parent.name == match.group("semester"):
            # Already under its semester; this handles the malformed nested form.
            destination = root / match.group("semester") / f"{title}--{match.group('id')}" / "files"
        children = list(source_dir.iterdir())
        summary.planned += len(children)
        if dry_run:
            continue
        try:
            for child in children:
                target = destination / child.name
                destination.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if child.is_file() and target.is_file() and _digest(child) == _digest(target):
                        child.unlink()
                        summary.moved += 1
                        continue
                    raise OSError(f"destination already exists: {target}")
                shutil.move(str(child), str(target))
                summary.moved += 1
            source_dir.rmdir()
        except OSError:
            summary.failed += 1
    return summary


def validate_managed_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root in {Path("/"), Path.home().resolve()}:
        raise ValueError("the managed directory cannot be the filesystem root or home directory")
    return root


def _stored_course(row: sqlite3.Row) -> Course:
    semester_code = str(row["semester_code"] or "") or None
    semester = Semester(semester_code, semester_code, semester_code) if semester_code else None
    return Course(
        str(row["course_id"]),
        str(row["course_name"]),
        "",
        semester=semester,
    )


def resolve_cached_course_id(config: Config, selector: str | None) -> str | None:
    if not selector or not config.database_path.exists():
        return selector
    with StateStore(config.database_path) as store:
        rows = store.db.execute("SELECT remote_id, name FROM courses WHERE active=1").fetchall()
    exact = [row for row in rows if str(row["remote_id"]) == selector]
    if exact:
        return str(exact[0]["remote_id"])
    folded = selector.casefold()
    matches = [row for row in rows if folded in str(row["name"]).casefold()]
    if len(matches) == 1:
        return str(matches[0]["remote_id"])
    if len(matches) > 1:
        raise ValueError("course selector is ambiguous; use an exact course ID")
    return selector


def upsert_directory_route(config: Config, route: DirectoryRoute) -> Config:
    from .routing import validate_route

    checked = validate_route(route)
    if not checked.route_id:
        checked = replace(checked, route_id=f"route-{uuid.uuid4().hex[:8]}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", checked.route_id):
        raise ValueError("route name may contain only letters, numbers, '.', '_', and '-'")
    routes = [value for value in config.directory_routes if value.route_id != checked.route_id]
    routes.append(checked)
    return replace(config, directory_routes=tuple(routes))


def remove_directory_route(config: Config, route_id: str) -> Config:
    routes = tuple(route for route in config.directory_routes if route.route_id != route_id)
    if len(routes) == len(config.directory_routes):
        raise ValueError(f"unknown directory route: {route_id}")
    return replace(config, directory_routes=routes)


def directory_routes_data(config: Config) -> list[dict[str, object]]:
    return [
        {
            "id": route.route_id,
            "destination": str(route.destination),
            "course_id": route.course_id,
            "semester_code": route.semester_code,
            "kind": route.kind,
            "remote_folder": "/".join(route.remote_folder) or None,
        }
        for route in config.directory_routes
    ]


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
            course = _stored_course(row)
            remote_path = str(row["remote_path"] or "")
            filename = str(row["file_name"] or Path(remote_path).name or row["remote_id"])
            folder = str(row["folder_path"] or "")
            folders = tuple(part for part in folder.split("/") if part)
            destination = routed_file_path(
                config,
                course,
                folders,
                filename,
                str(row["remote_id"]),
                default_root=root,
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
            course = _stored_course(row)
            source = Path(str(row["local_path"]))
            artifact_type = str(row["artifact_type"])
            category = (
                "videos"
                if artifact_type.startswith("video")
                else "syllabus"
                if artifact_type.startswith("syllabus")
                else "articles"
            )
            destination = (
                routed_category_dir(config, course, category, default_root=root) / source.name
            )
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


def directory_migration_conflicts(entries: list[MigrationEntry]) -> list[str]:
    conflicts: list[str] = []
    claimed: dict[Path, MigrationEntry] = {}
    for entry in entries:
        if entry.source == entry.destination or not entry.source.is_file():
            continue
        previous = claimed.get(entry.destination)
        if previous is not None and previous.source != entry.source:
            conflicts.append(f"multiple tracked files would target {entry.destination}")
            continue
        claimed[entry.destination] = entry
        if entry.destination.exists():
            try:
                if not entry.destination.is_file() or _digest(entry.source) != _digest(
                    entry.destination
                ):
                    conflicts.append(
                        f"destination contains different user or tracked content: {entry.destination}"
                    )
            except OSError as exc:
                conflicts.append(f"cannot compare {entry.destination}: {exc}")
    return conflicts


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
                        raise OSError(
                            f"destination already exists with different content: {entry.destination}"
                        )
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
