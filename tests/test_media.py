from __future__ import annotations

from snuetl.media import LearningXMediaResolver, MediaRequest
from snuetl.models import Course, ModuleItem


class _Response:
    status = 200
    url = "https://lcms.snu.ac.kr/viewer/ssplayer/uniplayer_support/content.php"

    def json(self):
        return {"item_content_data": {"content_id": "cms-123"}}

    def text(self):
        return '<media method="progressive" target="all">/video/[MEDIA_FILE]</media>'


class _RequestContext:
    def get(self, _url, **_kwargs):
        return _Response()


class _Context:
    request = _RequestContext()

    def cookies(self):
        return [{"name": "xn_api_token", "value": "redacted-token"}]


def test_learningx_resolver_returns_typed_lcms_candidate() -> None:
    course = Course("101", "Course", "https://lms.test/courses/101")
    item = ModuleItem(
        "module",
        "Week 1",
        "item",
        course.remote_id,
        "ExternalTool",
        "Lecture",
        external_url="https://lms.test/learningx/lecture_attendance/items/view/99",
    )

    candidates = LearningXMediaResolver().resolve(MediaRequest(_Context(), object(), course, item))

    assert len(candidates) == 1
    assert candidates[0].url == "https://lcms.snu.ac.kr/video/screen.mp4"
    assert candidates[0].referer == "https://lcms.snu.ac.kr/"
