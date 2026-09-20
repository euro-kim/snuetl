from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .canvas_api import CanvasClient, token_status
from .config import Config
from .discovery import DiscoveryBackend
from .errors import AuthenticationRequired, DiscoveryError
from .lms_session import AuthenticatedLmsSession
from .models import ContentItem, Course, ModuleItem, RemoteFile
from .state import StateStore
from .token_discovery import TokenDiscoveryAdapter

LOGGER = logging.getLogger(__name__)


class CourseSelectionError(DiscoveryError):
    """The user's course selector cannot match this catalog."""


@dataclass(frozen=True, slots=True)
class CatalogResult:
    courses: tuple[Course, ...]
    files: tuple[tuple[Course, RemoteFile], ...] = ()
    items: tuple[tuple[Course, ContentItem], ...] = ()
    modules: tuple[tuple[Course, ModuleItem], ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogRefreshSummary:
    courses: int
    files: int
    articles: int
    assignments: int
    module_items: int = 0
    quizzes: int = 0
    videos: int = 0


def _persist_courses(store: StateStore, courses: tuple[Course, ...]) -> None:
    store.deactivate_courses()
    for course in courses:
        store.upsert_course(course)
    store.record_catalog_refresh("courses", len(courses))


def _persist_result(
    config: Config,
    result: CatalogResult,
    kind: str,
    *,
    all_courses: tuple[Course, ...] | None = None,
) -> None:
    with StateStore(config.database_path) as store, store.transaction():
        _persist_courses(store, all_courses or result.courses)
        if kind == "files":
            for course in result.courses:
                values = [
                    remote for owner, remote in result.files if owner.remote_id == course.remote_id
                ]
                store.replace_catalog_files(course, values)
        elif kind == "articles":
            for course in result.courses:
                values = [
                    item for owner, item in result.items if owner.remote_id == course.remote_id
                ]
                store.replace_catalog_content(
                    course,
                    ("announcement", "page"),
                    values,
                    scope="articles",
                )
        elif kind in {"assignments", "quizzes"}:
            for course in result.courses:
                values = [
                    item for owner, item in result.items if owner.remote_id == course.remote_id
                ]
                store.replace_catalog_content(
                    course,
                    ("assignment", "quiz"),
                    values,
                    scope="coursework",
                )
        elif kind == "videos":
            for course in result.courses:
                values = [
                    item for owner, item in result.modules if owner.remote_id == course.remote_id
                ]
                store.replace_catalog_modules(course, values)


def select_courses(courses: list[Course], query: str | None) -> list[Course]:
    if not query:
        return courses
    folded = query.casefold()
    exact = [course for course in courses if course.remote_id == query]
    if exact:
        return exact
    matches = [course for course in courses if folded in course.name.casefold()]
    if not matches:
        raise CourseSelectionError(f"no active course matches {query!r}")
    if len(matches) > 1:
        names = ", ".join(f"{course.name} ({course.remote_id})" for course in matches)
        raise CourseSelectionError(f"course name is ambiguous; use an ID: {names}")
    return matches


def inspect_catalog(
    config: Config,
    *,
    kind: str,
    course_query: str | None = None,
    headless: bool | None = None,
) -> CatalogResult:
    if token_status(config).get("ready"):
        try:
            with CanvasClient(config) as client:
                adapter = TokenDiscoveryAdapter(client)
                if not adapter.probe():
                    raise AuthenticationRequired("Canvas token belongs to a different account")
                return _inspect_with_discovery(
                    config, adapter, kind, course_query
                )
        except CourseSelectionError:
            raise
        except (AuthenticationRequired, DiscoveryError) as exc:
            LOGGER.warning("Canvas token catalog unavailable; trying browser session: %s", exc)
    with AuthenticatedLmsSession(config, headless=headless) as session:
        assert session.discovery is not None
        result = _inspect_with_discovery(config, session.discovery, kind, course_query)
        session.persist(session.page.url)
        return result


def _inspect_with_discovery(
    config: Config, discovery: DiscoveryBackend, kind: str, course_query: str | None
) -> CatalogResult:
    courses = [
        course
        for course in discovery.discover_courses()
        if course.remote_id not in config.excluded_course_ids
    ]
    selected = select_courses(courses, course_query)
    if kind == "courses":
        result = CatalogResult(tuple(selected))
        _persist_result(config, CatalogResult(tuple(courses)), kind)
        return result
    if kind == "files":
        result = CatalogResult(
            tuple(selected),
            files=tuple(
                (course, remote)
                for course in selected
                for remote in discovery.discover_files(course)
            ),
        )
    elif kind == "articles":
        result = CatalogResult(
            tuple(selected),
            items=tuple(
                (course, item)
                for course in selected
                for item in discovery.discover_articles(course)
            ),
        )
    elif kind in {"assignments", "quizzes"}:
        coursework = tuple(
            (course, item)
            for course in selected
            for item in discovery.discover_assignments(course)
        )
        result = CatalogResult(tuple(selected), items=coursework)
    elif kind == "videos":
        result = CatalogResult(
            tuple(selected),
            modules=tuple(
                (course, item)
                for course in selected
                for item in discovery.discover_modules(course)
            ),
        )
    else:
        raise ValueError(f"unsupported catalog kind {kind!r}")
    _persist_result(config, result, kind, all_courses=tuple(courses))
    if kind in {"assignments", "quizzes"}:
        wanted = "quiz" if kind == "quizzes" else "assignment"
        return CatalogResult(
            tuple(selected),
            items=tuple(pair for pair in result.items if pair[1].kind == wanted),
        )
    return result


def refresh_catalog(
    config: Config,
    *,
    headless: bool | None = None,
    progress: Callable[[str], None] | None = None,
) -> CatalogRefreshSummary:
    """Fetch and atomically cache every canonical catalog entity."""
    if token_status(config).get("ready"):
        try:
            with CanvasClient(config) as client:
                adapter = TokenDiscoveryAdapter(client)
                if not adapter.probe():
                    raise AuthenticationRequired("Canvas token belongs to a different account")
                return _refresh_with_discovery(config, adapter, progress)
        except (AuthenticationRequired, DiscoveryError) as exc:
            LOGGER.warning("Canvas token refresh unavailable; trying browser session: %s", exc)
    with AuthenticatedLmsSession(config, headless=headless) as session:
        assert session.discovery is not None
        summary = _refresh_with_discovery(config, session.discovery, progress)
        session.persist(session.page.url)
        return summary


def _refresh_with_discovery(
    config: Config, discovery: DiscoveryBackend, progress: Callable[[str], None] | None
) -> CatalogRefreshSummary:
    courses = tuple(
        course
        for course in discovery.discover_courses()
        if course.remote_id not in config.excluded_course_ids
    )
    files_by_course: dict[str, list[RemoteFile]] = {}
    articles_by_course: dict[str, list[ContentItem]] = {}
    assignments_by_course: dict[str, list[ContentItem]] = {}
    modules_by_course: dict[str, list[ModuleItem]] = {}
    for course in courses:
        if progress is not None:
            progress(f"Refreshing {course.display_name}")
        files_by_course[course.remote_id] = discovery.discover_files(course)
        articles_by_course[course.remote_id] = discovery.discover_articles(course)
        assignments_by_course[course.remote_id] = discovery.discover_assignments(course)
        modules_by_course[course.remote_id] = discovery.discover_modules(course)

    with StateStore(config.database_path) as store, store.transaction():
        _persist_courses(store, courses)
        for course in courses:
            store.replace_catalog_files(course, files_by_course[course.remote_id])
            store.replace_catalog_content(
                course,
                ("announcement", "page"),
                articles_by_course[course.remote_id],
                scope="articles",
            )
            store.replace_catalog_content(
                course,
                ("assignment", "quiz"),
                assignments_by_course[course.remote_id],
                scope="coursework",
            )
            store.replace_catalog_modules(course, modules_by_course[course.remote_id])
        file_count = sum(map(len, files_by_course.values()))
        article_count = sum(map(len, articles_by_course.values()))
        assignment_count = sum(
            1
            for values in assignments_by_course.values()
            for item in values
            if item.kind == "assignment"
        )
        quiz_count = sum(
            1 for values in assignments_by_course.values() for item in values if item.kind == "quiz"
        )
        module_count = sum(map(len, modules_by_course.values()))
        video_count = sum(
            1
            for values in modules_by_course.values()
            for item in values
            if item.item_type.casefold() == "externaltool"
        )
        store.record_catalog_refresh("files", file_count)
        store.record_catalog_refresh("articles", article_count)
        store.record_catalog_refresh("assignments", assignment_count)
        store.record_catalog_refresh("quizzes", quiz_count)
        store.record_catalog_refresh("modules", module_count)
    return CatalogRefreshSummary(
        courses=len(courses),
        files=file_count,
        articles=article_count,
        assignments=assignment_count,
        quizzes=quiz_count,
        module_items=module_count,
        videos=video_count,
    )
