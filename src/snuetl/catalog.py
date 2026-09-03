from __future__ import annotations

from dataclasses import dataclass

from .adapters import DiscoveryService
from .browser import (
    authenticated_entry_url,
    browser_looks_authenticated,
    open_authenticated_browser,
    persist_auth_state,
)
from .config import Config
from .errors import AuthenticationRequired, DiscoveryError
from .models import ContentItem, Course, RemoteFile
from .profile import profile_lock
from .state import StateStore


@dataclass(frozen=True, slots=True)
class CatalogResult:
    courses: tuple[Course, ...]
    files: tuple[tuple[Course, RemoteFile], ...] = ()
    items: tuple[tuple[Course, ContentItem], ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogRefreshSummary:
    courses: int
    files: int
    articles: int
    assignments: int


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
                values = [remote for owner, remote in result.files if owner.remote_id == course.remote_id]
                store.replace_catalog_files(course, values)
        elif kind == "articles":
            for course in result.courses:
                values = [item for owner, item in result.items if owner.remote_id == course.remote_id]
                store.replace_catalog_content(
                    course,
                    ("announcement", "page"),
                    values,
                    scope="articles",
                )
        elif kind == "assignments":
            for course in result.courses:
                values = [item for owner, item in result.items if owner.remote_id == course.remote_id]
                store.replace_catalog_content(
                    course,
                    ("assignment",),
                    values,
                    scope="assignments",
                )


def select_courses(courses: list[Course], query: str | None) -> list[Course]:
    if not query:
        return courses
    folded = query.casefold()
    exact = [course for course in courses if course.remote_id == query]
    if exact:
        return exact
    matches = [course for course in courses if folded in course.name.casefold()]
    if not matches:
        raise DiscoveryError(f"no active course matches {query!r}")
    if len(matches) > 1:
        names = ", ".join(f"{course.name} ({course.remote_id})" for course in matches)
        raise DiscoveryError(f"course name is ambiguous; use an ID: {names}")
    return matches


def inspect_catalog(
    config: Config,
    *,
    kind: str,
    course_query: str | None = None,
    headless: bool | None = None,
) -> CatalogResult:
    if not config.profile_dir.exists() or not config.auth_state_path.exists():
        raise AuthenticationRequired("no saved authentication session; run 'snuetl login' first")
    effective_headless = config.headless if headless is None else headless
    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=effective_headless) as (context, page),
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        if not browser_looks_authenticated(context, page):
            raise AuthenticationRequired(
                "saved browser trust is missing or expired; run 'snuetl login'"
            )
        discovery = DiscoveryService(context, page, timeout_seconds=config.timeout_seconds)
        courses = [
            course
            for course in discovery.discover_courses()
            if course.remote_id not in config.excluded_course_ids
        ]
        persist_auth_state(context, config, page.url)
        selected = select_courses(courses, course_query)
        if kind == "courses":
            result = CatalogResult(tuple(selected))
            # Course discovery itself is always global, even when future display
            # filtering is added, so persist the complete active-course snapshot.
            _persist_result(config, CatalogResult(tuple(courses)), kind)
            return result
        if kind == "files":
            files = tuple(
                (course, remote)
                for course in selected
                for remote in discovery.discover_files(course)
            )
            result = CatalogResult(tuple(selected), files=files)
            _persist_result(config, result, kind, all_courses=tuple(courses))
            return result
        if kind == "articles":
            items = tuple(
                (course, item)
                for course in selected
                for item in discovery.discover_articles(course)
            )
            result = CatalogResult(tuple(selected), items=items)
            _persist_result(config, result, kind, all_courses=tuple(courses))
            return result
        if kind == "assignments":
            items = tuple(
                (course, item)
                for course in selected
                for item in discovery.discover_assignments(course)
            )
            result = CatalogResult(tuple(selected), items=items)
            _persist_result(config, result, kind, all_courses=tuple(courses))
            return result
        raise ValueError(f"unsupported catalog kind {kind!r}")


def refresh_catalog(
    config: Config,
    *,
    headless: bool | None = None,
) -> CatalogRefreshSummary:
    """Fetch and atomically cache every canonical catalog entity."""
    if not config.profile_dir.exists() or not config.auth_state_path.exists():
        raise AuthenticationRequired("no saved authentication session; run 'snuetl login' first")
    effective_headless = config.headless if headless is None else headless
    with (
        profile_lock(config.lock_path),
        open_authenticated_browser(config, headless=effective_headless) as (context, page),
    ):
        page.goto(authenticated_entry_url(config), wait_until="domcontentloaded")
        if not browser_looks_authenticated(context, page):
            raise AuthenticationRequired(
                "saved browser trust is missing or expired; run 'snuetl login'"
            )
        discovery = DiscoveryService(context, page, timeout_seconds=config.timeout_seconds)
        courses = tuple(
            course
            for course in discovery.discover_courses()
            if course.remote_id not in config.excluded_course_ids
        )
        files_by_course: dict[str, list[RemoteFile]] = {}
        articles_by_course: dict[str, list[ContentItem]] = {}
        assignments_by_course: dict[str, list[ContentItem]] = {}
        for course in courses:
            files_by_course[course.remote_id] = discovery.discover_files(course)
            articles_by_course[course.remote_id] = discovery.discover_articles(course)
            assignments_by_course[course.remote_id] = discovery.discover_assignments(course)
        persist_auth_state(context, config, page.url)

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
                ("assignment",),
                assignments_by_course[course.remote_id],
                scope="assignments",
            )
        file_count = sum(map(len, files_by_course.values()))
        article_count = sum(map(len, articles_by_course.values()))
        assignment_count = sum(map(len, assignments_by_course.values()))
        store.record_catalog_refresh("files", file_count)
        store.record_catalog_refresh("articles", article_count)
        store.record_catalog_refresh("assignments", assignment_count)
    return CatalogRefreshSummary(
        courses=len(courses),
        files=file_count,
        articles=article_count,
        assignments=assignment_count,
    )
