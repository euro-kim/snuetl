from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from snuetl.dom_adapter import DomAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "dom"


class _Locator:
    def __init__(self, nodes):
        self.nodes = list(nodes)

    def count(self):
        return len(self.nodes)

    def nth(self, index):
        return _Locator(self.nodes[index : index + 1])

    @property
    def first(self):
        return self.nth(0)

    def get_attribute(self, name):
        return self.nodes[0].get(name)

    def inner_text(self):
        return self.nodes[0].get_text(" ", strip=True)

    def locator(self, selector):
        return _Locator(self.nodes[0].select(selector))


class _Page:
    def __init__(self):
        self.url = "https://lms.test/"
        self._load("dashboard")

    def _load(self, name):
        source = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
        self.soup = BeautifulSoup(source, "html.parser")

    def goto(self, url, **_kwargs):
        self.url = url
        route = url.rstrip("/").rsplit("/", 1)[-1]
        self._load(route)

    def locator(self, selector):
        return _Locator(self.soup.select(selector))

    def wait_for_selector(self, *_args, **_kwargs):
        return None

    def wait_for_function(self, *_args, **_kwargs):
        return None


def test_dom_fixtures_map_every_catalog_entity_to_domain_models() -> None:
    page = _Page()
    adapter = DomAdapter(object(), page, "https://lms.test", timeout_seconds=1)

    course = adapter.discover_courses()[0]
    assert (course.remote_id, course.name) == ("101", "Algorithms")

    remote = adapter.discover_files(course)[0]
    assert remote.folder_path == ("Week 1",)
    assert remote.download_url.endswith("/files/201/download?download_frd=1")

    assert [(item.kind, item.title) for item in adapter.discover_articles(course)] == [
        ("announcement", "Welcome"),
        ("page", "Overview"),
    ]
    assert [(item.kind, item.title) for item in adapter.discover_assignments(course)] == [
        ("assignment", "Homework 1"),
        ("quiz", "Week 1 Quiz"),
    ]
    assert adapter.discover_modules(course)[0].title == "Lecture"
