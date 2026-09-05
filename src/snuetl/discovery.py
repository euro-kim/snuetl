from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, TypeVar

from .errors import AdapterUnavailable, AuthenticationRequired, DiscoveryError
from .models import ContentItem, Course, ModuleItem, RemoteFile

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class DiscoveryBackend(Protocol):
    """The LMS catalog operations supported by every discovery backend."""

    def discover_courses(self) -> list[Course]: ...

    def discover_files(self, course: Course) -> list[RemoteFile]: ...

    def discover_articles(self, course: Course) -> list[ContentItem]: ...

    def discover_assignments(self, course: Course) -> list[ContentItem]: ...

    def discover_modules(self, course: Course) -> list[ModuleItem]: ...


class DiscoveryCoordinator:
    """Prefer the Canvas API and isolate the policy for falling back to the DOM."""

    def __init__(self, api: DiscoveryBackend, dom: DiscoveryBackend):
        self.api = api
        self.dom = dom
        self.use_api = False

    def discover_courses(self) -> list[Course]:
        # Course discovery is the capability probe. Authentication failures are
        # intentionally not swallowed: a DOM fallback cannot repair an expired session.
        try:
            courses = self.api.discover_courses()
        except AuthenticationRequired:
            raise
        except AdapterUnavailable:
            LOGGER.info("authenticated course API unavailable; using DOM discovery")
            self.use_api = False
            return self.dom.discover_courses()
        self.use_api = True
        LOGGER.info("using the authenticated LearningX/Canvas API")
        return courses

    def _from_active_backend(
        self,
        operation: str,
        course: Course,
        api_call: Callable[[Course], list[T]],
        dom_call: Callable[[Course], list[T]],
    ) -> list[T]:
        if self.use_api:
            try:
                return api_call(course)
            except AuthenticationRequired:
                raise
            except DiscoveryError:
                LOGGER.warning(
                    "API operation=%s unavailable for course=%s; using DOM fallback",
                    operation.removeprefix("discover_"),
                    course.name,
                )
        return dom_call(course)

    def discover_files(self, course: Course) -> list[RemoteFile]:
        return self._from_active_backend(
            "discover_files", course, self.api.discover_files, self.dom.discover_files
        )

    def discover_articles(self, course: Course) -> list[ContentItem]:
        return self._from_active_backend(
            "discover_articles", course, self.api.discover_articles, self.dom.discover_articles
        )

    def discover_assignments(self, course: Course) -> list[ContentItem]:
        return self._from_active_backend(
            "discover_assignments",
            course,
            self.api.discover_assignments,
            self.dom.discover_assignments,
        )

    def discover_modules(self, course: Course) -> list[ModuleItem]:
        return self._from_active_backend(
            "discover_modules", course, self.api.discover_modules, self.dom.discover_modules
        )
