from dataclasses import replace

import pytest

from snuetl.catalog import inspect_catalog, select_courses
from snuetl.config import load_config
from snuetl.errors import DiscoveryError
from snuetl.models import ContentItem, Course
from snuetl.state import StateStore

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


class _Discovery:
    course = Course("101", "Systems", "https://lms.test/courses/101")

    def discover_courses(self):
        return [self.course]

    def discover_assignments(self, course):
        assert course == self.course
        return [
            ContentItem(
                "a1",
                course.remote_id,
                "assignment",
                "Homework",
                "https://lms.test/assignments/a1",
            ),
            ContentItem(
                "q1",
                course.remote_id,
                "quiz",
                "Quiz",
                "https://lms.test/quizzes/q1",
            ),
        ]


class _Session:
    def __init__(self, *_args, **_kwargs):
        self.discovery = _Discovery()
        self.page = type("Page", (), {"url": "https://lms.test/"})()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def persist(self, _url):
        return None


def test_assignment_and_quiz_catalogs_are_partitioned_and_persisted(tmp_path, monkeypatch):
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    course = _Discovery.course
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.replace_catalog_content(
            course,
            ("assignment",),
            [ContentItem("q1", "101", "assignment", "Quiz", "https://lms.test/quizzes/q1")],
            scope="assignments",
        )
    monkeypatch.setattr("snuetl.catalog.AuthenticatedLmsSession", _Session)
    assignments = inspect_catalog(config, kind="assignments")
    quizzes = inspect_catalog(config, kind="quizzes")
    assert [item.kind for _course, item in assignments.items] == ["assignment"]
    assert [item.kind for _course, item in quizzes.items] == ["quiz"]
    with StateStore(config.database_path) as store:
        assert store.catalog_counts() == (0, 0, 1, 1)
        reclassified = store.db.execute(
            """SELECT content_type, available FROM catalog_content_items
               WHERE course_id='101' AND content_id='q1'
               ORDER BY content_type"""
        ).fetchall()
        assert [(row["content_type"], row["available"]) for row in reclassified] == [
            ("assignment", 0),
            ("quiz", 1),
        ]
