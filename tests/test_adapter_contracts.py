from __future__ import annotations

import json
from pathlib import Path

from snuetl.canvas_adapter import CanvasApiAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "canvas" / "catalog.json"


class _Response:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Request:
    def __init__(self, fixture):
        self.fixture = fixture

    def get(self, url, **_kwargs):
        if "/folders?" in url:
            key = "folders"
        elif "/files?" in url:
            key = "files"
        elif "/announcements?" in url:
            key = "announcements"
        elif "/pages?" in url:
            key = "pages"
        elif "/assignments?" in url:
            key = "assignments"
        elif "/modules?" in url:
            key = "modules"
        elif "/courses?" in url:
            key = "courses"
        else:
            raise AssertionError(url)
        return _Response(self.fixture[key])


class _Context:
    def __init__(self, fixture):
        self.request = _Request(fixture)


def test_canvas_fixture_maps_every_catalog_entity_to_domain_models() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    adapter = CanvasApiAdapter(_Context(fixture), "https://lms.test", timeout_seconds=5)

    course = adapter.discover_courses()[0]
    assert course.remote_id == "101"
    assert course.semester is not None
    assert course.semester.semester_code == "2026-2"

    remote = adapter.discover_files(course)[0]
    assert remote.folder_path == ("Week 1",)
    assert remote.content_type == "application/pdf"

    assert [(item.kind, item.title) for item in adapter.discover_articles(course)] == [
        ("announcement", "Welcome"),
        ("page", "Overview"),
    ]
    assert adapter.discover_assignments(course)[0].due_at == "2026-09-10T00:00:00Z"
    assert [(item.kind, item.title) for item in adapter.discover_assignments(course)] == [
        ("assignment", "Homework 1"),
        ("quiz", "Week 1 Quiz"),
    ]
    assert adapter.discover_modules(course)[0].title == "Lecture"
