from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .config import Config, DirectoryRoute
from .models import Course, Semester
from .routing import (
    VIDEO_DIRECTORY_ROUTE_ID,
    routed_category_dir,
    routed_file_path,
)
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


@dataclass(frozen=True, slots=True)
class DirectoryPermissionReport:
    path: Path
    exists: bool
    is_directory: bool
    mode: int | None
    owner_uid: int | None
    current_uid: int | None
    owned_by_current_user: bool
    readable: bool
    writable: bool
    searchable: bool
    safe: bool
    issues: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    changed: bool = False
    previous_mode: int | None = None


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


def directory_permission_report(path: Path) -> DirectoryPermissionReport:
    """Audit whether a managed content root is usable by this process."""
    root = validate_managed_root(path)
    current_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if not root.exists():
        return DirectoryPermissionReport(
            path=root,
            exists=False,
            is_directory=False,
            mode=None,
            owner_uid=None,
            current_uid=current_uid,
            owned_by_current_user=True,
            readable=False,
            writable=False,
            searchable=False,
            safe=False,
            issues=("directory does not exist",),
            remediation=("It will be created with owner-only mode 0700 when applied.",),
        )

    details = root.stat()
    mode = stat.S_IMODE(details.st_mode)
    is_directory = stat.S_ISDIR(details.st_mode)
    owned = current_uid is None or details.st_uid == current_uid
    readable = os.access(root, os.R_OK)
    writable = os.access(root, os.W_OK)
    searchable = os.access(root, os.X_OK)
    issues: list[str] = []
    remediation: list[str] = []
    quoted = shlex.quote(str(root))
    if not is_directory:
        issues.append("path exists but is not a directory")
        remediation.append("Choose a directory path instead of a file.")
    if not owned and not (readable and writable and searchable):
        issues.append(
            f"directory is owned by UID {details.st_uid}, not the current UID {current_uid}"
        )
        remediation.append(
            f"Choose a directory you own or run: sudo chown {current_uid}:{os.getegid()} -- {quoted}"
        )
    if owned and mode & 0o700 != 0o700 and not (readable and writable and searchable):
        usable_mode = mode | 0o700
        issues.append(f"the owner lacks read, write, or search permission (mode {mode:04o})")
        remediation.append(f"Restore owner access with: chmod {usable_mode:04o} -- {quoted}")
    if not readable:
        issues.append("directory is not readable by snuetl")
    if not writable:
        issues.append("directory is not writable by snuetl")
    if not searchable:
        issues.append("directory is not searchable by snuetl")

    return DirectoryPermissionReport(
        path=root,
        exists=True,
        is_directory=is_directory,
        mode=mode,
        owner_uid=details.st_uid,
        current_uid=current_uid,
        owned_by_current_user=owned,
        readable=readable,
        writable=writable,
        searchable=searchable,
        safe=not issues,
        issues=tuple(dict.fromkeys(issues)),
        remediation=tuple(dict.fromkeys(remediation)),
    )


def secure_managed_directory(path: Path) -> DirectoryPermissionReport:
    """Create a content root or minimally repair access needed by this process."""
    root = validate_managed_root(path)
    before = directory_permission_report(root)
    if before.exists and not before.is_directory:
        raise PermissionError(f"managed path is not a directory: {root}")
    if before.exists and os.environ.get("SNUETL_RUNTIME") == "container":
        if not (before.readable and before.writable and before.searchable):
            raise PermissionError(
                f"mounted directory is not usable by the container user: {root}. "
                + "; ".join(before.issues)
            )
        return before
    if before.exists and not before.safe and not before.owned_by_current_user:
        remediation = before.remediation[0] if before.remediation else "Choose a directory you own."
        raise PermissionError(
            f"cannot make managed directory usable {root}: {'; '.join(before.issues)}. "
            f"{remediation}"
        )
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = root.stat()
        current_uid = os.geteuid() if hasattr(os, "geteuid") else None
        mode = stat.S_IMODE(details.st_mode)
        if details.st_uid == current_uid and mode & 0o700 != 0o700:
            root.chmod(mode | 0o700)
    except OSError as exc:
        raise PermissionError(
            f"cannot create or prepare managed directory {root}: {exc}. "
            "Choose an owner-writable directory and try again."
        ) from exc
    after = directory_permission_report(root)
    if not after.safe:
        guidance = " ".join(after.remediation) or "Choose a usable directory."
        raise PermissionError(
            f"managed directory is still unusable for {root}: {'; '.join(after.issues)}. {guidance}"
        )
    return replace(
        after,
        changed=not before.exists or before.mode != after.mode,
        previous_mode=before.mode,
    )


def directory_permission_data(report: DirectoryPermissionReport) -> dict[str, object]:
    return {
        "path": str(report.path),
        "exists": report.exists,
        "is_directory": report.is_directory,
        "mode": f"{report.mode:04o}" if report.mode is not None else None,
        "previous_mode": f"{report.previous_mode:04o}"
        if report.previous_mode is not None
        else None,
        "owner_uid": report.owner_uid,
        "current_uid": report.current_uid,
        "owned_by_current_user": report.owned_by_current_user,
        "readable": report.readable,
        "writable": report.writable,
        "searchable": report.searchable,
        "safe": report.safe,
        "changed": report.changed,
        "issues": list(report.issues),
        "remediation": list(report.remediation),
    }


def configured_directory_permission_reports(
    config: Config, *, secure: bool = False
) -> list[DirectoryPermissionReport]:
    """Audit each unique default or bound managed root in configuration order."""
    paths = (config.download_dir, *(route.destination for route in config.directory_routes))
    unique = tuple(dict.fromkeys(path.expanduser().resolve() for path in paths))
    reports = [directory_permission_report(path) for path in unique]
    if not secure:
        return reports
    blockers = [
        report
        for report in reports
        if report.exists
        and (not report.is_directory or (not report.safe and not report.owned_by_current_user))
    ]
    if blockers:
        detail = " | ".join(
            f"{report.path}: {'; '.join((*report.issues, *report.remediation))}"
            for report in blockers
        )
        raise PermissionError(
            "cannot prepare configured directories without changing ownership: " + detail
        )
    return [secure_managed_directory(path) for path in unique]


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


def configured_video_directory(config: Config) -> Path:
    for route in reversed(config.directory_routes):
        if route.route_id == VIDEO_DIRECTORY_ROUTE_ID:
            return route.destination
    return config.download_dir


def has_separate_video_directory(config: Config) -> bool:
    return any(route.route_id == VIDEO_DIRECTORY_ROUTE_ID for route in config.directory_routes)


def set_video_directory(config: Config, destination: Path) -> Config:
    destination = validate_managed_root(destination)
    routes = tuple(
        route for route in config.directory_routes if route.route_id != VIDEO_DIRECTORY_ROUTE_ID
    )
    updated = replace(config, directory_routes=routes)
    if destination == config.download_dir.expanduser().resolve():
        return updated
    return upsert_directory_route(
        updated,
        DirectoryRoute(VIDEO_DIRECTORY_ROUTE_ID, destination, kind="videos"),
    )


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
