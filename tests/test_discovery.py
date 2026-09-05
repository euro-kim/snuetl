from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from snuetl.discovery import DiscoveryCoordinator
from snuetl.errors import AdapterUnavailable, AuthenticationRequired, DiscoveryError
from snuetl.models import Course


@dataclass
class _Backend:
    courses: list[Course] = field(default_factory=list)
    course_error: Exception | None = None
    files_error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def discover_courses(self):
        self.calls.append("courses")
        if self.course_error:
            raise self.course_error
        return self.courses

    def discover_files(self, course):
        self.calls.append(f"files:{course.remote_id}")
        if self.files_error:
            raise self.files_error
        return []

    def discover_articles(self, course):
        self.calls.append(f"articles:{course.remote_id}")
        return []

    def discover_assignments(self, course):
        self.calls.append(f"assignments:{course.remote_id}")
        return []

    def discover_modules(self, course):
        self.calls.append(f"modules:{course.remote_id}")
        return []


def test_unavailable_api_falls_back_to_dom() -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    api = _Backend(course_error=AdapterUnavailable("missing"))
    dom = _Backend(courses=[course])
    discovery = DiscoveryCoordinator(api, dom)

    assert discovery.discover_courses() == [course]
    assert not discovery.use_api
    assert api.calls == ["courses"]
    assert dom.calls == ["courses"]


def test_course_discovery_does_not_hide_nonavailability_errors() -> None:
    discovery = DiscoveryCoordinator(
        _Backend(course_error=DiscoveryError("denied")),
        _Backend(),
    )

    with pytest.raises(DiscoveryError, match="denied"):
        discovery.discover_courses()


def test_per_course_api_failure_uses_dom_but_authentication_propagates() -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    api = _Backend(courses=[course], files_error=DiscoveryError("changed"))
    dom = _Backend(courses=[course])
    discovery = DiscoveryCoordinator(api, dom)
    discovery.discover_courses()

    assert discovery.discover_files(course) == []
    assert dom.calls == ["files:101"]

    api.files_error = AuthenticationRequired("expired")
    with pytest.raises(AuthenticationRequired):
        discovery.discover_files(course)
