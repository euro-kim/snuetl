from dataclasses import replace
from pathlib import Path

import pytest

from snuetl.config import DirectoryRoute, load_config
from snuetl.models import Course, Semester
from snuetl.routing import parse_remote_folder, resolve_route, routed_file_path, validate_route


def _course() -> Course:
    return Course(
        "101",
        "Database Systems",
        "https://lms.test/courses/101",
        semester=Semester("semester-1", "2026-2", "2026년 2학기", 2026),
    )


def test_most_specific_folder_binding_wins_and_strips_its_prefix(tmp_path: Path) -> None:
    routes = (
        DirectoryRoute(
            route_id="x",
            destination=tmp_path / "A",
            kind="files",
            remote_folder=("x",),
        ),
        DirectoryRoute(
            route_id="xy",
            destination=tmp_path / "A" / "B",
            kind="files",
            remote_folder=("x", "y"),
        ),
        DirectoryRoute(
            route_id="z",
            destination=tmp_path / "A" / "C",
            kind="files",
            remote_folder=("z",),
        ),
    )
    config = replace(load_config(tmp_path / "missing.toml"), directory_routes=routes)

    assert routed_file_path(config, _course(), ("x",), "one.pdf", "1") == (
        tmp_path / "A" / "one.pdf"
    )
    assert routed_file_path(config, _course(), ("x", "y"), "two.pdf", "2") == (
        tmp_path / "A" / "B" / "two.pdf"
    )
    assert routed_file_path(config, _course(), ("z", "week"), "three.pdf", "3") == (
        tmp_path / "A" / "C" / "week" / "three.pdf"
    )


def test_course_specific_binding_wins_over_general_binding(tmp_path: Path) -> None:
    routes = (
        DirectoryRoute(
            route_id="all-files",
            destination=tmp_path / "general",
            kind="files",
        ),
        DirectoryRoute(
            route_id="course-files",
            destination=tmp_path / "course",
            course_id="101",
            kind="files",
        ),
    )
    config = replace(load_config(tmp_path / "missing.toml"), directory_routes=routes)

    result = resolve_route(config, _course(), "files", remote_folder=("Week 1",))

    assert result.base == tmp_path / "course"
    assert result.remaining_folder == ("Week 1",)


def test_remote_folder_matching_uses_sanitized_etl_components(tmp_path: Path) -> None:
    route = DirectoryRoute(
        route_id="unsafe-name",
        destination=tmp_path / "safe",
        kind="files",
        remote_folder=("x_y",),
    )
    config = replace(load_config(tmp_path / "missing.toml"), directory_routes=(route,))

    result = resolve_route(config, _course(), "files", remote_folder=("x/y",))

    assert result.route_id == "unsafe-name"
    assert result.remaining_folder == ()


def test_invalid_bindings_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must constrain"):
        validate_route(DirectoryRoute("empty", tmp_path / "target"))
    with pytest.raises(ValueError, match="only target file pulls"):
        validate_route(
            DirectoryRoute(
                "bad-kind",
                tmp_path / "target",
                kind="articles",
                remote_folder=("Week",),
            )
        )
    with pytest.raises(ValueError, match="relative"):
        parse_remote_folder("../outside")
