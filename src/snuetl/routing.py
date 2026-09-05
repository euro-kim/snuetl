from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Config, DirectoryRoute
from .models import Course
from .paths import course_content_dir, sanitize_component


@dataclass(frozen=True, slots=True)
class ResolvedRoute:
    base: Path
    remaining_folder: tuple[str, ...]
    route_id: str | None = None


def parse_remote_folder(value: str) -> tuple[str, ...]:
    raw = value.strip().replace("\\", "/").strip("/")
    if not raw:
        return ()
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("remote folder must be a relative eTL folder path")
    return tuple(sanitize_component(part) for part in parts)


def validate_route(route: DirectoryRoute) -> DirectoryRoute:
    destination = route.destination.expanduser().resolve()
    if (
        not route.route_id
        or not route.route_id.isascii()
        or not all(character.isalnum() or character in ".-_" for character in route.route_id)
    ):
        raise ValueError("route name may contain only letters, numbers, '.', '_', and '-'")
    if destination in {Path("/"), Path.home().resolve()}:
        raise ValueError("a route destination cannot be the filesystem root or home directory")
    if not any((route.course_id, route.semester_code, route.kind, route.remote_folder)):
        raise ValueError("a route must constrain course, semester, kind, or remote folder")
    if route.remote_folder and route.kind not in {None, "files"}:
        raise ValueError("remote-folder routes can only target file pulls")
    if any(
        not part or part in {".", ".."} or "/" in part or "\\" in part
        for part in route.remote_folder
    ):
        raise ValueError("remote folder parts must be non-empty relative path components")
    remote_folder = tuple(sanitize_component(part) for part in route.remote_folder)

    return DirectoryRoute(
        route_id=route.route_id,
        destination=destination,
        course_id=route.course_id,
        semester_code=route.semester_code,
        kind="files" if route.remote_folder and route.kind is None else route.kind,
        remote_folder=remote_folder,
    )


def _matches(
    route: DirectoryRoute,
    course: Course,
    kind: str,
    remote_folder: tuple[str, ...],
) -> bool:
    if route.course_id is not None and route.course_id != course.remote_id:
        return False
    semester = course.semester.semester_code if course.semester else None
    if route.semester_code is not None and route.semester_code != semester:
        return False
    if route.kind is not None and route.kind != kind:
        return False
    prefix = route.remote_folder
    return not prefix or remote_folder[: len(prefix)] == prefix


def resolve_route(
    config: Config,
    course: Course,
    kind: str,
    *,
    remote_folder: tuple[str, ...] = (),
    default_root: Path | None = None,
) -> ResolvedRoute:
    normalized_folder = tuple(sanitize_component(part) for part in remote_folder)
    matches = [
        (index, route)
        for index, route in enumerate(config.directory_routes)
        if _matches(route, course, kind, normalized_folder)
    ]
    if not matches:
        root = (default_root or config.download_dir).expanduser().resolve()
        if course.semester is None and kind == "files":
            base = root / sanitize_component(f"{course.display_name}-{course.remote_id}")
        else:
            base = course_content_dir(
                root,
                course.display_name,
                course.remote_id,
                course.semester.semester_code if course.semester else None,
                kind,
            )
        return ResolvedRoute(
            base,
            normalized_folder,
        )
    _, selected = max(
        matches,
        key=lambda item: (
            sum(
                value is not None
                for value in (
                    item[1].course_id,
                    item[1].semester_code,
                    item[1].kind,
                )
            ),
            len(item[1].remote_folder),
            item[0],
        ),
    )
    return ResolvedRoute(
        selected.destination,
        normalized_folder[len(selected.remote_folder) :],
        selected.route_id,
    )


def routed_category_dir(
    config: Config,
    course: Course,
    kind: str,
    *,
    default_root: Path | None = None,
) -> Path:
    return resolve_route(config, course, kind, default_root=default_root).base


def routed_file_path(
    config: Config,
    course: Course,
    remote_folder: tuple[str, ...],
    filename: str,
    remote_id: str,
    is_claimed: Callable[[Path], bool] | None = None,
    *,
    default_root: Path | None = None,
) -> Path:
    resolved = resolve_route(
        config,
        course,
        "files",
        remote_folder=remote_folder,
        default_root=default_root,
    )
    folders = [sanitize_component(part) for part in resolved.remaining_folder]
    leaf = sanitize_component(filename, fallback=f"file-{remote_id}")
    candidate = resolved.base.joinpath(*folders, leaf)
    if is_claimed and is_claimed(candidate):
        stem = Path(leaf).stem
        suffix = Path(leaf).suffix
        remote_suffix = sanitize_component(remote_id, fallback="remote", limit=40)
        candidate = resolved.base.joinpath(*folders, f"{stem}__snuetl-{remote_suffix}{suffix}")
        number = 2
        while is_claimed(candidate):
            candidate = resolved.base.joinpath(
                *folders, f"{stem}__snuetl-{remote_suffix}-{number}{suffix}"
            )
            number += 1
    base = resolved.base.resolve()
    destination = candidate.resolve()
    if base != destination and base not in destination.parents:
        raise ValueError("generated path escapes its routed directory")
    return candidate
