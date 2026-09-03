import pytest

from snuetl.catalog import select_courses
from snuetl.errors import DiscoveryError
from snuetl.models import Course

COURSES = [
    Course("101", "Introduction to Algorithms", "https://lms.test/courses/101"),
    Course("102", "Advanced Algorithms", "https://lms.test/courses/102"),
    Course("201", "Databases", "https://lms.test/courses/201"),
]


def test_select_course_by_id_or_unique_title() -> None:
    assert select_courses(COURSES, "201") == [COURSES[2]]
    assert select_courses(COURSES, "database") == [COURSES[2]]


def test_rejects_ambiguous_or_unknown_course() -> None:
    with pytest.raises(DiscoveryError, match="ambiguous"):
        select_courses(COURSES, "algorithms")
    with pytest.raises(DiscoveryError, match="no active course"):
        select_courses(COURSES, "physics")
