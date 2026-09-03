from snuetl.adapters import CanvasApiAdapter, _parse_next_link, _response_json
from snuetl.models import Course


def test_parse_canvas_next_link() -> None:
    header = (
        '<https://lms.test/api/v1/courses?page=1>; rel="current", '
        '<https://lms.test/api/v1/courses?page=2>; rel="next"'
    )
    assert _parse_next_link(header) == "https://lms.test/api/v1/courses?page=2"


def test_decodes_snu_json_guard() -> None:
    class GuardedResponse:
        def json(self):
            raise ValueError("guarded")

        def text(self):
            return 'while(1);[{"id": 7}]'

    assert _response_json(GuardedResponse()) == [{"id": 7}]


def test_canvas_pagination_fetches_every_page() -> None:
    class PageResponse:
        status = 200

        def __init__(self, payload, next_url=None):
            self.payload = payload
            self.headers = (
                {"link": f'<{next_url}>; rel="next"'} if next_url is not None else {}
            )

        def json(self):
            return self.payload

    class PageRequest:
        def get(self, url, **kwargs):
            if "page=2" in url:
                return PageResponse([{"id": 2}])
            return PageResponse([{"id": 1}], "https://lms.test/api/items?page=2")

    class PageContext:
        request = PageRequest()

    adapter = CanvasApiAdapter(PageContext(), "https://lms.test", timeout_seconds=5)
    assert adapter._get_pages("/api/items") == [{"id": 1}, {"id": 2}]


def test_folder_paths_follow_parent_tree() -> None:
    result = CanvasApiAdapter._folder_paths(
        [
            {"id": 1, "name": "course files", "parent_folder_id": None},
            {"id": 2, "name": "Week 1", "parent_folder_id": 1},
            {"id": 3, "name": "Slides", "parent_folder_id": 2},
        ]
    )
    assert result["1"] == ()
    assert result["2"] == ("Week 1",)
    assert result["3"] == ("Week 1", "Slides")


class _Response:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload


class _Request:
    def get(self, url, **kwargs):
        if "/announcements?" in url:
            return _Response(
                [{"id": 7, "title": "Welcome", "posted_at": "2026-09-01", "html_url": "/a/7"}]
            )
        if "/pages?" in url:
            return _Response([{"url": "syllabus", "title": "Syllabus", "updated_at": "2026-08-20"}])
        if "/assignments?" in url:
            return _Response(
                [{"id": 9, "name": "Homework 1", "due_at": "2026-09-10", "html_url": "/x/9"}]
            )
        raise AssertionError(url)


class _Context:
    request = _Request()


def test_canvas_article_and_assignment_titles() -> None:
    adapter = CanvasApiAdapter(_Context(), "https://lms.test", timeout_seconds=5)
    course = Course("101", "Algorithms", "https://lms.test/courses/101")
    articles = adapter.discover_articles(course)
    assignments = adapter.discover_assignments(course)
    assert [(item.kind, item.title) for item in articles] == [
        ("announcement", "Welcome"),
        ("page", "Syllabus"),
    ]
    assert assignments[0].title == "Homework 1"
    assert assignments[0].due_at == "2026-09-10"
